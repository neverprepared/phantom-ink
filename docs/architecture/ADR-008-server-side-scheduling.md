# ADR-008: Scheduling belongs to the platform, management stays in the app

- **Status**: Accepted
- **Date**: 2026-11-04
- **Supersedes / relates to**: supersedes ADR-006's "the schedule lives in the
  app" decision; completes ADR-001 (the host stops being a parallel
  orchestration runtime); applies ADR-000 P5 (bus + rules reactive fabric) and
  P7 (resist building a workflow engine speculatively)

## Context

The collect scheduler in `app/collect.go` was the last recurring scheduler in
the platform: a 30-second goroutine started from `app.go`, scanning
`collect_jobs` in `~/.phantom-ink.db`. Four consequences, all observed rather
than theorised:

- **It died with the app.** Scheduled capture stopped whenever the laptop slept
  or the app restarted; ADR-006 recorded both inbox jobs ~10 minutes overdue
  after a `wails dev` rebuild.
- **Two app instances could both fire the same job.** There was no shared
  claim, so the only thing preventing a double run was not opening the app
  twice.
- **A missed window was silently skipped.** Due-ness was derived from
  `last_run_at` plus cadence columns, and a same-`YearDay` guard meant a day
  missed was a day forgotten, with nothing recording that it had happened.
- **A failing job retried on its normal cadence forever.** `markCollectJobRun`
  wrote `last_run_at` on success *and* failure, with no attempt counter and no
  backoff.

Nothing server-side could trigger on time. The rules engine in phantom-events
matched event patterns only — `event_match.py`'s operators are
`prefix/suffix/exists/anything-but/numeric`, with no time trigger and no time
arithmetic. phantom-brain carried River v0.39 but never used its `PeriodicJobs`
facility. So this was net-new capability, not a relocation.

ADR-001 had already decided the host "stops being a parallel orchestration
runtime"; the app-local automation and sequences engines were retired at that
point and collect was the straggler.

### Why ADR-006's rejection does not hold

ADR-006 considered moving the schedule out and rejected it:

> A per-profile LaunchAgent owning the schedule … with the app reduced to a
> status viewer was considered and rejected: it would take the jobs out of the
> Collectors panel and with them the per-job play button.

That objection was about a **UI affordance**, not about where execution belongs
— and it was already answered elsewhere in this repo. `AutomationsPanel.svelte`
is a thin shell over server-side `/api/rules`: CRUD, enable/disable and inline
validation errors against state the app does not own. The Collectors panel can
be the same thing. Management stays in the app console; only execution moves.

The LaunchAgent shape ADR-006 evaluated was also the wrong one. A per-profile
timer on the laptop fixes "survives the app" but not "one authoritative
schedule", not retries, and not visibility — and it would have put the schedule
somewhere the app genuinely could not show it.

## Decision

**Schedules are a first-class record in phantom-events, firing through the
automation rails that service already owns. The app manages them and does not
execute them.**

1. **A sibling table, not a column on `event_rules`.** A rule fires on a
   pattern; a schedule fires on a clock. One record serving both would make
   `pattern` nullable with an exactly-one-of invariant to police. The `actions`
   payload is the same union, which is what lets the executions ledger be
   shared.
2. **Firing goes through the normal write path**, the way `reaper.py` closes
   orphans: a synthetic `schedule.fired` envelope is ingested and one execution
   row per action is queued against it. The fire is therefore auditable and
   replayable like any other event, every existing read surface shows scheduled
   runs with no new code, and action templating — which renders against the
   event — keeps working.
3. **`next_run_at` is materialized, not derived.** That makes a missed window
   *visible* instead of silent, turns the due scan into an index lookup, and
   doubles as the optimistic-concurrency token: the advance is a
   compare-and-swap on it, so no row lock is held across the cadence
   computation and two schedulers cannot fire one slot twice.
4. **Fire-then-advance ordering.** A crash mid-fire repeats rather than drops,
   because losing a scheduled run is worse than repeating an idempotent one.
   The envelope id `sched:<id>/<fire_at_ms>` makes the slot the dedupe key, so
   the repeat collapses on `UNIQUE (rule_id, event_seq, action_index)`.
5. **Missed windows are an explicit `catch_up` policy** (`skip` | `run_once`),
   replacing the accidental same-day guard.
6. **Cadence resolves in the schedule's own timezone** via `croniter` — a
   borrowed library, not a borrowed service. Epoch arithmetic gets DST wrong by
   an hour twice a year.
