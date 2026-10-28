/**
 * Store for the Work hub's Jira tab — this profile's tickets from its own JQL.
 *
 * Module-scoped and cached per profile, for the same reason the Code tab's
 * store is: the hub renders its tabs under `{#if}`, so switching code -> jira
 * -> code destroyed this component and reloaded Jira from scratch, on top of
 * the same cost on every profile switch. One resource per profile means both
 * round trips are paid once each and switching back paints immediately.
 *
 * The JQL draft and the link picker stay in the component: they are editor
 * state for one visit, not data about a profile.
 */
import { keyedResource, type Resource } from './resource.svelte';
import { getApi } from '../utils/api';
import { notifications } from '../notifications.svelte';
import { httpsCloneURL, type DispatchTarget, type Repo } from './code.svelte';
import type { main } from '../../../wailsjs/go/models';

/**
 * Resolve the repository a ticket's work happens in — ref first, else nothing.
 *
 * A Jira ticket carries no repo, but every begin-work lane needs one. The
 * ticket's own code refs are the best available answer: if a pull request
 * already references the key, that is where the work lives. Only when there is
 * no ref at all does the caller fall back to asking (the inline repo picker),
 * which arrives here as `chosen`.
 *
 * Returns null when there is nothing to go on, which is the signal to show
 * that picker rather than a dead button.
 */
export function jiraDispatchTarget(
  tk: main.JiraTicket,
  repos: Repo[],
  chosen?: string,
): DispatchTarget | null {
  const base = {
    kind: 'jira' as const,
    number: 0,
    title: tk?.summary ?? '',
    htmlURL: tk?.url ?? '',
    issueKey: tk?.key ?? '',
  };

  // An explicit choice wins over any ref: the operator is correcting us.
  if (chosen) {
    const picked = repos.find((r) => r.full_name === chosen);
    return picked
      ? { ...base, repoFullName: picked.full_name, repoURL: picked.clone_url }
      : null;
  }

  const ref = (tk?.refs ?? [])[0];
  if (!ref?.repo) return null;

  const known = repos.find((r) => r.full_name === ref.repo);
  if (known) return { ...base, repoFullName: known.full_name, repoURL: known.clone_url };

  // The ref points at a repo this profile does not list — a fork, or another
  // org's. Its pull-request url still normalizes to a clone url, which beats
  // offering the operator nothing on a ticket that plainly has code.
  const url = httpsCloneURL({ repoURL: '', htmlURL: ref.url } as DispatchTarget);
  return url ? { ...base, repoFullName: ref.repo, repoURL: url } : null;
}

type Tickets = main.JiraTicketsResult | null;
type Presets = main.JiraJQLPreset[];

class JiraStore {
  /** The profile whose tickets are on screen. Every read below is scoped to it. */
  private requested = $state('');
  /** The profile whose PRESETS are on screen — tracked apart from `requested`
   *  so loading a chip row never makes the ticket list look cold. */
  private presetProfile = $state('');
  /** Per-profile "bypass the server memo next fetch" — see CodeStore for why
   *  this is keyed rather than a single boolean. */
  private forceNext = new Map<string, boolean>();

  private tickets = keyedResource<Tickets>(
    (profile) => (api) =>
      api.JiraTickets(profile, this.forceNext.get(profile) ?? false) as Promise<
        main.JiraTicketsResult
      >,
    {
      initial: null,
      label: (profile) => `Jira tickets (${profile})`,
      onError: (m) => notifications.error(m),
    },
  );

  /**
   * The profile's named queries, cached the same way its tickets are.
   *
   * A second resource rather than a field on the tickets payload: the presets
   * change only when the operator edits them, so folding them into the
   * 45s-TTL ticket read would re-fetch a list that almost never moves, and a
   * ticket refresh would churn the chip row.
   */
  private presetsRes = keyedResource<Presets>(
    (profile) => (api) =>
      api.GetJiraJQLPresets(profile) as Promise<main.JiraJQLPreset[]>,
    {
      initial: [],
      label: (profile) => `Jira presets (${profile})`,
      onError: (m) => notifications.error(m),
    },
  );

  private get current(): Resource<Tickets> | null {
    return this.requested ? this.tickets.for(this.requested) : null;
  }

