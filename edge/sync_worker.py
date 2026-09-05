"""
edge/sync_worker.py — offline store-and-forward cloud sync.

A background loop that: (1) checks real internet reachability, (2) pulls unsynced
sessions + telemetry from SQLite, (3) uploads them gzip-compressed to the cloud
endpoint, and (4) marks rows sync_status=1 only on a verified 2xx receipt.

Safe no-op when OA_CLOUD_ENDPOINT is unset or the network is down.
"""
from __future__ import annotations

import gzip
import json
import os
import socket
import threading
import time
from typing import Dict, List, Optional
from urllib import request as urlrequest
from urllib.error import URLError

from . import database

CLOUD_ENDPOINT = os.environ.get("OA_CLOUD_ENDPOINT", "")          # e.g. https://cloud.example/api/ingest
CLOUD_TOKEN = os.environ.get("OA_CLOUD_TOKEN", "")
POLL_SECONDS = float(os.environ.get("OA_SYNC_POLL", "20"))
BATCH_SESSIONS = int(os.environ.get("OA_SYNC_BATCH_SESSIONS", "50"))
BATCH_FRAMES = int(os.environ.get("OA_SYNC_BATCH_FRAMES", "2000"))


def is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Cheap connectivity probe that doesn't depend on the cloud endpoint being up."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cloud_ready() -> bool:
    return bool(CLOUD_ENDPOINT) and is_online()


def _post_gzip(payload: Dict) -> bool:
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    req = urlrequest.Request(CLOUD_ENDPOINT, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Encoding", "gzip")
    if CLOUD_TOKEN:
        req.add_header("Authorization", f"Bearer {CLOUD_TOKEN}")
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (URLError, OSError, ValueError):
        return False


def sync_once() -> Dict[str, int]:
    """Push one batch. Returns counts actually synced. No-op if offline/unconfigured."""
    if not cloud_ready():
        return {"sessions": 0, "frames": 0, "skipped": 1}

    sessions: List[Dict] = database.pending_sessions(BATCH_SESSIONS)
    frames: List[Dict] = database.pending_frames(BATCH_FRAMES)
    if not sessions and not frames:
        return {"sessions": 0, "frames": 0}

    payload = {
        "device_id": os.environ.get("OA_DEVICE_ID", "edge-node-01"),
        "sent_at": time.time(),
        "sessions": sessions,
        "frames": frames,
    }
    if not _post_gzip(payload):
        return {"sessions": 0, "frames": 0, "error": 1}

    database.mark_synced(
        session_ids=[s["session_id"] for s in sessions],
        frame_ids=[f["id"] for f in frames],
    )
    return {"sessions": len(sessions), "frames": len(frames)}


class SyncWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_result: Dict[str, int] = {}
        self.last_run: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="oa-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.last_result = sync_once()
                self.last_run = time.time()
            except Exception as e:
                self.last_result = {"error": 1, "detail": str(e)}


worker = SyncWorker()
