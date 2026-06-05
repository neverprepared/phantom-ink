/** SSE event bus — receives brainbox events forwarded from the Go SSE listener. */

import { connectionState } from './stores.svelte';
import { notifications } from './notifications.svelte';

export interface BrainboxEvent {
  type?: string;
  data?: unknown;
  raw: string;
}

let _last = $state<BrainboxEvent | null>(null);
let _log = $state<BrainboxEvent[]>([]);

export const brainboxEvents = {
  get last() { return _last; },
  get log() { return _log; },
};

// If no event arrives within 90 s, mark the connection as lost.
// Reset on every received event so normal traffic keeps the indicator green.
const HEARTBEAT_TIMEOUT_MS = 90_000;
let _heartbeatTimer: ReturnType<typeof setTimeout> | null = null;

function resetHeartbeat() {
  if (_heartbeatTimer) clearTimeout(_heartbeatTimer);
  _heartbeatTimer = setTimeout(() => {
    connectionState.disconnect();
  }, HEARTBEAT_TIMEOUT_MS);
}

function handleRaw(raw: string) {
  let parsed: BrainboxEvent;
  try {
    const data = JSON.parse(raw);
    parsed = { type: data?.type ?? raw, data, raw };

    if (data?.action === 'runner.status') {
      const runner = data.runner ?? 'runner';
      const msg = data.message ?? '';
      notifications.info(`${runner}: ${msg}`, 6000);
    }
  } catch {
    parsed = { raw };
  }
  _last = parsed;
  _log = [parsed, ..._log].slice(0, 500);
  connectionState.recordEvent(raw.slice(0, 80));
  resetHeartbeat();
}

/**
 * Start listening for brainbox events from the Go layer via window.runtime.
 * Returns a cleanup function. Call once at App.svelte mount.
 *
 * In Wails, the runtime is injected as /wails/runtime.js and exposed on
 * window.runtime. We read it at runtime to avoid build-time import errors.
 */
export function startEventListener(): () => void {
  if (typeof window === 'undefined') return () => {};

  // Wails injects window.runtime at startup
  const rt = (window as any).runtime;
  if (!rt?.EventsOn) {
    // Not running inside Wails yet (browser dev mode). Poll for readiness.
    let attempts = 0;
    const timer = setInterval(() => {
      const r = (window as any).runtime;
      if (r?.EventsOn) {
        clearInterval(timer);
        r.EventsOn('brainbox:event', handleRaw);
      }
      if (++attempts > 100) clearInterval(timer); // give up after 10s
    }, 100);
    return () => clearInterval(timer);
  }

  rt.EventsOn('brainbox:event', handleRaw);
  rt.EventsOn('app:startup-error', (msg: string) => {
    notifications.error(msg, 0); // persistent — DB failure is not transient
  });
  resetHeartbeat(); // start the watchdog; first real event will reset it
  return () => {
    if (_heartbeatTimer) clearTimeout(_heartbeatTimer);
    (window as any).runtime?.EventsOff?.('brainbox:event');
    (window as any).runtime?.EventsOff?.('app:startup-error');
  };
}
