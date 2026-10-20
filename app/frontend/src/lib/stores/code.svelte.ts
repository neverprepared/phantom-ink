/**
 * Store for the Code panel — the profile's GitHub launchpad.
 *
 * One `CodeOverview(profile)` call fills every section, so the four result
 * lists, the loading/error flags, and the dispatch-modal target all live here
 * rather than in CodePanel's `$state`. The panel's sections and the dispatch
 * modal read the same source without prop-drilling through three levels.
 *
 * ## Stale-while-revalidate
 *
 * The overview goes through `resource()`, which exists for exactly this
 * problem. AppShell renders panels under `{#if}`, so every navigation away
 * destroys CodePanel; this store is a module singleton that outlives it. The
 * panel used to `reset()` on mount, discarding rows that were still in memory
 * and dropping into a full-panel spinner over data that had been on screen a
 * second earlier. Now a same-profile remount keeps its rows and flips
 * `refreshing`; only a real profile change clears them.
 *
 * Isolation is unchanged and still the point: the resource is KEYED by
 * profile, so a key change clears the retained value and a response that
 * lands after a switch is discarded. Showing one profile's repositories under
 * another profile's name would be a leak, not a glitch.
 */
import { getApi } from '../utils/api';
import { notifications } from '../notifications.svelte';
import { resource } from './resource.svelte';

export interface Repo {
  provider: 'github' | 'ado';
  id: string;
  owner: string;
  name: string;
  full_name: string;
  description: string;
  html_url: string;
  clone_url: string;
  default_branch: string;
  pushed_at: string;
  stars: number;
  open_issues: number;
}

export interface Issue {
  provider: 'github' | 'ado';
  repo_full_name: string;
  repo_id: string;
  number: number;
  title: string;
  state: string;
  html_url: string;
  updated_at: string;
  user: string;
  draft: boolean;
  is_pull_request: boolean;
  /** Why this row is listed: authored / review-requested / assigned. */
  reason: string;
}

/** Identifies one repository for a `RepoDetail` call, provider-aware. */
export interface RepoRef {
  provider: 'github' | 'ado';
  owner: string;
  name: string;
  id: string;
  clone_url: string;
  default_branch: string;
}

export interface GitHubNotification {
  id: string;
  repo_full_name: string;
  subject_title: string;
  subject_type: string;
  reason: string;
  updated_at: string;
  url: string;
}

/** One code->ticket link. Manual ones can be removed; derived ones cannot. */
export interface IssueLink {
  key: string;
  manual: boolean;
}

export interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  /** new | indeterminate | done — Jira's 3-value status category. */
  status_category: string;
  assignee: string;
  url: string;
}

export interface CodeOverview {
  profile: string;
  token_missing: boolean;
  token_invalid: boolean;
  repos: Repo[];
  pull_requests: Issue[];
  issues: Issue[];
  notifications: GitHubNotification[];
  repos_error: string;
  pull_requests_error: string;
  issues_error: string;
  notifications_error: string;
  /** Row key (provider:repo#number) -> that row's Jira links. */
  issue_keys: Record<string, IssueLink[]>;
  jira_issues: Record<string, JiraIssue>;
  /** Non-fatal: a Jira outage costs the chips, never the git rows. */
  jira_error: string;
  /** RFC3339 timestamp of when these rows came off the providers. */
  fetched_at: string;
  /** True when the backend served a past-TTL copy and is refreshing behind it. */
  stale: boolean;
}

export interface Branch {
  name: string;
  sha: string;
}

export interface Commit {
  sha: string;
  /** The FULL commit message; the panel renders only its first line. */
  message: string;
  author: string;
  date: string;
  html_url: string;
}

