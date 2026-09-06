# AI-Assisted Osteoarthritis (OA) Detection — Screening Prototype

A low-cost, field-deployable prototype for early knee osteoarthritis **risk stratification** and **case finding** in settings with little or no orthopaedic access (e.g. rural health camps). Built for a hackathon-style sprint, it pairs a phone-only screening app with an optional ~₹2,850 wearable sensor rig.

> **Not a diagnostic device.** This is an untested student/hackathon prototype. It is a screening aid, not a clinical diagnosis. See [Limitations](#limitations--honest-scope) below.

## Core idea

Most gait- or camera-based OA screening tools measure a *downstream* signal: a person changes how they walk only after they're already in pain, which itself follows structural joint change. This prototype instead prioritizes two more **upstream** signals:

- **Joint acoustic emission** (crepitus) — sound at the joint surface itself, captured with contact microphones and analyzed per degree of knee flexion.
- **Quadriceps strength** — isometric torque via a load cell, one of the best-evidenced predictors of incident symptomatic knee OA.

Gait, range of motion (ROM), and functional tests (chair-stand, Timed Up and Go) are used for **severity/functional staging**, not early detection.

The system produces two separate, never-conflated outputs:

| Track | Question it answers | Driven by |
|---|---|---|
| **Track 1 — Risk** | Will this person develop OA? | Validated questionnaire (Nottingham knee OA risk model) |
| **Track 2 — Case finding** | Is this person already symptomatic but undiagnosed? | Sensor data (acoustic, strength, ROM, temperature) |

## What's in this repo

| File / folder | What it is |
|---|---|
| `OA Screening.dc.html` | Interactive app prototype (design-canvas export) — field health-worker screening flow **and** the PHC/district supervisor dashboard, rendered as an Android-device mockup. Open in a browser to view/click through. |
| `android-frame.jsx` | Reusable Material 3 "Android device frame" component (status bar, app bar, gesture nav) used to present the mockup screens. |
| `support.js` | Generated runtime bundle the prototype HTML depends on (compiled from an internal `dc-runtime` toolchain — not hand-edited). |
| `_ds/` | Design-system assets (styles, lint config, bundle, manifest) backing the prototype's look and feel. |
| `uploads/OA-Screening-Prototype-Build-Guide.md` | Full hardware build guide: bill of materials (~₹2,850), wiring/pin map, per-sensor calibration and protocols, firmware setup, data flow, draft screening thresholds, day-by-day build plan, and pitch/demo notes. |
| `uploads/SPRINT-PLAN.md` | 5-day, 6-person sprint plan: pod structure, daily gates, demo script, risk register, and definition of done. |
| `github.md` | Sync notes — the linked source repo (`tanishp13/OA-Prediction`) was empty at last sync, so this prototype was authored directly from the build guide and sprint plan rather than imported code. |
| `.thumbnail` | Preview image of the prototype. |

**Note:** the build guide and sprint plan reference firmware/analysis files (`oa_sensor_node.ino`, `mock_node.py`, `vag_features.py`) as part of the intended full build — those are not included in this export; only the UI prototype and planning docs are.

## Screening pipeline (as designed)

```
ESP32 (SoftAP "OA-Screen")
  ├─ WebSocket /ws
  │    ├─ JSON @50 Hz : knee angle, thigh/shank IMU, force (kg), temp (°C)
  │    └─ Binary: stereo 16 kHz audio (joint acoustic emission)
  └─ HTTP → Offline-first PWA
        ├─ IndexedDB storage + background sync
        ├─ Feature extraction (per modality)
        └─ Per-modality scoring → logit-space fusion → report + referral slip
```

Sensors used: 2× MPU6050 (knee flexion via complementary filter), 2× INMP441 MEMS mics (acoustic emission, medial + lateral joint line), load cell + HX711 (isometric quadriceps torque), optional MLX90614 (left–right knee ΔT). Full wiring, calibration steps, and protocols are in the [build guide](uploads/OA-Screening-Prototype-Build-Guide.md).

A **Tier 0, phone-only mode** (risk questionnaire, 30-second chair-stand test, Timed Up and Go, camera-based ROM/gait via MediaPipe Pose) works with zero hardware and is the fallback if the sensor rig fails.

## Viewing the prototype

Open `OA Screening.dc.html` directly in a browser — it's a self-contained mockup (no build step, no server required) covering:

- Field health-worker screening flow (patient registration, questionnaire, guided sensor capture, per-channel report, referral slip)
- PHC/district supervisor dashboard (screening list, risk distribution, case tracking)

## Running frontend and backend together

On Windows, install the backend dependencies and run `start-local.ps1` from the project root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r edge\requirements.txt
.\start-local.ps1
```

This starts the FastAPI backend on `http://127.0.0.1:8000` and the frontend on `http://127.0.0.1:8080/OA%20Screening.dc.html`. The frontend is configured to connect to the backend automatically. Screening records are written first to `edge/oa_edge.db`, then remain queued until a configured cloud receiver acknowledges them.

For production or Vercel, do not use the local SQLite file as the source of truth. Vercel deployments need a managed durable database or a separately hosted cloud receiver; configure `OA_CLOUD_ENDPOINT` and `OA_CLOUD_TOKEN` so the edge node forwards records and only marks them synced after a successful response.

## Limitations & honest scope

- Crepitus (joint sound) occurs in some asymptomatic knees too, so **specificity is the acknowledged weak point** of the acoustic channel.
- True early *structural* detection would require MRI (T2/T1ρ); this system is positioned as **high-yield case finding and risk stratification**, with portable ultrasound at a district hospital as the confirmatory step — not a replacement for clinical diagnosis.
- Draft screening thresholds in the build guide are starting points from published norms (CDC STEADI, Nottingham risk model, etc.) and are **not clinically validated** for this device.
- Any human testing should follow the consent and safety notes in the build guide (verbal consent, no unnecessary identifying data, no diagnostic claims, and screening out uncontrolled hypertension/recent cardiac events from the maximal-strength test).

## Estimated cost

Core sensor kit: **~₹2,850** (~$34) in parts, sourced online (Robu.in, Robocraze, Sunrom, Amazon.in) or same-day from the Chenoy Trade Centre / SD Road electronics market in Secunderabad, Hyderabad.

## Further reading

- [`uploads/OA-Screening-Prototype-Build-Guide.md`](uploads/OA-Screening-Prototype-Build-Guide.md) — bill of materials, wiring, calibration, day-by-day build, pitch notes
- [`uploads/SPRINT-PLAN.md`](uploads/SPRINT-PLAN.md) — team structure, daily gates, demo script, risk register
