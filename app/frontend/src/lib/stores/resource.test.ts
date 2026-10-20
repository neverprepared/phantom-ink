/**
 * Tests for the resource()/action() primitives.
 *
 * These guard behaviour that 239 eventual call sites depend on, and two of the
 * guards exist because of bugs this codebase actually had: a fetch resolving
 * after a profile switch painting the wrong tenant's rows, and a panel
 * claiming it had no data when it could not ask for any.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushSync } from 'svelte';

// getApi is the module boundary; everything under test goes through it.
const mockGetApi = vi.fn();
vi.mock('../utils/api', () => ({
  getApi: () => mockGetApi(),
}));

const { resource, action, keyedResource } = await import('./resource.svelte');

/** A fake bindings object — the fetcher only ever receives this. */
const API = {} as any;

beforeEach(() => {
  mockGetApi.mockReset();
  mockGetApi.mockResolvedValue(API);
});

/** Promise with externally-resolvable settle, for ordering races precisely. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('resource', () => {
  it('loads, exposes the value, and marks itself loaded', async () => {
    const r = resource(async () => [1, 2, 3], { initial: [] as number[], label: 'nums' });

    expect(r.loading).toBe(false);
    expect(r.loaded).toBe(false);

    await r.refresh();

    expect(r.value).toEqual([1, 2, 3]);
    expect(r.loaded).toBe(true);
    expect(r.error).toBeNull();
    expect(r.loading).toBe(false);
  });

  it('reports loading only on the FIRST load, and refreshing afterwards', async () => {
    const d1 = deferred<number[]>();
    let current = d1;
    const r = resource(async () => current.promise, { initial: [] as number[], label: 'nums' });

    const first = r.refresh();
    flushSync();
    expect(r.loading).toBe(true); // nothing to show yet
    expect(r.refreshing).toBe(false);
    d1.resolve([1]);
    await first;

    const d2 = deferred<number[]>();
    current = d2;
    const second = r.refresh();
    flushSync();
    // Data is already on screen: this must NOT present as a cold load, which
    // is what made every panel navigation blank behind a spinner.
    expect(r.loading).toBe(false);
    expect(r.refreshing).toBe(true);
    expect(r.value).toEqual([1]); // retained while revalidating
    d2.resolve([2]);
    await second;
    expect(r.value).toEqual([2]);
    expect(r.refreshing).toBe(false);
  });

  it('treats unavailable bindings as an ERROR, not an empty result', async () => {
    mockGetApi.mockResolvedValue(null);
    const onError = vi.fn();
    const r = resource(async () => ['never'], { initial: [] as string[], label: 'things', onError });

    await r.refresh();

    // The bug this replaces: GatewayPanel returned early on !a and rendered a
    // confident "No catalog servers".
    expect(r.error).toMatch(/bindings unavailable/i);
    expect(r.loaded).toBe(false);
    expect(onError).toHaveBeenCalledOnce();
  });

  it('records the failure message and calls onError once', async () => {
    const onError = vi.fn();
    const r = resource(async () => {
      throw new Error('boom');
    }, { initial: null as string | null, label: 'thing', onError });

    await r.refresh();

    expect(r.error).toBe('boom');
    expect(onError).toHaveBeenCalledWith('thing: boom');
  });

  it('keeps error alongside the data so failed and empty are distinguishable', async () => {
    let fail = false;
    const r = resource(async () => {
      if (fail) throw new Error('remote down');
      return [] as string[];
    }, { initial: [] as string[], label: 'rows' });

    await r.refresh();
    // Genuinely empty.
    expect(r.value).toEqual([]);
    expect(r.error).toBeNull();
    expect(r.loaded).toBe(true);

    fail = true;
    await r.refresh();
    // Still empty-looking, but now for a completely different reason — which
    // safe(p, fallback) structurally could not express.
    expect(r.value).toEqual([]);
    expect(r.error).toBe('remote down');
  });

  it('discards a superseded response even when it lands last', async () => {
    const slow = deferred<string>();
    const fast = deferred<string>();
    let next = slow;
    const r = resource(async () => next.promise, { initial: '', label: 'v' });

    const a = r.refresh();            // takes `slow`
    next = fast;
    const b = r.refresh({ force: true }); // takes `fast`, supersedes

    fast.resolve('second');
    await b;
    slow.resolve('first');            // the older request finishes LAST
    await a;

    expect(r.value).toBe('second');
  });

  it('collapses concurrent refreshes into one request', async () => {
    const fetcher = vi.fn(async () => 'x');
    const r = resource(fetcher, { initial: '', label: 'v' });

    await Promise.all([r.refresh(), r.refresh(), r.refresh()]);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('force bypasses the in-flight collapse', async () => {
    const fetcher = vi.fn(async () => 'x');
    const r = resource(fetcher, { initial: '', label: 'v' });

    await Promise.all([r.refresh(), r.refresh({ force: true })]);

    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('reset clears the value and the loaded flag', async () => {
    const r = resource(async () => ['a'], { initial: [] as string[], label: 'v' });
    await r.refresh();
    expect(r.loaded).toBe(true);

    r.reset();

    expect(r.value).toEqual([]);
    expect(r.loaded).toBe(false);
    expect(r.error).toBeNull();
  });

  it('set replaces the value for an optimistic update', async () => {
    const r = resource(async () => ['server'], { initial: [] as string[], label: 'v' });
    r.set(['optimistic']);
    expect(r.value).toEqual(['optimistic']);
  });
});

describe('resource scope key', () => {
  it('DISCARDS a response whose scope changed while it was in flight', async () => {
    // The real failure: a fetch issued under profile A resolves after the user
    // switches to B. It is the NEWEST response, so a sequence guard accepts
    // it, and profile A's rows paint under profile B's header.
    let profile = 'alpha';
    const d = deferred<string>();
    const r = resource(async () => d.promise, {
      initial: '',
      label: 'rows',
      key: () => profile,
    });

    const pending = r.refresh();
    profile = 'beta';          // user switches mid-flight
    d.resolve('alpha-rows');
    await pending;

    expect(r.value).toBe('');        // alpha's rows rejected
    expect(r.loaded).toBe(false);
  });

  it('accepts a response whose scope is unchanged', async () => {
    const profile = 'alpha';
    const r = resource(async () => 'alpha-rows', {
      initial: '',
      label: 'rows',
      key: () => profile,
    });

    await r.refresh();

    expect(r.value).toBe('alpha-rows');
    expect(r.loaded).toBe(true);
  });

  it('clears retained data when the scope changes, so SWR cannot leak it', async () => {
    let profile = 'alpha';
    const r = resource(async () => `${profile}-rows`, {
      initial: '',
      label: 'rows',
      key: () => profile,
    });

    await r.refresh();
    expect(r.value).toBe('alpha-rows');

    profile = 'beta';
    const pending = r.refresh();
    flushSync();
    // Mid-load under the new scope, the OLD scope's value must be gone — not
    // held on screen as stale-while-revalidate would otherwise do.
    expect(r.value).toBe('');
    expect(r.loading).toBe(true);
    await pending;
    expect(r.value).toBe('beta-rows');
  });
});

describe('action', () => {
  it('runs, reports success, and clears busy', async () => {
    const onSuccess = vi.fn();
    const fn = vi.fn(async () => undefined);
    const a = action(fn, { label: 'start', successMessage: 'started', onSuccess });

    expect(a.busy).toBe(false);
    const ok = await a.run();

    expect(ok).toBe(true);
    expect(fn).toHaveBeenCalledOnce();
    expect(onSuccess).toHaveBeenCalledWith('started');
    expect(a.busy).toBe(false);
    expect(a.error).toBeNull();
  });

  it('is busy while in flight', async () => {
    const d = deferred<void>();
    const a = action(async () => d.promise, { label: 'start' });

    const pending = a.run();
    flushSync();
    expect(a.busy).toBe(true);

    d.resolve();
    await pending;
    expect(a.busy).toBe(false);
  });

  it('ignores a repeat invocation while busy (double-submit guard)', async () => {
    const d = deferred<void>();
    const fn = vi.fn(async () => d.promise);
    const a = action(fn, { label: 'start' });

    const first = a.run();
    const second = await a.run(); // the impatient second click

    expect(second).toBe(false);
    expect(fn).toHaveBeenCalledTimes(1);

    d.resolve();
    await first;
  });

  it('returns false and records the error on failure', async () => {
    const onError = vi.fn();
    const a = action(async () => {
      throw new Error('nope');
    }, { label: 'stop', onError });

    const ok = await a.run();

    expect(ok).toBe(false);
    expect(a.error).toBe('nope');
    expect(onError).toHaveBeenCalledWith('stop: nope');
    expect(a.busy).toBe(false);
  });

  it('treats unavailable bindings as a failure', async () => {
    mockGetApi.mockResolvedValue(null);
    const a = action(async () => undefined, { label: 'stop' });

    expect(await a.run()).toBe(false);
    expect(a.error).toMatch(/bindings unavailable/i);
  });

  it('passes its arguments through to the bound call', async () => {
    const fn = vi.fn(async (_api: unknown, id: string, flag: boolean) => undefined);
    const a = action(fn, { label: 'toggle' });

    await a.run('abc', true);

    expect(fn).toHaveBeenCalledWith(API, 'abc', true);
  });
});

describe('keyedResource', () => {
  it('keeps one resource per key, so returning to a key is instant', async () => {
    const seen: string[] = [];
    const kr = keyedResource<string>(
      (key) => async () => {
        seen.push(key);
        return `value-${key}`;
      },
      { initial: '', label: 'thing' },
    );

    await kr.for('a').refresh();
    await kr.for('b').refresh();
    expect(kr.for('a').value).toBe('value-a');
    expect(kr.for('b').value).toBe('value-b');

    // Coming back to 'a' still has its value BEFORE any refetch.
    expect(kr.for('a').value).toBe('value-a');
    expect(kr.for('a').loaded).toBe(true);
    expect(seen).toEqual(['a', 'b']);
  });

  it('returns the same resource instance for a key', () => {
    const kr = keyedResource<string>(
      () => async () => 'x',
      { initial: '', label: 'thing' },
    );
    expect(kr.for('a')).toBe(kr.for('a'));
    expect(kr.for('a')).not.toBe(kr.for('b'));
  });

  it('isolates loading state per key', async () => {
    const { promise, resolve } = deferred<string>();
    const kr = keyedResource<string>(
      (key) => () => (key === 'slow' ? promise : Promise.resolve(`value-${key}`)),
      { initial: '', label: 'thing' },
    );

    await kr.for('fast').refresh();
    const pending = kr.for('slow').refresh();

    expect(kr.for('slow').loading).toBe(true);
    // The cached key must not report the other key's spinner.
    expect(kr.for('fast').loading).toBe(false);
    expect(kr.for('fast').value).toBe('value-fast');

    resolve('value-slow');
    await pending;
    expect(kr.for('slow').value).toBe('value-slow');
  });

  it('a second refresh on a key refreshes rather than cold-loads', async () => {
    const kr = keyedResource<string>(
      (key) => async () => `value-${key}`,
      { initial: '', label: 'thing' },
    );
    await kr.for('a').refresh();

    const p = kr.for('a').refresh({ force: true });
    expect(kr.for('a').refreshing).toBe(true);
    expect(kr.for('a').loading).toBe(false);
    await p;
  });

  it('drop forgets one key and leaves the rest', async () => {
    const kr = keyedResource<string>(
      (key) => async () => `value-${key}`,
      { initial: '', label: 'thing' },
    );
    await kr.for('a').refresh();
    await kr.for('b').refresh();

    kr.drop('a');
    expect(kr.keys()).toEqual(['b']);
    expect(kr.for('a').loaded).toBe(false);
    expect(kr.for('b').value).toBe('value-b');
  });

  it('clear forgets everything', async () => {
    const kr = keyedResource<string>(
      (key) => async () => `value-${key}`,
      { initial: '', label: 'thing' },
    );
    await kr.for('a').refresh();
    await kr.for('b').refresh();

    kr.clear();
    expect(kr.keys()).toEqual([]);
    expect(kr.for('a').loaded).toBe(false);
  });

  it('labels errors per key when given a label function', async () => {
    const onError = vi.fn();
    const kr = keyedResource<string>(
      () => async () => {
        throw new Error('boom');
      },
      { initial: '', label: (key) => `thing (${key})`, onError },
    );
    await kr.for('a').refresh();
    expect(onError).toHaveBeenCalledWith('thing (a): boom');
  });
});
