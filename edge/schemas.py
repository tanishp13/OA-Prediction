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
    severity_grade: int = Field(..., ge=0, le=4, description="Kellgren-Lawrence 0..4 (stored as ml_severity_grade)")
    risk_level: str = Field("LOW", description="LOW | MODERATE | HIGH — derived from grade + risk bands")
    risk_index: int = Field(0, ge=0, le=100, description="0..100 risk index for the UI gauge")
    confidence: float = Field(..., ge=0.0, le=1.0)
    biomarkers: Dict[str, float] = {}
    contributions: List[BiomarkerContribution] = []
    model_version: str = "placeholder-1.0"


# ───────────── frontend API contracts ─────────────

class ClinicalIntake(BaseModel):
    """General + health questionnaire captured at patient intake (items 4 & 5)."""
    age: Optional[int] = None
    gender: Optional[str] = None
    joint: Optional[str] = None
    vas: Optional[int] = Field(None, ge=0, le=10, description="Pain score 0..10")
    questionnaire: Dict[str, object] = {}       # weighted screening answers, keyed by question id
    questionnaire_score: Optional[int] = None    # normalized 0..100 subscore
    history: Dict[str, object] = {}              # clinical history flags (injury, comorbidity, …)


class SessionStartRequest(BaseModel):
    patient_hash: str = Field(..., description="Anonymized patient identifier")
    cv_metrics: Optional[CVAlignmentResult] = None
    doctor_notes: Optional[str] = None
    worker_id: Optional[str] = None
    facility: Optional[str] = None
    intake: Optional[ClinicalIntake] = None


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
    ml_severity_grade: Optional[int] = None   # convenience mirror of ml.severity_grade
    risk_level: Optional[str] = None          # convenience mirror of ml.risk_level
    doctor_notes: Optional[str]
    sync_status: int


class SystemStatus(BaseModel):
    hardware: str = Field(..., description="CONNECTED | SIMULATION")
    serial_port: Optional[str]
    db_rows: Dict[str, int]
    sync_ready: bool
    cloud_endpoint: Optional[str]
    model: Optional[Dict[str, object]] = None   # ml artifact status (loaded, version, feature_order)


class SessionSummary(BaseModel):
    """Row shape for the supervisor dashboard list (GET /api/sessions)."""
    session_id: str
    patient_hash: str
    worker_id: Optional[str] = None
    facility: Optional[str] = None
    started_at: float
    ended_at: Optional[float] = None
    severity_grade: Optional[int] = None
    risk_level: Optional[str] = None
    risk_index: Optional[int] = None
    confidence: Optional[float] = None
    sync_status: int = 0
