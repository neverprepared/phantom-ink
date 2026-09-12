/**
 * Store for the Code panel — the profile's GitHub launchpad.
 *
 * One `GitHubOverview(profile)` call fills every section, so the four result
 * lists, the loading/error flags, and the dispatch-modal target all live here
 * rather than in CodePanel's `$state`. The panel's sections and the dispatch
 * modal read the same source without prop-drilling through three levels.
 *
 * Nothing is cached across profiles: a refresh for profile B overwrites the
 * lists, and a response that lands after a profile switch is DROPPED (the
 * overview echoes back which profile it belongs to). Showing one profile's
 * repositories under another profile's name would be a leak, not a glitch.
 */
import { getApi } from '../utils/api';
import { notifications } from '../notifications.svelte';

export interface Repo {
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
  repo_full_name: string;
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

export interface GitHubNotification {
  id: string;
  repo_full_name: string;
  subject_title: string;
  subject_type: string;
  reason: string;
  updated_at: string;
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
 * Prompt templates per target kind. These SEED an editable textarea — the
 * operator is expected to tweak before sending, so each one bakes the context
 * (repo, number, title, url) into prose the agent can act on without a second
 * lookup rather than trying to be a complete brief.
 */
export const PROMPT_TEMPLATES: Record<DispatchKind, PromptTemplate[]> = {
  pr: [
    {
      label: 'Review this PR',
      build: (t) =>
        `Review pull request #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Read the diff, then report correctness bugs, missing test coverage, and anything that ` +
        `breaks the repo's conventions. Post the review as PR comments. Do not push code.`,
    },
    {
      label: 'Fix failing CI',
      build: (t) =>
        `CI is failing on pull request #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Check out the PR branch, reproduce the failure locally, fix the cause (not the symptom), ` +
        `and push to the SAME branch. Wait for CI to go green.`,
    },
  ],
  issue: [
    {
      label: 'Address this issue',
      build: (t) =>
        `Address issue #${t.number} ("${t.title}") in ${t.repoFullName}.\n${t.htmlURL}\n\n` +
        `Read the issue, implement the fix on a feature branch with tests, and open a PR that ` +
        `references the issue. Stay in scope — note anything else you find in the PR body.`,
    },
  ],
  repo: [
    {
      label: 'Start work',
      build: (t) =>
        `Repository: ${t.repoFullName}\n${t.htmlURL}\n\n` +
        `<describe the work here>\n\n` +
        `Work on a feature branch, add tests for every behaviour you touch, and open a PR.`,
    },
  ],
};

const DEFAULT_AGENT = 'worker';

class CodeStore {
  // ── Overview ─────────────────────────────────────────────────────────────
  profile = $state('');
  loading = $state(false);
  /** Set only when the single overview call itself failed (no sections at all). */
  loadError = $state<string | null>(null);
  tokenMissing = $state(false);
  tokenInvalid = $state(false);

  repos = $state<Repo[]>([]);
  pullRequests = $state<Issue[]>([]);
  issues = $state<Issue[]>([]);
  notifications = $state<GitHubNotification[]>([]);

  reposError = $state('');
  pullRequestsError = $state('');
  issuesError = $state('');
  notificationsError = $state('');

  /** Client-side filter over the repositories list. */
  repoFilter = $state('');

  // ── Dispatch modal ───────────────────────────────────────────────────────
  dispatchTarget = $state<DispatchTarget | null>(null);
  dispatchPrompt = $state('');
  dispatchAgent = $state(DEFAULT_AGENT);
  dispatching = $state(false);
  agents = $state<string[]>([DEFAULT_AGENT]);
  /** Task id of the last successful dispatch — the panel links it to Jobs. */
  lastTaskID = $state('');

  /** Repos narrowed by the filter box (matches name or description). */
  filteredRepos = $derived.by(() => {
    const q = this.repoFilter.trim().toLowerCase();
    if (!q) return this.repos;
    return this.repos.filter(
      (r) => r.full_name.toLowerCase().includes(q) || (r.description ?? '').toLowerCase().includes(q)
    );
  });

  /** Rows the operator is on the hook for — PRs plus assigned issues. */
  attentionCount = $derived(this.pullRequests.length + this.issues.length);

  templates = $derived.by(() =>
    this.dispatchTarget ? PROMPT_TEMPLATES[this.dispatchTarget.kind] : []
  );

  // ── Loaders ──────────────────────────────────────────────────────────────

  /**
   * Fetch one profile's whole launchpad. `profile` is passed explicitly (not
   * read off profileState) so the caller's $effect owns the re-fetch trigger.
   */
  async refresh(profile: string): Promise<void> {
    if (!profile) {
      this.reset();
      return;
    }
    const a = await getApi();
    if (!a) {
      this.loadError = 'API bindings unavailable';
      return;
    }
    this.loading = true;
    try {
      const ov = (await (a as any).GitHubOverview(profile)) as CodeOverview;
      // A response for a profile the user has since switched away from is
      // stale — dropping it keeps the header's profile name honest.
      if (ov.profile && ov.profile !== profile) return;
      this.apply(ov);
      this.loadError = null;
    } catch (e) {
      this.loadError = String(e);
    } finally {
      this.loading = false;
    }
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

  private apply(ov: CodeOverview): void {
    this.profile = ov.profile;
    this.tokenMissing = ov.token_missing;
    this.tokenInvalid = ov.token_invalid;
    this.repos = ov.repos ?? [];
    this.pullRequests = ov.pull_requests ?? [];
    this.issues = ov.issues ?? [];
    this.notifications = ov.notifications ?? [];
    this.reposError = ov.repos_error ?? '';
    this.pullRequestsError = ov.pull_requests_error ?? '';
    this.issuesError = ov.issues_error ?? '';
    this.notificationsError = ov.notifications_error ?? '';
  }

  reset(): void {
    this.repos = [];
    this.pullRequests = [];
    this.issues = [];
    this.notifications = [];
    this.reposError = '';
    this.pullRequestsError = '';
    this.issuesError = '';
    this.notificationsError = '';
    this.tokenMissing = false;
    this.tokenInvalid = false;
    this.loadError = null;
  }

  // ── Dispatch ─────────────────────────────────────────────────────────────

  /** Open the modal on a target, pre-seeded with its first template. */
  openDispatch(target: DispatchTarget): void {
    this.dispatchTarget = target;
    this.lastTaskID = '';
    const first = PROMPT_TEMPLATES[target.kind][0];
    this.dispatchPrompt = first ? first.build(target) : '';
  }

  closeDispatch(): void {
    this.dispatchTarget = null;
    this.dispatchPrompt = '';
  }

  /** Re-seed the textarea from a template, discarding hand edits. */
  applyTemplate(tpl: PromptTemplate): void {
    if (!this.dispatchTarget) return;
    this.dispatchPrompt = tpl.build(this.dispatchTarget);
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
}

export const codeState = new CodeStore();
