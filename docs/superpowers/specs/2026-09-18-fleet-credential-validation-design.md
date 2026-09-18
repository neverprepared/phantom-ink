# Fleet Credential Validation — `doctor --fleet` (Design Spec)

**Date:** 2026-09-18
**Status:** Implemented (v1). Scope below is the shipped surface.
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

`delivery-broken` and `fix-locally-first` exit non-zero. `inconclusive` never
does — the same rule a skipped check follows: an offline node is not the user's
to fix.

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

**In (v1): `GITHUB_TOKEN` only.**

`remoteProbes()` is a catalog of `{credential, localCheck, buildProbe, classify,
deliveryFix}`. Adding AWS or Azure is a new entry, not a new code path — but v1
ships one entry and says so. `FleetCoverageNote` is rendered with **every**
report, text and JSON, because a clean run proves one credential is delivered
and letting that read as full coverage would be the more dangerous outcome.

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
- the short-circuit creates and execs nothing when the oracle is not `ok`;
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
