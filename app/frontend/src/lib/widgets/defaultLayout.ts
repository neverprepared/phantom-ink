import type { DashboardLayout } from './types';

// 12-column grid, cellHeight = 60px.
// Kept intentionally small — first-time users get a quick overview;
// add more widgets via the widget drawer.
export const DEFAULT_LAYOUT: DashboardLayout = {
  version: 1,
  widgets: [
    {
      id: 'stat-sessions',
      kind: 'stat-counter',
      config: { label: 'ACTIVE SESSIONS', color: 'green', navTarget: 'sessions', dataKey: 'activeSessions' },
      x: 0, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-running',
      kind: 'stat-counter',
      config: { label: 'RUNNING TASKS', color: 'blue', navTarget: 'stream', dataKey: 'runningTasks' },
      x: 2, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-failed',
      kind: 'stat-counter',
      config: { label: 'FAILED (24h)', color: 'red', navTarget: 'stream', dataKey: 'failedTasks' },
      x: 4, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'action-items',
      kind: 'action-items',
      config: {},
      x: 6, y: 0, w: 6, h: 2, minW: 3, minH: 2,
    },
    {
      id: 'calendar',
      kind: 'calendar',
      config: {},
      x: 0, y: 2, w: 12, h: 4, minW: 3, minH: 3,
    },
  ],
};