/** One repository's detail view — the backend's RepoDetailResult. */
export interface RepoDetail {
  profile: string;
  provider: 'github' | 'ado';
  owner: string;
  repo: string;
  default_branch: string;
  token_missing: boolean;
  token_invalid: boolean;
  branches: Branch[];
  commits: Commit[];
  prs: Issue[];
  issues: Issue[];
  /** RAW markdown. Sanitizing happens at render — see MarkdownRenderer. */
  readme: string;
  readme_url: string;
  branches_error: string;
  commits_error: string;
  readme_error: string;
  prs_error: string;
  issues_error: string;
}

/** What a dispatch is about — drives which prompt templates are offered. */
export type DispatchKind = 'repo' | 'pr' | 'issue';

export interface DispatchTarget {
  kind: DispatchKind;
  /** owner/name — the human label and the template substitution. */
  repoFullName: string;
  /** Clone/html URL handed to the agent as repo_url. */
  repoURL: string;
  /** PR/issue number, 0 for a repo-level dispatch. */
  number: number;
  title: string;
  /** github.com link to the PR/issue (or the repo). */
  htmlURL: string;
}

export interface PromptTemplate {
  label: string;
  build: (t: DispatchTarget) => string;
}

/**
 * House style appended to every template. The operator wants terse,
 * WHY-focused writing — PR/review text that explains the reason, not the diff,
 * and no padding the code with explanatory comments. One place to tune it.
 */
export const STYLE_NOTE =
  'Style: keep all writing terse. Any PR description or review comment must be ' +
  'short and to the point — lead with WHY (the reason/decision), not a ' +
  'restatement of what the diff does. No lengthy explanations, and do not add ' +
  'explanatory code comments.';

/**
 * Prompt templates per target kind. These SEED an editable textarea — the
 * operator is expected to tweak before sending, so each one bakes the context
 * (repo, number, title, url) into prose the agent can act on without a second
 * lookup rather than trying to be a complete brief. Every template ends with
 * STYLE_NOTE so the agent's PR/review writing stays terse by default.
 */
export const PROMPT_TEMPLATES: Record<DispatchKind, PromptTemplate[]> = {
  pr: [
    {
      label: 'Review this PR',
      build: (t) =>
        `Review pull request #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Read the diff, then report correctness bugs, missing test coverage, and anything that ` +
        `breaks the repo's conventions. Post the review as PR comments. Do not push code.\n\n` +
        STYLE_NOTE,
    },
    {
      label: 'Fix failing CI',
      build: (t) =>
        `CI is failing on pull request #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Check out the PR branch, reproduce the failure locally, fix the cause (not the symptom), ` +
        `and push to the SAME branch. Wait for CI to go green.\n\n` +
        STYLE_NOTE,
    },
  ],
  issue: [
    {
      label: 'Address this issue',
      build: (t) =>
        `Address issue #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Read the issue, implement the fix on a feature branch with tests, and open a PR that ` +
        `references the issue. Stay in scope.\n\n` +
        STYLE_NOTE,
    },
  ],
  repo: [
    {
      label: 'Start work',
      build: (t) =>
        `Repository: ${t.repoFullName}\n${t.htmlURL}\n\n` +
        `<describe the work here>\n\n` +
        `Work on a feature branch, add tests for every behaviour you touch, and open a PR.\n\n` +
        STYLE_NOTE,
    },
  ],
};

/**
 * Which lane a "Begin work" submit takes.
 *
 * `task`    — an autonomous fleet agent (DispatchRepoTask): it clones, works,
 *             opens a PR, and reports to the hub. Nobody watches it.
 * `session` — an interactive container session (LaunchInteractiveSession) the
 *             operator attaches to and drives.
 *
 * The third lane (clone to the host + open a terminal) has no modal at all —
 * it's the repo row's direct `Clone + terminal` button.
 */
export type DispatchLane = 'task' | 'session';

/**
 * The https clone URL for a target. A repo row carries a real clone_url, but a
 * PR/issue search hit only has an html_url, so normalize both to something
 * `git clone` accepts before it goes into a prompt.
 */
