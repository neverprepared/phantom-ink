/**
 * Which optional tabs a profile has in the Work hub.
 *
 * Module-scoped and keyed by profile, because AppShell renders panels under
 * `{#if}`: the hub is destroyed on every navigation away, and when this lived
 * in component state the tab STRIP waited on the probes before it could
 * render, so the tabs visibly popped in a moment after the panel.
 *
 * The probe is split in two deliberately:
 *
 *   probeLocal  — reads SQLite only (is Jira on at all, has this profile
 *                 opted in). Instant, and it is what decides whether the tab
 *                 appears.
 *   confirm     — the full JiraStatus, which resolves the profile's gateway
 *                 env to see whether credentials are actually present. That
 *                 endpoint costs ~1.6s of server-side decryption, so nothing
 *                 the operator looks at may wait on it.
 *
 * Opting in is an explicit operator action, so it is a good predictor of
 * "this profile has Jira" -- and when `confirm` disagrees the tab is withdrawn
 * and the operator is moved off it. Availability is a UI affordance, not an
 * authorization: each tab fetches its own data per profile, so a wrong guess
 * costs one frame of a tab, never another profile's rows.
 */
import { getApi } from '../utils/api';
import { workState } from '../stores.svelte';

type Availability = { jira: boolean; dora: boolean };

class WorkTabsStore {
  private availability = $state<Record<string, Availability>>({});

  jira(profile: string): boolean {
    return Boolean(profile && this.availability[profile]?.jira);
  }

  dora(profile: string): boolean {
    return Boolean(profile && this.availability[profile]?.dora);
  }

  /** True once this profile has been probed, so a remount can skip the flash. */
  known(profile: string): boolean {
    return Boolean(profile && this.availability[profile]);
  }

  /**
   * Record one answer, and un-strand the operator if their tab just vanished.
   *
   * `active` is the profile on screen RIGHT NOW, passed in rather than read
   * here: a probe that resolves after a profile switch must not move the tab
   * of the profile that replaced it.
   */
  private set(profile: string, tab: 'jira' | 'dora', on: boolean, active?: string): void {
    const cur = this.availability[profile] ?? { jira: false, dora: false };
    this.availability[profile] = { ...cur, [tab]: on };
    if (!on && active === profile && workState.tab === tab) workState.tab = 'code';
  }

  /** The instant half: local reads decide whether the tab is offered. */
  async probeLocal(profile: string): Promise<void> {
    if (!profile) return;
    const api = await getApi();
    if (!api) return;
    try {
      const [enabled, optedIn] = await Promise.all([
        api.JiraGloballyEnabled(),
        api.JiraOptedIn(profile),
      ]);
      this.set(profile, 'jira', Boolean(enabled && optedIn));
    } catch {
      this.set(profile, 'jira', false);
    }
  }

  /**
   * The slow half: confirm Jira's credentials and probe delivery.
   *
   * The two run concurrently. They used to be sequential, which meant a slow
   * or hanging JiraStatus also withheld the delivery tab, even though
   * DORAOverview reads local SQLite and costs nothing.
   */
  async confirm(profile: string, active: string): Promise<void> {
    if (!profile) return;
    const api = await getApi();
    if (!api) return;
    await Promise.all([
      (async () => {
        try {
          const st = await api.JiraStatus(profile);
          this.set(profile, 'jira', Boolean(st?.configured && st?.opted_in), active);
        } catch {
          this.set(profile, 'jira', false, active);
        }
      })(),
      (async () => {
        try {
          const ov = await api.DORAOverview(profile, 30);
          this.set(profile, 'dora', !ov?.token_missing, active);
        } catch {
          this.set(profile, 'dora', false, active);
        }
      })(),
    ]);
  }

  /** Forget every profile — for tests. */
  hardReset(): void {
    this.availability = {};
  }
}

export const workTabsState = new WorkTabsStore();
