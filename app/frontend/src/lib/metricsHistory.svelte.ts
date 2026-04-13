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

// Aggregate history: Docker containers + local Claude processes combined.
let _combinedHistory = $state<CombinedSample[]>([]);

export const combinedHistory = {
  get value() { return _combinedHistory; },
  push(sample: CombinedSample) {
    _combinedHistory = [..._combinedHistory, sample].slice(-MAX_SAMPLES);
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
