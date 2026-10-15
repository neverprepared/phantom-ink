# Doctor panel — design

Date: 2026-10-15
Status: approved design, not yet implemented

## Problem

The app has `ServicesPanel` (compose state) and launch-time integration
reconcile (#371), but nowhere that answers "is this install actually healthy?"
A dozen recurring failures are documented in prose across `CLAUDE.md` and the
memory vault — orphan nginx holding `:8080` so Traefik cannot bind, a host
launchd router shadowing `:9910`, profile `env_key` drift, per-profile
`direnv` approvals, a missing `/usr/local/bin/docker` symlink after the
OrbStack migration, an unmigrated SoR 500-ing a whole vault. Each is
mechanically checkable. Today each is rediscovered by hand.

A second, related defect motivates the state model: the "image built" badge
renders a registry **401** as *"no image"*. That is a fake red — the install
reports a state it did not observe. Doctor exists to never do that.

## Decisions

Three settled frames, from brainstorming:

1. **Scope** — checks reach both the local host and the remote platform.
   Local-only would miss most of the documented gotchas, which are
   platform-side.
2. **Remediation** — Doctor diagnoses and offers an opt-in one-click fix, but
   only for remedies marked safe and idempotent. Anything destructive, or
   needing a secret or a judgment call, shows a copyable command instead.
   Fixes sit behind a master switch that defaults to off.
3. **Registry home** — checks live in `app/doctor/` behind a narrow interface
   with an injected executor, designed for a later lift into phantom-router.
   Same library-first shape that kept the loops extraction's test churn at
   zero.

### Constraint found during design

There is **no generic remote command executor**. The only remote seam is
`client.PlatformActionOn(node, action, service)`
(`app/app_platform_services.go:229`), which performs compose actions
(`up`/`stop`/`restart`) and cannot run arbitrary shell on a node.

Remote checks therefore derive from existing read APIs —
`ListPlatformServices()` returns `State`, `Status`, `Health`, `Addr`,
`WebURL` per service — plus direct HTTP probes from the app. Adding a generic
remote-exec endpoint was rejected: it is a cross-repo change that buys a
handful of extra checks and creates a permanent new attack surface reachable
from a desktop app.

The executor therefore exposes three capabilities — local shell, platform
read, HTTP probe — and no remote shell.

## Architecture

```
app/doctor/
  result.go        State, Remedy, Result
  registry.go      Check interface, Register()
  checks_local.go     Shell() checks
  checks_platform.go  PlatformServices() + Probe() checks
app/app_doctor.go  RunDoctor(scope) / ApplyRemedy(id) bindings
app/frontend/src/lib/panels/DoctorPanel.svelte
```

```go
type State string // "pass" | "warn" | "fail" | "unknown"

type Remedy struct {
    Command  string // always shown, always copyable
    AutoSafe bool   // idempotent AND non-destructive AND needs no secret
    Confirm  string // one-line "this will…", shown before an auto-run
}

type Result struct {
    ID     string
    Title  string
    Scope  string // "local" | "platform"
    State  State
    Detail string // the observed truth
    Remedy *Remedy
    Docs   string
}

type Check interface {
    ID() string
    Scope() Scope
    Run(context.Context, Executor) Result
}

// One interface, three capabilities. A check uses only what it needs, and
// tests supply a single fake. A command-shaped Executor was rejected: the
// platform and probe executors are not shell, so forcing them through a
// cmd []string signature would be a lie in the type.
type Executor interface {
    Shell(ctx context.Context, cmd []string) (stdout string, code int, err error)
    PlatformServices(ctx context.Context) ([]PlatformService, error)
    Probe(ctx context.Context, url string) (status int, err error)
}
```

Three invariants:

- **`unknown` is a first-class state.** A check may return `pass` only when it
  positively observed the good state. A probe that times out returns `unknown`
  with the reason, never `pass` and never `fail`.
- **`Run` never mutates.** Diagnosis is idempotent and safe to repeat.
  Mutation has exactly one entry point, `ApplyRemedy`, which re-resolves the
  check and acts only if `AutoSafe` and the master switch is on.
- **The executor is injected.** Checks never shell out directly, so every
  check is testable against canned output with no real infrastructure.

`ApplyRemedy` returns the **re-run** result, so the UI shows post-fix truth
rather than flipping optimistically green.

## Check catalog

### Local (`Shell`)

| Check | Observes | Remedy | Auto |
|---|---|---|---|
| `docker-symlink` | `/usr/local/bin/docker` resolves (GUI/launchd PATH) | recreate symlink | yes |
| `github-token-shadow` | `GITHUB_TOKEN`/`GH_TOKEN` set while `gh auth status` shows an idle keyring account | unset the env var | no (shell-scoped) |
| `direnv-approval` | each profile's `.envrc` is approved | `direnv allow` | yes |
| `app-db` | `~/.phantom-ink.db` exists, non-zero, migrations at head | run migrations | yes |
| `port-shadow-9910` | a host launchd router holds `:9910` ahead of the platform | unload launchd job | no (destructive) |
| `gateway-mcp-pin` | gateway venv still pins `mcp<2` | re-pin | no (needs rebuild) |

`app-db` fails on the current machine: the file is 0 bytes and has no `tasks`
table.

### Platform (`PlatformServices` + `Probe`)

| Check | Observes | Remedy | Auto |
|---|---|---|---|
| `traefik-bound` | Traefik running **and** its `Addr` answers; running-but-unreachable is the orphan-nginx signature | identify orphan | no |
| `service-health` | any service `Health == "unhealthy"`, honoring `OneShot` | restart service | yes |
| `registry-auth` | registry probe distinguishes **401** from **404** | set `REGISTRY_PASSWORD` | no (secret) |
| `vault-reachability` | all five brain vaults answer with their bearer | — | no |
| `sor-migrations` | each profile's SoR is at head; the daemon does not auto-apply | `pbrainctl server db migrate <profile>` | no (ordering) |
| `profile-api-key` | `gsa`/`lakeview` `CL_API_KEY` is still a placeholder | set a real key | no (secret) |

Four of twelve are auto-safe. That ratio falls out of the rule, not taste.

## UI

`DoctorPanel.svelte`, registered in the `system` section of `panels.ts`, no
keyboard shortcut. Reuses `Badge.svelte` and `CardExpander.svelte`; adds no
new primitives.

Two collapsible cards (Local, Platform), each with a header summary
(`Platform · 1 fail · 2 warn · 1 unknown`). One **Run checks** button. No
polling and no auto-run on open — opening the panel must not fire probes at
the platform.

Rows sort `fail → unknown → warn → pass`.

| State | Badge variant | Reads as |
|---|---|---|
| `fail` | `error` | observed broken |
| `warn` | `warning` | observed degraded |
| `unknown` | muted/default | could not determine |
| `pass` | `running`/`active` | observed good |

`unknown` takes muted grey, not yellow: an absence of evidence must not look
like a mild failure. Every `unknown` row states why it could not tell.

### Remediation gates

All three must pass before anything mutates:

1. A master **Allow fixes** switch in the panel header. In-memory only and
   never persisted, so it is off on every app launch and cannot be left on
   by accident. While off, `Fix` buttons are not rendered at all.
2. The remedy is `AutoSafe`. Non-auto-safe remedies always render a
   copy-command button instead, regardless of the switch.
3. A per-fix confirm showing `Remedy.Confirm` verbatim. Every time; no
   "don't ask again".

Remediation is scoped to the single check. Doctor never converges adjacent
drift it was not asked about.

## Testing

- `app/doctor/*_test.go` — a table test per check against a `fakeExecutor`.
  Required negative cases: a 401 maps to `fail` with auth detail, never
  `pass`; a timeout maps to `unknown`, never `fail`; a `OneShot` service at
  `exited (0)` maps to `pass`.
- `registry_test.go` — no two checks share an ID; every `AutoSafe` remedy has
  a non-empty `Confirm`.
- `cd app && go test ./... -race`; `cd app/frontend && npm run check`.

No migration and no contract change, so contract codegen and the event-bus
conformance test are unaffected.

## Out of scope

- **Agent work-tree visualization.** Blocked on data, not design:
  `parent_task_id` is declared in `app/db_tasks.go:24` and the
  `brainbox/loops_runtime.go` wire types, but nothing writes a non-empty
  value. Real fan-out grouping is `job_id` — a flat sibling set, one level
  deep. Emitting true task parentage is a router-side prerequisite and its
  own piece of work.
- Generic remote exec (rejected above).
- Tri-state health for the existing Services/Gateway badges. Doctor
  establishes the state model; retrofitting those badges is a follow-up.