7. **The local scheduler keeps dashboard-widget jobs only.** A widget polling a
   metric while the app is open is not an infrastructure concern, and nothing
   needs it to fire when the app is closed. `collectSchedulerOwns` is the single
   place that partition is decided.
8. **A new `run_profile_command` action** closes the one gap that stopped a
   collector running anywhere but the user's Mac: `run_script` ran arbitrary
   argv but on the API host with the *router's* environment, while a workspace
   profile's environment lives in the credentials broker. It runs in a
   short-lived, always-hardened container from the profile image, with env from
   the broker.

### Rejected alternatives

- **An off-the-shelf scheduler** (Dkron, Cronicle, k8s CronJob). What they sell
  is a clock, and the clock is the cheap part — the due-scanner is ~150 lines.
  They provide none of what matters here: profile-scoped records visible in the
  app console, actions in the existing union, an executions ledger with retry
  and DLQ, fires as bus envelopes, env from the broker. Bridging one needs a
  shim that calls the router anyway, leaving a second deployable and — fatally
  — a second source of truth for what is scheduled.
- **launchd/systemd timers curling the router.** Real precedent in this
  platform (`pbrainctl mart`'s LaunchAgent). Satisfies "survives the app"
  cheaply; fails "one authoritative store" and "observable", and makes the
  schedule invisible to the app console, which is the thing that must stay.
- **A standalone scheduler service.** Honest separation, but a new deployable,
  DB and CI surface, against ADR-000 P7 and ADR-003's "thin mechanism and
  contract, not an orchestrator". Revisit if scheduling grows past firing
  actions.
- **River's `PeriodicJobs`.** The closest "we already own it", but it lives in
  phantom-brain while schedules belong beside the actions/executions ledger in
  phantom-events; using it would split automation across two services.

## Consequences

**Gained.** Schedules fire with the laptop closed. One authoritative store, with
replica-safe claiming. Retry, backoff and a dead-letter queue, inherited rather
than rebuilt. Real cron, which the local scheduler never had. A visible overdue
state. Scheduling logic ships independently of the signed desktop binary. The
60-second collect-job cap is gone — that cap was the forcing function behind
ADR-006 splitting one logical collector into two scheduled jobs per profile, so
that workaround can be undone.

**Costs and risks.**

- **A second failure domain for capture.** A scheduled collector now needs the
  bus, the router and the credentials broker. The mitigation is that a broker
  that cannot answer is a hard failure rather than a credential-less run: a
  collector with no credentials usually exits 0 and emits quietly wrong output,
  which is worse than failing loudly.
- **Reusing the executions ledger was not free.** `_run_execution` resolves an
  execution's `rule_id` to the actions it must run, and a schedule id is not a
  rule id — so every scheduled execution initially dead-lettered as "rule,
  event, or action no longer available", with 122 unit tests passing because
  they asserted executions were *queued* and stopped at the ledger boundary. A
  fallback resolver fixed it. The transferable lesson: when reusing another
  subsystem's table, assert the work **executes**, not that a row was written.
- **`run_profile_command` is privileged** — a container holding a profile's real
  credentials. It has its own gate (`CL_RULES__ALLOW_PROFILE_COMMAND`),
  deliberately not a reuse of `allow_run_script`: that would force an operator
  to enable arbitrary host-exec in order to use the strictly more contained
  action.
- **Legacy local jobs go inert** the moment the local scheduler becomes
  widget-only. `ImportLocalSchedules` carries them over — idempotently, without
  deleting the originals — and the Collectors panel counts and surfaces any
  that have not been carried yet, so none can go quiet unannounced.
- **Runner-target jobs cannot be imported automatically.** They ran
  `claude -p <prompt>` locally; the server equivalent (`submit_task`) needs an
  agent identity the local row never stored. They are reported for the operator
  to recreate from the Todos panel, where the agent is chosen, rather than
  guessed at.
- **The planned timeline shows one future fire per server schedule**, because
  the server materializes a single `next_run_at`. Expanding further would mean
  reimplementing cron and DST client-side — the duplication this ADR exists to
  avoid — and the extra rows would be guesses.
- **Neither server repo has a full CI gate for this.** phantom-events has no
  workflow at all; phantom-router's test job is skipped unless `CI_REPO_TOKEN`
  is set. Verification is local until that secret lands.

## Revision to ADR-006

ADR-006's bullet "**The schedule lives in the app, and that is accepted rather
than solved**" is superseded. The inbox's schedule is now a server schedule like
any other; the inbox is a background service after all, and its two-jobs-per-
profile split (a workaround for the 60-second cap) is no longer necessary.
