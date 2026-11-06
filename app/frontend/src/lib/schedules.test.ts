import { describe, it, expect } from 'vitest';
import { authoredActionKind, fmtTarget } from './schedules';
import { draftFromAction, draftToAction } from './rules';

// The panel authors exactly two of the server's ten action shapes. Everything
// else is passed through untouched, so this classifier is what keeps a
// host-exec schedule from being silently rewritten into a container one.
describe('authoredActionKind', () => {
  it('classifies a profile command as the command kind', () => {
    expect(authoredActionKind([{ type: 'run_profile_command', command: 'echo hi' }])).toBe('command');
  });

  it('classifies a submit_task as the task kind', () => {
    expect(authoredActionKind([{ type: 'submit_task', agent_name: 'a', description: 'd' }])).toBe('task');
  });

  it('refuses an action shape the form cannot express', () => {
    expect(authoredActionKind([{ type: 'run_host_command', runner: 'm3', argv: ['ls'] }])).toBeNull();
    expect(authoredActionKind([{ type: 'webhook', url: 'https://x' }])).toBeNull();
  });

  it('refuses a multi-action schedule — the form authors one action', () => {
    expect(authoredActionKind([
      { type: 'run_profile_command', command: 'echo hi' },
      { type: 'notify', channel: 'slack' },
    ])).toBeNull();
  });

  it('refuses an empty or missing actions list', () => {
    expect(authoredActionKind([])).toBeNull();
    expect(authoredActionKind(null)).toBeNull();
    expect(authoredActionKind(undefined)).toBeNull();
  });
});

describe('fmtTarget', () => {
  it('shows the prompt for a scheduled agent task', () => {
    expect(fmtTarget([{ type: 'submit_task', agent_name: 'reviewer', description: 'triage open PRs' }]))
      .toBe('task: triage open PRs');
  });

  it('shows the command for a profile command', () => {
    expect(fmtTarget([{ type: 'run_profile_command', command: 'inbox-drain' }])).toBe('inbox-drain');
  });
});

// The panel serializes its task action through the rules editor's draft model
// rather than hand-rolling a second one. That is only safe if a round trip
// preserves the fields — including ones this form has no input for.
describe('submit_task draft round-trip', () => {
  it('preserves the fields the form authors', () => {
    const action = {
      type: 'submit_task',
      agent_name: 'reviewer',
      description: 'triage open PRs',
      priority: 5,
      workspace_profile: 'personal',
      repo_url: 'https://github.com/a/b',
    };
    expect(draftToAction(draftFromAction(action))).toMatchObject(action);
  });

  it('preserves a field the form has no input for', () => {
    const action = {
      type: 'submit_task',
      agent_name: 'reviewer',
      description: 'd',
      // The panel offers no model-target picker; losing it on an edit would
      // silently retarget the agent.
      model_target: { provider: 'ollama', model: 'qwen3' },
    };
    expect(draftToAction(draftFromAction(action)).model_target).toEqual(action.model_target);
  });
});
