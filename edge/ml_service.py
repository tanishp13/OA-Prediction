"""
edge/ml_service.py — decoupled OA severity inference service.

Loads a serialized model (ONNX / Joblib / PyTorch state dict) if present, builds a
feature vector from the session's aggregated telemetry + CV markers, and returns a
Kellgren-Lawrence severity grade (0..4) with confidence and per-biomarker breakdown.

If no model file is found (or its runtime isn't installed), a transparent clinical
heuristic stands in so the whole system runs end-to-end. Drop your trained weights at
OA_MODEL_PATH and implement `_predict_with_model` — the feature contract stays fixed.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

from . import database
from .schemas import BiomarkerContribution, MLPrediction

MODEL_PATH = os.environ.get("OA_MODEL_PATH", os.path.join(os.path.dirname(__file__), "model", "oa_gbt.joblib"))
MODEL_VERSION = os.environ.get("OA_MODEL_VERSION", "placeholder-1.0")

# Ordered feature contract the model consumes. Keep in sync with training.
FEATURE_ORDER = [
    "rom_deg",           # kinematic range of motion
    "angular_velocity",  # mean |d(angle)/dt|
    "peak_force_n",      # peak axial load
    "crepitus_energy",   # angle-gated acoustic RMS envelope (30-65 deg)
    "spectral_peak_hz",  # dominant crepitus frequency
    "q_angle",           # CV morphometric
    "varus_valgus",      # CV morphometric
]


class _ModelHandle:
    """Lazy loader that tolerates a missing model or runtime."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.kind: Optional[str] = None
        self.obj: Any = None
        self._tried = False

    def load(self) -> None:
        if self._tried:
            return
        self._tried = True
        if not os.path.exists(self.path):
            return
        ext = os.path.splitext(self.path)[1].lower()
        try:
            if ext in (".joblib", ".pkl"):
                import joblib
                self.obj = joblib.load(self.path)
                self.kind = "sklearn"
            elif ext == ".onnx":
                import onnxruntime as ort
                self.obj = ort.InferenceSession(self.path, providers=["CPUExecutionProvider"])
                self.kind = "onnx"
            elif ext in (".pt", ".pth"):
                import torch
                self.obj = torch.load(self.path, map_location="cpu")
                self.kind = "torch"
        except Exception:
            self.obj = None
            self.kind = None

    @property
    def available(self) -> bool:
        return self.obj is not None


_MODEL = _ModelHandle(MODEL_PATH)


# ───────────────────────── feature engineering ─────────────────────────

def build_features(session: Dict[str, Any], frames: List[Dict[str, Any]]) -> Dict[str, float]:
    angles = [f["angle_deg"] for f in frames if f.get("angle_deg") is not None]
    forces = [f["force_n"] for f in frames if f.get("force_n") is not None]

    rom = (max(angles) - min(angles)) if angles else 0.0

    # mean angular velocity from consecutive samples
    vel = 0.0
    if len(frames) > 1:
        deltas = []
        for a, b in zip(frames, frames[1:]):
            dt = (b.get("ts", 0) - a.get("ts", 0)) or 1e-3
            if a.get("angle_deg") is not None and b.get("angle_deg") is not None:
                deltas.append(abs(b["angle_deg"] - a["angle_deg"]) / dt)
        vel = sum(deltas) / len(deltas) if deltas else 0.0

    peak_force = max(forces) if forces else 0.0

    # angle-gated crepitus energy: RMS only inside the 30-65 deg patellofemoral zone
    gated = [f for f in frames if f.get("angle_deg") is not None and 30.0 <= f["angle_deg"] <= 65.0]
    crepitus = (sum(f.get("acoustic_rms", 0.0) for f in gated) / len(gated)) if gated else 0.0
    peaks = [f.get("peak_freq_hz", 0.0) for f in gated if f.get("peak_freq_hz")]
    spectral_peak = (sum(peaks) / len(peaks)) if peaks else 0.0

    return {
        "rom_deg": round(rom, 3),
        "angular_velocity": round(vel, 3),
        "peak_force_n": round(peak_force, 3),
        "crepitus_energy": round(crepitus, 3),
        "spectral_peak_hz": round(spectral_peak, 1),
        "q_angle": float(session.get("q_angle") or 0.0),
        "varus_valgus": float(session.get("varus_valgus") or 0.0),
    }


