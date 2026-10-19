import { describe, expect, it } from 'vitest';
import { eventName, fmtDuration, isFailure, logSummary, type LogEntry } from './logSummary';

// Fixtures mirror real logs-otel documents. The point of this module is that a
// row says what happened; the regression it guards is a tab where every line
// read "claude_code.hook_execution_complete" and nothing else.

const hook = (over: Partial<LogEntry> = {}): LogEntry => ({
  time: '2026-10-19T19:59:58.066Z',
  body: 'claude_code.hook_execution_complete',
  event: 'hook_execution_complete',
  workspace: 'personal',
  session: '0d924ba8-9590-4a00-a55f-91b275f1720f',
  duration_ms: 38,
  // NOTE: strings, not numbers. These indices are dynamically mapped and the
  // hook num_* / total_duration_ms attributes really do arrive as strings.
  attrs: {
    hook_name: 'PostToolUse:Bash',
    hook_event: 'PostToolUse',
    num_hooks: '3',
    num_success: '3',
    num_blocking: '0',
    num_cancelled: '0',
    num_non_blocking_error: '0',
  },
  ...over,
});

const tool = (over: Partial<LogEntry> = {}): LogEntry => ({
  time: '2026-10-19T02:48:51.589Z',
  body: 'claude_code.tool_result',
  event: 'tool_result',
  duration_ms: 9403,
  // success is the STRING "false" on tool_result, not a boolean.
  attrs: { tool_name: 'Bash', success: 'false', error_type: 'ShellError' },
  ...over,
});

describe('logSummary', () => {
  it('answers "what did the hook do?"', () => {
    expect(logSummary(hook())).toBe('hook_execution_complete · PostToolUse:Bash · 3/3 ok');
  });

  it('surfaces blocked hooks', () => {
    const l = hook({ attrs: { ...hook().attrs, num_success: '2', num_blocking: '1' } });
    expect(logSummary(l)).toContain('1 blocked');
  });

  it('surfaces errored hooks', () => {
    const l = hook({ attrs: { ...hook().attrs, num_non_blocking_error: '2' } });
    expect(logSummary(l)).toContain('2 errored');
  });

  it('omits zero counts', () => {
    const s = logSummary(hook());
    expect(s).not.toContain('blocked');
    expect(s).not.toContain('cancelled');
  });

  it('makes a failed tool result unmistakable', () => {
    // Previously identical to a success, because the fields were never fetched.
    expect(logSummary(tool())).toBe('tool_result · Bash · FAILED ShellError');
  });

  it('marks a successful tool result as ok', () => {
    const l = tool({ attrs: { tool_name: 'Read', success: 'true' } });
    expect(logSummary(l)).toBe('tool_result · Read · ok');
  });

  it('handles a failure with no error type', () => {
    const l = tool({ attrs: { tool_name: 'Bash', success: 'false' } });
    expect(logSummary(l)).toBe('tool_result · Bash · FAILED');
  });

  it('summarises an api request with tokens', () => {
    const l: LogEntry = {
      time: 't',
      body: 'claude_code.api_request',
      event: 'api_request',
      model: 'claude-opus-5',
      attrs: { input_tokens: 1200, output_tokens: 340 },
    };
    expect(logSummary(l)).toBe('api_request · claude-opus-5 · 1200→340 tok');
  });

  it('falls back to the event name rather than inventing detail', () => {
    const l: LogEntry = { time: 't', body: 'claude_code.auth', event: 'auth' };
    expect(logSummary(l)).toBe('auth');
  });

  it('derives the event name from body when the attribute is absent', () => {
    const l: LogEntry = { time: 't', body: 'claude_code.compaction' };
    expect(eventName(l)).toBe('compaction');
    expect(logSummary(l)).toBe('compaction');
  });

  it('survives a body with no dot', () => {
    expect(eventName({ time: 't', body: 'standalone' })).toBe('standalone');
  });

  it('does not throw on a bare entry', () => {
    expect(() => logSummary({ time: 't', body: '' })).not.toThrow();
  });
});

describe('isFailure', () => {
  it('is false for a healthy hook run', () => {
    expect(isFailure(hook())).toBe(false);
  });

  it('is true when a tool reported success=false', () => {
    expect(isFailure(tool())).toBe(true);
  });

  it('is true when any hook blocked', () => {
    expect(isFailure(hook({ attrs: { ...hook().attrs, num_blocking: '1' } }))).toBe(true);
  });

  it('is true when severity is ERROR even with no attrs', () => {
    expect(isFailure({ time: 't', body: 'x', severity: 'ERROR' })).toBe(true);
  });

  it('is false for an empty record', () => {
    expect(isFailure({ time: 't', body: 'x' })).toBe(false);
  });
});

describe('fmtDuration', () => {
  it.each([
    [undefined, ''],
    [0, ''],
    [38, '38ms'],
    [999, '999ms'],
    [1500, '1.5s'],
    [9403, '9.4s'],
    [45_000, '45s'],
    [90_000, '1m30s'],
    [120_000, '2m'],
  ])('formats %s as %s', (ms, want) => {
    expect(fmtDuration(ms as number | undefined)).toBe(want);
  });
});

describe('type coercion (dynamically mapped indices)', () => {
  // The same attribute name arrives as a different JSON type depending on which
  // document typed it first in a given daily index. Comparing instead of
  // coercing is what made a failed tool render with no outcome at all.

  it('reads string counts as numbers', () => {
    const l = hook({ attrs: { hook_name: 'H', num_hooks: '5', num_success: '4' } });
    expect(logSummary(l)).toContain('4/5 ok');
  });

  it('reads numeric counts as numbers too', () => {
    const l = hook({ attrs: { hook_name: 'H', num_hooks: 5, num_success: 4 } });
    expect(logSummary(l)).toContain('4/5 ok');
  });

  it('treats the string "false" as a failure', () => {
    const l = tool({ attrs: { tool_name: 'Bash', success: 'false' } });
    expect(isFailure(l)).toBe(true);
    expect(logSummary(l)).toContain('FAILED');
  });

  it('treats a real false as a failure', () => {
    const l = tool({ attrs: { tool_name: 'Bash', success: false } });
    expect(isFailure(l)).toBe(true);
  });

  it('treats the string "true" as success', () => {
    const l = tool({ attrs: { tool_name: 'Read', success: 'true' } });
    expect(isFailure(l)).toBe(false);
    expect(logSummary(l)).toContain('ok');
  });

  it('ignores a junk success value rather than guessing', () => {
    const l = tool({ attrs: { tool_name: 'Read', success: 'maybe' } });
    expect(isFailure(l)).toBe(false);
    expect(logSummary(l)).toBe('tool_result · Read');
  });

  it('ignores non-numeric counts rather than rendering NaN', () => {
    const l = hook({ attrs: { hook_name: 'H', num_hooks: 'lots', num_success: '1' } });
    expect(logSummary(l)).toBe('hook_execution_complete · H');
    expect(logSummary(l)).not.toContain('NaN');
  });

  it('counts string-valued blocking hooks as a failure', () => {
    const l = hook({ attrs: { hook_name: 'H', num_blocking: '2' } });
    expect(isFailure(l)).toBe(true);
  });
});
