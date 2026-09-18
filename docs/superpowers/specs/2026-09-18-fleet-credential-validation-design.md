# Fleet Credential Validation — `doctor --fleet` (Design Spec)

**Date:** 2026-09-18
**Status:** Implemented (v2). Scope below is the shipped surface.
**Revised:** 2026-09-18 — v2 extends coverage from `GITHUB_TOKEN` alone to the
brain vault tokens, the router key, and the AWS/Azure CLIs, batched into one
ephemeral session. gcloud is deferred.
**Revised:** 2026-09-18 — v2.1, from the first live `--fleet` run. `CL_API_KEY`
is dropped from the probe registry (it is the operator hub key and is
deliberately absent from a session); the brain-token fix hints point at the
brain binding rather than the gateway env store; and only `delivery-broken`
fails the run — `fix-locally-first` is reported and counted on its own line.
See §7.
**Surface:** `shell-profiler doctor <profile> --fleet [--runner <name>] [--json]`

## 1. Motivation

`shell-profiler doctor` answers one question well: *does this profile work
here?* Since #345 and #346 it answers it functionally rather than structurally —
`GITHUB_TOKEN` is not merely present, GitHub actually accepts it, resolved from
the profile file or from the live direnv environment.

That is necessary and not sufficient. **Agents do not run here.** They run in
containers on fleet runners, and the credential a container gets is whatever the
broker's env store delivers for that profile — a *separate copy*, curated
through the gateway env editor and provisioned into the profile's broker
namespace. Two copies of a secret with independent update paths drift. The
failure mode is specific and expensive:

> The local check is green. The operator rotates the PAT locally, or curates a
> value into the gateway env store with a typo, or never adds it at all. Every
> local signal says the profile is healthy. Three worker-runs later a private
> clone 401s inside a container on `m3-64`, and the symptom surfaces as "the
> agent is broken" rather than "the delivered token is stale".

Nothing in the system currently detects that divergence. A local check cannot:
it is measuring the wrong copy.

## 2. The method — an oracle/delivery differential

The design is a **differential**, and the choice of oracle is what makes it
sound.

| | question | copy under test |
|---|---|---|
| **Oracle** (local) | is the credential itself good? | the local/effective value |
| **Delivery** (remote) | did *that* credential survive the trip? | the broker-delivered value |

The local functional check is the **oracle**. It establishes independently that
the credential is good. Only then is the same credential exercised remotely,
through the existing delivery path, by a thin probe.

This ordering is load-bearing in both directions:

- **Local first, or a remote failure is ambiguous.** Without a known-good
  baseline, a remote 401 could mean the token is expired *or* that delivery is
  broken. Those have opposite fixes. With the oracle green, a remote 401 has
  exactly one reading: **the delivered copy is not the copy that works.**
- **Local fail short-circuits.** If the oracle is not `ok`, no session is
  created at all. Standing up a container to confirm that an already-broken
  credential is also broken costs a runner slot and tells the user nothing they
  don't know. The row reads `fix locally first`.

### Classification matrix

| local oracle | remote probe | verdict | reading |
|---|---|---|---|
| `ok` | HTTP 200 | `pass` | delivered and accepted on `<runner>` |
| `ok` | HTTP 401 | `delivery-broken` | **stale value** — the broker's copy is wrong |
| `ok` | token unset in container | `delivery-broken` | **not delivered** — nothing reached the container |
| `ok` | 000 / 403 / 5xx / exec error | `inconclusive` | no verdict; not a finding |
| `fail` | *(not run)* | `fix-locally-first` | the credential itself is broken |
| `skip` | *(not run)* | `inconclusive` | no baseline could be established |

The matrix is written with the GitHub probe's HTTP codes; the CLI-backed
probes map onto the same rows via their markers — `SPFLEET:OK` where `200` sits,
`SPFLEET:FAIL` where `401` sits, `SPFLEET:UNSET` unchanged, and no recognisable
marker where `000` sits.

