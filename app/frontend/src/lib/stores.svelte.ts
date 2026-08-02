/** Reactive stores for panel routing, sidebar state, and connection status. */

// ---------------------------------------------------------------------------
// Sidebar collapsed state (localStorage-persisted)
// ---------------------------------------------------------------------------

const SIDEBAR_KEY = 'pi-sidebar-collapsed';

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === 'true';
  } catch {
    return false;
  }
}

let _collapsed = $state(loadCollapsed());

export const sidebarCollapsed = {
  get value() { return _collapsed; },
  set value(v: boolean) {
    _collapsed = v;
    try { localStorage.setItem(SIDEBAR_KEY, String(v)); } catch { /* noop */ }
  },
  toggle() { this.value = !this.value; },
};

// ---------------------------------------------------------------------------
// Current panel (hash-synced)
// ---------------------------------------------------------------------------

const DEFAULT_PANEL = 'dashboard';

function panelFromHash(): string {
  if (typeof window === 'undefined') return DEFAULT_PANEL;
  const h = location.hash.replace('#', '');
  return h || DEFAULT_PANEL;
}

let _panel = $state(panelFromHash());
// Recent-panel stack drives ⌘[ / ⌘] cycling. Newest at the front; capped
// at 8 to avoid stale entries piling up. Excludes the current panel.
let _panelHistory = $state<string[]>([]);
const PANEL_HISTORY_MAX = 8;

function _pushPanelHistory(prev: string, next: string) {
  if (!prev || prev === next) return;
  // Move prev to the front; remove any existing copy first.
  const filtered = _panelHistory.filter(p => p !== prev && p !== next);
  _panelHistory = [prev, ...filtered].slice(0, PANEL_HISTORY_MAX);
}

export const currentPanel = {
  get value() { return _panel; },
  set value(id: string) {
    const prev = _panel;
    _panel = id;
    _pushPanelHistory(prev, id);
    if (typeof window !== 'undefined') {
      history.replaceState(null, '', `#${id}`);
    }
  },
  /** Step back to the most-recent panel. ⌘[ */
  cyclePrev(): void {
    if (_panelHistory.length === 0) return;
    this.value = _panelHistory[0];
  },
  /** Step forward — opposite end of the stack. ⌘] */
  cycleNext(): void {
    if (_panelHistory.length === 0) return;
    this.value = _panelHistory[_panelHistory.length - 1];
  },
  get history() { return _panelHistory; },
};

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    const next = panelFromHash();
    const prev = _panel;
    _panel = next;
    _pushPanelHistory(prev, next);
  });
}

// ---------------------------------------------------------------------------
// Background refresh tick — increments every 30 s. Panels that lack live
// event feeds subscribe via $effect so they reload silently while visible.
// The interval is started once at module load; panels are unmounted when not
// active (AppShell uses {#if}), so their effects only fire while on screen.
// ---------------------------------------------------------------------------

let _tick = $state(0);

export const refreshTick = {
  get count() { return _tick; },
};

if (typeof window !== 'undefined') {
  setInterval(() => { _tick++; }, 30_000);
}

// ---------------------------------------------------------------------------
// Panel focus — one-shot signal for cross-panel navigation. Used to seed a
// new conversation with pre-selected sessions without reinventing routing.
// ---------------------------------------------------------------------------

let _conversationSeed = $state<string[]>([]);

export const panelFocus = {
  /** Start a new conversation with the named sessions pre-selected. */
  startConversationWith(sessionNames: string[]) {
    _conversationSeed = [...sessionNames];
    currentPanel.value = 'conversations';
  },
  consumeConversationSeed(): string[] {
    const out = _conversationSeed;
    _conversationSeed = [];
    return out;
  },
};

// ---------------------------------------------------------------------------
// Command palette
// ---------------------------------------------------------------------------

let _paletteOpen = $state(false);

