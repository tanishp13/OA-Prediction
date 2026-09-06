# edge/models/ — trained inference artifacts

`edge/ml_service.py` loads three files from this folder at startup (lazily, on first
prediction). All three are optional — if any is missing, the service falls back to the
transparent clinical heuristic and reports `model_version: "heuristic-fallback"`.

| File | What it is | Required for live ML |
|------|------------|----------------------|
| `osteo_classifier.joblib` | Trained LightGBM classifier — either an sklearn `LGBMClassifier` (has `predict_proba`) or a raw `lightgbm.Booster` | **yes** |
| `feature_scaler.joblib` | The *fitted* scaler used in training (`StandardScaler`, `MinMaxScaler`, …). Applied with `.transform()` before predict | recommended |
| `model_metadata.json` | Feature order, version, class→grade map, risk bands | recommended |

> Binary artifacts are not tracked in this design project — drop your real
> `.joblib` files here on the deployment machine before `uvicorn edge.main:app`.

## model_metadata.json contract

```json
{
  "model_version": "osteo-lgbm-2026.09",
  "feature_order": [
    "rom_deg", "angular_velocity", "peak_force_n",
    "crepitus_energy", "spectral_peak_hz", "q_angle", "varus_valgus"
  ],
  "class_map": [0, 1, 2, 3, 4],
  "risk_bands": { "LOW": [0, 1], "MODERATE": [2], "HIGH": [3, 4] }
}
```

- **`feature_order`** — MUST match the column order the scaler + classifier were fit on.
  If omitted, the service uses `DEFAULT_FEATURE_ORDER` in `ml_service.py`. Keep the two
  in step or predictions will be silently wrong.
- **`class_map`** — maps classifier class index → Kellgren-Lawrence grade (0..4). Omit if
  your classes already ARE 0..4 (the service reads `clf.classes_` as a fallback).
- **`risk_bands`** — maps KL grade → `risk_level` string the UI shows. Defaults to
  LOW = KL 0–1, MODERATE = KL 2, HIGH = KL 3–4.

## Verifying the artifacts loaded

```
GET /api/system/status  →  { ..., "model": { "loaded": true, "version": "...",
                                              "classifier": true, "scaler": true,
                                              "metadata": true, "feature_order": [...] } }
```

If `loaded` is `false` while the files exist, the runtime is missing —
`pip install lightgbm scikit-learn joblib`.
