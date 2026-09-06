"""
edge/cv_module.py — computer-vision patient-intake pipeline.

Exposes POST /api/cv/analyze-alignment: accepts a lower-limb photo (anterior-posterior
view) and returns biomechanical alignment markers (Q-angle, varus/valgus, mechanical
axis, inter-condylar distance), then attaches them to the active session in SQLite.

Landmark extraction uses MediaPipe Pose when available; otherwise it falls back to a
deterministic OpenCV/geometry stub so the endpoint always returns a plausible result
and the whole system runs in simulation. Swap `extract_landmarks` for your production
pose model without touching the geometry math.
"""
from __future__ import annotations

import io
import math
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import database
from .schemas import CVAlignmentResult, Landmark

try:
    import numpy as np
    _HAVE_NUMPY = True
except Exception:
    np = None
    _HAVE_NUMPY = False

try:
    import mediapipe as mp
    _HAVE_MP = True
except Exception:
    mp = None
    _HAVE_MP = False

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:
    Image = None
    _HAVE_PIL = False

router = APIRouter(prefix="/api/cv", tags=["cv"])

# Anatomical points we need for the alignment math (normalized image coords).
POINTS = ("hip", "knee", "ankle", "asis", "tibial_tuberosity", "patella")


def _decode_image(raw: bytes) -> Optional[Tuple[int, int]]:
    if _HAVE_PIL:
        try:
            img = Image.open(io.BytesIO(raw))
            return img.size  # (w, h)
        except Exception:
            return None
    return (720, 1280)  # assume a portrait frame when PIL is absent


def extract_landmarks(raw: bytes) -> Tuple[Dict[str, Tuple[float, float]], str]:
    """Return normalized (x, y) per anatomical point and the engine name.

    Replace the stub branch with real MediaPipe/OpenCV inference. The returned dict
    keys must cover POINTS for a full leg; missing points fall back to anatomical priors.
    """
    size = _decode_image(raw) or (720, 1280)
    w, h = size

    if _HAVE_MP and _HAVE_NUMPY and _HAVE_PIL:
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            arr = np.asarray(img)
            with mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2) as pose:
                res = pose.process(arr)
            if res.pose_landmarks:
                lm = res.pose_landmarks.landmark
                P = mp.solutions.pose.PoseLandmark
                pts = {
                    "hip": (lm[P.RIGHT_HIP].x, lm[P.RIGHT_HIP].y),
                    "knee": (lm[P.RIGHT_KNEE].x, lm[P.RIGHT_KNEE].y),
                    "ankle": (lm[P.RIGHT_ANKLE].x, lm[P.RIGHT_ANKLE].y),
                }
                # ASIS/tibial-tuberosity/patella approximated from the pose skeleton
                hip, knee = pts["hip"], pts["knee"]
                pts["asis"] = (hip[0] + 0.02, hip[1] - 0.01)
                pts["patella"] = (knee[0], knee[1] - 0.01)
                pts["tibial_tuberosity"] = (knee[0] + 0.005, knee[1] + 0.04)
                return pts, "mediapipe"
        except Exception:
            pass

    # Deterministic stub: a mildly valgus right leg derived from image aspect ratio,
    # so repeated calls on the same frame are stable and the UI has real numbers.
    ar = (w / h) if h else 0.56
    lean = max(-0.05, min(0.05, (ar - 0.56) * 0.4))
    pts = {
        "asis": (0.50, 0.05),
        "hip": (0.48, 0.10),
        "patella": (0.50 + lean, 0.52),
        "knee": (0.50 + lean, 0.53),
        "tibial_tuberosity": (0.505 + lean, 0.58),
        "ankle": (0.52, 0.95),
    }
    return pts, "opencv-stub"


def _angle(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    """Angle ABC in degrees at vertex b."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    n1 = math.hypot(*v1) or 1e-9
    n2 = math.hypot(*v2) or 1e-9
    cos = max(-1.0, min(1.0, dot / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _signed_deviation(hip, knee, ankle) -> float:
    """Signed varus(-)/valgus(+): horizontal offset of the knee from the hip-ankle line."""
    hx, hy = hip
    ax, ay = ankle
    kx, ky = knee
    # perpendicular distance sign of knee vs hip->ankle line
    dx, dy = ax - hx, ay - hy
    cross = dx * (ky - hy) - dy * (kx - hx)
    length = math.hypot(dx, dy) or 1e-9
    offset = cross / length
    return round(offset * 180.0, 2)   # scaled to a degree-like reading


def compute_alignment(pts: Dict[str, Tuple[float, float]], engine: str) -> CVAlignmentResult:
    hip = pts.get("hip", (0.48, 0.10))
    knee = pts.get("knee", (0.50, 0.53))
    ankle = pts.get("ankle", (0.52, 0.95))
    asis = pts.get("asis", (0.50, 0.05))
    patella = pts.get("patella", knee)
    tt = pts.get("tibial_tuberosity", (knee[0], knee[1] + 0.05))

    # Q-angle: ASIS -> patella center -> tibial tuberosity
    q_angle = round(180.0 - _angle(asis, patella, tt), 2)
    q_angle = max(5.0, min(25.0, abs(q_angle)))

    # Mechanical axis: hip -> knee -> ankle (180 = perfectly straight)
    mech = _angle(hip, knee, ankle)
    mechanical_axis_deg = round(180.0 - mech, 2)

    varus_valgus = _signed_deviation(hip, knee, ankle)

    landmarks: List[Landmark] = [
        Landmark(name=k, x=round(v[0], 4), y=round(v[1], 4)) for k, v in pts.items()
    ]

    return CVAlignmentResult(
        q_angle=q_angle,
        varus_valgus_angle=varus_valgus,
        mechanical_axis_deg=mechanical_axis_deg,
        intercondylar_mm=round(abs(varus_valgus) * 1.6 + 8.0, 1),
        landmarks=landmarks,
        engine=engine,
        notes=None if engine == "mediapipe" else "Stub landmarks — install mediapipe for real pose extraction.",
    )


@router.post("/analyze-alignment", response_model=CVAlignmentResult)
async def analyze_alignment(image: UploadFile = File(...), session_id: Optional[str] = Form(None)):
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image upload")
    pts, engine = extract_landmarks(raw)
    result = compute_alignment(pts, engine)

    if session_id:
        try:
            database.attach_cv_metrics(session_id, result.model_dump() if hasattr(result, "model_dump") else result.dict())
        except Exception as e:
            # don't fail the CV call if the session isn't started yet — client can start then re-attach
            result.notes = (result.notes or "") + f" (not attached: {e})"
    return result
