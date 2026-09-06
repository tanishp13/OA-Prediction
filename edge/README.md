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
| `ml_service.py` | LightGBM pipeline: loads `models/osteo_classifier.joblib` + `feature_scaler.joblib` + `model_metadata.json`, scales the feature vector, predicts KL severity 0–4, derives `risk_level` + `risk_index`; transparent heuristic fallback. See `models/README.md`. |
| `main.py` | FastAPI app, CORS, session/report/status/list endpoints, WebSocket server, startup wiring. |
| `sync_worker.py` | Connectivity probe + gzip batched upload of unsynced rows; sets `sync_status=1` on verified receipt. |
| `cloud_receiver.py` | **Reference** regional cloud: accepts the gzip sync batches, upserts to its own SQLite, serves the supervisor read model. Run separately. |

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions/start` | Create session; persists CV markers **+ intake** (questionnaire, clinical history, worker/facility); begins tagging telemetry. |
| POST | `/api/sessions/exercise` | Label subsequent telemetry frames with the active movement routine (`flex_ext` / `sit_to_stand` / `single_leg`). |
| POST | `/api/sessions/stop` | Run the LightGBM pipeline on aggregated data; persist `ml_severity_grade` + `risk_level` + `risk_index`. |
| GET | `/api/sessions` | Newest-first session list for the supervisor dashboard. |
| GET | `/api/sessions/{id}/report` | Full summary: CV markers, telemetry aggregates, ML result. |
| POST | `/api/cv/analyze-alignment` | Upload a leg photo → alignment markers, attach to session (the frontend also computes these client-side via webcam and sends them in the start payload — no image leaves the device). |
| GET | `/api/system/status` | `CONNECTED` vs `SIMULATION`, DB row counts, sync readiness, **ML artifact status**. |
| WS | `/ws/telemetry` | High-frequency frame stream for live charts. |

## Offline resilience & cloud sync (items 9 & 10)

The node is **local-first by construction** and loses nothing when offline:

1. Every write (session, telemetry frame, CV, intake, ML result) commits to local
   SQLite in **WAL** mode immediately — no network is ever on the write path.
2. Each write also enqueues onto `audit_sync_queue` and leaves rows at `sync_status=0`.
3. `sync_worker` polls (default 20 s), probes real connectivity, and — only when online
   — pushes unsynced rows gzip-compressed to `OA_CLOUD_ENDPOINT`. Rows flip to
   `sync_status=1` **only on a verified 2xx receipt**, so an interrupted upload simply
   retries the same rows next cycle (idempotent by `session_id` / device+frame id).

> We kept the proven synchronous `sqlite3` + WAL driver rather than migrating to
> `aiosqlite`: the durability guarantee above is a function of WAL + the sync queue, not
> of async I/O. `aiosqlite` is listed in `requirements.txt` for teams who want to move the
> DB layer off the event loop later; the store-and-forward contract is unchanged.

**Run the reference cloud + supervisor read model:**

```bash
uvicorn edge.cloud_receiver:app --port 9000                 # regional cloud ingest
OA_CLOUD_ENDPOINT=http://127.0.0.1:9000/api/ingest \
  uvicorn edge.main:app --port 8000                         # edge, pointed at the cloud
```

Cloud endpoints: `POST /api/ingest` (gzip batches), `GET /api/cloud/sessions[?facility=]`,
`GET /api/cloud/overview` (district roll-up). Set a shared `OA_CLOUD_TOKEN` on both sides
to require a Bearer token. The prototype's supervisor dashboard reads live sessions from
the edge `GET /api/sessions` whenever `backendUrl` is set.

## Swapping in your trained model

Drop the three artifacts in `edge/models/` (`osteo_classifier.joblib`,
`feature_scaler.joblib`, `model_metadata.json`) — see `edge/models/README.md` for the
metadata contract. `ml_service.py` loads them lazily, applies the scaler, and maps the
classifier output to a KL grade + `risk_level`. With no files present the heuristic runs
and the API still returns a full `MLPrediction`. Confirm loading via
`GET /api/system/status → model.loaded`.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `OA_DB_PATH` | `edge/oa_edge.db` | SQLite file location. |
| `OA_SERIAL_PORT` | *(auto-detect)* | Force a serial port, e.g. `/dev/ttyUSB0`, `COM3`. |
| `OA_SERIAL_BAUD` | `115200` | Serial baud rate. |
| `OA_SAMPLE_HZ` | `50` | Simulation/serial sample rate. |
| `OA_MODELS_DIR` | `edge/models/` | Folder holding the classifier, scaler, and metadata. |
| `OA_CLOUD_ENDPOINT` | *(unset)* | Cloud ingest URL; unset disables sync. |
| `OA_CLOUD_TOKEN` | *(unset)* | Bearer token for cloud upload (set on edge **and** cloud_receiver). |

## Firmware frame format (serial → JSON per line)

```json
{"angle": 42.1, "force": 260.4, "rms": 31.7, "peak": 1450.0}
```

The reader also accepts the long keys (`angle_deg`, `force_n`, `acoustic_rms`, `peak_freq_hz`).

> Screening aid, not a diagnostic device. The bundled heuristic and stub CV are for
> integration/testing only — replace with clinically validated components before any use.
