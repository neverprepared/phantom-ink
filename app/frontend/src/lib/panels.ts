/** Panel registry — sidebar navigation entries. */

/** Which sidebar group a panel belongs to. The sidebar renders a divider
 *  between sections. This is an explicit IA choice — NOT derived from whether
 *  a panel has a keyboard shortcut (shortcuts are pure accelerators). */
export type PanelSection = 'workspace' | 'system';

export interface Panel {
  id: string;
  label: string;
  /** Sidebar group. 'workspace' = things you actively drive/watch;
   *  'system' = knowledge stores, platform, and config. */
  section: PanelSection;
  icon: string;
  shortcut?: string;
  debug?: boolean;
  /** Optional feature flag — when set, the panel only appears in the
   *  sidebar when this flag is true in the integration state. */
  requires?: 'minio';
}

export const panels: Panel[] = [
  // ── Workspace ────────────────────────────────────────────────────────────
  // Order matters: the sidebar renders array order within each section.
  // Events (the agent bus) and Telemetry (OpenSearch) sit adjacent because
  // they are the two halves of one question; they are separate panels because
  // they have different transports and different durability guarantees.
  {
    id: 'dashboard',
    label: 'Dashboard',
    section: 'workspace',
    shortcut: '⌘1',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>`,
  },
  {
    id: 'stream',
    label: 'Events',
    section: 'workspace',
    shortcut: '⌘2',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="18" y2="18"/><circle cx="21" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>`,
  },
  {
    id: 'agent-work',
    label: 'Telemetry',
    section: 'workspace',
    shortcut: '⌘3',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="4" r="2"/><circle cx="5" cy="14" r="2"/><circle cx="12" cy="14" r="2"/><circle cx="19" cy="14" r="2"/><path d="M12 6v2M12 8H5v4M12 8h7v4M12 8v4"/><path d="M3 20h18"/></svg>`,
  },
  {
    id: 'sessions',
    label: 'Sessions',
    section: 'workspace',
    shortcut: '⌘4',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>`,
  },
  {
    // User-facing label is "Automations" (the hub over jobs/loops/collectors/
    // rules); the panel id stays 'jobs' internally to avoid a risky id migration.
    id: 'jobs',
    label: 'Automations',
    section: 'workspace',
    shortcut: '⌘5',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>`,
  },
  {
    id: 'conversations',
    label: 'Conversations',
    section: 'workspace',
    shortcut: '⌘6',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`,
  },
  {
    id: 'code',
    // Hub label: the panel hosts Code and Jira tabs. The id stays 'code' so
    // deep links, shortcuts and stored state keep working — same label-only
    // rename Jobs → Automations did.
    label: 'Work',
    section: 'workspace',
    shortcut: '⌘7',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>`,
  },
  // ── System ───────────────────────────────────────────────────────────────
  {
    id: 'mesh',
    label: 'Brain Mesh',
    section: 'system',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/><line x1="7" y1="19" x2="17" y2="19"/></svg>`,
  },
  {
    id: 'memory-graph',
    label: 'Memory Graph',
    section: 'system',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="18" r="2"/><line x1="10" y1="10" x2="6.5" y2="7.5"/><line x1="14" y1="10" x2="17.5" y2="7.5"/><line x1="10.5" y1="14" x2="7" y2="17.5"/><line x1="13.5" y1="14" x2="16.5" y2="16.5"/></svg>`,
  },
  {
    id: 'files',
    label: 'Files',
    section: 'system',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6a2 2 0 0 0-2-2h-7l-2-2H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6z"/></svg>`,
    requires: 'minio',
  },
  {
    id: 'doctor',
    label: 'Doctor',
    section: 'system',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2a2 2 0 0 0-2 2v5H4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h5v5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-5h5a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-5V4a2 2 0 0 0-2-2z"/></svg>`,
  },
  {
    id: 'settings',
    label: 'Settings',
    section: 'system',
    shortcut: '⌘,',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>`,
  },
];
