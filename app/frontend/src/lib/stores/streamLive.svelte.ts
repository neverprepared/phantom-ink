/**
 * Live-tab store for the Stream panel.
 *
 * Owns the "live" envelope list plus its client-side view machinery — chip
 * filters, saved presets, playbook-selection mode, and per-envelope history
 * drill-down. Lifting this out of StreamPanel lets StreamLiveTab read the
 * state directly instead of receiving a dozen props, and keeps the panel
 * focused on tab routing, polling lifecycle, and the SSE subscription (which
 * delegate to `refreshLive()` / `applyAgentEvent()` here).
 *
 * The panel still owns the setInterval/SSE wiring and calls into this store;
 * the store owns the data and the transforms.
 */
import { getApi } from '../utils/api';
import { notifications } from '../notifications.svelte';
import { profileState, playbookSeed, attentionStore } from '../stores.svelte';

export interface AgentStateItem {
  id: string;
  kind: string;
  source: string;
  type: string;
  status: string;
  title: string;
  subtitle: string;
  workspace: string;
  parent_id: string;
  url: string;
  start_at: number | null;
  end_at: number | null;
  tags: string[];
  metadata: Record<string, any>;
  actions: Record<string, any>[];
  outcome: Record<string, any> | null;
  created_at: number;
  updated_at: number;
}

export interface AgentEventEntry {
  seq: number;
  id: string;
  source: string;
  type: string;
  status: string;
  parent_id: string;
  ts: number;
  envelope: Record<string, any>;
}

export interface LiveFilters {
  sources: string[];   // e.g. ['task','hub']
  statuses: string[];  // 'upcoming' | 'active' | 'blocked' | 'needs_action'
  tags: string[];
}
export interface LivePreset {
  name: string;
  filters: LiveFilters;
}

const FILTERS_KEY = 'pi-stream-filters-v1';
const PRESETS_KEY = 'pi-stream-presets-v1';
export const STATUS_OPTIONS = ['upcoming', 'active', 'blocked', 'needs_action'];

// Active-state statuses queried for / retained by the Live view.
const LIVE_STATUSES = 'upcoming,active,blocked,needs_action';

function loadFilters(): LiveFilters {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (raw) return JSON.parse(raw) as LiveFilters;
  } catch {}
  return { sources: [], statuses: [], tags: [] };
}
function loadPresets(): LivePreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    if (raw) return JSON.parse(raw) as LivePreset[];
  } catch {}
  return [];
}

class StreamLiveStore {
  live = $state<AgentStateItem[]>([]);
  liveLoading = $state(true);
  liveError = $state<string | null>(null);

  liveFilters = $state<LiveFilters>(loadFilters());
  presets = $state<LivePreset[]>(loadPresets());

  selectMode = $state(false);
  selected = $state<Set<string>>(new Set());

  // History drill-down — keyed by envelope id, holds the fetched event
  // sequence. A card is expanded when its id is present in this map.
  history = $state<Record<string, AgentEventEntry[]>>({});
  historyLoading = $state<Record<string, boolean>>({});

  workspaceFilter = $derived(profileState.active?.name ?? '');

  // ── Filters ────────────────────────────────────────────────────────────
  // Persisted client-side (via the mutators below) so the same operator view
  // survives a reload. A row matches when each non-empty bucket has at least
  // one chip matching the envelope (AND across buckets, OR within).

  activeFilterCount = $derived(
    this.liveFilters.sources.length + this.liveFilters.statuses.length + this.liveFilters.tags.length
  );

  // Tag chip universe = every tag we've seen on a live envelope, plus filter
  // tags so removed-then-readded chips stick around.
  availableTags = $derived.by(() => {
    const set = new Set<string>(this.liveFilters.tags);
    for (const item of this.live) for (const t of item.tags ?? []) set.add(t);
    return Array.from(set).sort();
  });
  availableSources = $derived.by(() => {
    const set = new Set<string>(['task', 'loop', 'entry', 'hub', 'bus']);
    for (const item of this.live) if (item.source) set.add(item.source);
    return Array.from(set).sort();
  });

  filteredLive = $derived.by(() => this.live.filter((it) => this.passesFilters(it)));

  passesFilters(it: AgentStateItem): boolean {
    const f = this.liveFilters;
    if (f.sources.length && !f.sources.includes(it.source)) return false;
    if (f.statuses.length && !f.statuses.includes(it.status)) return false;
    if (f.tags.length) {
      const itemTags = new Set(it.tags ?? []);
      if (!f.tags.some((t) => itemTags.has(t))) return false;
    }
    return true;
  }

