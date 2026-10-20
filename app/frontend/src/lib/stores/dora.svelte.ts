/**
 * Store for the Work hub's Delivery tab — this profile's DORA metrics.
 *
 * Cached, keyed by `profile|windowDays`. The hub renders its tabs under
 * `{#if}`, so a tab switch destroyed this component and re-ran the read; and
 * the 30/90-day toggle only CLAIMED to be instant (DORAOverview is mostly
 * local SQLite, but it also makes a broker round-trip for the token banner).
 * Keying on the window as well as the profile means toggling back to a window
 * you have already seen paints immediately.
 *
 * Runner health used to live here too. It does not belong on a per-profile
 * delivery page: a runner is fleet infrastructure, and the table mixed
 * fleet-wide queue depth with profile-scoped task counts in one row. It now
 * lives with the rest of the fleet in Settings -> runners.
 */
import { keyedResource, type Resource } from './resource.svelte';
import { notifications } from '../notifications.svelte';
import type { main } from '../../../wailsjs/go/models';

type Overview = main.DORAOverview | null;

/** Metrics cache key. The window is part of the identity, not a filter. */
function metricsKey(profile: string, windowDays: number): string {
  return `${profile}|${windowDays}`;
}

class DoraStore {
  /** Which window the tab is showing. Persisted across mounts with the data. */
  windowDays = $state(30);

  private requested = $state('');

  /**
   * When each cached key was last read, for the freshness label.
   *
   * Tracked here rather than carried in the payload because DORAOverview has
   * no server-side memo and so nothing stamps it -- unlike CodeOverview and
   * JiraTicketsResult, whose `fetched_at` describes the age of the DATA. This
   * is the age of the read, which is the honest thing to show when the read
   * itself is the only source.
   */
  private fetchedAtByKey = $state<Record<string, number>>({});

  private overviews = keyedResource<Overview>(
    (key) => (api) => {
      // The key carries both dimensions; split rather than close over state,
      // so a resource always fetches exactly what it is keyed for.
      const sep = key.lastIndexOf('|');
      const profile = key.slice(0, sep);
      const days = Number(key.slice(sep + 1));
      return api.DORAOverview(profile, days) as Promise<main.DORAOverview>;
    },
    {
      initial: null,
      label: (key) => `Delivery metrics (${key})`,
      onError: (m) => notifications.error(m),
    },
  );

  private get current(): Resource<Overview> | null {
    return this.requested ? this.overviews.for(metricsKey(this.requested, this.windowDays)) : null;
  }

  private get ov(): main.DORAOverview | null {
    return this.current?.value ?? null;
  }

  /** The profile owning what is on screen — derived, never tracked. */
  get profile() {
    return this.requested;
  }

  get loading() {
    return this.current?.loading ?? false;
  }
  get refreshing() {
    return this.current?.refreshing ?? false;
  }
  get loadError() {
    return this.current?.error ?? null;
  }
  /** True while a background check is running. No server-side memo here, so
   *  unlike the Code and Jira tabs this is the only source. */
  get backgroundBusy() {
    return this.refreshing;
  }

  /** ISO time the visible metrics were read, or '' before any load. */
  get fetchedAt() {
    if (!this.requested) return '';
    const at = this.fetchedAtByKey[metricsKey(this.requested, this.windowDays)];
    return at ? new Date(at).toISOString() : '';
  }

  metrics = $derived(this.ov?.metrics ?? null);
  bands = $derived(this.ov?.metrics?.bands ?? {});
  tokenMissing = $derived(this.ov?.token_missing ?? false);
  tokenInvalid = $derived(this.ov?.token_invalid ?? false);
  windowOf = $derived(this.ov?.window_days ?? 0);
  lastSyncedAt = $derived(this.ov?.last_synced_at ?? '');
  syncError = $derived(this.ov?.sync_error ?? '');
  caveats = $derived(this.ov?.caveats ?? []);
  /** Sorted so the table does not reshuffle between loads. */
  perRepo = $derived(
    Object.entries(this.ov?.metrics?.per_repo ?? {}).sort((a, b) => a[0].localeCompare(b[0])),
  );

  /** Load the metrics for the current profile and window. */
  async refresh(profile: string, force = false): Promise<void> {
    if (!profile) return;
    this.requested = profile;
    await this.loadKey(metricsKey(profile, this.windowDays), force);
  }

  /** Switch window. A window already seen for this profile paints instantly. */
  async setWindow(profile: string, days: number): Promise<void> {
    this.windowDays = days;
    if (profile) await this.loadKey(metricsKey(profile, days));
  }

  /** Read one key and record when it landed, for the freshness label. */
  private async loadKey(key: string, force = false): Promise<void> {
    const r = this.overviews.for(key);
    await r.refresh({ force });
    // Only a successful read advances the age: a failed poll leaves the label
    // reporting how old the rows on screen actually are.
    if (r.loaded && !r.error) this.fetchedAtByKey[key] = Date.now();
  }

  /**
   * Drop this profile's cached metrics across EVERY window.
   *
   * A sync rewrites the underlying deploy/revert rows, so both windows are
   * wrong afterwards — dropping only the visible one would leave the toggle
   * showing pre-sync numbers.
   */
  invalidate(profile: string): void {
    for (const key of this.overviews.keys()) {
      if (key.slice(0, key.lastIndexOf('|')) === profile) {
        this.overviews.drop(key);
        delete this.fetchedAtByKey[key];
      }
    }
  }

  hardReset(): void {
    this.overviews.clear();
    this.fetchedAtByKey = {};
    this.requested = '';
    this.windowDays = 30;
  }
}

export const doraState = new DoraStore();
