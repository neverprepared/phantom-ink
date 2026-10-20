# Remove Langfuse

**Date:** 2026-10-20
**Status:** approved design, pending implementation plan
**Scope:** three repos — `phantom-ink`, `phantom-router`, `phantom-platform`.

## Why

Langfuse was deprecated by operator decision on 2026-10-19/20 in favour of
OpenSearch. That decision is settled and this document does not re-open it; it
completes it by deleting the code.

The decision's own test for re-opening was: *has OpenSearch lost its content
flags, or has a tree view become load-bearing?* Neither has happened.
`OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES` and
`OTEL_LOG_TOOL_DETAILS` are on, so `logs-otel` carries full prompt and
assistant text, and `opensearch.AgentSession` is a strict superset of the
Langfuse row it replaced.

What the removal costs, stated plainly: the parent/child **observation tree**.
OpenSearch gives flat events grouped by `turn_id`. Nothing else is lost —
moving traces to Data Prepper actually *gains* tool output, which Langfuse
discards, and Langfuse's cost figures were understated by roughly half (813
tool calls / $309.05 against OpenSearch's 1786 / $416.26 for the same session)
because its Claude Code capture is a Stop hook that misses delegated subagent
work.

## Scope, as directed

Three operator directives shape this beyond the Langfuse removal itself:

1. **`reflex/` goes away entirely** — not merely its Langfuse parts. It has
   been deprecated for a long time. See *Deleting reflex* below.
2. **Stale docs get updated**, including ones an earlier draft of this spec
   proposed leaving alone.
3. **IaC is out of scope for now.** So the Langfuse entries in
   `phantom-router/ansible/` (`inventory/group_vars/all.yml`,
   `roles/mcp-servers/tasks/python.yml`) and in
   `phantom-ink/brainbox/ansible/inventory/group_vars/all.yml` are
   **deliberately left in place**. `neverprepared-iac` is already clean
   (zero references), so nothing was pending there anyway.

Consequence worth stating: because the ansible trees are skipped, the final
`grep -rli langfuse` will legitimately still hit them. That is expected, not a
miss, and the verification step accounts for it.

## Ordering

`phantom-router` goes first, because an unmerged branch there touches the same
files the removal does.

### 1. Finish and merge `phantom-router` `feat/llm-langfuse-tracing`

The branch's name is stale relative to its own trajectory. Its four commits
run: add Langfuse tracing on the `complete()` seam → also export spans to
OpenSearch → fix the transport to gRPC (the only one Data Prepper accepts) →
**emit spans without Langfuse credentials**. That last commit already fixed
the backwards dependency, in its own words: *"OpenSearch is the system of
record here, so the dependency was backwards."*

So finishing it means: delete `src/phantom_router/llm/tracing.py` (the
instrumentation that goes through the Langfuse SDK) and leave `llm/otel.py`'s
OTel-native span path as the only one. Merge that, so the LLM-plane span
export survives the removal instead of being thrown away with it.

### 2. Strip Langfuse from `phantom-router` (14 of the 16 files on `main`)

Langfuse appears in 16 files on `main`; two of them are under `ansible/` and
are skipped per the IaC directive, leaving 14.

Code: `src/phantom_router/langfuse_client.py` (delete), the `/api/langfuse/*`
routes in `api.py`, the Langfuse MCP tools in `mcp_server.py`,
`LangfuseSettings` and its `_langfuse_*` factories in `config.py`, the
credential entries in `secrets.py`, and the Langfuse env injection in
`backends/configure.py`.

`backends/configure.py` is the one piece here that is **live behaviour, not
dead code** — it injects Langfuse env into session containers. Removing it
changes what a session gets, so it needs its own verification that sessions
still provision.

Dashboard: the Langfuse portions of `dashboard/src/lib/ObservabilityPanel.svelte`
and `dashboard/src/lib/api.js`.

IaC: **skipped** — `ansible/inventory/group_vars/all.yml` and
`ansible/roles/mcp-servers/tasks/python.yml` keep their Langfuse entries for
now, per directive. They are inert once the code that reads them is gone, but
they remain as the one deliberate loose end.

Tests: delete `tests/test_langfuse_api.py` and `tests/test_langfuse_client.py`.
Remove the Langfuse cases from `tests/test_executor.py`,
`tests/test_secrets.py`, `tests/test_trust_zones.py` and `tests/conftest.py`,
which must still pass.

Docs: `CLAUDE.md`.

### 3. Strip Langfuse from `phantom-ink`

