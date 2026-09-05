// OA·Sanjeevani — useTelemetry React hook (ESM)
// Live /ws/telemetry stream with reconnection + Real-vs-Simulated hardware indicator.
//
//   import { useTelemetry } from "./useTelemetry.js";
//   const { frames, last, status, connected } = useTelemetry("http://127.0.0.1:8000", { window: 300 });
//
// Requires React 16.8+. Uses client.js under the hood.

import { connectTelemetry } from "./client.js";
import { useEffect, useRef, useState, useCallback } from "react";

/**
 * @param {string} baseUrl  Edge server base, e.g. "http://127.0.0.1:8000". Pass null/"" to stay idle.
 * @param {object} opts { window?: number, enabled?: boolean }  window = max frames retained for charts.
 */
export function useTelemetry(baseUrl, opts = {}) {
  const { window: keep = 300, enabled = true } = opts;
  const [frames, setFrames] = useState([]);       // ring buffer for charts
  const [last, setLast] = useState(null);          // latest frame
  const [status, setStatus] = useState({ connected: false, source: "DISCONNECTED" });
  const bufRef = useRef([]);
  const connRef = useRef(null);

  useEffect(() => {
    if (!enabled || !baseUrl) {
      setStatus({ connected: false, source: "SIMULATION" });
      return;
    }
    const conn = connectTelemetry(baseUrl, {
      onFrame: (f) => {
        const buf = bufRef.current;
        buf.push(f);
        if (buf.length > keep) buf.splice(0, buf.length - keep);
        setFrames(buf.slice());
        setLast(f);
      },
      onStatus: (s) => setStatus(s),
      onError: () => {},
    });
    connRef.current = conn;
    return () => { conn.close(); connRef.current = null; };
  }, [baseUrl, keep, enabled]);

  const clear = useCallback(() => { bufRef.current = []; setFrames([]); setLast(null); }, []);

  return {
    frames,
    last,
    status,                             // { connected, source }
    connected: status.connected,
    // "CONNECTED" (real ESP32) | "SIMULATION" (server fallback generator) | "DISCONNECTED"
    hardware: status.source,
    isSimulated: status.source === "SIMULATION",
    clear,
  };
}

export default useTelemetry;