def _vectorize(features: Dict[str, float]) -> List[float]:
    return [float(features.get(k, 0.0)) for k in FEATURE_ORDER]


# ───────────────────────── prediction ─────────────────────────

def _predict_with_model(vec: List[float]) -> Optional[Dict[str, Any]]:
    """Return {grade, confidence} using the loaded model, or None to fall back."""
    _MODEL.load()
    if not _MODEL.available:
        return None
    try:
        if _MODEL.kind == "sklearn":
            grade = int(_MODEL.obj.predict([vec])[0])
            conf = 0.8
            if hasattr(_MODEL.obj, "predict_proba"):
                proba = _MODEL.obj.predict_proba([vec])[0]
                conf = float(max(proba))
                grade = int(max(range(len(proba)), key=lambda i: proba[i]))
            return {"grade": max(0, min(4, grade)), "confidence": round(conf, 3)}
        if _MODEL.kind == "onnx":
            import numpy as np
            name = _MODEL.obj.get_inputs()[0].name
            out = _MODEL.obj.run(None, {name: np.array([vec], dtype="float32")})
            logits = out[0][0]
            grade = int(max(range(len(logits)), key=lambda i: logits[i]))
            exp = [math.exp(x) for x in logits]
            conf = max(exp) / (sum(exp) or 1.0)
            return {"grade": max(0, min(4, grade)), "confidence": round(float(conf), 3)}
        if _MODEL.kind == "torch":
            import torch
            model = _MODEL.obj
            if hasattr(model, "eval"):
                model.eval()
                with torch.no_grad():
                    logits = model(torch.tensor([vec], dtype=torch.float32))[0]
                grade = int(torch.argmax(logits).item())
                conf = float(torch.softmax(logits, dim=0).max().item())
                return {"grade": max(0, min(4, grade)), "confidence": round(conf, 3)}
    except Exception:
        return None
    return None


def _heuristic(features: Dict[str, float]) -> Dict[str, Any]:
    """Transparent clinical stand-in mirroring the biomarker weights the model learns."""
    score = 0.0
    score += min(1.0, features["crepitus_energy"] / 45.0) * 34.0     # acoustic crepitus dominant
    score += min(1.0, features["peak_force_n"] / 350.0) * 16.0       # loading
    score += min(1.0, abs(features["varus_valgus"]) / 12.0) * 16.0   # malalignment
    score += min(1.0, features["q_angle"] / 22.0) * 12.0             # Q-angle
    score += (1.0 - min(1.0, features["rom_deg"] / 90.0)) * 22.0     # ROM loss
    grade = 0 if score < 18 else 1 if score < 34 else 2 if score < 55 else 3 if score < 75 else 4
    conf = round(0.6 + min(0.35, score / 300.0), 3)
    return {"grade": grade, "confidence": conf, "score": round(score, 1)}


def _contributions(features: Dict[str, float]) -> List[BiomarkerContribution]:
    weights = {
        "crepitus_energy": 0.34, "rom_deg": 0.22, "peak_force_n": 0.16,
        "varus_valgus": 0.16, "q_angle": 0.12,
    }
    return [BiomarkerContribution(name=k, value=features.get(k, 0.0), weight=w) for k, w in weights.items()]


def predict_for_session(session_id: str) -> MLPrediction:
    session = database.get_session(session_id)
    if session is None:
        raise ValueError(f"Unknown session {session_id}")
    frames = database.frames_for_session(session_id)
    features = build_features(session, frames)
    vec = _vectorize(features)

    out = _predict_with_model(vec)
    if out is None:
        out = _heuristic(features)
        version = "heuristic-fallback"
    else:
        version = MODEL_VERSION

    return MLPrediction(
        session_id=session_id,
        severity_grade=int(out["grade"]),
        confidence=float(out["confidence"]),
        biomarkers=features,
        contributions=_contributions(features),
        model_version=version,
    )
