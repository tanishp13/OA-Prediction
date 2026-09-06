"""
edge/cloud_receiver.py — reference regional cloud ingest + supervisor read model.

This is the OTHER side of edge/sync_worker.py: a minimal, standalone FastAPI service
that accepts the gzip-compressed store-and-forward batches each edge node pushes, upserts
them into its own SQLite database (idempotent by session_id / frame identity), and serves a
read model the regional supervisor dashboard can query across every device.

It is intentionally self-contained (no dependency on the edge package) so you can run it on a
laptop, a PHC server, or a real cloud VM unchanged. Swap the SQLite store for Postgres in
production; the ingest/read contract stays the same.

Run:   uvicorn edge.cloud_receiver:app --port 9000
Point an edge node at it:   OA_CLOUD_ENDPOINT=http://<host>:9000/api/ingest uvicorn edge.main:app
Optional shared secret:     OA_CLOUD_TOKEN=... on BOTH sides (edge sends it as a Bearer token)
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

CLOUD_DB = os.environ.get("OA_CLOUD_DB", os.path.join(os.path.dirname(__file__), "oa_cloud.db"))
CLOUD_TOKEN = os.environ.get("OA_CLOUD_TOKEN", "")

app = FastAPI(title="OA·Sanjeevani Cloud Ingest", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(CLOUD_DB, check_same_thread=False, timeout=30.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c


def _init() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS cloud_sessions (
                session_id     TEXT PRIMARY KEY,
                device_id      TEXT,
                patient_hash   TEXT,
                worker_id      TEXT,
                facility       TEXT,
                started_at     REAL,
                ended_at       REAL,
                severity_grade INTEGER,
                risk_level     TEXT,
                risk_index     INTEGER,
                confidence     REAL,
                intake_json    TEXT,
                received_at    REAL
            );
            CREATE TABLE IF NOT EXISTS cloud_frames (
                device_id    TEXT,
                frame_uid    TEXT PRIMARY KEY,   -- device_id:frame_id — idempotent across retries
                session_id   TEXT,
                ts           REAL,
                angle_deg    REAL,
                force_n      REAL,
                acoustic_rms REAL,
                peak_freq_hz REAL,
                exercise     TEXT,
                source       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_sessions_facility ON cloud_sessions(facility);
            """
        )


_init()


def _auth(authorization: Optional[str]) -> None:
    if not CLOUD_TOKEN:
        return
    if authorization != f"Bearer {CLOUD_TOKEN}":
        raise HTTPException(status_code=401, detail="Bad or missing cloud token")


@app.post("/api/ingest")
async def ingest(request: Request, authorization: Optional[str] = Header(None),
                 content_encoding: Optional[str] = Header(None)):
    _auth(authorization)
    raw = await request.body()
    if (content_encoding or "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed gzip body")
    try:
        payload: Dict[str, Any] = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    device_id = payload.get("device_id", "unknown")
    sessions: List[Dict] = payload.get("sessions", []) or []
    frames: List[Dict] = payload.get("frames", []) or []
    now = time.time()

    with _conn() as c:
        for s in sessions:
            c.execute(
                """INSERT INTO cloud_sessions
                   (session_id, device_id, patient_hash, worker_id, facility, started_at, ended_at,
                    severity_grade, risk_level, risk_index, confidence, intake_json, received_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     ended_at=excluded.ended_at, severity_grade=excluded.severity_grade,
                     risk_level=excluded.risk_level, risk_index=excluded.risk_index,
                     confidence=excluded.confidence, intake_json=excluded.intake_json,
                     received_at=excluded.received_at""",
                (s.get("session_id"), device_id, s.get("patient_hash"), s.get("worker_id"),
                 s.get("facility"), s.get("started_at"), s.get("ended_at"),
                 s.get("severity_grade"), s.get("risk_level"), s.get("risk_index"),
                 s.get("confidence"), s.get("intake_json"), now),
            )
        for f in frames:
            uid = f"{device_id}:{f.get('id')}"
            c.execute(
                """INSERT OR IGNORE INTO cloud_frames
                   (device_id, frame_uid, session_id, ts, angle_deg, force_n,
                    acoustic_rms, peak_freq_hz, exercise, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (device_id, uid, f.get("session_id"), f.get("ts"), f.get("angle_deg"),
                 f.get("force_n"), f.get("acoustic_rms"), f.get("peak_freq_hz"),
                 f.get("exercise"), f.get("source")),
            )
    # 2xx receipt → the edge marks these rows sync_status=1 and never resends them
    return {"ok": True, "sessions": len(sessions), "frames": len(frames)}


@app.get("/api/cloud/sessions")
async def cloud_sessions(facility: Optional[str] = None, limit: int = 200):
    q = "SELECT * FROM cloud_sessions"
    args: List[Any] = []
    if facility:
        q += " WHERE facility=?"
        args.append(facility)
    q += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    return rows


@app.get("/api/cloud/overview")
async def cloud_overview():
    """District roll-up for the regional supervisor dashboard."""
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM cloud_sessions").fetchone()["n"]
        high = c.execute("SELECT COUNT(*) n FROM cloud_sessions WHERE risk_level='HIGH'").fetchone()["n"]
        mod = c.execute("SELECT COUNT(*) n FROM cloud_sessions WHERE risk_level='MODERATE'").fetchone()["n"]
        low = c.execute("SELECT COUNT(*) n FROM cloud_sessions WHERE risk_level='LOW'").fetchone()["n"]
        devices = c.execute("SELECT COUNT(DISTINCT device_id) n FROM cloud_sessions").fetchone()["n"]
        facilities = [dict(r) for r in c.execute(
            "SELECT facility, COUNT(*) n FROM cloud_sessions GROUP BY facility ORDER BY n DESC"
        ).fetchall()]
    return {"total": total, "high": high, "moderate": mod, "low": low,
            "devices": devices, "facilities": facilities}


@app.get("/api/health")
async def health():
    return {"ok": True, "db": CLOUD_DB}
