/**
 * Per-listener subscriptions to a Wails event topic.
 *
 * Wails' `EventsOff(topic)` removes **every** handler for that topic, so one
 * component unmounting would silently unsubscribe every other consumer. The
 * fix is to register exactly ONE Wails handler per topic and fan out to a set
 * of callbacks, which each consumer can leave independently.
 *
 * agentEvents.ts and collectEvents.ts each implemented this, byte-identical
 * apart from one topic string and one exported name — and each documented the
 * same trap in its own words, which is how the trap then got violated
 * elsewhere (events.svelte.ts, ConversationsPanel, ProfilesPanel all still
 * called the broad form). One factory, one place to get it right.
 */

type Callback = (payload?: unknown) => void;

interface Topic {
  callbacks: Set<Callback>;
  installed: boolean;
}

const topics = new Map<string, Topic>();

function topicFor(name: string): Topic {
  let t = topics.get(name);
  if (!t) {
    t = { callbacks: new Set(), installed: false };
    topics.set(name, t);
  }
  return t;
}

function install(name: string): void {
  const t = topicFor(name);
  if (t.installed) return;

  const rt = (window as any).runtime;
  if (rt?.EventsOn) {
    installInner(name, rt);
    return;
  }

  // The Wails runtime is injected asynchronously as /wails/runtime.js, so a
  // module that loads first has to wait for it. Bounded at ~10s: past that it
  // is not coming, and an unbounded timer would outlive the page.
  let attempts = 0;
  const timer = setInterval(() => {
    const r = (window as any).runtime;
    if (r?.EventsOn) {
      clearInterval(timer);
      installInner(name, r);
      return;
    }
    if (++attempts > 100) clearInterval(timer);
  }, 100);
}

function installInner(name: string, rt: any): void {
  const t = topicFor(name);
  if (t.installed) return;
  t.installed = true;
  rt.EventsOn(name, (payload?: unknown) => {
    // Copy before iterating: a callback may unsubscribe itself, and mutating
    // the set mid-iteration would skip a sibling.
    for (const cb of [...t.callbacks]) {
      try {
        cb(payload);
      } catch {
        /* one bad consumer must not stop the others */
      }
    }
  });
}

/**
 * Subscribe to a Wails topic. Returns an unsubscribe function.
 *
 * Never call `EventsOff` on a topic used here — drop your callback instead.
 */
export function onWailsTopic(name: string, cb: Callback): () => void {
  install(name);
  const t = topicFor(name);
  t.callbacks.add(cb);
  return () => {
    t.callbacks.delete(cb);
  };
}

/** Subscribe to `agent:event` (a new/updated bus envelope). */
export function onAgentEvent(cb: Callback): () => void {
  return onWailsTopic('agent:event', cb);
}

/** Subscribe to `collect:update`. */
export function onCollectUpdate(cb: Callback): () => void {
  return onWailsTopic('collect:update', cb);
}
