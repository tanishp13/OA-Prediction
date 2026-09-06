# Frontend integration — OA·Sanjeevani edge backend

Two drop-in ESM modules that connect your existing dashboard to the FastAPI edge server
(`edge/main.py`). Everything degrades gracefully: with no server reachable, the telemetry
hook reports `SIMULATION` / `DISCONNECTED` and your UI can keep its own mock stream.

## Files

| File | What it is |
|---|---|
| `client.js` | `OAEdgeClient` — fetch wrappers for every REST endpoint + `connectTelemetry` (auto-reconnecting WebSocket). |
| `useTelemetry.js` | React hook wrapping `connectTelemetry` with a ring buffer for charts and a Real-vs-Simulated indicator. |

Both are framework-neutral ESM. `client.js` has zero dependencies; `useTelemetry.js` needs React 16.8+.

## 1. REST calls — `client.js`

```js
import { OAEdgeClient } from "./client.js";
const api = new OAEdgeClient("http://127.0.0.1:8000");

// system health (drives the CONNECTED / SIMULATION badge)
const status = await api.systemStatus();      // { hardware, db_rows, sync_ready }

// CV intake — call before screening, bind to your camera <input type="file"> or canvas.toBlob()
const cv = await api.analyzeAlignment(fileOrBlob);   // { q_angle, varus_valgus_angle, ... }

// session lifecycle
const { session_id } = await api.startSession({ patient_hash: hash, cv_metrics: cv });
// ... stream telemetry while screening ...
const ml = await api.stopSession(session_id);        // { severity_grade, confidence, biomarkers }
const report = await api.getReport(session_id);      // full summary
```

## 2. Live charts — `useTelemetry.js` (React)

```jsx
import { useTelemetry } from "./useTelemetry.js";

function LiveChart({ base }) {
  const { frames, last, hardware, connected } = useTelemetry(base, { window: 300 });
  return (
    <>
      <Badge>{connected ? hardware : "OFFLINE"}</Badge>  {/* CONNECTED | SIMULATION */}
      <Sparkline data={frames.map(f => f.acoustic_rms)} />
      <div>{last?.angle_deg}° · {last?.force_n} N</div>
    </>
  );
}
```

`hardware` is `"CONNECTED"` when a real ESP32 is on the serial port and `"SIMULATION"` when the
server is running its fallback generator — surface that difference so the clinician knows whether
the trace is real.

## 3. Binding to the existing prototype (`OA Screening.dc.html`)

The prototype already ships a **built-in connector** so you can preview against a live server
without touching its source:

1. Open the design's **Tweaks** panel and set **`backendUrl`** to your server, e.g.
   `http://127.0.0.1:8000`. Leave it blank to keep the in-browser simulation.
2. When a URL is set, the prototype probes `GET /api/system/status`, opens `WS /ws/telemetry`,
   and the sensor-feed header switches its chip from **SIMULATION** to **LIVE · CONNECTED**.
3. **Acquire signal** then drives progress from real frames; **Run inference** POSTs
   `/api/sessions/stop` and renders the returned severity grade instead of the local mock.

### Wiring your own buttons

| UI element | Call |
|---|---|
| Camera capture / file input (intake) | `api.analyzeAlignment(blob)` then keep the returned metrics for `startSession` |
| "Start screening" button | `api.startSession({ patient_hash, cv_metrics })` |
| Live chart component | `useTelemetry(base)` → plot `frames` |
| "Finish / Run inference" button | `api.stopSession(session_id)` → show grade |
| Report / referral view | `api.getReport(session_id)` |
| Connectivity badge | `api.systemStatus()` on an interval, or `status.source` from the hook |

## Notes

- CORS: `edge/main.py` ships permissive CORS for local dev — lock it down for deployment.
- The WebSocket auto-reconnects with capped exponential backoff; no action needed on drop.
- Frame shape (from the server): `{ ts, angle_deg, force_n, acoustic_rms, peak_freq_hz, source }`.