export function httpsCloneURL(t: DispatchTarget): string {
  const raw = (t.repoURL || t.htmlURL || '').trim().replace(/\/+$/, '');
  if (!raw) return '';
  // Azure DevOps repo URLs put the repo name after a literal `/_git/` segment
  // (e.g. https://dev.azure.com/org/project/_git/repo/pullrequest/7), so the
  // github-shaped "host + first two segments" regex below would truncate to
  // .../org/project.git — wrong repo entirely. Detect `/_git/` and keep
  // everything up to and including the repo segment instead.
  //   httpsCloneURL({repoURL: '', htmlURL: 'https://dev.azure.com/org/project/_git/repo/pullrequest/7'} as DispatchTarget)
  //     === 'https://dev.azure.com/org/project/_git/repo.git'
  const adoMatch = raw.match(/^(https?:\/\/[^/]+\/[^/]+\/[^/]+\/_git\/[^/]+)(?:\/.*)?$/);
  if (adoMatch) {
    const base = adoMatch[1];
    return base.endsWith('.git') ? base : `${base}.git`;
  }
  // A PR/issue URL carries extra segments (…/pull/7) — keep host + owner/repo
  // so what lands in the prompt is something `git clone` accepts. The backend
  // normalizes again; this is so the operator SEES the right URL.
  const m = raw.match(/^(https?:\/\/[^/]+)\/([^/]+)\/([^/]+?)(?:\.git)?(?:\/.*)?$/);
  if (m) return `${m[1]}/${m[2]}/${m[3]}.git`;
  return raw.endsWith('.git') ? raw : `${raw}.git`;
}

/** Best clone URL for a PR/issue row, provider-aware. */
export function itemCloneURL(i: Issue): string {
  // A row carries its html_url; httpsCloneURL trims PR/issue path segments and
  // appends .git, working for both github.com and dev.azure.com/_git URLs.
  return httpsCloneURL({ repoURL: '', htmlURL: i.html_url } as DispatchTarget);
}

/**
 * The line that makes clone-in-session work. A brainbox session has no repo
 * field — the repo only reaches the container through the seeded task — so the
 * session lane spells out the clone AND that the credential is already there
 * (CreateSession forwards the profile env, GITHUB_TOKEN included).
 */
export function cloneInstruction(t: DispatchTarget): string {
  const url = httpsCloneURL(t);
  return `First: git clone ${url} (GITHUB_TOKEN is already in this session's env), then cd into it.`;
}

/**
 * Prepend the clone instruction unless it's already there. Idempotent so
 * switching lanes back and forth, or re-applying a template, never stacks
 * duplicate copies on top of the operator's edits.
 */
function withCloneInstruction(prompt: string, t: DispatchTarget): string {
  const url = httpsCloneURL(t);
  if (!url || prompt.includes(`git clone ${url}`)) return prompt;
  return `${cloneInstruction(t)}\n\n${prompt}`;
}

const DEFAULT_AGENT = 'worker';

/** Split a possibly-multi reason ("authored, review-requested") into parts. */
function splitReasons(reason: string): string[] {
  return (reason ?? '')
    .split(',')
    .map((r) => r.trim())
    .filter(Boolean);
}

class CodeStore {
  // ── Overview ─────────────────────────────────────────────────────────────
  //
  // The profile being viewed. Held separately from the resource's key because
  // the key is read DURING a fetch, while this is what the header renders —
  // it must not change until rows for the new profile actually land.
  profile = $state('');
  /** Which profile the in-flight (or next) fetch is for — the resource key. */
  private requested = $state('');
  /** Set on the next fetch only: the Refresh button bypasses the server memo. */
  private forceNext = false;

  // One resource holds the whole payload. Every list below is a view of it, so
  // there is no second copy to keep in sync and no `apply()` to forget a field.
  private overview = resource<CodeOverview | null>(
    (api) => api.CodeOverview(this.requested, this.forceNext) as Promise<CodeOverview>,
    {
      initial: null,
      label: 'Code overview',
      // Profile scoping, the invariant this codebase cares most about.
      key: () => this.requested,
    },
  );

