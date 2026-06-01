import type { DashboardLayout } from './types';

// 12-column grid, cellHeight = 60px
export const DEFAULT_LAYOUT: DashboardLayout = {
  version: 1,
  collections: [
    { id: 'default-morning', name: 'Morning' },
    { id: 'default-ops',     name: 'Ops' },
  ],
  activeCollectionId: 'default-morning',
  widgets: [
    // --- Morning collection ---
    {
      id: 'cal-main',
      kind: 'calendar',
      config: {},
      collectionId: 'default-morning',
      x: 0, y: 0, w: 4, h: 4, minW: 3, minH: 3,
    },
    {
      id: 'tasks-main',
      kind: 'tasks',
      config: {},
      collectionId: 'default-morning',
      x: 4, y: 0, w: 5, h: 5, minW: 3, minH: 3,
    },
    {
      id: 'sessions-mini',
      kind: 'sessions-mini',
      config: {},
      collectionId: 'default-morning',
      x: 9, y: 0, w: 3, h: 5, minW: 2, minH: 3,
    },
    {
      id: 'stat-sessions-m',
      kind: 'stat-counter',
      config: { label: 'ACTIVE SESSIONS', color: 'green', navTarget: 'sessions', dataKey: 'activeSessions' },
      collectionId: 'default-morning',
      x: 0, y: 4, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-running-m',
      kind: 'stat-counter',
      config: { label: 'RUNNING TASKS', color: 'blue', navTarget: 'timeline', dataKey: 'runningTasks' },
      collectionId: 'default-morning',
      x: 2, y: 4, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'dispatch-m',
      kind: 'dispatch-form',
      config: {},
      collectionId: 'default-morning',
      x: 0, y: 6, w: 6, h: 4, minW: 4, minH: 3,
    },
    {
      id: 'notes-m',
      kind: 'notes',
      config: { widgetId: 'notes-m' },
      collectionId: 'default-morning',
      x: 6, y: 5, w: 3, h: 5, minW: 2, minH: 2,
    },
    {
      id: 'action-items-m',
      kind: 'action-items',
      config: {},
      collectionId: 'default-morning',
      x: 9, y: 5, w: 3, h: 5, minW: 2, minH: 2,
    },

    // --- Ops collection ---
    {
      id: 'stat-sessions',
      kind: 'stat-counter',
      config: { label: 'ACTIVE SESSIONS', color: 'green', navTarget: 'sessions', dataKey: 'activeSessions' },
      collectionId: 'default-ops',
      x: 0, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-running',
      kind: 'stat-counter',
      config: { label: 'RUNNING TASKS', color: 'blue', navTarget: 'timeline', dataKey: 'runningTasks' },
      collectionId: 'default-ops',
      x: 2, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-failed',
      kind: 'stat-counter',
      config: { label: 'FAILED (24h)', color: 'red', navTarget: 'timeline', dataKey: 'failedTasks' },
      collectionId: 'default-ops',
      x: 4, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-scheduled',
      kind: 'stat-counter',
      config: { label: 'SCHEDULED', color: 'default', navTarget: 'chains', dataKey: 'scheduledFires' },
      collectionId: 'default-ops',
      x: 6, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'stat-actions',
      kind: 'stat-counter',
      config: { label: 'ACTION ITEMS', color: 'orange', dataKey: 'actionItems' },
      collectionId: 'default-ops',
      x: 8, y: 0, w: 2, h: 2, minW: 2, minH: 2,
    },
    {
      id: 'dispatch-form',
      kind: 'dispatch-form',
      config: {},
      collectionId: 'default-ops',
      x: 0, y: 2, w: 6, h: 4, minW: 4, minH: 3,
    },
    {
      id: 'chains-list',
      kind: 'chains-list',
      config: {},
      collectionId: 'default-ops',
      x: 6, y: 2, w: 3, h: 4, minW: 2, minH: 2,
    },
    {
      id: 'action-items',
      kind: 'action-items',
      config: {},
      collectionId: 'default-ops',
      x: 9, y: 2, w: 3, h: 4, minW: 2, minH: 2,
    },
    {
      id: 'resource-monitor',
      kind: 'resource-monitor',
      config: {},
      collectionId: 'default-ops',
      x: 0, y: 6, w: 12, h: 4, minW: 4, minH: 3,
    },
  ],
};