  private get res(): main.JiraTicketsResult | null {
    return this.current?.value ?? null;
  }

  /** The profile that owns the rows on screen — derived, never tracked. See
   *  CodeStore: a field assigned after a load resolves names the PREVIOUS
   *  profile during a warm switch, and anything writing through it targets the
   *  wrong account. */
  get profile() {
    return this.requested;
  }

  /** True only while there is nothing to show yet. */
  get loading() {
    return this.current?.loading ?? false;
  }
  /** True while re-fetching over tickets that are already on screen. */
  get refreshing() {
    return this.current?.refreshing ?? false;
  }
  get loadError() {
    return this.current?.error ?? null;
  }
  /** RFC3339 time the visible tickets came off Jira, or '' before any load. */
  get fetchedAt() {
    return this.res?.fetched_at ?? '';
  }
  /** The backend served a past-TTL copy and is refreshing behind us. */
  get serverRevalidating() {
    return this.res?.stale ?? false;
  }
  /** True while a background check is running in either layer. */
  get backgroundBusy() {
    return this.refreshing || this.serverRevalidating;
  }

  rows = $derived(this.res?.tickets ?? []);
  /** Jira's own message — a JQL typo, or "not enabled". Not a load failure. */
  jiraError = $derived(this.res?.error ?? '');
  /** The saved query this profile's tickets came from. */
  jql = $derived(this.res?.jql ?? '');

  /** This profile's named queries. Empty until loadPresets has resolved. */
  presets = $derived(
    this.presetProfile ? (this.presetsRes.for(this.presetProfile).value ?? []) : [],
  );
  /** True only while there is no chip row to show yet. */
  get presetsLoading() {
    return this.presetProfile ? this.presetsRes.for(this.presetProfile).loading : false;
  }

  /** Read this profile's presets, painting from cache on a revisit. */
  async loadPresets(profile: string): Promise<void> {
    if (!profile) return;
    this.presetProfile = profile;
    await this.presetsRes.for(profile).refresh();
  }

  /**
   * Make one preset the active query.
   *
   * The tickets on screen are the PREVIOUS query's results, so they are
   * dropped here — the backend drops its own memo inside SetJiraJQL, but the
   * frontend resource would otherwise keep serving what it already holds and
   * the chip would appear to do nothing.
   *
   * Errors are rethrown rather than swallowed: the caller decides whether a
   * failed switch is a toast or an inline message.
   */
  async applyPreset(profile: string, name: string): Promise<void> {
    const api = await getApi();
    if (!api) throw new Error('API bindings unavailable');
    await api.ApplyJiraJQLPreset(profile, name);
    this.invalidate(profile);
  }

  /**
   * Replace this profile's preset list, then re-read it.
   *
   * The re-read matters: the backend trims names and queries, so what ends up
   * on screen is what was actually stored and not the operator's draft.
   *
   * The tickets are deliberately NOT invalidated — editing the list does not
   * change which query is active.
   */
  async savePresets(profile: string, presets: main.JiraJQLPreset[]): Promise<void> {
    const api = await getApi();
    if (!api) throw new Error('API bindings unavailable');
    await api.SetJiraJQLPresets(profile, presets);
    this.presetsRes.drop(profile);
    await this.loadPresets(profile);
  }

  async refresh(profile: string, force = false): Promise<void> {
    if (!profile) return;
    this.requested = profile;
    this.forceNext.set(profile, force);
    try {
      await this.tickets.for(profile).refresh({ force });
    } finally {
      this.forceNext.delete(profile);
    }
  }

  /**
   * Drop one profile's cached tickets so the next read refetches.
   *
   * The backend invalidates its own memo on these writes; this is the
   * frontend half, because the resource would otherwise keep serving the value
   * it already holds and never ask again.
   */
  invalidate(profile: string): void {
    this.tickets.drop(profile);
    this.forceNext.delete(profile);
  }

  /** Forget every profile — for tests and for "no profile at all". */
  hardReset(): void {
    this.tickets.clear();
    this.presetsRes.clear();
    this.forceNext.clear();
    this.requested = '';
    this.presetProfile = '';
  }
}

export const jiraState = new JiraStore();
