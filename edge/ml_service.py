import os
import json
import logging
from typing import Dict, Any, Optional
import numpy as np
import joblib

logger = logging.getLogger("edge.ml_service")

# Filepaths relative to edge directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")

MODEL_PATH = os.path.join(MODELS_DIR, "osteo_classifier.joblib")
SCALER_PATH = os.path.join(MODELS_DIR, "feature_scaler.joblib")
METADATA_PATH = os.path.join(MODELS_DIR, "model_metadata.json")


class OsteoInferenceEngine:
    def __init__(self):
        self.model = None
        self.scaler = None
        self.metadata = None
        self.is_ready = False
        self._load_artifacts()

    def _load_artifacts(self):
        """Loads trained artifacts if present, else flags fallback mode."""
        if (
            os.path.exists(MODEL_PATH)
            and os.path.exists(SCALER_PATH)
            and os.path.exists(METADATA_PATH)
        ):
            try:
                self.model = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                with open(METADATA_PATH, "r") as f:
                    self.metadata = json.load(f)
                self.is_ready = True
                logger.info("Trained clinical model and scaler loaded successfully.")
            except Exception as e:
                logger.error(f"Failed loading model artifacts: {e}. Defaulting to mock engine.")
                self.is_ready = False
        else:
            logger.warning("Model artifacts missing in edge/models/. Operating in mock heuristic mode.")
            self.is_ready = False

    def validate_inputs(self, features: Dict[str, float]) -> Optional[str]:
        """Ensures physical readings do not represent disconnected/faulty hardware."""
        bounds = {
            "q_angle": (5.0, 35.0),
            "varus_valgus_deg": (-20.0, 20.0),
            "max_flexion_deg": (0.0, 150.0),
            "peak_force_n": (0.0, 2500.0),
            "crepitus_rms": (0.0, 1.5),
            "crepitus_peak_freq": (50.0, 3000.0),
        }
        for key, (low, high) in bounds.items():
            val = features.get(key)
            if val is None or not (low <= val <= high):
                return f"Sensor out-of-bounds: '{key}' = {val}. Expected range: [{low}, {high}]."
        return None

    def _mock_predict(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Plausible clinical rule-based backup if trained weights are absent."""
        rms = features.get("crepitus_rms", 0.0)
        q_angle = features.get("q_angle", 14.0)

        if rms < 0.05 and q_angle < 16.0:
            grade, conf, risk = 0, 0.88, "Normal"
        elif rms < 0.12:
            grade, conf, risk = 1, 0.74, "Doubtful"
        elif rms < 0.25:
            grade, conf, risk = 2, 0.79, "Mild"
        elif rms < 0.40:
            grade, conf, risk = 3, 0.82, "Moderate"
        else:
            grade, conf, risk = 4, 0.91, "Severe"

        return {
            "severity_grade": grade,
            "grade_label": ["Normal", "Doubtful", "Mild", "Moderate", "Severe"][grade],
            "confidence": conf,
            "risk_level": risk,
            "is_mock_prediction": True,
            "probabilities": {f"Grade_{i}": 0.05 for i in range(5)},
            "warning": "Running on heuristic mock. Drop trained model files into edge/models/ to activate ML.",
        }

    def predict(
        self,
        q_angle: float,
        varus_valgus_deg: float,
        max_flexion_deg: float,
        peak_force_n: float,
        crepitus_rms: float,
        crepitus_peak_freq: float,
    ) -> Dict[str, Any]:
        features = {
            "q_angle": float(q_angle),
            "varus_valgus_deg": float(varus_valgus_deg),
            "max_flexion_deg": float(max_flexion_deg),
            "peak_force_n": float(peak_force_n),
            "crepitus_rms": float(crepitus_rms),
            "crepitus_peak_freq": float(crepitus_peak_freq),
        }

        # 1. Reject biomechanically invalid or saturated sensor signals
        validation_error = self.validate_inputs(features)
        if validation_error:
            return {
                "status": "REJECTED",
                "severity_grade": None,
                "error": validation_error,
                "recommendation": "Check sensor attachments, skin contact gel, and re-screen.",
            }

        # 2. Fall back to mock if training artifacts are not present
        if not self.is_ready:
            res = self._mock_predict(features)
            res["status"] = "SUCCESS"
            return res

        # 3. Assemble feature vector in the strict order defined by training metadata
        feature_order = self.metadata.get(
            "feature_order",
            [
                "q_angle",
                "varus_valgus_deg",
                "max_flexion_deg",
                "peak_force_n",
                "crepitus_rms",
                "crepitus_peak_freq",
            ],
        )
        input_row = np.array([[features[col] for col in feature_order]], dtype=np.float32)

        # 4. Standardize features using the training distribution
        input_scaled = self.scaler.transform(input_row)

        # 5. Run LightGBM inference
        # predict() on multiclass LightGBM booster returns array of probabilities per class
        probabilities = self.model.predict(input_scaled)[0]
        predicted_grade = int(np.argmax(probabilities))
        confidence = float(probabilities[predicted_grade])

        min_conf = self.metadata.get("minimum_confidence_threshold", 0.40)
        labels = self.metadata.get(
            "class_labels", ["Normal", "Doubtful", "Mild", "Moderate", "Severe"]
        )

        risk_tier = (
            "Low Risk"
            if predicted_grade <= 1
            else ("Moderate Risk" if predicted_grade == 2 else "High Risk")
        )

        result = {
            "status": "SUCCESS" if confidence >= min_conf else "LOW_CONFIDENCE",
            "severity_grade": predicted_grade,
            "grade_label": labels[predicted_grade],
            "confidence": round(confidence, 4),
            "risk_level": risk_tier,
            "is_mock_prediction": False,
            "probabilities": {
                f"Grade_{i}": round(float(p), 4) for i, p in enumerate(probabilities)
            },
        }

        if confidence < min_conf:
            result["warning"] = "Low model confidence. Clinical manual review recommended."

        return result


# Singleton instance accessible across FastAPI routers
engine = OsteoInferenceEngine()