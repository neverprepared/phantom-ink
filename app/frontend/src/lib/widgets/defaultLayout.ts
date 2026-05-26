import type { DashboardLayout } from './types';

// 12-column grid, cellHeight = 60px
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
      config: { label: 'RUNNING TASKS', color: 'blue', navTarget: 'timeline', dataKey: 'runningTasks' },
      x: 2, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-failed',
      kind: 'stat-counter',
      config: { label: 'FAILED (24h)', color: 'red', navTarget: 'timeline', dataKey: 'failedTasks' },
      x: 4, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-scheduled',
      kind: 'stat-counter',
      config: { label: 'SCHEDULED', color: 'default', navTarget: 'chains', dataKey: 'scheduledFires' },
      x: 6, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-actions',
      kind: 'stat-counter',
      config: { label: 'ACTION ITEMS', color: 'orange', dataKey: 'actionItems' },
      x: 8, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'dispatch-form',
      kind: 'dispatch-form',
      config: {},
      x: 0, y: 2, w: 6, h: 4, minW: 4, minH: 3,
    },
    {
      id: 'chains-list',
      kind: 'chains-list',
      config: {},
      x: 6, y: 2, w: 3, h: 4, minW: 2, minH: 2,
    },
    {
      id: 'action-items',
      kind: 'action-items',
      config: {},
      x: 9, y: 2, w: 3, h: 4, minW: 2, minH: 2,
    },
    {
      id: 'resource-monitor',
      kind: 'resource-monitor',
      config: {},
      x: 0, y: 6, w: 12, h: 4, minW: 4, minH: 3,
    },
  ],
};