export const commandPalette = {
  get open() { return _paletteOpen; },
  set open(v: boolean) { _paletteOpen = v; },
  toggle() { _paletteOpen = !_paletteOpen; },
  close() { _paletteOpen = false; },
};

// ---------------------------------------------------------------------------
// Active profile
// ---------------------------------------------------------------------------

export interface Profile {
  name: string;
  path: string;
  workspace_home: string;
  has_secrets: boolean;
  secrets_mode: 'onepassword' | 'plaintext' | 'none';
  secrets_path: string;
}

let _profiles = $state<Profile[]>([]);
let _activeProfile = $state<Profile | null>(null);
// Names of profiles the user has chosen to hide from picker UI.
// UI-only: the profile still exists on disk.
let _hiddenProfiles = $state<Set<string>>(new Set());

export const profileState = {
  get profiles() { return _profiles; },
  set profiles(v: Profile[]) { _profiles = v; },
  get active() { return _activeProfile; },
  set active(v: Profile | null) { _activeProfile = v; },
  get hidden() { return _hiddenProfiles; },
  set hidden(v: Set<string>) { _hiddenProfiles = v; },
  /** Profiles minus the ones the user has hidden — what UI should show. */
  get visible(): Profile[] {
    return _profiles.filter(p => !_hiddenProfiles.has(p.name));
  },
  isHidden(name: string): boolean { return _hiddenProfiles.has(name); },
  setHidden(name: string, hide: boolean): Set<string> {
    const next = new Set(_hiddenProfiles);
    if (hide) next.add(name); else next.delete(name);
    _hiddenProfiles = next;
    return next;
  },
};

// ---------------------------------------------------------------------------
// Feature flags (driven by service enabled state)
// ---------------------------------------------------------------------------

export interface ServiceFlag {
  name: string;
  enabled: boolean;
  running: boolean;
}

let _serviceFlags = $state<ServiceFlag[]>([]);

export const featureFlags = {
  get services() { return _serviceFlags; },
  set services(v: ServiceFlag[]) { _serviceFlags = v; },
  /** Check if a service is enabled in config. */
  isEnabled(name: string): boolean {
    return _serviceFlags.some(s => s.name === name && s.enabled);
  },
  /** Check if a service is enabled AND currently running. */
  isActive(name: string): boolean {
    return _serviceFlags.some(s => s.name === name && s.enabled && s.running);
  },
};

// ---------------------------------------------------------------------------
// Integration state — drives conditional sidebar entries (e.g. Files panel
// only shows when MinIO is configured + reachable). AppShell.svelte
// refreshes this on mount and on a 30s timer.
// ---------------------------------------------------------------------------

let _minioEnabled = $state(false);

export const integrationState = {
  get minioEnabled() { return _minioEnabled; },
  set minioEnabled(v: boolean) { _minioEnabled = v; },
};

// ---------------------------------------------------------------------------
// Profile color overrides (profile name → palette index string)
// ---------------------------------------------------------------------------

let _profileColorOverrides = $state<Record<string, string>>({});

export const profileColorStore = {
  get overrides() { return _profileColorOverrides; },
  set overrides(v: Record<string, string>) { _profileColorOverrides = v; },
  setOverride(name: string, idx: string) {
    _profileColorOverrides = { ..._profileColorOverrides, [name]: idx };
  },
  getOverride(name: string): string {
    return _profileColorOverrides[name] ?? '';
  },
};

// ---------------------------------------------------------------------------
// Dashboard widget layout + shared runtime data
// ---------------------------------------------------------------------------

import type { DashboardLayout, DashboardData, WidgetInstance } from './widgets/types';
import { getApi } from './utils/api';

let _dashboardLayout = $state<DashboardLayout | null>(null);

export const dashboardState = {
  get layout() { return _dashboardLayout; },
  set layout(v: DashboardLayout | null) { _dashboardLayout = v; },
  get widgets(): WidgetInstance[] { return _dashboardLayout?.widgets ?? []; },
  updateWidgets(widgets: WidgetInstance[]) {
    _dashboardLayout = _dashboardLayout
      ? { ..._dashboardLayout, widgets }
      : { version: 1, widgets };
  },
};

