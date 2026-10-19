/** SSE event bus — receives brainbox events forwarded from the Go SSE listener. */

import { connectionState } from './stores.svelte';
import { notifications } from './notifications.svelte';
import { onWailsTopic } from './utils/wailsTopic';

export interface BrainboxEvent {
  type?: string;
  data?: unknown;
  raw: string;
  /**
   * Epoch ms at which this event reached the app.
   *
   * Stamped HERE, on arrival, not at render time. EventLogPanel used to stamp
   * `new Date()` into a component-scoped WeakMap the first time it rendered
   * each event -- but the log is a module-level ring that outlives the panel,
   * so leaving the panel and coming back restamped all 500 historical events
   * to the moment of reopen. A debug log with timestamps that silently lie is
   * worse than one with none.
   */
  receivedAt: number;
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
  const receivedAt = Date.now();
  let parsed: BrainboxEvent;
  try {
    const data = JSON.parse(raw);
    parsed = { type: data?.type ?? raw, data, raw, receivedAt };

    if (data?.action === 'runner.status') {
      const runner = data.runner ?? 'runner';
      const msg = data.message ?? '';
      notifications.info(`${runner}: ${msg}`, 6000);
    }
  } catch {
    parsed = { raw, receivedAt };
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

  // Subscribe through the per-listener fan-out. This used to call
  // EventsOff('brainbox:event') on teardown, which removes EVERY handler for
  // that topic — including StreamPanel's — so one unmount silently
  // unsubscribed another component. wailsTopic also handles waiting for the
  // Wails runtime to be injected, which this function previously open-coded.
  const offRaw = onWailsTopic('brainbox:event', (payload) => {
    if (typeof payload === 'string') handleRaw(payload);
  });
  const offStartupError = onWailsTopic('app:startup-error', (payload) => {
    notifications.error(String(payload ?? 'startup error'), 0); // persistent — a DB failure is not transient
  });

  resetHeartbeat(); // start the watchdog; first real event will reset it
  return () => {
    if (_heartbeatTimer) clearTimeout(_heartbeatTimer);
    offRaw();
    offStartupError();
  };
}