  /** True only while there is nothing to show yet — the full-panel spinner. */
  get loading() {
    return this.overview.loading;
  }
  /** True while re-fetching over rows that are already on screen. */
  get refreshing() {
    return this.overview.refreshing;
  }
  /** Set only when the single overview call itself failed (no sections at all). */
  get loadError() {
    return this.overview.error;
  }
  /** RFC3339 time the visible rows came off the providers, or '' before any load. */
  get fetchedAt() {
    return this.overview.value?.fetched_at ?? '';
  }
  /**
   * True when the BACKEND handed over a past-TTL copy and is refreshing it
   * behind us. Distinct from `refreshing`, which is this store's own fetch:
   * both mean "a background check is running", and the panel shows one
   * indicator for either.
   */
  get serverRevalidating() {
    return this.overview.value?.stale ?? false;
  }
  /** True while any background check is in flight, from either layer. */
  get backgroundBusy() {
    return this.refreshing || this.serverRevalidating;
  }

  private get ov(): CodeOverview | null {
    return this.overview.value;
  }

  tokenMissing = $derived(this.ov?.token_missing ?? false);
  tokenInvalid = $derived(this.ov?.token_invalid ?? false);

  repos = $derived(this.ov?.repos ?? []);
  pullRequests = $derived(this.ov?.pull_requests ?? []);
  issues = $derived(this.ov?.issues ?? []);
  notifications = $derived(this.ov?.notifications ?? []);

  issueKeys = $derived(this.ov?.issue_keys ?? {});
  jiraIssues = $derived(this.ov?.jira_issues ?? {});
  jiraError = $derived(this.ov?.jira_error ?? '');

  reposError = $derived(this.ov?.repos_error ?? '');
  pullRequestsError = $derived(this.ov?.pull_requests_error ?? '');
  issuesError = $derived(this.ov?.issues_error ?? '');
  notificationsError = $derived(this.ov?.notifications_error ?? '');

  /** Client-side filter over the repositories list. */
  repoFilter = $state('');

  /** Provider filter across every section: 'all' | 'github' | 'ado'. */
  providerFilter = $state<'all' | 'github' | 'ado'>('all');
  /** Reason filter for the needs-attention list: 'all' | a reason string. */
  reasonFilter = $state('all');

  // ── Repo detail ──────────────────────────────────────────────────────────
  //
  // Held ALONGSIDE the overview, never instead of it: `closeDetail` clears only
  // these fields, so going back is instant rather than a second round-trip to
  // GitHub for a list the operator was looking at a moment ago.

  /** The repo the detail view is open on. Null means "show the overview". */
  detailRepo = $state<Repo | null>(null);
  detailLoading = $state(false);
  /** Set only when the RepoDetail call itself failed (no sections at all). */
  detailError = $state<string | null>(null);

  repoBranches = $state<Branch[]>([]);
  repoCommits = $state<Commit[]>([]);
  repoPRs = $state<Issue[]>([]);
  repoIssues = $state<Issue[]>([]);
  /** Raw markdown — MarkdownRenderer sanitizes before it reaches the DOM. */
  readme = $state('');
  readmeURL = $state('');

  branchesError = $state('');
  commitsError = $state('');
  readmeError = $state('');
  prsError = $state('');
  repoIssuesError = $state('');

  // ── Dispatch modal ───────────────────────────────────────────────────────
  dispatchTarget = $state<DispatchTarget | null>(null);
  dispatchPrompt = $state('');
  dispatchAgent = $state(DEFAULT_AGENT);
  dispatching = $state(false);
  agents = $state<string[]>([DEFAULT_AGENT]);
  /** Task id of the last successful dispatch — the panel links it to Jobs. */
  lastTaskID = $state('');

