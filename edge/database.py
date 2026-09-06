"""
edge/database.py — SQLite (WAL) store for the OA·Sanjeevani edge node.

Owns the local database: schema creation, a thread-safe connection, and typed
helpers used by the API, ingestion, CV, ML and sync layers. WAL mode keeps reads
non-blocking while the ingestion worker writes high-frequency telemetry.

Runs standalone:  python -m edge.database   (creates ./oa_edge.db and prints counts)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = os.environ.get("OA_DB_PATH", os.path.join(os.path.dirname(__file__), "oa_edge.db"))

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL: concurrent readers during writes; NORMAL sync is the durable-enough field default.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def get_conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = _connect()
        return _CONN


@contextmanager
def cursor():
    """Serialized cursor context; commits on success, rolls back on error."""
    conn = get_conn()
    with _LOCK:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS screening_sessions (
    session_id       TEXT PRIMARY KEY,
    patient_hash     TEXT NOT NULL,
    started_at       REAL NOT NULL,
    ended_at         REAL,
    -- CV alignment metrics (from edge/cv_module.py)
    q_angle          REAL,
    varus_valgus     REAL,
    mechanical_axis  REAL,
    intercondylar_mm REAL,
    -- ML prediction (from edge/ml_service.py)
    severity_grade   INTEGER,          -- ml_severity_grade (Kellgren-Lawrence 0..4)
    risk_level       TEXT,             -- LOW | MODERATE | HIGH
    risk_index       INTEGER,          -- 0..100 UI gauge value
    confidence       REAL,
    biomarkers_json  TEXT,
    doctor_notes     TEXT,
    worker_id        TEXT,
    facility         TEXT,
    intake_json      TEXT,             -- general + health questionnaire (items 4 & 5)
    device_id        TEXT NOT NULL DEFAULT 'edge-node-01',
    sync_status      INTEGER NOT NULL DEFAULT 0   -- 0 = local only, 1 = pushed to cloud
);

CREATE TABLE IF NOT EXISTS telemetry_frames (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    ts            REAL NOT NULL,
    angle_deg     REAL,
    force_n       REAL,
    acoustic_rms  REAL,
    peak_freq_hz  REAL,
    exercise      TEXT,                 -- movement routine label (item 7): flex_ext | sit_to_stand | single_leg
    source        TEXT NOT NULL DEFAULT 'SIMULATION',
    sync_status   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES screening_sessions(session_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_sync_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity      TEXT NOT NULL,        -- 'session' | 'telemetry'
    entity_id   TEXT NOT NULL,
    enqueued_at REAL NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_frames_session ON telemetry_frames(session_id);
CREATE INDEX IF NOT EXISTS idx_frames_sync    ON telemetry_frames(sync_status);
CREATE INDEX IF NOT EXISTS idx_sessions_sync  ON screening_sessions(sync_status);
"""


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
        # forward-compatible migration: add columns to a store created before they existed
        scols = {r["name"] for r in cur.execute("PRAGMA table_info(screening_sessions)").fetchall()}
        for col, ddl in (("risk_level", "TEXT"), ("risk_index", "INTEGER"),
                         ("worker_id", "TEXT"), ("facility", "TEXT"), ("intake_json", "TEXT")):
            if col not in scols:
                cur.execute(f"ALTER TABLE screening_sessions ADD COLUMN {col} {ddl}")
        fcols = {r["name"] for r in cur.execute("PRAGMA table_info(telemetry_frames)").fetchall()}
        if "exercise" not in fcols:
            cur.execute("ALTER TABLE telemetry_frames ADD COLUMN exercise TEXT")


# ─────────────────────────── sessions ───────────────────────────

