# OA·Sanjeevani — Edge Backend (`edge/`)

Offline-first FastAPI edge node for the OA screening rig. Runs on the field laptop/PHC
mini-PC, talks to the ESP32 sensor node over USB serial, scores screenings locally
against an ML model, and store-and-forwards to the cloud when a network appears.

**Everything runs in simulation with only FastAPI installed** — no ESP32, no MediaPipe,
no model file required. Hardware, CV, and ML each degrade to a plausible fallback.

## Quick start (simulation mode)

```bash
cd <project root>            # the folder that contains the edge/ directory
python -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" pydantic     # minimum to boot
uvicorn edge.main:app --reload --port 8000
```

Then:
- `GET http://127.0.0.1:8000/api/system/status` → `hardware: "SIMULATION"`
- Open the prototype (`OA Screening.dc.html`), open **Tweaks**, set **`backendUrl`** to
  `http://127.0.0.1:8000`. The inference screen's chip flips to **LIVE · ESP32 CONNECTED**
  once frames stream, and *Run inference* hits the real `/api/sessions/stop`.

Install the rest for real hardware/CV/ML: `pip install -r edge/requirements.txt`.

## Modules

| File | Responsibility |
|---|---|
| `database.py` | SQLite (WAL) schema + typed helpers: `screening_sessions`, `telemetry_frames`, `audit_sync_queue`. |
| `schemas.py` | Pydantic contracts for hardware frames, CV I/O, ML output, and frontend API. |
| `ingestion.py` | Auto-reconnecting pyserial reader with `SimulationMode` fallback; broadcasts to `/ws/telemetry`. |
| `cv_module.py` | `POST /api/cv/analyze-alignment` — MediaPipe pose (or geometry stub) → Q-angle / varus-valgus / mechanical axis. |
| `ml_service.py` | Feature vector + severity (KL 0–4) inference; loads Joblib/ONNX/PyTorch or uses a transparent heuristic. |
| `main.py` | FastAPI app, CORS, session/report/status endpoints, WebSocket server, startup wiring. |
| `sync_worker.py` | Connectivity probe + gzip batched upload of unsynced rows; sets `sync_status=1` on verified receipt. |

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions/start` | Create session, link CV baseline, begin tagging telemetry. |
| POST | `/api/sessions/stop` | Run ML inference on aggregated data, persist grade. |
| GET | `/api/sessions/{id}/report` | Full summary: CV markers, telemetry aggregates, ML result. |
| POST | `/api/cv/analyze-alignment` | Upload a leg photo → alignment markers, attach to session. |
| GET | `/api/system/status` | `CONNECTED` vs `SIMULATION`, DB row counts, sync readiness. |
| WS | `/ws/telemetry` | High-frequency frame stream for live charts. |

## Swapping in your trained model

Drop weights at `edge/model/oa_gbt.joblib` (or set `OA_MODEL_PATH` to a `.onnx` / `.pt`).
`ml_service._predict_with_model` already handles sklearn / ONNX / PyTorch; the feature
contract is fixed in `FEATURE_ORDER`. With no file present, the heuristic runs and the
API still returns a full `MLPrediction`.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `OA_DB_PATH` | `edge/oa_edge.db` | SQLite file location. |
| `OA_SERIAL_PORT` | *(auto-detect)* | Force a serial port, e.g. `/dev/ttyUSB0`, `COM3`. |
| `OA_SERIAL_BAUD` | `115200` | Serial baud rate. |
| `OA_SAMPLE_HZ` | `50` | Simulation/serial sample rate. |
| `OA_MODEL_PATH` | `edge/model/oa_gbt.joblib` | Serialized model. |
| `OA_CLOUD_ENDPOINT` | *(unset)* | Cloud ingest URL; unset disables sync. |
| `OA_CLOUD_TOKEN` | *(unset)* | Bearer token for cloud upload. |

## Firmware frame format (serial → JSON per line)

```json
{"angle": 42.1, "force": 260.4, "rms": 31.7, "peak": 1450.0}
```

The reader also accepts the long keys (`angle_deg`, `force_n`, `acoustic_rms`, `peak_freq_hz`).

> Screening aid, not a diagnostic device. The bundled heuristic and stub CV are for
> integration/testing only — replace with clinically validated components before any use.