let _dashboardData = $state<DashboardData | null>(null);

export const dashboardDataStore = {
  get value() { return _dashboardData; },
  set value(v: DashboardData | null) { _dashboardData = v; },
};

// ---------------------------------------------------------------------------
// Attention queue — singleton 5s poll of ListAttention. Exposed so the sidebar
// can badge the Stream entry and the Dashboard can fold attention items into
// its ActionItems widget without each consumer re-polling. StreamPanel itself
// reads from this store too, so a profile switch reflects everywhere in one
// tick instead of waiting on each panel's own timer.
// ---------------------------------------------------------------------------

export interface AttentionItemLite {
  id: string;
  source: string;
  source_id: string;
  status: string;
  title: string;
  subtitle: string;
  reason: string;
  workspace: string;
  time: number;
  url?: string;
  actions: string[];
  user_reply?: string;
  session_name?: string;
  runner_name?: string;
}

let _attention = $state<AttentionItemLite[]>([]);
let _attentionLoaded = $state(false);
let _attentionPoll: number | undefined;
let _attentionWorkspace = '';
const ATTENTION_POLL_MS = 5_000;

async function _refreshAttention(): Promise<void> {
  if (typeof window === 'undefined') return;
  const api = await getApi();
  if (!api?.ListAttention) return;
  try {
    const items = (await api.ListAttention(_attentionWorkspace)) ?? [];
    _attention = items as AttentionItemLite[];
    _attentionLoaded = true;
  } catch {
    // best-effort: leave previous value in place
  }
}

export const attentionStore = {
  get items(): AttentionItemLite[] { return _attention; },
  get count(): number { return _attention.length; },
  get loaded(): boolean { return _attentionLoaded; },
  /** Force-refresh (e.g. after the user dismisses or retries an item). */
  refresh(): Promise<void> { return _refreshAttention(); },
  /** Update the workspace filter; safe to call repeatedly. */
  setWorkspace(ws: string): void {
    if (_attentionWorkspace === ws) return;
    _attentionWorkspace = ws;
    void _refreshAttention();
  },
  /** Locally apply a dismissal so the count updates instantly. */
  removeLocal(id: string): void {
    _attention = _attention.filter(i => i.id !== id);
  },
  /** Bootstrap the poll. Idempotent — safe to call from multiple places. */
  start(): void {
    if (typeof window === 'undefined' || _attentionPoll !== undefined) return;
    void _refreshAttention();
    _attentionPoll = window.setInterval(_refreshAttention, ATTENTION_POLL_MS);
  },
};

// ---------------------------------------------------------------------------
// Stream panel focus — one-shot signal so other panels (Dashboard, OpenSearch
// metric widget) can ask Stream to land on a particular tab with an optional
// preset filter (e.g. logs sorted by cost).
// ---------------------------------------------------------------------------

export interface StreamFocus {
  tab: 'live' | 'attention' | 'logs';
  sortBy?: 'cost' | 'duration' | 'tokens';
}

let _streamFocus = $state<StreamFocus | null>(null);

export const streamFocus = {
  get value(): StreamFocus | null { return _streamFocus; },
  /** Navigate to Stream with the given tab/filter intent. */
  focus(target: StreamFocus): void {
    _streamFocus = target;
    currentPanel.value = 'stream';
  },
  /** Read once and clear — StreamPanel calls this on mount/effect. */
  consume(): StreamFocus | null {
    const f = _streamFocus;
    _streamFocus = null;
    return f;
  },
};

// ---------------------------------------------------------------------------
// Playbook seed — used by Stream's "save as playbook" flow to hand off a
// markdown body + suggested name to the Playbooks panel's create modal.
// ---------------------------------------------------------------------------

export interface PlaybookSeed {
  name: string;
  markdown: string;
  scope?: 'profile' | 'global';
}