Nearly all of this is already dead. The one thing to establish before cutting,
because it looks live and is not: `AgentWorkPanel.svelte:513` passes the string
`'GetAgentWorkTimeline'` to `withTimeout()` as a **label for error messaging**.
The actual call on that line is `api.GetAgentSessionTimeline(...)`, the
OpenSearch path. The frontend never invokes the Langfuse-backed method.

**Go — delete outright:**
- the `app/langfuse/` package (`client.go`, `metrics.go`, `sessions.go` and
  their two tests)
- four bound `*App` methods, every one orphaned from the frontend:
  `GetAgentWorkTimeline`, `GetLangfuseHealth`, `ListAgentWork`,
  `GetAgentWorkFreshness`
- their helpers `langfuseClient()` and `langfuseEnvironment()` in
  `app_agent_work.go`
- in `app/brainbox/observability.go`: the `Trace` type, `GetLangfuseHealth()`
  and `GetSessionTraces()`, all of which call `/api/langfuse/*` on an API that
  will no longer serve those routes
- the Langfuse Integrations entry in `app/services.go`, together with the long
  comment explaining why it is deliberately not `Platform:true`

**Go — tests.** `app/app_agent_work_test.go` carries 23 Langfuse references
and `app_agent_work_live_test.go` six. The rule is: a test of deleted code is
deleted with it; a test of surviving code keeps its coverage and loses only
the Langfuse case. Concretely, `TestLangfuseEnvironmentPrefersTheStampedValue`,
`…FallsBackToTheProfileName` and `…HandlesAnUnknownProfile` all test
`langfuseEnvironment()` and go with it. `TestMergeLivenessKeepsLangfuseTimestampWhenFresher`
tests `mergeLiveness`, so whether it goes depends on whether `mergeLiveness`
still has a caller once `ListAgentWork` is deleted — establish that before
cutting rather than assuming either way. The remaining tests in that file
(`TestStrictProfileHome*`, `TestOverlayTimeout*`, `TestChartsTimeout*`,
`TestAgentWorkChartsMarshalsWithoutNulls`) are OpenSearch-side and must
survive untouched.

Deleting bound methods changes the Wails binding surface, so
`app/frontend/wailsjs` must be regenerated and committed. A stale binding is
how a deleted method keeps appearing to exist.

**Frontend — delete:**
- `app/frontend/src/lib/components/SessionTree.svelte`. This is the
  parent/child tree view. It was deliberately left in the tree "for whoever
  takes that on" when traces moved to OpenSearch; with Langfuse gone it has no
  data source at all, so it goes rather than sitting dormant and misleading.

**Python (`brainbox/`) — delete:** `src/brainbox/langfuse_client.py`, the
`/api/langfuse/*` routes and `_langfuse_op` in `api.py`, the four Langfuse MCP
tools in `src/brainbox/mcp_server.py`, the settings in `config.py`, the
credential entries in `secrets.py`, the env injection in
`backends/configure.py`, and the Langfuse bits of `dashboard/src/lib/api.js`
and `dashboard/src/lib/ObservabilityPanel.svelte`.
`brainbox/ansible/inventory/group_vars/all.yml` is **skipped** per the IaC
directive.

Tests: delete `tests/test_langfuse_api.py` and `tests/test_langfuse_client.py`;
strip the Langfuse cases from `test_executor.py`, `test_secrets.py` and
`test_trust_zones.py`.

Also `packages/brainbox-mcp/src/brainbox_mcp/mcp_server.py` — the guest MCP
variant shipped into session containers.

**Deployment — two compose stacks, not one:**
- `docker/langfuse/` (`docker-compose.yml`, `.env.example`, `README.md`)
- `app/compose/langfuse/docker-compose.yml` — the copy the app itself manages

Plus the `reflex-langfuse` recipe in the root `Justfile`, and the Langfuse env
in `docker/brainbox/Dockerfile`, `Dockerfile.researcher`,
`hooks-requirements.txt`, and the three `setup/**/settings.json` files.

**`reflex/` is deleted wholesale** — see *Deleting reflex* below. Its Langfuse
command, hook scripts and `hooks.json` entries go with it, so they need no
separate treatment.

**Docs.** Per directive, stale docs get updated rather than left:

- `README.md`, `CLAUDE.md`, `docker/README.md`, `docs/brainbox/*.md`
  (`api-reference`, `dashboard`, `lifecycle`, `observability`, `README`) —
  drop Langfuse. `CLAUDE.md` additionally loses the `reflex/` row from its
  monorepo table, the two `just reflex-*` commands, the `test-reflex` mention
  in its CI notes, and all four Langfuse MCP tools from its tool list.