  // ── Begin-work lanes ─────────────────────────────────────────────────────
  /** Which lane the modal submits on. Autonomous task stays the default. */
  dispatchLane = $state<DispatchLane>('task');
  /** URL of the last launched interactive session — the panel links to attach. */
  lastSessionURL = $state('');
  /** full_name of the repo whose host clone is in flight (one at a time per row). */
  cloningRepo = $state('');
  /** Checkout path of the last successful host clone + terminal open. */
  lastClonePath = $state('');
  /** git's own stderr from the last failed clone, kept for the panel to show. */
  cloneError = $state('');

  /** Which providers appear in the loaded data — drives whether the provider
   *  toggle is worth showing (only when both are present). Notifications are
   *  GitHub-only, so they count toward GitHub. */
  hasGitHub = $derived(
    this.repos.some((r) => r.provider === 'github') ||
      this.pullRequests.some((i) => i.provider === 'github') ||
      this.issues.some((i) => i.provider === 'github') ||
      this.notifications.length > 0
  );
  hasADO = $derived(
    this.repos.some((r) => r.provider === 'ado') ||
      this.pullRequests.some((i) => i.provider === 'ado') ||
      this.issues.some((i) => i.provider === 'ado')
  );
  showProviderFilter = $derived(this.hasGitHub && this.hasADO);

  /** Repos narrowed by the provider filter and the text filter box. */
  filteredRepos = $derived.by(() => {
    const q = this.repoFilter.trim().toLowerCase();
    const pf = this.providerFilter;
    return this.repos.filter(
      (r) =>
        (pf === 'all' || r.provider === pf) &&
        (!q ||
          r.full_name.toLowerCase().includes(q) ||
          (r.description ?? '').toLowerCase().includes(q))
    );
  });

  /** The merged needs-attention rows (PRs + assigned issues/work-items). */
  attention = $derived.by(() => [...this.pullRequests, ...this.issues]);

  /** Distinct reasons present across attention rows (for the reason chips).
   *  A row may carry several ("authored, review-requested"). */
  reasonsPresent = $derived.by(() => {
    const set = new Set<string>();
    for (const i of this.attention) for (const r of splitReasons(i.reason)) set.add(r);
    return [...set];
  });

  /** Attention rows after the provider + reason filters. */
  filteredAttention = $derived.by(() => {
    const pf = this.providerFilter;
    const rf = this.reasonFilter;
    return this.attention.filter(
      (i) =>
        (pf === 'all' || i.provider === pf) &&
        (rf === 'all' || splitReasons(i.reason).includes(rf))
    );
  });

  /** Notifications after the provider filter (GitHub-only, so ADO hides them). */
  filteredNotifications = $derived.by(() =>
    this.providerFilter === 'ado' ? [] : this.notifications
  );

  /** Total rows the operator is on the hook for (unfiltered), for the header. */
  attentionCount = $derived(this.pullRequests.length + this.issues.length);

  templates = $derived.by(() =>
    this.dispatchTarget ? PROMPT_TEMPLATES[this.dispatchTarget.kind] : []
  );

  // ── Loaders ──────────────────────────────────────────────────────────────

  /**
   * Fetch one profile's whole launchpad. `profile` is passed explicitly (not
   * read off profileState) so the caller's $effect owns the re-fetch trigger.
   */
  async refresh(profile: string, force = false): Promise<void> {
    if (!profile) {
      this.hardReset();
      return;
    }
    // A real profile change resets the per-profile view state. The ROWS are
    // not cleared here — the resource is keyed by profile and clears them
    // itself, which keeps that decision in one place.
    const changed = profile !== this.requested;
    if (changed) this.reset();
    this.requested = profile;
    // force=true bypasses the Go-side per-profile memo; plain navigation
    // reuses it, which is what stops every panel visit re-issuing the whole
    // provider fan-out. A profile switch is NOT a force: the new profile's
    // own memo is perfectly good.
    this.forceNext = force;
    try {
      // The resource collapses concurrent refreshes onto the one in flight,
      // which is right for a remount and WRONG across a profile switch: the
      // in-flight request is for the old key, so it would be discarded on
      // arrival and the new profile would load nothing at all. Opting out of
      // the collapse on a key change is what makes the switch land.
      await this.overview.refresh({ force: force || changed });
    } finally {
      this.forceNext = false;
    }
    // Only name the profile in the header once its rows are the ones showing.
    if (this.overview.loaded) this.profile = this.ov?.profile || profile;
  }