let _playbookSeed = $state<PlaybookSeed | null>(null);

export const playbookSeed = {
  get value(): PlaybookSeed | null { return _playbookSeed; },
  seed(target: PlaybookSeed): void {
    _playbookSeed = target;
    currentPanel.value = 'playbooks';
  },
  consume(): PlaybookSeed | null {
    const f = _playbookSeed;
    _playbookSeed = null;
    return f;
  },
};

// ---------------------------------------------------------------------------
// Rule seed — used by Stream's "create rule" flow to hand off a pre-filled
// rule draft (pattern derived from a live event card + suggested name/profile)
// to the Automations panel's Rules tab, which opens the new-rule form with it.
// ---------------------------------------------------------------------------

export interface RuleSeed {
  name?: string;
  profile?: string;
  description?: string;
  pattern: Record<string, any>;
  actions?: Record<string, any>[];
}

let _ruleSeed = $state<RuleSeed | null>(null);

export const ruleSeed = {
  get value(): RuleSeed | null { return _ruleSeed; },
  seed(target: RuleSeed): void {
    _ruleSeed = target;
    currentPanel.value = 'automations';
  },
  consume(): RuleSeed | null {
    const f = _ruleSeed;
    _ruleSeed = null;
    return f;
  },
};

// ---------------------------------------------------------------------------
// Outbox health — singleton 10s poll of OutboxPending so the StatusBar chip,
// any future detail panel, and panel-level diagnostics all share one source.
// ---------------------------------------------------------------------------

let _outboxPending = $state(0);
let _outboxLastError = $state<string>('');
let _outboxLastSuccessAt = $state<number>(0);
let _outboxPoll: number | undefined;
const OUTBOX_POLL_MS = 10_000;

async function _refreshOutbox(): Promise<void> {
  if (typeof window === 'undefined') return;
  const api = await getApi();
  if (!api?.OutboxPending) return;
  try {
    _outboxPending = (await api.OutboxPending()) ?? 0;
    _outboxLastError = '';
    _outboxLastSuccessAt = Date.now();
  } catch (err: any) {
    _outboxLastError = `${err?.message ?? err}`;
  }
}

export const outboxStore = {
  get pending(): number { return _outboxPending; },
  get lastError(): string { return _outboxLastError; },
  get lastSuccessAt(): number { return _outboxLastSuccessAt; },
  /** Heuristic: stuck if pending stays > 0 and no successful poll in last 2min. */
  get stuck(): boolean {
    return _outboxPending > 0
      && _outboxLastSuccessAt > 0
      && Date.now() - _outboxLastSuccessAt > 120_000;
  },
  refresh(): Promise<void> { return _refreshOutbox(); },
  start(): void {
    if (typeof window === 'undefined' || _outboxPoll !== undefined) return;
    void _refreshOutbox();
    _outboxPoll = window.setInterval(_refreshOutbox, OUTBOX_POLL_MS);
  },
};

// ---------------------------------------------------------------------------
// Connection status
// ---------------------------------------------------------------------------

let _connected = $state(false);
let _lastEventTime = $state<Date | null>(null);
let _lastEventText = $state('');

export const connectionState = {
  get connected() { return _connected; },
  set connected(v: boolean) { _connected = v; },
  get lastEventTime() { return _lastEventTime; },
  get lastEventText() { return _lastEventText; },
  disconnect() { _connected = false; },
  recordEvent(text: string) {
    _connected = true;
    _lastEventTime = new Date();
    _lastEventText = text;
  },
};

// ---------------------------------------------------------------------------
// Debug flags
// ---------------------------------------------------------------------------

let _showEventLog = $state(
  typeof localStorage !== 'undefined' && localStorage.getItem('debug_event_log') === 'true'
);

export const debugState = {
  get showEventLog() { return _showEventLog; },
  set showEventLog(v: boolean) {
    _showEventLog = v;
    if (typeof localStorage !== 'undefined') localStorage.setItem('debug_event_log', String(v));
  },
};
