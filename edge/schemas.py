"""
edge/schemas.py — Pydantic contracts shared across the edge API.

Grouped by boundary: hardware frames in, CV inference in/out, ML output, and the
frontend-facing session/report/status responses. Pydantic v2 preferred; falls back
to v1 import path if that's what's installed.
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:  # pydantic v2
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore


# ───────────── hardware ingestion ─────────────

class TelemetryFrame(BaseModel):
    ts: float = Field(..., description="Unix epoch seconds")
    angle_deg: float = Field(..., description="Knee flexion angle (MPU-6050)")
    force_n: float = Field(..., description="Axial load in Newtons (HX711 + load cell)")
    acoustic_rms: float = Field(..., description="INMP441 RMS acoustic energy")
    peak_freq_hz: float = Field(0.0, description="Dominant spectral peak of crepitus band")
    source: str = Field("SIMULATION", description="CONNECTED (real ESP32) | SIMULATION")


# ───────────── computer vision ─────────────

class Landmark(BaseModel):
    name: str
    x: float
    y: float
    confidence: float = 1.0


class CVAlignmentResult(BaseModel):
    q_angle: float = Field(..., description="Quadriceps angle, degrees")
    varus_valgus_angle: float = Field(..., description="+ = valgus (knock-knee), - = varus (bow-leg)")
    mechanical_axis_deg: float = Field(..., description="Hip-knee-ankle mechanical axis deviation")
    intercondylar_mm: Optional[float] = Field(None, description="Inter-condylar distance estimate")
    landmarks: List[Landmark] = []
    engine: str = Field("mediapipe", description="mediapipe | opencv-stub")
    notes: Optional[str] = None


# ───────────── machine learning ─────────────

class BiomarkerContribution(BaseModel):
    name: str
    value: float
    weight: float


class MLPrediction(BaseModel):
    session_id: str
    severity_grade: int = Field(..., ge=0, le=4, description="Kellgren-Lawrence 0..4")
    confidence: float = Field(..., ge=0.0, le=1.0)
    biomarkers: Dict[str, float] = {}
    contributions: List[BiomarkerContribution] = []
    model_version: str = "placeholder-1.0"


# ───────────── frontend API contracts ─────────────

class SessionStartRequest(BaseModel):
    patient_hash: str = Field(..., description="Anonymized patient identifier")
    cv_metrics: Optional[CVAlignmentResult] = None
    doctor_notes: Optional[str] = None


class SessionStartResponse(BaseModel):
    session_id: str
    patient_hash: str
    started_at: float
    cv_baseline: Optional[CVAlignmentResult] = None


class SessionStopRequest(BaseModel):
    session_id: str


class SessionReport(BaseModel):
    session_id: str
    patient_hash: str
    started_at: float
    ended_at: Optional[float]
    cv: Dict[str, Optional[float]]
    telemetry: Dict[str, float]          # aggregates: rom_deg, peak_force_n, mean_rms, peak_freq_hz, n_frames
    ml: Optional[MLPrediction]
    doctor_notes: Optional[str]
    sync_status: int


class SystemStatus(BaseModel):
    hardware: str = Field(..., description="CONNECTED | SIMULATION")
    serial_port: Optional[str]
    db_rows: Dict[str, int]
    sync_ready: bool
    cloud_endpoint: Optional[str]
