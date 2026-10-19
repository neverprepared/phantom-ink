/**
 * Async resource + action primitives for panel data.
 *
 * ## Why
 *
 * The shell `getApi()` → `if (!a) return` → `try` → `notifications.error` →
 * `finally { loading = false }` appears 231 times across 57 files. The
 * duplication is in the STATE MACHINE, not the markup, which is why this is a
 * store factory rather than a component.
 *
 * It had already drifted into defects. GatewayPanel returned on `!a` while
 * surfacing no error at all, so the panel rendered a confident but false "No
 * catalog servers" whenever the bindings were unavailable. Every hand-rolled
 * copy is another chance to forget one of the four states.
 *
 * ## Stale-while-revalidate
 *
 * Panel data lived in panel-local `$state`, and AppShell renders panels under
 * `{#if}` — so every navigation destroyed the component, re-fetched from
 * scratch, and showed a full-panel spinner over data that had been on screen
 * two seconds earlier. A resource created at module scope outlives panel
 * mounts: `refresh()` keeps the previous rows visible and flips `refreshing`
 * instead of `loading`, so the panel can render a subtle marker (the Dashboard
 * already has exactly that affordance) and reserve the spinner for a genuinely
 * empty first load.
 *
 * ## Four states, not two
 *
 * `safe(p, fallback)` collapsed "not configured", "auth failed", "remote down"
 * and "genuinely empty" into one value, so StatusBar showed "0 sessions" for
 * all four. A resource keeps `error` alongside the data so a caller can tell
 * an empty result from a failed one — which is the minimum needed to show
 * something actionable.
 */

import { getApi } from '../utils/api';

export interface ResourceOptions<T> {
  /** Value before the first successful load. */
  initial: T;
  /** Human label used in error messages and logs. */
  label: string;
  /**
   * Scope key, read at request time and re-read when the response lands. A
   * response whose key no longer matches is DISCARDED.
   *
   * This exists for profile scoping, which is the invariant this codebase
   * cares most about: every panel is profile-scoped, and a fetch that resolves
   * after the user switches profile would otherwise paint one profile's rows
   * under another profile's header. A sequence guard alone does not catch it —
   * the response is the newest, it is just for the wrong tenant.
   *
   * Changing the key also clears the retained value, so stale-while-revalidate
   * never shows profile A's data while profile B is loading.
   */
  key?: () => string;
  /**
   * Called when a load fails. Omit to stay quiet (the error is still readable
   * on the resource) — pass notifications.error to surface a toast.
   */
  onError?: (message: string) => void;
}

export interface Resource<T> {
  readonly value: T;
  /** True only while there is nothing to show yet. */
  readonly loading: boolean;
  /** True while re-fetching over data that is already on screen. */
  readonly refreshing: boolean;
  /** Last failure message, or null. Distinguishes "failed" from "empty". */
  readonly error: string | null;
  /** True once a load has succeeded at least once. */
  readonly loaded: boolean;
  /** Fetch. Concurrent calls are collapsed; stale responses are discarded. */
  refresh: (opts?: { force?: boolean }) => Promise<void>;
  /** Drop the cached value, so the next refresh is a cold load again. */
  reset: () => void;
  /** Replace the value locally, e.g. for an optimistic update. */
  set: (next: T) => void;
}

type Api = NonNullable<Awaited<ReturnType<typeof getApi>>>;

/**
 * Create a resource backed by one binding call.
 *
 * `fetcher` receives the resolved API object, so a caller never repeats the
 * `getApi()` / null-guard dance — and crucially, an unavailable binding
 * becomes a visible error rather than a silent empty result.
 */
export function resource<T>(
  fetcher: (api: Api) => Promise<T>,
  options: ResourceOptions<T>,
): Resource<T> {
  let value = $state<T>(options.initial);
  let loading = $state(false);
  let refreshing = $state(false);
  let error = $state<string | null>(null);
  let loaded = $state(false);

  // Monotonic sequence: a response from a superseded request is discarded
  // rather than overwriting a newer one. This is the race that let one
  // profile's rows render under another profile's header.
  let seq = 0;
  let inFlight: Promise<void> | null = null;
  // The key the currently-held value belongs to.
  let valueKey: string | null = null;

  async function run(): Promise<void> {
    const mySeq = ++seq;
    const myKey = options.key?.() ?? null;

    // A key change invalidates the retained value: showing the previous
    // scope's rows while the new one loads is exactly the leak this guards.
    if (myKey !== valueKey && loaded) {
      value = options.initial;
      loaded = false;
      error = null;
    }

    if (loaded) {
      refreshing = true;
    } else {
      loading = true;
    }
    try {
      const api = await getApi();
      if (!api) {
        // NOT a silent empty result. Returning quietly here is what made a
        // panel claim it had no data when it simply could not ask.
        throw new Error('API bindings unavailable');
      }
      const next = await fetcher(api);
      if (mySeq !== seq) return; // superseded
      // Re-read the key: the scope may have changed while this was in flight.
      if ((options.key?.() ?? null) !== myKey) return;
      value = next;
      valueKey = myKey;
      error = null;
      loaded = true;
    } catch (e) {
      if (mySeq !== seq) return;
      const message = e instanceof Error ? e.message : String(e);
      error = message;
      options.onError?.(`${options.label}: ${message}`);
    } finally {
      if (mySeq === seq) {
        loading = false;
        refreshing = false;
      }
      inFlight = null;
    }
  }

  return {
    get value() {
      return value;
    },
    get loading() {
      return loading;
    },
    get refreshing() {
      return refreshing;
    },
    get error() {
      return error;
    },
    get loaded() {
      return loaded;
    },
    refresh(opts) {
      // Collapse concurrent refreshes: a panel that mounts and also ticks
      // would otherwise issue two identical calls back to back.
      if (inFlight && !opts?.force) return inFlight;
      inFlight = run();
      return inFlight;
    },
    reset() {
      seq++; // invalidate anything in flight
      value = options.initial;
      valueKey = null;
      loaded = false;
      error = null;
      loading = false;
      refreshing = false;
    },
    set(next: T) {
      value = next;
    },
  };
}

export interface ActionOptions {
  label: string;
  onError?: (message: string) => void;
  onSuccess?: (message: string) => void;
  /** Message passed to onSuccess. Omit for no success toast. */
  successMessage?: string;
}

export interface Action<Args extends unknown[]> {
  /** True while running. Bind to a button's disabled to stop double-submits. */
  readonly busy: boolean;
  readonly error: string | null;
  run: (...args: Args) => Promise<boolean>;
}

/**
 * Create a one-shot action (start a session, delete a rule, ...).
 *
 * Returns whether it succeeded, so callers can chain a refresh without
 * re-implementing the try/catch/busy bookkeeping. `busy` exists because
 * double-submit guards were hand-rolled per call site and inconsistently.
 */
export function action<Args extends unknown[]>(
  fn: (api: Api, ...args: Args) => Promise<unknown>,
  options: ActionOptions,
): Action<Args> {
  let busy = $state(false);
  let error = $state<string | null>(null);

  return {
    get busy() {
      return busy;
    },
    get error() {
      return error;
    },
    async run(...args: Args): Promise<boolean> {
      if (busy) return false; // in-flight: ignore the repeat click
      busy = true;
      error = null;
      try {
        const api = await getApi();
        if (!api) throw new Error('API bindings unavailable');
        await fn(api, ...args);
        if (options.successMessage) options.onSuccess?.(options.successMessage);
        return true;
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        error = message;
        options.onError?.(`${options.label}: ${message}`);
        return false;
      } finally {
        busy = false;
      }
    },
  };
}
