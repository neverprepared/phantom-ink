# ADR-007: A logging convention for our own services

- **Status**: Accepted
- **Date**: 2026-10-19
- **Relates to**: ADR-006 (Drive inbox, the first adopter)

## Context

The platform already has a working log store and a viewer, and almost nothing of
ours in it.

**What exists.** OpenSearch runs on the platform node, resolved from the router
base URL (`app/app_opensearch.go:22`), holding `logs-otel-YYYY.MM.DD` daily
indices — append-only, date-partitioned, searchable, weeks deep, around 18,000
documents a day. The Stream panel has a `logs` tab backed by it
(`StreamPanel.svelte:57`), gated on reachability so it hides itself when the
cluster is unavailable. That is a CloudWatch-shaped logging system, already
running.

**What is missing.** On a representative day, **100% of those documents came
from `service=claude-code`** — Claude Code's own telemetry. Not one record from
the router, brainbox, the platform, or any job we run. The logs describe the
tool, not the system. That is why they read as useless to their owner: they are
somebody else's logs.

Two further facts shape the convention, both learned the hard way while fixing
the viewer (see *What this corrects*):

**Severity is absent.** `severityText` is empty on every one of those 18,000
documents. There is no level to filter on, so an error cannot be separated from
routine chatter.

**Dynamic mapping types fields inconsistently.** These indices have no explicit
field mappings for attributes, so whichever document first carried a field in a
given daily index decides its type. In practice the *same attribute name
arrives as a different JSON type depending on the event*:

| Attribute | `tool_result` | `api_request` | hook events |
|---|---|---|---|
| `duration_ms` | string | number | — |
| `total_duration_ms` | — | — | string |
| `success` | string `"false"` | — | — |
| `num_*` | — | — | string |

`app/opensearch/client.go` already documents a symptom of this for the latency
aggregation: an `avg` over `log.attributes.duration_ms` throws
`illegal_argument_exception` on shards where it mapped as text, and OpenSearch
answers that partial failure with HTTP 200 and a truncated count.

## Decision

A convention for **records we emit**. It does not attempt to change
claude-code's telemetry, which is not ours.

### The envelope every record carries

| Field | Example | Why |
|---|---|---|
| `time` | ISO-8601 | — |
| `severityText` | `INFO` / `WARN` / `ERROR` | the filter that currently cannot exist |
| `body` | `drain: spooled 3, rejected 1` | one human sentence, readable with no tooling |
| `resource.attributes.service.name` | `phantom-inbox` | separates our records from claude-code's |
| `resource.attributes.workspace` | `personal` | the profile; `TailLogs` filters on it |

### Event identity and correlation

Mapping CloudWatch's model onto this store: **`event.name` is the log group,
`run.id` is the log stream.**

| Field | Example | Why |
|---|---|---|
| `event.name` | `inbox.drain.run` | dotted and hierarchical, so prefixes group |
| `event.outcome` | `ok` / `partial` / `error` | the one thing a scanner reads first |
| `run.id` | ULID, one per run | ties every record of a single run together |
| `duration_ms` | number | how long it took |

Correlate to other systems with the ids those systems already use —
`session.id`, `item.id`, `task.id` — rather than inventing new ones.

### Rules

**1. Emit numbers as JSON numbers, and be consistent per field.** This is the
rule the existing data breaks. A field that is sometimes `38` and sometimes
`"38"` cannot be aggregated, sorted, or reliably read, and the failure is
silent: a reader that only handles the numeric form sees `0`, which renders as
*absent* rather than as *wrong*.

**2. Always set `severityText`.** An unset level is indistinguishable from
`INFO` and makes the whole corpus unfilterable.

**3. A record must answer "what happened, and does it need me?" on its own.** If
it cannot, it is noise — either enrich it or do not emit it. A record that needs
a second lookup to be meaningful has failed.

**4. Never log content or credentials.** Message bodies, file contents, tokens.
Claude Code redacts `prompt` and `response` to `<REDACTED>` and keeps only
lengths; follow that. Content belongs in a store with an access model, not in a
log tail. Identity attributes (`user@*`, `organization@*`) are likewise
excluded by the reader and should not be emitted.

