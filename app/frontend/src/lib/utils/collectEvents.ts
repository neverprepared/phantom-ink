// Wails' EventsOff(event) removes *all* handlers for the given event —
// calling it on widget unmount nukes every other subscriber. To avoid
// that, we register a single Wails handler at module load and fan out to
// per-consumer callbacks, so each consumer can subscribe/unsubscribe
// independently.

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
  rt.EventsOn('collect:update', () => {
    for (const cb of callbacks) {
      try { cb(); } catch { /* swallow per-callback errors */ }
    }
  });
}

/** Subscribe to `collect:update`. Returns an unsubscribe function. */
export function onCollectUpdate(cb: Callback): () => void {
  install();
  callbacks.add(cb);
  return () => { callbacks.delete(cb); };
}
