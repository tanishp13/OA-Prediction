"""
edge/ingestion.py — resilient sensor ingestion with automatic simulation fallback.

A background daemon reads JSON frames from the ESP32 over USB serial (pyserial).
If the port is missing, disconnected, or pyserial is not installed, it transparently
switches to SimulationMode, which generates medically plausible flexion cycles with
crepitus/load bursts gated to the 30-65 degree patellofemoral pressure zone.

Every frame (real or simulated) is: (1) persisted via edge.database.insert_frame,
and (2) broadcast to all connected /ws/telemetry clients through the WSManager.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional, Set

try:
    import serial  # pyserial
    from serial.tools import list_ports
    _HAVE_SERIAL = True
except Exception:
    serial = None
    list_ports = None
    _HAVE_SERIAL = False

from . import database

SERIAL_PORT = os.environ.get("OA_SERIAL_PORT", "")   # e.g. /dev/ttyUSB0 or COM3; empty = auto-detect
BAUD = int(os.environ.get("OA_SERIAL_BAUD", "115200"))
SAMPLE_HZ = float(os.environ.get("OA_SAMPLE_HZ", "50"))


# ───────────────────────── WebSocket fan-out ─────────────────────────

class WSManager:
    """Thread-safe broadcaster bridging the ingestion thread to asyncio WS clients."""

    def __init__(self) -> None:
        self._clients: Set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws) -> None:
        await ws.accept()
        with self._lock:
            self._clients.add(ws)

    def disconnect(self, ws) -> None:
        with self._lock:
            self._clients.discard(ws)

    def broadcast(self, frame: Dict[str, Any]) -> None:
        """Called from the ingestion thread; schedules sends on the event loop."""
        if not self._loop:
            return
        with self._lock:
            targets = list(self._clients)
        if not targets:
            return
        payload = json.dumps(frame)
        for ws in targets:
            asyncio.run_coroutine_threadsafe(self._safe_send(ws, payload), self._loop)

    async def _safe_send(self, ws, payload: str) -> None:
        try:
            await ws.send_text(payload)
        except Exception:
            self.disconnect(ws)


ws_manager = WSManager()


# ───────────────────────── simulation generator ─────────────────────────

class SimulationMode:
    """Generates realistic gait/flexion cycles at SAMPLE_HZ.

    - Flexion sweeps 0->90->0 degrees on a ~2.4 s cycle (sinusoidal).
    - Inside the 30-65 degree patellofemoral zone, axial load and acoustic
      crepitus energy are elevated, with occasional friction-click bursts.
    """

    ZONE_LO, ZONE_HI = 30.0, 65.0

    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)
        self.t0 = time.time()
        self.cycle_s = 2.4

    def _flexion(self, t: float) -> float:
        phase = (t % self.cycle_s) / self.cycle_s
        return 45.0 - 45.0 * math.cos(2 * math.pi * phase)  # 0..90..0

    def frame(self) -> Dict[str, Any]:
        t = time.time()
        angle = self._flexion(t - self.t0)
        in_zone = self.ZONE_LO <= angle <= self.ZONE_HI

        base_force = 180.0 + 40.0 * math.sin((t - self.t0) * 1.3)
        force = base_force + (120.0 if in_zone else 0.0) + self.rng.gauss(0, 8)

        base_rms = 12.0 + 3.0 * self.rng.random()
        crepitus = 0.0
        peak_freq = 0.0
        if in_zone:
            crepitus = 18.0 + 14.0 * self.rng.random()
            if self.rng.random() < 0.15:            # intermittent friction click
                crepitus += 20.0 + 20.0 * self.rng.random()
            peak_freq = 900.0 + self.rng.random() * 1400.0   # crepitus band 0.9-2.3 kHz
        rms = base_rms + crepitus

        return {
            "ts": t,
            "angle_deg": round(angle, 2),
            "force_n": round(max(0.0, force), 2),
            "acoustic_rms": round(rms, 2),
            "peak_freq_hz": round(peak_freq, 1),
            "source": "SIMULATION",
        }


# ───────────────────────── serial reader ─────────────────────────

def _autodetect_port() -> Optional[str]:
    if not _HAVE_SERIAL or list_ports is None:
        return None
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if any(k in desc for k in ("usb", "cp210", "ch340", "esp", "serial")):
            return p.device
    ports = list(list_ports.comports())
    return ports[0].device if ports else None


def _parse_serial_line(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)               # firmware emits JSON per line
    except Exception:
        return None
    return {
        "ts": time.time(),
        "angle_deg": float(obj.get("angle", obj.get("angle_deg", 0.0))),
        "force_n": float(obj.get("force", obj.get("force_n", 0.0))),
        "acoustic_rms": float(obj.get("rms", obj.get("acoustic_rms", 0.0))),
        "peak_freq_hz": float(obj.get("peak", obj.get("peak_freq_hz", 0.0))),
        "source": "CONNECTED",
    }


class IngestionDaemon:
    """Auto-reconnecting reader. Falls back to SimulationMode whenever serial is unavailable."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mode = "SIMULATION"
        self._port: Optional[str] = None
        self._sim = SimulationMode()
        self._session_id: Optional[str] = None   # frames tag onto the active session, if any
        self._exercise: Optional[str] = None      # current movement routine label (item 7)
        self._last_frame: Optional[Dict[str, Any]] = None

    # public state -------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def port(self) -> Optional[str]:
        return self._port

    @property
    def last_frame(self) -> Optional[Dict[str, Any]]:
        return self._last_frame

    def set_active_session(self, session_id: Optional[str]) -> None:
        self._session_id = session_id

    def set_active_exercise(self, exercise: Optional[str]) -> None:
        self._exercise = exercise

    # lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="oa-ingestion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # main loop ----------------------------------------------------
    def _run(self) -> None:
        period = 1.0 / SAMPLE_HZ
        while not self._stop.is_set():
            ser = self._try_open_serial()
            if ser is None:
                self._mode = "SIMULATION"
                self._simulate_until_reconnect(period)
            else:
                self._mode = "CONNECTED"
                self._read_serial(ser, period)

    def _try_open_serial(self):
        if not _HAVE_SERIAL:
            return None
        port = SERIAL_PORT or _autodetect_port()
        if not port:
            return None
        try:
            ser = serial.Serial(port, BAUD, timeout=1.0)
            self._port = port
            return ser
        except Exception:
            self._port = None
            return None

    def _read_serial(self, ser, period: float) -> None:
        try:
            while not self._stop.is_set():
                raw = ser.readline().decode("utf-8", errors="ignore")
                frame = _parse_serial_line(raw)
                if frame is None:
                    # no valid data this tick — keep the UI alive with a heartbeat sim frame
                    continue
                self._emit(frame)
        except Exception:
            # device yanked mid-stream → drop to simulation on next loop
            self._port = None
        finally:
            try:
                ser.close()
            except Exception:
                pass

    def _simulate_until_reconnect(self, period: float) -> None:
        # Emit simulated frames; periodically re-probe for a real device.
        probe_every = max(1, int(3.0 / period))
        i = 0
        while not self._stop.is_set():
            self._emit(self._sim.frame())
            time.sleep(period)
            i += 1
            if i % probe_every == 0 and _HAVE_SERIAL and (SERIAL_PORT or _autodetect_port()):
                return  # a device appeared — break out so _run reopens serial

    def _emit(self, frame: Dict[str, Any]) -> None:
        self._last_frame = frame
        if self._exercise:
            frame = dict(frame, exercise=self._exercise)
        try:
            database.insert_frame(frame, self._session_id, self._exercise)
        except Exception:
            pass
        ws_manager.broadcast(frame)


daemon = IngestionDaemon()