Only `delivery-broken` exits non-zero (v2.1). `fix-locally-first` and
`inconclusive` never do: a local-credential fault is a different axis — the
remote probe was never run, so the run measured nothing about delivery — and an
offline node is not the user's to fix. Both are still reported, with their fix
hint, and the local-first rows get their own summary line ("M credential(s)
need a local fix first"). See §7.1.

**"Not delivered" is separated from "rejected" deliberately.** An absent token
would otherwise degrade into an unauthenticated request and return 401, reading
as a rejection — pointing the operator at rotating a token when the real fault
is that nothing was ever curated. Different bugs, different fixes, so the probe
tests for the unset case *before* making any request.

## 3. Composition — existing infrastructure only

**No new server endpoint. No new work-kind. Nothing baked into the container
image. No image change.** The remote side is a one-line shell probe over the
session exec path that already exists.

Every fleet operation is one of four calls, all verified in the code before
implementation (Phase 0):

| operation | endpoint | verified at | prouterctl equivalent |
|---|---|---|---|
| list runners | `GET /api/runners` | `brainbox/src/brainbox/api.py` | `prouterctl runners` |
| create session | `POST /api/create` | `api.py` `api_create_session` | `prouterctl session-create` |
| exec | `POST /api/sessions/{name}/exec` | `api.py` `api_exec_session` | — (Go precedent: `app/brainbox/sessions.go` `ExecSession`) |
| delete session | `POST /api/delete` | `api.py` | `prouterctl delete-session` |

Auth is the `X-API-Key` header (`brainbox/src/brainbox/auth.py`
`require_api_key`). Config comes from `CL_ROUTER_API` + `CL_API_KEY`, resolved
file-then-live exactly as the functional checks resolve credentials, so a
working `prouterctl` is a working `doctor --fleet`.

For a runner-hosted session, `api_exec_session` dispatches to the node via
`_dispatch_runner_op(name, "session.exec", …)`. That dispatch **is** the path
under test — the probe rides the same broker → runner → container route a real
agent's credentials take.

### Why `workspace_profile` is the whole check

`POST /api/create` takes `workspace_profile`, which selects the profile whose
credentials the broker delivers into the container (`lifecycle.py`
`_read_profile_vars` → the profile's `.env` / `.env.secrets` on the broker host,
plus the gateway env store). Creating the ephemeral session **for the profile
under test** is what makes the container hold the delivered copy. Without it the
probe would measure nothing.

### Why the probe must land on a *remote* runner

The `Local` runner (`host == "local-process"`) shares this machine's
environment. A delivery check that ran there would confirm the local copy a
second time and report a pass that proves nothing about the fleet. `--runner` is
honoured but still validated as remote; with no flag, the least-loaded remote
session-capable runner is auto-selected and **named in the report** — a verdict
that doesn't say which node it came from is not actionable.

## 4. Secrets hygiene

The existing rule — a token value never reaches a `Result`, a log, or an argv —
gains a second surface, because the probe runs **inside the container**.

The obvious probe is wrong: passing the header as a `curl -H` argument puts the
token in curl's `argv`, which is world-readable to any process in the container.
The shipped probe avoids that on both halves:

- The unset case is tested first with `[ -z "${GITHUB_TOKEN}" ]` and reports a
  distinct `SPFLEET:UNSET` marker.
- The header is built with `printf`, a shell **builtin** — expanding the token
  forks no process, so it never appears in `ps`.
- `curl` reads the header from **stdin** via `-H @-` — the token is not in
  curl's argv either.
- Only the status code crosses back, via
  `-o /dev/null -w 'SPFLEET:HTTP:%{http_code}'`. The response body is discarded.

See `githubTokenProbe` in `internal/doctor/fleet.go` for the exact command.

The CLI-backed probes (`statusProbe`, `cloudProbe`) hold the same line by a
different route: the tool reads its credential from the environment the broker
delivered, so the shell only ever expands the value into a `[ -z ]` test or an
`env KEY="${KEY}"` assignment — never an argv. Tool output is discarded
wholesale (`>/dev/null 2>&1`), because a tool's stdout can echo the very token
it was handed and only the marker is ever needed. Every probe is bounded by
`timeout 20`, so a container that cannot reach a service produces an
inconclusive verdict promptly rather than holding the ephemeral session — and
with it a live copy of the credentials — open.

On the local side: the exec command string is built from the env var name and
never carries a literal value; every `Detail` and `Fix` passes through
`Profile.Redact` (matching `RunChecks`), so even a container that echoed the
token back cannot leak it into the report; and container output is never quoted
verbatim into a row.

**Teardown is unconditional.** The ephemeral session is deleted via `defer` on
every path — exec error, unparseable output, and a create that failed after
provisioning started. A leaked session holds a runner slot and, far worse, a
live copy of the profile's credentials on a remote node. Session names are
random (`doctor-credcheck-<8 hex>`) rather than derived from the profile, so two
concurrent runs cannot collide and delete each other's session.

**`/rate_limit`, not `/user`**, matching the local oracle: it returns 200 for
*any* valid credential — classic and fine-grained PATs and App installation
tokens — where `/user` 403s for installation tokens. Probing a different
endpoint than the oracle would make the two sides non-comparable, and the
comparison is the entire point.

## 5. Scope

**In (v2): eight credentials, one session.**

| credential | local oracle | in-container probe |
|---|---|---|
| `GITHUB_TOKEN` | `github token` | `printf \| curl -H @-` against `/rate_limit` |
| `CL_BRAIN_API_TOKEN` (memory vault) | `brain token (memory)` | `pbrainctl client recall --limit 1 doctor` |
| `CL_TODO_API_TOKEN` | `brain token (todo)` | same, token aliased into `CL_BRAIN_API_TOKEN` |
| `CL_SKILLS_API_TOKEN` | `brain token (skills)` | same |
| `CL_AGENTS_API_TOKEN` | `brain token (agents)` | same |
| AWS credentials | `aws` | `aws sts get-caller-identity` |
| Azure credentials | `azure` | `az account show` |

`remoteProbes()` remains a catalog of `{credential, localCheck, buildProbe,
classify, deliveryFix}`; the brain entries are generated from the same
`brainVaults` table the local checks use, so a vault added there cannot be
silently missed here. `FleetCoverageNote` is rendered with **every** report,
text and JSON, and now names exactly what was probed *and* what was not.

### The brain probes also test ENDPOINT delivery

`pbrainctl` resolves both `CL_BRAIN_API` and the vault token from the
environment. The local oracle injects the vault's token as
`CL_BRAIN_API_TOKEN` whichever vault it came from, and the probe aliases it the
same way — a probe reading a different key than the oracle would make the two
sides non-comparable, and the comparison is the point.

The consequence is deliberate: a `CL_BRAIN_API` pointing at a loopback address
that is alive on the workstation and dead inside a container passes locally and
fails remotely. That is a delivery fault, and it is exactly the class of bug
this mode exists to surface. The rejected detail names both possibilities —
refused credential *or* unreachable endpoint — so the fix is not mistaken for
"rotate the token".

### One session for all probes

v1 created an ephemeral session per credential. With eight credentials that
would pay the provisioning cost — up to a five-minute create timeout — once per
row, for a container that is **identical every time**: it is the *profile* that
decides what is delivered into it, not the credential being tested. The run is
therefore staged:

1. Run **every** local oracle first. Credentials that do not pass are recorded
   (`fix-locally-first` / `inconclusive`) and are not probed.
2. If at least one passed, create **one** ephemeral session on the target
   runner for the profile.
3. Run each surviving credential's probe via `Exec` in **that** session. A
   failing exec does not abort the batch — one probe tripping over a missing
   tool must not erase the answer for the others.
4. Tear the session down **once**, via `defer`, on every path including a
   create that failed after provisioning started.

Zero survivors means no session is created at all.

### Not delivered vs. rejected — a distinction the report must keep

The two failure modes both exit non-zero, but they send the operator to
different places, so they are never collapsed:

| verdict detail | meaning | fix hint |
|---|---|---|
| **not delivered** (`FleetUnset`) | nothing reached the container | provision it where that credential actually comes from |
| **rejected** (`FleetRejected`) | it arrived and was refused | the delivered value is stale/wrong — re-provision it |

*Where* differs per credential, and the hint must name the right place (v2.1):

| credential | delivered by | fix hint points at |
|---|---|---|
| `GITHUB_TOKEN` | the profile's gateway env store | the gateway env store |
| brain vault tokens | phantom-router's **brain binding** | the profile's vault binding, *not* the env store |
| AWS / Azure | nothing — they are local files | the unwired file-vs-env-var gap |

For the env-delivered credentials the unset case is carved out **before** any
work is done, with `[ -z "${KEY}" ]` reporting `SPFLEET:UNSET`. Without it an
absent credential degrades into a generic tool failure and reads as a
rejection, pointing the operator at rotating a token when nothing was ever
curated.

The cloud CLIs have no single env key to test, so their probe captures the
CLI's own failure text and matches it against the phrases each tool uses for
"there are no credentials here at all" — `Unable to locate credentials` for
AWS, `az login` for Azure. The text is **matched, never reported**: it is
untrusted container output and a CLI error can quote the argument that upset
it. A missing CLI (`command not found`) is matched separately and yields
`inconclusive`, because a tool that was never run is not a credential verdict.

**A not-delivered verdict for AWS/Azure is a legitimate finding, not a broken
probe.** Cloud credentials are local **files** (`~/.aws`, `~/.azure`) while the
broker delivers **env vars**, so nothing is currently wired to ship them to
fleet containers at all. The fix hint says exactly that and asks for a
decision, rather than naming a store entry that was never meant to exist.

### gcloud — deferred, and stated

`CL_API_KEY` is **not** probed either (v2.1). It is the *operator* hub key; a
session container authenticates to the hub with a hub-issued, session-scoped
`BRAINBOX_TOKEN` and deliberately never receives `CL_API_KEY` (it is not in the
app's `routerManagedVars`). Its absence in a container is correct by design, so
probing it produced a `delivery-broken` row for a non-fault. It remains the
config key `--fleet` itself authenticates with; it is simply not a delivery
subject. `FleetCoverageNote` states the exclusion and its reason.

`gcloud` is **not** in the `docker/brainbox` container image. A probe would
report "not delivered" for every profile and say nothing about delivery.
Covering it needs a Dockerfile change plus a rebuild and redistribution of
`brainbox:latest` to every runner — a separate prereq — so no gcloud probe is
registered, and `FleetCoverageNote` states the exclusion so a clean run is not
misread as covering GCP.

**Out (deliberately):** `--all` (the local oracle reads the loaded environment,
which belongs to one profile — see below); parallel probes across runners;
persisting results; any remediation. The check reports and points at the fix; it
does not curate the gateway env store.

**Current-profile only.** Same constraint as the live-env oracle in #346. Run
against another profile, the differential would compare *this* profile's live
credential against *that* profile's delivered one and call the mismatch a
delivery bug. Naming a different profile is an error, not a silent
reinterpretation.

## 6. Testability

`FleetClient` is the seam — `ListRunners` / `CreateSession` / `Exec` /
`DeleteSession`, exactly the four operations a probe needs. **No test touches a
real fleet.** Every remote outcome is a canned reply on a fake, covering:

- the full classification matrix (local `{ok, fail, skip}` × remote
  `{200, 401, unset, 000, 403, exec-error}`);
- teardown fires on every path, including exec error, garbage output, and a
  failed create, and deletes the session that was created;
- the short-circuit creates and execs nothing when NO oracle is `ok`, and is
  applied **per credential** — one broken local credential must not suppress
  the delivery verdict for the ones that are fine;
- the batching contract: N locally-passing credentials produce exactly ONE
  `CreateSession` and ONE `DeleteSession`, every probe runs in that session,
  and teardown still fires on an exec error, garbage output, and a failed
  create;
- `classifyStatusProbe` over `{OK, UNSET, FAIL}` plus banners, missing markers
  and non-zero exits; `classifyCloudProbe` over the AWS/Azure not-delivered
  phrases, a refused-credential error, and a missing CLI;
- every `deliveryFix` distinguishes not-delivered from rejected; the cloud hints
  name the `~/.aws` / `~/.azure` file-vs-env-var gap; the brain hints name the
  brain binding and never the gateway env store; `GITHUB_TOKEN`'s keeps it;
- `CL_API_KEY` is absent from `remoteProbes()` and from the coverage note;
- the counting split: a report of {1 `delivery-broken`, 2 `fix-locally-first`,
  1 `inconclusive`} yields `DeliveryFailCount() == 1`, `LocalFirstCount() == 2`
  and a non-zero exit; the same report without the delivery-broken row exits
  **zero** while still rendering both local fix hints;
- every `localCheck` names a check that actually exists in `DefaultChecks()` —
  a renamed check would otherwise silently drop a credential to "no baseline";
- no probe command carries a credential VALUE, asserted against a profile
  holding a distinctive secret for all six env-delivered keys;
- the probe references the `GITHUB_TOKEN` env var, carries no literal token, and
  has the `printf | curl -H @-` shape — asserted on the builder *and* on the
  command string that actually reaches `Exec`;
- untrusted container output (banners, truncation, trailing junk, no marker)
  yields `inconclusive`, never a fabricated verdict;
- an echoed token is redacted out of the report.

An `httptest`-backed test pins the Phase-0 endpoint contract: the four paths,
the `X-API-Key` header, and `workspace_profile` in the create payload.

## 7. Files

| file | role |
|---|---|
| `internal/doctor/fleet.go` | types, probe, classification, differential, runner selection, config |
| `internal/doctor/fleet_http.go` | `FleetClient` over the existing orchestration API |
| `internal/doctor/fleet_format.go` | text + JSON report |
| `internal/commands/doctor_fleet.go` | `RunDoctorFleet` — profile resolution, wiring, exit code |
| `internal/cli/app.go` | `--fleet` / `--runner` parsing and help |

## 7. v2.1 — corrections from the first live run

Running `doctor --fleet` against a real profile exposed three defects in the
v2 multi-credential check. All three were the same mistake in different
clothes: the report asserting more than the probe had measured.

### 7.1 A local fault is not a delivery failure

AWS and Azure returned `fix-locally-first` — expired local credentials, so
correctly never probed remotely. The summary and the exit code counted them
alongside real `delivery-broken` rows, so a run reported "3 credential(s)
failed the delivery check" and exited non-zero having measured delivery for
exactly one of them.

`FleetResult.Failed()` is therefore split: `DeliveryFailed()` (delivery-broken
only) drives `FleetReport.Failed()`, `DeliveryFailCount()` and the exit code;
`NeedsAction()` (delivery-broken **or** fix-locally-first) drives only whether
a row renders its fix hint. `LocalFirstCount()` feeds a separate summary line.

### 7.2 `CL_API_KEY` was a false positive

Reported `delivery-broken` because it is unset in the container — which is the
designed behaviour, not a fault. Removed from `remoteProbes()` entirely, along
with `routerProbe()`; probing whether the operator key reaches a session is a
non-goal, and a PASS row for a credential that is *supposed* to be absent would
still imply the question was worth asking.

### 7.3 The brain tokens' fix hint named the wrong place

`envDeliveryFix` sent the operator to "the profile's gateway env store" for
every env-delivered credential. True for `GITHUB_TOKEN`; wrong for the vault
tokens, which phantom-router's brain binding provisions and forwards — that
store holds a couple of keys and never held these. `brainDeliveryFix(tokenKey,
label)` replaces it for those rows and names the binding and the vault;
`envDeliveryFix` is gone, `GITHUB_TOKEN` keeping its own (correct) hint.
