"""
edge/ml_service.py — OA severity inference service (LightGBM production artifacts).

Loads the trained pipeline from edge/models/:
    - osteo_classifier.joblib   the LightGBM classifier (sklearn LGBMClassifier or Booster)
    - feature_scaler.joblib     the fitted feature scaler (StandardScaler / MinMaxScaler / …)
    - model_metadata.json       feature order, class→grade map, version, risk bands

Builds a feature vector from the session's aggregated telemetry + CV markers, scales it
with the SAME scaler used in training, runs the classifier, and returns a Kellgren-Lawrence
severity grade (0..4) with confidence, a derived risk_level (LOW|MODERATE|HIGH), a 0..100
risk index, and a per-biomarker breakdown.

If the artifacts (or their runtimes) are absent, a transparent clinical heuristic stands in
so the whole system still runs end-to-end. Override paths with OA_MODELS_DIR / OA_MODEL_PATH.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

from . import database
from .schemas import BiomarkerContribution, MLPrediction

# ── artifact locations ────────────────────────────────────────────────────────
MODELS_DIR = os.environ.get("OA_MODELS_DIR", os.path.join(os.path.dirname(__file__), "models"))
CLASSIFIER_PATH = os.environ.get("OA_MODEL_PATH", os.path.join(MODELS_DIR, "osteo_classifier.joblib"))
SCALER_PATH = os.environ.get("OA_SCALER_PATH", os.path.join(MODELS_DIR, "feature_scaler.joblib"))
METADATA_PATH = os.environ.get("OA_METADATA_PATH", os.path.join(MODELS_DIR, "model_metadata.json"))

# Default ordered feature contract — OVERRIDDEN by model_metadata.json["feature_order"] when present.
DEFAULT_FEATURE_ORDER = [
    "rom_deg",           # kinematic range of motion
    "angular_velocity",  # mean |d(angle)/dt|
    "peak_force_n",      # peak axial load
    "crepitus_energy",   # angle-gated acoustic RMS envelope (30-65 deg)
    "spectral_peak_hz",  # dominant crepitus frequency
    "q_angle",           # CV morphometric
    "varus_valgus",      # CV morphometric
]

# KL grade → risk band. Overridable via metadata["risk_bands"] = {"LOW":[0,1],"MODERATE":[2],"HIGH":[3,4]}.
DEFAULT_RISK_BANDS = {"LOW": [0, 1], "MODERATE": [2], "HIGH": [3, 4]}


def grade_to_risk(grade: int, bands: Dict[str, List[int]]) -> str:
    for label, grades in bands.items():
        if grade in grades:
            return label
    return "HIGH" if grade >= 3 else "MODERATE" if grade == 2 else "LOW"


class _Pipeline:
    """Lazy loader for classifier + scaler + metadata. Tolerates missing files/runtimes."""

    def __init__(self) -> None:
        self.clf: Any = None
        self.clf_kind: Optional[str] = None      # 'sklearn' | 'booster'
        self.scaler: Any = None
        self.meta: Dict[str, Any] = {}
        self.feature_order: List[str] = list(DEFAULT_FEATURE_ORDER)
        self.risk_bands: Dict[str, List[int]] = dict(DEFAULT_RISK_BANDS)
        self.version: str = "heuristic-fallback"
        self.class_map: Optional[List[int]] = None   # classifier class index -> KL grade
        self._tried = False

    def load(self) -> None:
        if self._tried:
            return
        self._tried = True

        # metadata first (drives feature order + version + risk bands + class map)
        if os.path.exists(METADATA_PATH):
            try:
                with open(METADATA_PATH, "r", encoding="utf-8") as fh:
                    self.meta = json.load(fh) or {}
                order = (self.meta.get("feature_order") or self.meta.get("features")
                         or self.meta.get("feature_names"))
                if isinstance(order, list) and order:
                    self.feature_order = [str(x) for x in order]
                if isinstance(self.meta.get("risk_bands"), dict):
                    self.risk_bands = {k: list(v) for k, v in self.meta["risk_bands"].items()}
                self.version = str(self.meta.get("model_version") or self.meta.get("version")
                                   or "osteo_classifier")
                cm = self.meta.get("class_map") or self.meta.get("classes")
                if isinstance(cm, list) and cm:
                    self.class_map = [int(x) for x in cm]
            except Exception:
                self.meta = {}

        # scaler
        if os.path.exists(SCALER_PATH):
            try:
                import joblib
                self.scaler = joblib.load(SCALER_PATH)
            except Exception:
                self.scaler = None

        # classifier
        if os.path.exists(CLASSIFIER_PATH):
            try:
                import joblib
                obj = joblib.load(CLASSIFIER_PATH)
                if hasattr(obj, "predict_proba") or hasattr(obj, "predict"):
                    # sklearn-style estimator (LGBMClassifier, Pipeline, etc.)
                    self.clf = obj
                    self.clf_kind = "sklearn"
                if self.clf is None:
                    # raw lightgbm Booster
                    try:
                        import lightgbm as lgb  # noqa: F401
                        if obj.__class__.__name__ == "Booster":
                            self.clf = obj
                            self.clf_kind = "booster"
                    except Exception:
                        pass
                # derive class map from a fitted sklearn estimator if metadata didn't provide one
                if self.class_map is None and hasattr(self.clf, "classes_"):
                    try:
                        self.class_map = [int(c) for c in list(self.clf.classes_)]
                    except Exception:
                        self.class_map = None
                if self.clf is not None and self.version == "heuristic-fallback":
                    self.version = str(self.meta.get("model_version") or "osteo_classifier")
            except Exception:
                self.clf = None
                self.clf_kind = None

    @property
    def available(self) -> bool:
        return self.clf is not None

    def scale(self, vec: List[float]) -> List[List[float]]:
        row = [vec]
        if self.scaler is not None and hasattr(self.scaler, "transform"):
            try:
                return self.scaler.transform(row)
            except Exception:
                return row
        return row

    def _class_to_grade(self, idx: int) -> int:
        if self.class_map and 0 <= idx < len(self.class_map):
            return int(self.class_map[idx])
        return int(idx)

    def predict(self, vec: List[float]) -> Optional[Dict[str, Any]]:
        """Return {grade, confidence} from the loaded pipeline, or None to fall back."""
        if not self.available:
            return None
        X = self.scale(vec)
        try:
            if self.clf_kind == "sklearn":
                if hasattr(self.clf, "predict_proba"):
                    proba = list(self.clf.predict_proba(X)[0])
                    best = max(range(len(proba)), key=lambda i: proba[i])
                    return {"grade": max(0, min(4, self._class_to_grade(best))),
                            "confidence": round(float(proba[best]), 3)}
                grade = int(self.clf.predict(X)[0])
                return {"grade": max(0, min(4, grade)), "confidence": 0.8}
            if self.clf_kind == "booster":
                out = self.clf.predict(X)
                row = out[0]
                if hasattr(row, "__len__"):           # multiclass probability vector
                    best = max(range(len(row)), key=lambda i: row[i])
                    return {"grade": max(0, min(4, self._class_to_grade(best))),
                            "confidence": round(float(row[best]), 3)}
                grade = int(round(float(row)))         # single-output regressor/booster
                return {"grade": max(0, min(4, grade)), "confidence": 0.75}
        except Exception:
            return None
        return None


_PIPE = _Pipeline()


# ───────────────────────── feature engineering ─────────────────────────

def build_features(session: Dict[str, Any], frames: List[Dict[str, Any]]) -> Dict[str, float]:
    angles = [f["angle_deg"] for f in frames if f.get("angle_deg") is not None]
    forces = [f["force_n"] for f in frames if f.get("force_n") is not None]

    rom = (max(angles) - min(angles)) if angles else 0.0

    vel = 0.0
    if len(frames) > 1:
        deltas = []
        for a, b in zip(frames, frames[1:]):
            dt = (b.get("ts", 0) - a.get("ts", 0)) or 1e-3
            if a.get("angle_deg") is not None and b.get("angle_deg") is not None:
                deltas.append(abs(b["angle_deg"] - a["angle_deg"]) / dt)
        vel = sum(deltas) / len(deltas) if deltas else 0.0

    peak_force = max(forces) if forces else 0.0

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


def _vectorize(features: Dict[str, float], order: List[str]) -> List[float]:
    return [float(features.get(k, 0.0)) for k in order]


# ───────────────────────── heuristic fallback ─────────────────────────

def _heuristic(features: Dict[str, float]) -> Dict[str, Any]:
    """Transparent clinical stand-in mirroring the biomarker weights the model learns."""
    score = 0.0
    score += min(1.0, features["crepitus_energy"] / 45.0) * 34.0
    score += min(1.0, features["peak_force_n"] / 350.0) * 16.0
    score += min(1.0, abs(features["varus_valgus"]) / 12.0) * 16.0
    score += min(1.0, features["q_angle"] / 22.0) * 12.0
    score += (1.0 - min(1.0, features["rom_deg"] / 90.0)) * 22.0
    grade = 0 if score < 18 else 1 if score < 34 else 2 if score < 55 else 3 if score < 75 else 4
    conf = round(0.6 + min(0.35, score / 300.0), 3)
    return {"grade": grade, "confidence": conf}


def _contributions(features: Dict[str, float]) -> List[BiomarkerContribution]:
    weights = {
        "crepitus_energy": 0.34, "rom_deg": 0.22, "peak_force_n": 0.16,
        "varus_valgus": 0.16, "q_angle": 0.12,
    }
    return [BiomarkerContribution(name=k, value=features.get(k, 0.0), weight=w) for k, w in weights.items()]


def _risk_index(grade: int, confidence: float) -> int:
    """0..100 index: grade sets the band centre, confidence nudges within it."""
    base = grade * 22.0                       # 0,22,44,66,88
    return int(max(0, min(100, round(base + confidence * 12.0))))


# ───────────────────────── public API ─────────────────────────

def predict_for_session(session_id: str) -> MLPrediction:
    _PIPE.load()
    session = database.get_session(session_id)
    if session is None:
        raise ValueError(f"Unknown session {session_id}")
    frames = database.frames_for_session(session_id)
    features = build_features(session, frames)
    vec = _vectorize(features, _PIPE.feature_order)

    out = _PIPE.predict(vec)
    if out is None:
        out = _heuristic(features)
        version = "heuristic-fallback"
    else:
        version = _PIPE.version

    grade = int(out["grade"])
    confidence = float(out["confidence"])
    risk_level = grade_to_risk(grade, _PIPE.risk_bands)

    return MLPrediction(
        session_id=session_id,
        severity_grade=grade,
        risk_level=risk_level,
        risk_index=_risk_index(grade, confidence),
        confidence=confidence,
        biomarkers=features,
        contributions=_contributions(features),
        model_version=version,
    )


def artifacts_status() -> Dict[str, Any]:
    """Introspection for /api/system/status and startup logging."""
    _PIPE.load()
    return {
        "classifier": os.path.exists(CLASSIFIER_PATH),
        "scaler": os.path.exists(SCALER_PATH),
        "metadata": os.path.exists(METADATA_PATH),
        "loaded": _PIPE.available,
        "version": _PIPE.version,
        "feature_order": _PIPE.feature_order,
    }
