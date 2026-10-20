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
import { notifications } from '../notifications.svelte';
import type { main } from '../../../wailsjs/go/models';

type Tickets = main.JiraTicketsResult | null;

class JiraStore {
  /** The profile whose tickets are on screen. Every read below is scoped to it. */
  private requested = $state('');
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
    this.forceNext.clear();
    this.requested = '';
  }
}

export const jiraState = new JiraStore();