def create_session(patient_hash: str, cv: Optional[Dict[str, Any]] = None,
                   doctor_notes: Optional[str] = None, device_id: str = "edge-node-01",
                   worker_id: Optional[str] = None, facility: Optional[str] = None,
                   intake: Optional[Dict[str, Any]] = None) -> str:
    sid = str(uuid.uuid4())
    cv = cv or {}
    with cursor() as cur:
        cur.execute(
            """INSERT INTO screening_sessions
               (session_id, patient_hash, started_at, q_angle, varus_valgus,
                mechanical_axis, intercondylar_mm, doctor_notes, worker_id, facility,
                intake_json, device_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, patient_hash, time.time(), cv.get("q_angle"), cv.get("varus_valgus_angle"),
             cv.get("mechanical_axis_deg"), cv.get("intercondylar_mm"), doctor_notes,
             worker_id, facility, json.dumps(intake) if intake else None, device_id),
        )
    enqueue_sync("session", sid)
    return sid


def attach_cv_metrics(session_id: str, cv: Dict[str, Any]) -> None:
    with cursor() as cur:
        cur.execute(
            """UPDATE screening_sessions
               SET q_angle=?, varus_valgus=?, mechanical_axis=?, intercondylar_mm=?, sync_status=0
               WHERE session_id=?""",
            (cv.get("q_angle"), cv.get("varus_valgus_angle"), cv.get("mechanical_axis_deg"),
             cv.get("intercondylar_mm"), session_id),
        )
    enqueue_sync("session", session_id)


def finalize_session(session_id: str, prediction: Dict[str, Any]) -> None:
    with cursor() as cur:
        cur.execute(
            """UPDATE screening_sessions
               SET ended_at=?, severity_grade=?, risk_level=?, risk_index=?,
                   confidence=?, biomarkers_json=?, sync_status=0
               WHERE session_id=?""",
            (time.time(), prediction.get("severity_grade"), prediction.get("risk_level"),
             prediction.get("risk_index"), prediction.get("confidence"),
             json.dumps(prediction.get("biomarkers", {})), session_id),
        )
    enqueue_sync("session", session_id)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM screening_sessions WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def recent_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    """Newest-first list for the supervisor dashboard (item 10)."""
    with cursor() as cur:
        rows = cur.execute(
            """SELECT session_id, patient_hash, worker_id, facility, started_at, ended_at,
                      severity_grade, risk_level, risk_index, confidence, sync_status
               FROM screening_sessions ORDER BY started_at DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── telemetry ───────────────────────────

def insert_frame(frame: Dict[str, Any], session_id: Optional[str] = None,
                 exercise: Optional[str] = None) -> None:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO telemetry_frames
               (session_id, ts, angle_deg, force_n, acoustic_rms, peak_freq_hz, exercise, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, frame.get("ts", time.time()), frame.get("angle_deg"),
             frame.get("force_n"), frame.get("acoustic_rms"), frame.get("peak_freq_hz"),
             exercise or frame.get("exercise"), frame.get("source", "SIMULATION")),
        )


def insert_frames(frames: Iterable[Dict[str, Any]], session_id: Optional[str] = None) -> int:
    rows = [
        (session_id, f.get("ts", time.time()), f.get("angle_deg"), f.get("force_n"),
         f.get("acoustic_rms"), f.get("peak_freq_hz"), f.get("source", "SIMULATION"))
        for f in frames
    ]
    if not rows:
        return 0
    with cursor() as cur:
        cur.executemany(
            """INSERT INTO telemetry_frames
               (session_id, ts, angle_deg, force_n, acoustic_rms, peak_freq_hz, source)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def frames_for_session(session_id: str) -> List[Dict[str, Any]]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM telemetry_frames WHERE session_id=? ORDER BY ts ASC", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── sync queue ───────────────────────────

def enqueue_sync(entity: str, entity_id: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO audit_sync_queue (entity, entity_id, enqueued_at) VALUES (?,?,?)",
            (entity, entity_id, time.time()),
        )


def pending_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM screening_sessions WHERE sync_status=0 AND ended_at IS NOT NULL "
            "ORDER BY started_at ASC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def pending_frames(limit: int = 2000) -> List[Dict[str, Any]]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM telemetry_frames WHERE sync_status=0 ORDER BY ts ASC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_synced(session_ids: List[str], frame_ids: List[int]) -> None:
    with cursor() as cur:
        if session_ids:
            cur.executemany("UPDATE screening_sessions SET sync_status=1 WHERE session_id=?",
                            [(s,) for s in session_ids])
        if frame_ids:
            cur.executemany("UPDATE telemetry_frames SET sync_status=1 WHERE id=?",
                            [(i,) for i in frame_ids])


def counts() -> Dict[str, int]:
    with cursor() as cur:
        s = cur.execute("SELECT COUNT(*) c FROM screening_sessions").fetchone()["c"]
        f = cur.execute("SELECT COUNT(*) c FROM telemetry_frames").fetchone()["c"]
        pend = cur.execute(
            "SELECT COUNT(*) c FROM screening_sessions WHERE sync_status=0 AND ended_at IS NOT NULL"
        ).fetchone()["c"]
    return {"sessions": s, "frames": f, "pending_sessions": pend}


if __name__ == "__main__":
    init_db()
    print("DB ready at", DB_PATH)
    print("counts:", counts())
