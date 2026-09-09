// Fan-out for the Wails `agent:event` topic (the bus SSE relay, emitted by
// app.go). Like collectEvents.ts, we register ONE Wails handler at module load
// and fan out to per-consumer callbacks — calling Wails' EventsOff(topic) would
// remove every subscriber's handler, so we never call it; consumers unsubscribe
// by dropping their callback from the set.

type Callback = () => void;

const callbacks = new Set<Callback>();
let installed = false;

function install(): void {
  if (installed) return;
  const rt = (window as any).runtime;
  if (!rt?.EventsOn) {
    // Wails runtime isn't ready yet — poll briefly and retry.
    let attempts = 0;
    const timer = setInterval(() => {
      const r = (window as any).runtime;
      if (r?.EventsOn) { clearInterval(timer); installInner(r); }
      if (++attempts > 100) clearInterval(timer);
    }, 100);
    return;
  }
  installInner(rt);
}

function installInner(rt: any): void {
  if (installed) return;
  installed = true;
  rt.EventsOn('agent:event', () => {
    for (const cb of callbacks) {
      try { cb(); } catch { /* swallow per-callback errors */ }
    }
  });
}

/** Subscribe to `agent:event` (a new/updated bus envelope). Returns unsubscribe. */
export function onAgentEvent(cb: Callback): () => void {
  install();
  callbacks.add(cb);
  return () => { callbacks.delete(cb); };
}