**5. Counts over events.** Prefer one record saying `spooled 3, rejected 1` to
four records. Volume buries signal: the two `hook_execution_*` events are 44% of
daily volume, and the one `api_error` of the day sits underneath them.

### Field naming, and how it lands

OTEL attributes are flattened by the ingest pipeline with `.` replaced by `@`
inside the attribute name, and prefixed by scope. So `event.name` is stored and
queried as:

```
log.attributes.event@name            # the field
log.attributes.event@name.keyword    # what you aggregate, sort, and term-query on
```

Aggregating or sorting on the bare field fails with *"Text fields are not
optimised for operations that require per-document field data"*. Always use the
`.keyword` subfield. Note `body` has **no** keyword subfield, which is the
reason `event.name` exists as a separate attribute rather than relying on
parsing `body`.

### Readers coerce; writers are strict

Our emitters follow rule 1. Our readers must still tolerate the existing mess,
because claude-code's records are not ours to fix and the historical indices are
already typed. `app/opensearch/client.go` coerces numerics, and
`logSummary.ts` coerces both numerics and booleans — comparing `success` with
`=== false` silently missed the string `"false"`.

## What this corrects

An earlier reading of this problem, recorded here so it is not repeated:

- **"The logs lack structure."** False. The attributes are rich and already
  carry `.keyword` subfields. The viewer was projecting six fields and
  discarding the rest.
- **"Hook events carry no attributes."** False, and the opposite of useful: they
  carry sixteen, including `hook_name` and the `num_*` counts. The mistake was
  reading `log.attributes` as a nested object when the ingest pipeline stores
  **flattened dotted keys**.
- **"Drop the noisy hook events at ingest."** Proposed and withdrawn. Those
  events answer "what did the hook do?" completely once projected. Discarding
  data at write time to compensate for a reader bug would have been
  irreversible.

The lesson generalises: **verify against the store before concluding anything
about its contents**, and prefer a reader fix to a writer fix when the data is
already good.

## Consequences

**Good.** No new infrastructure: the store, the index template, the viewer and
the panel all exist. A service adopting this convention becomes visible
immediately, filterable by severity, and groupable by event name. `run.id` gives
the per-run grouping a log stream provides.

**Costs and open edges.**

- **Volume and cost.** More services shipping means more documents. There is no
  retention policy documented beyond what ISM is doing by default, and no
  severity-based sampling. Worth revisiting before a chatty service adopts this.
- **The dynamic-mapping trap stays open for new fields.** Rule 1 avoids it for
  disciplined emitters, but nothing enforces it. An explicit index template
  mapping our common fields (`event@name`, `event@outcome`, `run@id`,
  `duration_ms`, `service@name`) would make it structural rather than cultural.
  Not done here: it changes ingest for every service and deserves its own
  change.
- **A log is not an attention feed.** Three surfaces, three jobs, and they
  should not be conflated: **logs** are history, the **attention/bus feed** is
  "act on this" (an entry reaches the bus only with a non-empty `actions[]` —
  `app/app_envelope.go:74`), and an upserted **status row** is current state.
- **Content stays out**, so answering "what was actually said" means joining to
  the session transcript by `session.id`. That join is not built.
- **Adoption is the whole value.** This ADR changes nothing on its own. Until a
  second service emits, the Logs tab remains a view of claude-code.

## Alternatives rejected

- **Drop noisy events at ingest.** Withdrawn once the data proved good; see
  *What this corrects*. Volume is a retention and sampling question, not a
  reason to destroy records at write time.
- **Invent a new log store or a `job_runs` table.** The substrate exists and is
  append-only, searchable and retained. A second one would split history.
- **Log message content.** Rejected on rule 4: it ships prompt and file text to
  a store with no access model, no severity and no retention policy, when the
  transcripts already hold it locally.
- **Put display strings in the emitter.** Rejected. Emitters write structured
  fields; rendering belongs to the reader, where it can change without a
  redeploy.
