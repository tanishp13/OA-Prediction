"""
edge/main.py — FastAPI application + WebSocket server for the OA·Sanjeevani edge node.

Boots the SQLite store, starts the ingestion daemon (real ESP32 or simulation
fallback) and the cloud sync worker, and serves the REST + WS API the frontend uses.

Run:  uvicorn edge.main:app --reload --port 8000
Then open the prototype and set its `backendUrl` tweak to http://127.0.0.1:8000
"""



from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import database, ml_service
from .cv_module import router as cv_router
from .ingestion import daemon, ws_manager
from .schemas import (
    MLPrediction,
    SessionReport,
    SessionStartRequest,
    SessionStartResponse,
    SessionStopRequest,
    SystemStatus,
)
from .sync_worker import CLOUD_ENDPOINT, worker
from edge.ml_service import engine

app = FastAPI(title="OA·Sanjeevani Edge API", version="1.0.0")

# Permissive CORS for the local frontend / design preview. Lock down for deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cv_router)


@app.on_event("startup")
async def _startup() -> None:
    database.init_db()
    ws_manager.bind_loop(asyncio.get_running_loop())
    daemon.start()      # real serial, or simulation fallback
    worker.start()      # store-and-forward cloud sync


@app.on_event("shutdown")
async def _shutdown() -> None:
    daemon.stop()
    worker.stop()


def _dump(model) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


# ───────────────────────── sessions ─────────────────────────

@app.post("/api/sessions/start", response_model=SessionStartResponse)
async def start_session(req: SessionStartRequest):
    cv = _dump(req.cv_metrics) if req.cv_metrics else None
    sid = database.create_session(req.patient_hash, cv=cv, doctor_notes=req.doctor_notes)
    daemon.set_active_session(sid)      # subsequent telemetry frames tag onto this session
    session = database.get_session(sid)
    return SessionStartResponse(
        session_id=sid,
        patient_hash=req.patient_hash,
        started_at=session["started_at"],
        cv_baseline=req.cv_metrics,
    )


@app.post("/api/sessions/stop", response_model=MLPrediction)
async def stop_session(req: SessionStopRequest):
    session = database.get_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    daemon.set_active_session(None)     # stop tagging new frames onto this session
    prediction = ml_service.predict_for_session(req.session_id)
    database.finalize_session(req.session_id, _dump(prediction))
    return prediction


@app.get("/api/sessions/{session_id}/report", response_model=SessionReport)
async def session_report(session_id: str):
    session = database.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    frames = database.frames_for_session(session_id)

    angles = [f["angle_deg"] for f in frames if f.get("angle_deg") is not None]
    forces = [f["force_n"] for f in frames if f.get("force_n") is not None]
    gated = [f for f in frames if f.get("angle_deg") is not None and 30.0 <= f["angle_deg"] <= 65.0]
    rms = [f.get("acoustic_rms", 0.0) for f in gated]
    peaks = [f.get("peak_freq_hz", 0.0) for f in gated if f.get("peak_freq_hz")]

    telemetry = {
        "n_frames": float(len(frames)),
        "rom_deg": round((max(angles) - min(angles)) if angles else 0.0, 2),
        "peak_force_n": round(max(forces) if forces else 0.0, 2),
        "mean_rms": round(sum(rms) / len(rms), 2) if rms else 0.0,
        "peak_freq_hz": round(sum(peaks) / len(peaks), 1) if peaks else 0.0,
    }

    ml: Optional[MLPrediction] = None
    if session.get("severity_grade") is not None:
        import json
        ml = MLPrediction(
            session_id=session_id,
            severity_grade=session["severity_grade"],
            confidence=session.get("confidence") or 0.0,
            biomarkers=json.loads(session.get("biomarkers_json") or "{}"),
            model_version="stored",
        )

    return SessionReport(
        session_id=session_id,
        patient_hash=session["patient_hash"],
        started_at=session["started_at"],
        ended_at=session.get("ended_at"),
        cv={
            "q_angle": session.get("q_angle"),
            "varus_valgus": session.get("varus_valgus"),
            "mechanical_axis": session.get("mechanical_axis"),
            "intercondylar_mm": session.get("intercondylar_mm"),
        },
        telemetry=telemetry,
        ml=ml,
        doctor_notes=session.get("doctor_notes"),
        sync_status=session.get("sync_status", 0),
    )


# ───────────────────────── system ─────────────────────────

@app.get("/api/system/status", response_model=SystemStatus)
async def system_status():
    return SystemStatus(
        hardware=daemon.mode,               # CONNECTED | SIMULATION
        serial_port=daemon.port,
        db_rows=database.counts(),
        sync_ready=bool(CLOUD_ENDPOINT),
        cloud_endpoint=CLOUD_ENDPOINT or None,
    )


@app.get("/api/health")
async def health():
    return {"ok": True}


# ───────────────────────── telemetry WebSocket ─────────────────────────

@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # push the last known frame immediately so late joiners see the current state
        if daemon.last_frame:
            await ws.send_json(daemon.last_frame)
        while True:
            # keep the socket alive; ignore any client chatter (this stream is server->client)
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)

@app.post("/api/sessions/stop")
async def stop_session(payload: StopSessionRequest):
    # 1. Fetch aggregated session metrics from SQLite or active memory
    # (these variables should already be in your existing stop endpoint)
    session_id = payload.session_id
    cv_q_angle = payload.q_angle           # from CV intake
    cv_varus = payload.varus_valgus_deg    # from CV intake
    max_flex = aggregated_max_flexion      # from session telemetry
    peak_force = aggregated_peak_force     # from session telemetry
    crepitus_rms = aggregated_mean_rms     # from session telemetry
    peak_freq = aggregated_peak_freq       # from session telemetry

    # 2. Run inference via the ML engine
    diagnostic_report = engine.predict(
        q_angle=cv_q_angle,
        varus_valgus_deg=cv_varus,
        max_flexion_deg=max_flex,
        peak_force_n=peak_force,
        crepitus_rms=crepitus_rms,
        crepitus_peak_freq=peak_freq
    )

    # 3. Update the session record in SQLite with the ML grade and risk
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE screening_sessions 
            SET status = 'COMPLETED',
                ml_severity_grade = ?,
                ml_confidence = ?,
                risk_level = ?
            WHERE session_id = ?
            """,
            (
                diagnostic_report.get("severity_grade"),
                diagnostic_report.get("confidence"),
                diagnostic_report.get("risk_level"),
                session_id
            )
        )
        await db.commit()

    # 4. Return the full report back to the frontend
    return {
        "session_id": session_id,
        "status": "COMPLETED",
        "diagnostic_report": diagnostic_report
    }