  /** Load the hub's agent role catalog for the modal's picker. */
  async loadAgents(): Promise<void> {
    const a = await getApi();
    if (!a) return;
    try {
      const roles = (await a.ListAgentRoles()) ?? [];
      const names = roles.map((r: any) => r.name).filter(Boolean);
      if (names.length) this.agents = names;
    } catch {
      /* fleet may be down — the picker falls back to ['worker'] */
    }
  }

  /**
   * Jira issues linked to one row. Mirrors rowKey() in app/app_code.go —
   * keep the two in lockstep or every chip silently vanishes.
   */
  jiraFor(row: Issue): (JiraIssue & { manual: boolean })[] {
    const key = `${row.provider}:${row.repo_full_name}#${row.number}`;
    return (this.issueKeys[key] ?? [])
      .map((l) => {
        const issue = this.jiraIssues[l.key];
        return issue ? { ...issue, manual: l.manual } : null;
      })
      .filter((i): i is JiraIssue & { manual: boolean } => Boolean(i));
  }

  /**
   * Clear the view state that belongs to one profile. The ROWS are not touched:
   * the resource is keyed by profile and drops them on a key change, so a
   * same-profile remount keeps its data while this still runs harmlessly.
   *
   * Call this on a profile change, NOT on every panel mount — resetting on
   * mount is what made returning to the Work panel show an empty spinner over
   * rows that were still in memory.
   */
  reset(): void {
    // A profile switch closes the detail view: leaving another account's
    // repo open under a new profile's name would be a leak, not a glitch.
    this.closeDetail();
    // Lane results belong to the profile they were started under.
    this.lastTaskID = '';
    this.lastSessionURL = '';
    this.lastClonePath = '';
    this.cloneError = '';
    // View filters reset with the profile — an 'ado' filter carried onto a
    // GitHub-only profile would blank every section confusingly.
    this.providerFilter = 'all';
    this.reasonFilter = 'all';
  }

  /** reset() plus the rows themselves — for "no profile at all" and for tests. */
  hardReset(): void {
    this.reset();
    this.overview.reset();
    this.profile = '';
    this.requested = '';
  }

  // ── Notifications: mark read ───────────────────────────────────────────────

  /**
   * Replace just the notifications on the held payload. The lists are views of
   * one resource value, so an optimistic edit has to go through it rather than
   * assigning the derived field.
   */
  private setNotifications(rows: GitHubNotification[]): void {
    const cur = this.ov;
    if (!cur) return;
    this.overview.set({ ...cur, notifications: rows });
  }


  /**
   * Mark one notification read on GitHub and drop it from the list.
   * Optimistic: the row disappears immediately; a failed write (e.g. the token
   * lacks the `notifications` scope) restores it and shows the error.
   */
  async markNotificationRead(id: string): Promise<void> {
    const profile = this.profile;
    if (!profile || !id) return;
    const a = await getApi();
    if (!a) return;
    const prev = this.notifications;
    this.setNotifications(prev.filter((n) => n.id !== id));
    try {
      await (a as any).MarkNotificationRead(profile, id);
    } catch (e: any) {
      this.setNotifications(prev);
      notifications.error(`Couldn't mark read: ${e?.message ?? e}`);
    }
  }

