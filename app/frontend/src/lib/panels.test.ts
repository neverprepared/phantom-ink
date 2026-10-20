/**
 * Panel-registry tests.
 *
 * The registry is the single source of truth for both the sidebar and the
 * ⌘-shortcut map (src/App.svelte:15-19), so a duplicate shortcut silently
 * shadows a panel rather than erroring. These assertions make the IA
 * contract mechanical.
 */

import { describe, expect, it } from 'vitest';
import { panels } from './panels';

describe('panel registry', () => {
  it('assigns each shortcut to exactly one panel', () => {
    const shortcuts = panels.filter(p => p.shortcut).map(p => p.shortcut);
    expect(new Set(shortcuts).size).toBe(shortcuts.length);
  });

  it('binds the renumbered workspace shortcuts to the expected panels', () => {
    const byShortcut = Object.fromEntries(
      panels.filter(p => p.shortcut).map(p => [p.shortcut, p.id]),
    );
    expect(byShortcut['⌘1']).toBe('dashboard');
    expect(byShortcut['⌘2']).toBe('stream');
    expect(byShortcut['⌘3']).toBe('agent-work');
    expect(byShortcut['⌘4']).toBe('sessions');
    expect(byShortcut['⌘5']).toBe('jobs');
    expect(byShortcut['⌘6']).toBe('conversations');
    expect(byShortcut['⌘7']).toBe('code');
  });

  it('keeps the stream and agent-work ids under their new labels', () => {
    const label = (id: string) => panels.find(p => p.id === id)?.label;
    expect(label('stream')).toBe('Events');
    expect(label('agent-work')).toBe('Telemetry');
  });

  it('no longer ships a panel whose label collides with Work', () => {
    expect(panels.map(p => p.label)).not.toContain('Agent Work');
    expect(panels.filter(p => p.label === 'Work')).toHaveLength(1);
  });

  it('drops the Event Log sidebar entry, which is now an Events tab', () => {
    expect(panels.find(p => p.id === 'event-log')).toBeUndefined();
  });

  it('keeps every panel in a known sidebar section', () => {
    for (const p of panels) {
      expect(['workspace', 'system']).toContain(p.section);
    }
  });
});