- `docs/profile-container-flow.md` — the most stale doc in the tree. It
  documents `/reflex:brainbox create` as *the* provisioning flow, including a
  mermaid subgraph labelled "Reflex Plugin". With reflex deleted that
  describes a path that no longer exists, so it is rewritten around the
  current flow, not patched.
- `docs/codex-integration.md` — drop the `reflex:codex-exec` section and the
  Langfuse references.
- `docs/persona_agentic/REFLEX/*.md` (4 files) — **deleted.** They are gap
  analyses and coverage reports of a subsystem being removed; there is nothing
  left for them to describe.
- `docs/architecture/ADR-002-mcp-gateway.md:64` — cites
  `reflex/plugins/reflex/mcp-catalog.json` as the catalog source. ADRs record
  a decision as it was taken, so the prose is **not** rewritten; a dated
  amendment note is appended recording that the catalog now lives at
  `phantom-platform/config/mcp-catalog.json` and that reflex was removed on
  2026-10-20. Editing the original text would misrepresent what was decided.
- `docs/IMPROVEMENTS_IMPLEMENTED_2026-02-14.md` — a dated as-built log.
  Treated the same way as the ADR: a header note marking it historical, body
  left intact. If it is simply dead weight, deleting it outright is also
  defensible — but silently rewriting a dated record is not.

Comment-only mentions in `app/opensearch/agentcharts.go`,
`app/app_mindwalk_test.go` and `app/opensearch/agentcharts_test.go` are
updated in place where they would otherwise read as live, and dropped where
they are pure history.

**`app/app_profiles.go` is not comment-only — it needs a decision.** Its
`wellKnownSecrets` list is live, feeds the bound `GetProfileSecrets()` and the
Profiles panel's secrets UI, and contains *exactly* the two Langfuse keys:

```go
var wellKnownSecrets = []string{
	"LANGFUSE_API_PUBLIC_KEY",
	"LANGFUSE_API_SECRET_KEY",
}
```

Removing them empties the list, so `GetProfileSecrets` returns nothing and that
UI renders an empty surface. There is no replacement to put in it: OpenSearch
runs unauthenticated here (`DISABLE_SECURITY_PLUGIN=true`), so no well-known
secret remains. The mechanism existed to serve Langfuse.

**Decision: remove the mechanism with it** — `wellKnownSecrets`,
`GetProfileSecrets()`, `SecretKeyStatus`, and the Profiles-panel surface that
renders them. A secrets list that lists nothing is worse than no list, and
leaving it means the next reader has to rediscover that it is vestigial. This
is a deliberate, visible UI removal and should be called out in the PR body
rather than slipped in as part of a deletion sweep. If a well-known secret
reappears later, the mechanism is eleven lines to restore.

### 4. `phantom-platform`

One stale comment at `docker-compose.yml:137`. No Langfuse service is deployed
from this repo and nothing else references it.

## Deleting reflex

Per directive, phantom-ink's `reflex/` subtree is removed entirely: 171 files,
3.8 MB, the Claude Code plugin marketplace. It has been deprecated for a long
time and no longer loads.