  /** Mark every notification read on GitHub and clear the list (optimistic). */
  async markAllNotificationsRead(): Promise<void> {
    const profile = this.profile;
    if (!profile || this.notifications.length === 0) return;
    const a = await getApi();
    if (!a) return;
    const prev = this.notifications;
    this.setNotifications([]);
    try {
      await (a as any).MarkAllNotificationsRead(profile);
      notifications.success('All notifications marked read');
    } catch (e: any) {
      this.setNotifications(prev);
      notifications.error(`Couldn't mark all read: ${e?.message ?? e}`);
    }
  }

  // ── Repo detail ──────────────────────────────────────────────────────────

  /**
   * Open the detail view on one repo and fill it.
   *
   * `detailRepo` is set BEFORE the await so the view swaps immediately and
   * renders its per-section skeletons, rather than leaving the operator on the
   * overview wondering whether the click landed.
   */
  async openDetail(profile: string, repo: Repo): Promise<void> {
    if (!profile || !repo) return;
    this.detailRepo = repo;
    this.detailError = null;
    this.clearDetailSections();
    const a = await getApi();
    if (!a) {
      this.detailError = 'API bindings unavailable';
      return;
    }
    this.detailLoading = true;
    try {
      const ref: RepoRef = {
        provider: repo.provider,
        owner: repo.owner,
        name: repo.name,
        id: repo.id,
        clone_url: repo.clone_url,
        default_branch: repo.default_branch,
      };
      const d = (await (a as any).RepoDetail(profile, ref)) as RepoDetail;
      // A response for a repo (or profile) the operator has since navigated
      // away from is stale — dropping it keeps the header honest, the same
      // rule the overview applies on a profile switch.
      if (d.profile && d.profile !== profile) return;
      if (this.detailRepo?.full_name !== repo.full_name) return;
      this.applyDetail(d);
    } catch (e) {
      this.detailError = String(e);
    } finally {
      this.detailLoading = false;
    }
  }

  /** Back to the overview. Deliberately leaves the overview lists alone. */
  closeDetail(): void {
    this.detailRepo = null;
    this.detailLoading = false;
    this.detailError = null;
    this.clearDetailSections();
  }

  private applyDetail(d: RepoDetail): void {
    this.tokenMissing = d.token_missing;
    this.tokenInvalid = d.token_invalid;
    this.repoBranches = d.branches ?? [];
    this.repoCommits = d.commits ?? [];
    this.repoPRs = d.prs ?? [];
    this.repoIssues = d.issues ?? [];
    this.readme = d.readme ?? '';
    this.readmeURL = d.readme_url ?? '';
    this.branchesError = d.branches_error ?? '';
    this.commitsError = d.commits_error ?? '';
    this.readmeError = d.readme_error ?? '';
    this.prsError = d.prs_error ?? '';
    this.repoIssuesError = d.issues_error ?? '';
  }

  private clearDetailSections(): void {
    this.repoBranches = [];
    this.repoCommits = [];
    this.repoPRs = [];
    this.repoIssues = [];
    this.readme = '';
    this.readmeURL = '';
    this.branchesError = '';
    this.commitsError = '';
    this.readmeError = '';
    this.prsError = '';
    this.repoIssuesError = '';
  }

  // ── Dispatch ─────────────────────────────────────────────────────────────

  /**
   * Open the modal on a target, pre-seeded with its first template. The lane
   * resets to `task` every time: a launcher that silently remembers "container"
   * from twenty minutes ago would start the wrong kind of work.
   */
  openDispatch(target: DispatchTarget): void {
    this.dispatchTarget = target;
    this.lastTaskID = '';
    this.lastSessionURL = '';
    this.dispatchLane = 'task';
    const first = PROMPT_TEMPLATES[target.kind][0];
    this.dispatchPrompt = first ? first.build(target) : '';
  }

  closeDispatch(): void {
    this.dispatchTarget = null;
    this.dispatchPrompt = '';
  }

