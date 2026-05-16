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

export const currentPanel = {
  get value() { return _panel; },
  set value(id: string) {
    _panel = id;
    if (typeof window !== 'undefined') {
      history.replaceState(null, '', `#${id}`);
    }
  },
};

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    _panel = panelFromHash();
  });
}

// ---------------------------------------------------------------------------
// Panel focus — one-shot signal for cross-panel navigation. Setter sets the
// target chain ID and switches the current panel; reader clears after use.
// Used by TasksSection (and later, others) to "open this chain" without
// reinventing routing.
// ---------------------------------------------------------------------------

let _chainFocus = $state<string>('');
let _conversationSeed = $state<string[]>([]);

export const panelFocus = {
  get chainID() { return _chainFocus; },
  focusChain(id: string) {
    _chainFocus = id;
    currentPanel.value = 'chains';
  },
  consumeChainFocus(): string {
    const id = _chainFocus;
    _chainFocus = '';
    return id;
  },
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

export const profileState = {
  get profiles() { return _profiles; },
  set profiles(v: Profile[]) { _profiles = v; },
  get active() { return _activeProfile; },
  set active(v: Profile | null) { _activeProfile = v; },
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
  recordEvent(text: string) {
    _connected = true;
    _lastEventTime = new Date();
    _lastEventText = text;
  },
};