  private persistFilters(): void {
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(this.liveFilters)); } catch {}
  }
  private persistPresets(): void {
    try { localStorage.setItem(PRESETS_KEY, JSON.stringify(this.presets)); } catch {}
  }

  toggleFilter(bucket: keyof LiveFilters, value: string): void {
    const cur = this.liveFilters[bucket];
    this.liveFilters = {
      ...this.liveFilters,
      [bucket]: cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value],
    };
    this.persistFilters();
  }
  clearFilters(): void {
    this.liveFilters = { sources: [], statuses: [], tags: [] };
    this.persistFilters();
  }

  savePreset(): void {
    const name = window.prompt('Name this preset (e.g. "blocked-only"):');
    if (!name?.trim()) return;
    this.presets = [
      { name: name.trim(), filters: { ...this.liveFilters, sources: [...this.liveFilters.sources], statuses: [...this.liveFilters.statuses], tags: [...this.liveFilters.tags] } },
      ...this.presets.filter((p) => p.name !== name.trim()),
    ].slice(0, 9);
    this.persistPresets();
  }
  applyPreset(p: LivePreset): void {
    this.liveFilters = {
      sources: [...p.filters.sources],
      statuses: [...p.filters.statuses],
      tags: [...p.filters.tags],
    };
    this.persistFilters();
  }
  deletePreset(name: string): void {
    this.presets = this.presets.filter((p) => p.name !== name);
    this.persistPresets();
  }

  // ── Selection mode (drives Save-as-playbook) ─────────────────────────────
  toggleSelect(id: string): void {
    const next = new Set(this.selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    this.selected = next;
  }
  clearSelection(): void { this.selected = new Set(); }
  exitSelectMode(): void { this.selectMode = false; this.clearSelection(); }
  toggleSelectMode(): void {
    this.selectMode = !this.selectMode;
    if (!this.selectMode) this.clearSelection();
  }

  saveAsPlaybook(): void {
    if (this.selected.size === 0) return;
    // Preserve the user's visible order so the playbook reads top-to-bottom.
    const picked = this.filteredLive.filter((it) => this.selected.has(it.id));
    if (picked.length === 0) return;
    const lines = picked.map((it) => {
      const title = (it.title || it.source || it.type || 'step').replace(/\s+/g, ' ').trim();
      const sub = (it.subtitle ?? '').trim();
      return sub ? `- [ ] ${title} — ${sub}` : `- [ ] ${title}`;
    });
    const seedName = `from-stream-${new Date().toISOString().slice(0, 10)}`;
    playbookSeed.seed({
      name: seedName,
      markdown: lines.join('\n'),
      scope: this.workspaceFilter ? 'profile' : 'global',
    });
    this.exitSelectMode();
  }

  // ── Loaders ──────────────────────────────────────────────────────────────
  async refreshLive(): Promise<void> {
    const a = await getApi();
    if (!a) return;
    try {
      this.live = ((await a.ListAgentState({
        status: LIVE_STATUSES,
        workspace: this.workspaceFilter,
        source: '',
        parent_id: '',
        limit: 200,
      })) ?? []) as AgentStateItem[];
      this.liveError = null;
    } catch (err: any) {
      this.liveError = `${err?.message ?? err}`;
    } finally {
      this.liveLoading = false;
    }
  }

  // Debounced attention refresh. Every agent:event might be a
  // failed/blocked/needs_action delta, but attentionStore.refresh() is a full
  // ListAttention round-trip that reassigns _attention $state and re-runs every
  // consumer (sidebar badge, dashboard action items, this panel). On a burst
  // that is a lot of cross-panel re-render churn for redundant reloads, so
  // coalesce a burst into one refresh. The 5s attention poll is the backstop.
  private attnTimer: ReturnType<typeof setTimeout> | null = null;
  private nudgeAttention(): void {
    if (this.attnTimer !== null) return; // trailing edge already scheduled
    this.attnTimer = setTimeout(() => {
      this.attnTimer = null;
      void attentionStore.refresh();
    }, 600);
  }

  // Apply one bus envelope delta into the live list without a full reload.
  // Matches the brainbox upsert semantics: same id mutates in place; new ids
  // append; terminal/done statuses drop off the live view.
  applyAgentEvent(env: any): void {
    if (!env || typeof env !== 'object') return;
    if (env.workspace && this.workspaceFilter && env.workspace !== this.workspaceFilter) return;

    const activeStatuses = ['upcoming', 'active', 'blocked', 'needs_action'];
    const isActive = activeStatuses.includes(env.status);

    // Nudge attention (debounced) — this delta might change the attention set.
    this.nudgeAttention();

    const idx = this.live.findIndex((i) => i.id === env.id);
    if (!isActive) {
      if (idx >= 0) this.live = this.live.filter((_, i) => i !== idx);
      return;
    }

    // ListAgentState returns rows enriched by brainbox (created_at, updated_at,
    // full metadata maps). The SSE payload is the envelope itself; merge the
    // fields we need for display and re-sort by updated_at desc.
    const merged: AgentStateItem = {
      id: env.id,
      kind: env.kind ?? 'event',
      source: env.source ?? '',
      type: env.type ?? '',
      status: env.status ?? '',
      title: env.title ?? '',
      subtitle: env.subtitle ?? '',
      workspace: env.workspace ?? '',
      parent_id: env.parent_id ?? '',
      url: env.url ?? '',
      start_at: env.start_at ?? null,
      end_at: env.end_at ?? null,
      tags: env.tags ?? [],
      metadata: env.metadata ?? {},
      actions: env.actions ?? [],
      outcome: env.outcome ?? null,
      created_at: idx >= 0 ? this.live[idx].created_at : Date.now(),
      updated_at: Date.now(),
    };
    if (idx >= 0) {
      const next = [...this.live];
      next[idx] = merged;
      this.live = next.sort((a, b) => b.updated_at - a.updated_at);
    } else {
      this.live = [merged, ...this.live];
    }
  }

  // Lazy-load the audit log for one envelope when the user expands a card.
  async toggleHistory(id: string): Promise<void> {
    if (this.history[id]) {
      const next = { ...this.history };
      delete next[id];
      this.history = next;
      return;
    }
    this.historyLoading = { ...this.historyLoading, [id]: true };
    const a = await getApi();
    if (!a) return;
    try {
      const events = ((await a.ListAgentEvents(id, '', 200)) ?? []) as AgentEventEntry[];
      this.history = { ...this.history, [id]: events };
    } catch (err: any) {
      notifications.error(`Failed to load history: ${err?.message ?? err}`);
    } finally {
      this.historyLoading = { ...this.historyLoading, [id]: false };
    }
  }
}

export const streamLive = new StreamLiveStore();