  /**
   * Switch lane, keeping the operator's edits. Moving to the session lane adds
   * the clone instruction the container needs; moving back leaves it in place
   * rather than editing text the operator can see and delete themselves.
   */
  setLane(lane: DispatchLane): void {
    this.dispatchLane = lane;
    if (lane === 'session' && this.dispatchTarget) {
      this.dispatchPrompt = withCloneInstruction(this.dispatchPrompt, this.dispatchTarget);
    }
  }

  /** Re-seed the textarea from a template, discarding hand edits. */
  applyTemplate(tpl: PromptTemplate): void {
    if (!this.dispatchTarget) return;
    const seeded = tpl.build(this.dispatchTarget);
    this.dispatchPrompt =
      this.dispatchLane === 'session'
        ? withCloneInstruction(seeded, this.dispatchTarget)
        : seeded;
  }

  /** Submit the modal down whichever lane is selected. */
  async submit(profile: string): Promise<void> {
    if (this.dispatchLane === 'session') return this.launchSession(profile);
    return this.dispatch(profile);
  }

  /**
   * Hand the edited prompt to a fleet agent. Fire-and-forget: the hub owns the
   * task from here and the Jobs panel is where it's watched.
   */
  async dispatch(profile: string): Promise<void> {
    const target = this.dispatchTarget;
    if (!target || !this.dispatchPrompt.trim() || this.dispatching) return;
    const a = await getApi();
    if (!a) return;
    this.dispatching = true;
    try {
      const task = await (a as any).DispatchRepoTask({
        profile,
        repo_url: target.repoURL,
        agent_name: this.dispatchAgent,
        description: this.dispatchPrompt.trim(),
      });
      this.lastTaskID = task?.id ?? '';
      notifications.success(`Dispatched ${this.dispatchAgent} on ${target.repoFullName}`);
      this.closeDispatch();
    } catch (e: any) {
      notifications.error(`Dispatch failed: ${e?.message ?? e}`);
    } finally {
      this.dispatching = false;
    }
  }

  /**
   * Start an interactive container session on the target. The session clones
   * the repo itself from the seeded prompt — there is no repo field on a
   * brainbox session — so the prompt is forced through withCloneInstruction
   * even if the operator switched lanes and then deleted the line.
   */
  async launchSession(profile: string): Promise<void> {
    const target = this.dispatchTarget;
    if (!target || !this.dispatchPrompt.trim() || this.dispatching) return;
    const a = await getApi();
    if (!a) return;
    this.dispatching = true;
    try {
      const res = await (a as any).LaunchInteractiveSession({
        profile,
        repo_url: httpsCloneURL(target),
        task: withCloneInstruction(this.dispatchPrompt.trim(), target),
      });
      if (res?.success === false) throw new Error(res?.error || 'session create failed');
      this.lastSessionURL = res?.url ?? '';
      notifications.success(`Session launched on ${target.repoFullName}`);
      this.closeDispatch();
    } catch (e: any) {
      notifications.error(`Session launch failed: ${e?.message ?? e}`);
    } finally {
      this.dispatching = false;
    }
  }

  /**
   * The host lane: clone into the profile's workspace (only if it isn't there
   * yet — an existing checkout is opened untouched) and open a terminal running
   * claude in it. No modal; the operator writes the prompt in the terminal.
   */
  async cloneAndOpen(profile: string, target: DispatchTarget): Promise<void> {
    if (!profile || this.cloningRepo) return;
    const a = await getApi();
    if (!a) return;
    this.cloningRepo = target.repoFullName;
    this.cloneError = '';
    try {
      const path = (await (a as any).OpenRepoLocally(profile, httpsCloneURL(target))) as string;
      this.lastClonePath = path;
      notifications.success(`Terminal open in ${path}`);
    } catch (e: any) {
      // git's stderr is the useful half of a private-repo auth failure — show
      // it rather than a generic "clone failed".
      this.cloneError = String(e?.message ?? e);
      notifications.error(this.cloneError);
    } finally {
      this.cloningRepo = '';
    }
  }
}

export const codeState = new CodeStore();
