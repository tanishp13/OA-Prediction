// OA·Sanjeevani — Edge Backend client (ESM)
// Framework-agnostic fetch + WebSocket wrapper for the FastAPI edge server.
// Import into your React/Vue/vanilla frontend:  import { OAEdgeClient, connectTelemetry } from "./client.js";

const DEFAULT_BASE = "http://127.0.0.1:8000";

function httpToWs(base) {
  return base.replace(/^http/i, (m) => (m.toLowerCase() === "https" ? "wss" : "ws"));
}

export class OAEdgeClient {
  /**
   * @param {string} baseUrl e.g. "http://127.0.0.1:8000" (no trailing slash needed)
   */
  constructor(baseUrl = DEFAULT_BASE) {
    this.base = String(baseUrl || DEFAULT_BASE).replace(/\/+$/, "");
  }

  async _json(path, opts = {}) {
    const res = await fetch(this.base + path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (_) { detail = await res.text().catch(() => ""); }
      throw new Error(`OAEdge ${res.status} ${path}${detail ? " — " + detail : ""}`);
    }
    return res.status === 204 ? null : res.json();
  }

  // GET /api/system/status → { hardware: "CONNECTED"|"SIMULATION", db_rows, sync_ready, ... }
  systemStatus() {
    return this._json("/api/system/status", { method: "GET" });
  }

  // POST /api/sessions/start → { session_id, patient_hash, started_at, cv_baseline }
  // body: { patient_hash, cv_metrics?, doctor_notes? }
  startSession(body = {}) {
    return this._json("/api/sessions/start", { method: "POST", body: JSON.stringify(body) });
  }

  // POST /api/sessions/stop → triggers ML inference; { session_id, severity_grade, confidence, biomarkers }
  stopSession(sessionId) {
    return this._json("/api/sessions/stop", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
  }

  // GET /api/sessions/{id}/report → full summary (session, cv, telemetry aggregates, ml)
  getReport(sessionId) {
    return this._json(`/api/sessions/${encodeURIComponent(sessionId)}/report`, { method: "GET" });
  }

  // POST /api/cv/analyze-alignment (multipart) → { q_angle, varus_valgus_angle, mechanical_axis_deg, landmarks }
  // image: File | Blob (anterior-posterior view of lower limbs)
  async analyzeAlignment(image, sessionId) {
    const fd = new FormData();
    fd.append("image", image, image.name || "frame.jpg");
    if (sessionId) fd.append("session_id", sessionId);
    const res = await fetch(this.base + "/api/cv/analyze-alignment", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`OAEdge ${res.status} /api/cv/analyze-alignment`);
    return res.json();
  }

  wsUrl() {
    return httpToWs(this.base) + "/ws/telemetry";
  }
}

/**
 * connectTelemetry — resilient WebSocket to /ws/telemetry with auto-reconnect.
 * @param {string} baseUrl
 * @param {object} handlers { onFrame(frame), onStatus(status), onError(err) }
 *   status: { connected: boolean, source: "CONNECTED"|"SIMULATION"|"DISCONNECTED" }
 * @returns {{ close: () => void, isOpen: () => boolean }}
 */
export function connectTelemetry(baseUrl, handlers = {}) {
  const { onFrame = () => {}, onStatus = () => {}, onError = () => {} } = handlers;
  const url = httpToWs(String(baseUrl || DEFAULT_BASE).replace(/\/+$/, "")) + "/ws/telemetry";
  let ws = null, closed = false, attempt = 0, retryT = null;

  const open = () => {
    if (closed) return;
    try { ws = new WebSocket(url); } catch (e) { onError(e); return schedule(); }
    ws.onopen = () => { attempt = 0; onStatus({ connected: true, source: "CONNECTED" }); };
    ws.onmessage = (ev) => {
      let frame;
      try { frame = JSON.parse(ev.data); } catch (_) { return; }
      // server tags simulated frames with source: "SIMULATION"
      if (frame && frame.source) onStatus({ connected: true, source: frame.source });
      onFrame(frame);
    };
    ws.onerror = (e) => onError(e);
    ws.onclose = () => { onStatus({ connected: false, source: "DISCONNECTED" }); schedule(); };
  };

  const schedule = () => {
    if (closed) return;
    attempt += 1;
    const delay = Math.min(8000, 500 * Math.pow(1.7, attempt)); // capped exponential backoff
    clearTimeout(retryT);
    retryT = setTimeout(open, delay);
  };

  open();
  return {
    close() { closed = true; clearTimeout(retryT); if (ws) try { ws.close(); } catch (_) {} },
    isOpen() { return !!ws && ws.readyState === WebSocket.OPEN; },
  };
}

export default OAEdgeClient;
