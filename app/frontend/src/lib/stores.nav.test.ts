/**
 * Navigation-store tests for the Events / Telemetry IA split.
 *
 * These exist because every regression in this change is SILENT: a
 * mis-pointed navigation intent renders a real panel on the wrong tab
 * rather than throwing, so only an assertion catches it.
 */

import { describe, expect, it, beforeEach, vi } from 'vitest';

// stores.svelte.ts imports these at :359-361; neither is exercised by the
// navigation surface, so they are stubbed rather than faked.
vi.mock('./utils/api', () => ({ getApi: async () => null }));
vi.mock('./utils/wailsTopic', () => ({ onWailsTopic: () => () => {} }));

const {
  currentPanel,
  eventsState,
  telemetryState,
  jobsState,
  ruleSeed,
  EVENTS_TAB_PANELS,
  JOBS_TAB_PANELS,
} = await import('./stores.svelte');

beforeEach(() => {
  eventsState.tab = 'attention';
  telemetryState.open('traces', null);
  jobsState.tab = 'jobs';
  ruleSeed.consume();
  currentPanel.value = 'dashboard';
});

describe('eventsState', () => {
  it('defaults to the attention tab, because attention is the only actionable surface', () => {
    expect(eventsState.tab).toBe('attention');
  });

  it('open() sets the tab and navigates to the retained stream panel id', () => {
    eventsState.open('rules');
    expect(eventsState.tab).toBe('rules');
    expect(currentPanel.value).toBe('stream');
  });
});

describe('telemetryState', () => {
  it('open() sets the tab, carries a sort key, and navigates to agent-work', () => {
    telemetryState.open('logs', 'cost');
    expect(telemetryState.tab).toBe('logs');
    expect(telemetryState.sortBy).toBe('cost');
    expect(currentPanel.value).toBe('agent-work');
  });

  it('open() without a sort key CLEARS a previous one', () => {
    telemetryState.open('logs', 'cost');
    telemetryState.open('logs');
    expect(telemetryState.sortBy).toBeNull();
  });
});

describe('legacy panel id redirects', () => {
  it('routes the retired event-log panel id to the Events raw tab', () => {
    expect(EVENTS_TAB_PANELS['event-log']).toBe('raw');
  });

  it('routes the legacy automations panel id to the Events rules tab', () => {
    expect(EVENTS_TAB_PANELS['automations']).toBe('rules');
  });

  it('routes the legacy timeline panel id to the Automations timeline tab', () => {
    expect(JOBS_TAB_PANELS['timeline']).toBe('timeline');
  });

  it('no longer routes any legacy id to an Automations rules tab', () => {
    expect(Object.values(JOBS_TAB_PANELS)).not.toContain('rules');
  });
});

describe('ruleSeed', () => {
  it('seeds a draft onto the Events rules tab, not Automations', () => {
    ruleSeed.seed({ pattern: { type: 'task.failed' } });
    expect(currentPanel.value).toBe('stream');
    expect(eventsState.tab).toBe('rules');
    expect(ruleSeed.consume()?.pattern).toEqual({ type: 'task.failed' });
  });
});
