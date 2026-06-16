/**
 * Module-level metrics history stores.
 * Survives panel navigation because they live outside any component.
 * 15-minute window at 10 s poll interval = 90 samples.
 */

const MAX_SAMPLES = 90;

export interface CombinedSample {
  ts: number;          // unix seconds (float)
  agent_count: number;
  total_cpu: number;
  total_mem: number;   // bytes
}

export interface LocalSample {
  ts: number;  // Date.now() ms
  cpu: number;
  mem: number; // MB
}

export interface DiskSample {
  ts: number;    // Date.now() ms
  bytes: number; // writable layer bytes
}

// Aggregate history: Docker containers + local Claude processes combined.
let _combinedHistory = $state<CombinedSample[]>([]);

export const combinedHistory = {
  get value() { return _combinedHistory; },
  push(sample: CombinedSample) {
    _combinedHistory = [..._combinedHistory, sample].slice(-MAX_SAMPLES);
  },
};

// Per-container disk history keyed by container name.
let _diskHistory = $state<Record<string, DiskSample[]>>({});

export const diskHistory = {
  get value() { return _diskHistory; },
  update(key: string, sample: DiskSample) {
    const prev = _diskHistory[key] ?? [];
    _diskHistory = { ..._diskHistory, [key]: [...prev, sample].slice(-MAX_SAMPLES) };
  },
  pruneKeys(activeKeys: Set<string>) {
    const next: Record<string, DiskSample[]> = {};
    for (const k of activeKeys) {
      if (_diskHistory[k]) next[k] = _diskHistory[k];
    }
    _diskHistory = next;
  },
};

// Per-runner queue depth history keyed by runner name. Records each sample
// from the RunnersPanel 5s poll so the table can render a sparkline and a
// dashboard widget can surface the 1h peak without server-side history.
export interface RunnerQueueSample {
  ts: number;    // Date.now() ms
  depth: number; // queue_depth at the time
  inflight: number;
}

let _runnerQueueHistory = $state<Record<string, RunnerQueueSample[]>>({});

export const runnerQueueHistory = {
  get value() { return _runnerQueueHistory; },
  update(name: string, sample: RunnerQueueSample) {
    const prev = _runnerQueueHistory[name] ?? [];
    _runnerQueueHistory = { ..._runnerQueueHistory, [name]: [...prev, sample].slice(-MAX_SAMPLES) };
  },
  pruneKeys(activeKeys: Set<string>) {
    const next: Record<string, RunnerQueueSample[]> = {};
    for (const k of activeKeys) {
      if (_runnerQueueHistory[k]) next[k] = _runnerQueueHistory[k];
    }
    _runnerQueueHistory = next;
  },
  /** Max queue depth observed for `name` within the last `windowMs`. */
  peak(name: string, windowMs: number): number {
    const cutoff = Date.now() - windowMs;
    return (_runnerQueueHistory[name] ?? [])
      .filter(s => s.ts >= cutoff)
      .reduce((acc, s) => Math.max(acc, s.depth), 0);
  },
  /** Peak across all runners — convenience for dashboard widgets. */
  peakAll(windowMs: number): number {
    let peak = 0;
    for (const samples of Object.values(_runnerQueueHistory)) {
      const cutoff = Date.now() - windowMs;
      for (const s of samples) {
        if (s.ts >= cutoff && s.depth > peak) peak = s.depth;
      }
    }
    return peak;
  },
};

// Per-process history keyed by TTY (or PID fallback).
let _localHistory = $state<Record<string, LocalSample[]>>({});

export const localHistory = {
  get value() { return _localHistory; },
  update(key: string, sample: LocalSample) {
    const prev = _localHistory[key] ?? [];
    _localHistory = { ..._localHistory, [key]: [...prev, sample].slice(-MAX_SAMPLES) };
  },
  pruneKeys(activeKeys: Set<string>) {
    // Remove history for processes that are no longer running.
    const next: Record<string, LocalSample[]> = {};
    for (const k of activeKeys) {
      if (_localHistory[k]) next[k] = _localHistory[k];
    }
    _localHistory = next;
  },
};