**Verified safe for the live MCP gateway.** `reflex/plugins/reflex/mcp-catalog.json`
looks load-bearing — `brainbox/gateway_catalog.py` and the router both describe
their catalog as "reflex's mcp-catalog.json", and ADR-002 says the gateway
reuses it. It is not. `catalog_path` defaults to `""` ("Empty = no downstream
servers") in both services, and the deployed stack mounts a *different* copy:
`phantom-platform/docker-compose.yml` sets
`CL_GATEWAY__CATALOG_PATH: /config/mcp-catalog.json` from
`./config/mcp-catalog.json`. That file exists in phantom-platform and its
contents differ from the reflex one, confirming the platform copy is the live
source. Deleting the subtree does not touch it.

This must be re-confirmed at implementation time rather than taken on trust
from this document, because it is the one way the deletion could break a
running system.

**What goes with the subtree:**
- `.github/workflows/test-reflex.yml` and `.github/workflows/release-reflex.yml`
- the `reflex-dev` and `reflex-langfuse` recipes in the root `Justfile`
- the `reflex/` row and `just reflex-*` entries in `CLAUDE.md`

**Consequence to state in the PR, not discover later:** `release-reflex.yml`
packages the plugin (`pkg-root/opt/reflex/share/reflex`), so deleting it ends
that release line. Anyone who installed the packaged plugin keeps what they
have and gets no further updates. That is the intent of deprecation, but it is
a published-artifact removal and belongs in the PR body.

**Left alone deliberately:** `shell-profiler/internal/commands/update.go:413`
sets `CODEX_PLUGIN_DIR` to `"${WORKSPACE_HOME:-$HOME}/.codex-plugins/reflex"`.
That is a path in the operator's *workspace*, not this repo's subtree, so
deleting `reflex/` does not break it and removing it is a separate decision
about profile scaffolding. Comment-level mentions in `app/preflight.go:96` and
`app/profileimage/builder.go` (which already says "Replaces the retired reflex
plugin skill tree") are updated in place.

## The rationale comments

`AgentWorkPanel.svelte` and `stores.svelte.ts` carry roughly twenty lines
recording why OpenSearch replaced Langfuse — freshness numbers, the cost
understatement, the lost tree. These are **condensed, not deleted**: two or
three lines saying OpenSearch is the system of record, pointing at this
document. The full reasoning lives here and in long-term memory, so the code
does not need to carry the blow-by-blow — but a future reader tempted to
re-add Langfuse should find a pointer, not silence.

## Trace data

`docker/langfuse/` runs six services (worker, web, clickhouse, minio, redis,
postgres) backed by three named volumes. Deleting the compose file would orphan
those volumes rather than remove them, leaving unreferenced state nobody owns.

The sequence is therefore: record the volume names, `pg_dump` the Langfuse
Postgres database to a file kept outside the repo, then `docker compose down -v`
to remove the volumes **explicitly**, and only then delete the compose
directory. Note the host/server Postgres major versions may differ, which
aborts a dump on a version check — point `pg_dump` at a matching binary if so.

This step runs against the platform node and is the only genuinely
irreversible part of the work, so it is the operator's to execute or approve,
not something the implementation should do unattended.

## Out of scope

Named explicitly so nobody sweeps them in:

- **All IaC**, per directive: `phantom-router/ansible/`,
  `phantom-ink/brainbox/ansible/`, and `neverprepared-iac` (already clean).
- `reflex` as a **standalone repo** (distinct from phantom-ink's `reflex/`
  subtree, which IS deleted here) — deprecated, last commit three months ago.
  Worth a follow-up now that the subtree is going.
- `maint` — a mirror of phantom-ink, not a live repo. Its 124 hits are copies.
- `phantom-ink-p2p`, `phantom-ink-pre-migration`, `ink-bunny` — snapshots and
  forks.
- `.claude/settings.local.json` — machine-local, not committed state.

## Shipping

Four PRs, not one, because the repos have separate CI and separate review
surfaces — and because step 1 is real engineering while the rest is mostly
mechanical deletion:

1. `phantom-router` — finish and merge `feat/llm-langfuse-tracing`.
2. `phantom-router` — the Langfuse removal, on top of (1).
3. `phantom-ink` — the Langfuse removal. Independent of (1) and (2); can run
   in parallel.
4. `phantom-ink` — **delete `reflex/`**, as its own PR. It is a different
   change from removing Langfuse: 171 files, two CI workflows, and the end of
   a published release line. Keeping it separate means review can judge it on
   its merits and a revert does not drag Langfuse back in. Order relative to
   (3) does not matter, but doing (3) first keeps its diff free of the 3.8 MB
   deletion.
5. `phantom-platform` — one comment; rides with whichever lands last.

Each must be independently green. The only hard sequence is (1) before (2),
since they touch the same files.

## Verification

Per repo, on the merged result:

- **phantom-ink:** `cd app && go test ./... -race`; `cd app/frontend && npm run check && npm test`. The contract codegen gate and `app_conformance_test.go` both still apply. `just app-build` must produce a working app, since deleting bound methods is exactly what a stale binding hides.
- **phantom-router:** `pytest` and `ruff check src/`. Plus one manual check that a session still provisions, because `backends/configure.py` changed live behaviour.
- **phantom-ink/brainbox:** `just bb-test` and `just bb-lint`.
- **reflex deletion:** before deleting, re-confirm the gateway catalog finding
  above — that no deployed service resolves `catalog_path` into the subtree.
  After deleting, `just` must still list every remaining recipe, and the two
  reflex workflows must be gone from `.github/workflows/`.
- **Across all three:** `grep -rli langfuse` returns **only** (a) the ansible
  trees, skipped by directive, and (b) the ADR and the dated as-built log,
  which keep their original prose plus an amendment note. Anything else is a
  miss. `grep -rli reflex` in phantom-ink returns only the deliberate
  leave-alones: `shell-profiler`'s `CODEX_PLUGIN_DIR`, the ADR-002 amendment,
  and `.claude/settings.local.json` (machine-local, uncommitted).

CI is path-filtered in phantom-ink, so the brainbox and app subtrees each
trigger their own workflow; a docs-only commit triggers none.
