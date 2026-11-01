# Session image slimming — design

**Date:** 2026-10-31
**Status:** approved, not yet implemented
**Scope:** `docker/brainbox/Dockerfile`, the gateway catalog, session volume mounts

**Which repo implements what — read this first.** The image and its wrapper live
in **phantom-ink** (`docker/brainbox/`). The catalog lives in
**phantom-platform** (`config/mcp-catalog.json`). The volume-mount wiring lives
in **phantom-router** (`src/phantom_router/lifecycle.py`) — *not* in
`phantom-ink/brainbox/`, which is a drifted fork that does not serve (see
phantom-ink#410). Editing the fork produces a change that passes CI, merges, and
affects nothing.

## Problem

The session base image (`registry.neverprepared.com/brainbox:latest`, built from
`docker/brainbox/Dockerfile`) has grown to **9.22 GB in a single layer** — one
`RUN` at `Dockerfile:154` that installs Homebrew, uv, nvm, goenv, rustup and a
set of global npm MCP servers.

Two consequences, one hard and one soft:

- **Hard:** the registry front door caps request bodies at 2 GB
  (`client_max_body_size 2000m` in the Nginx Proxy Manager fronting
  `registry.neverprepared.com` on storage.local), so *any* push of this image
  fails partway with `413 Request Entity Too Large`. Every image change now
  requires tunnelling past the proxy to the registry's `127.0.0.1:5000`.
  Profile images inherit the problem: blobs are stored per-repository, so a
  profile push re-uploads the same 9.22 GB blob and hits the same wall.
- **Soft:** the monolith is undifferentiated. Nothing records which concern owns
  which bytes, so the image only ever grows — which is how it reached 9.22 GB.

### Measured baseline

`/home/developer` totals 7.1 GB:

| component | size | detail |
|---|---|---|
| npm global MCP servers | ~4.8 GB | `@neverprepared` 3.9 GB (of which `@atlaskit` **3.4 GB**), `@openai/codex` 390 MB, `@microsoft` 252 MB, `@azure` 94 MB, `@azure-devops` 93 MB, `mssql-mcp` 91 MB |
| uv + Python 3.11/3.12 | 881 MB | 694 MB tools + 187 MB interpreters |
| linuxbrew | 530 MB | includes 142 MB of `Homebrew/.git` |
| goenv + Go 1.21.12/1.22.5 | 485 MB | |
| Node runtimes (20 + 22) | ~330 MB | v20 is 163 MB and otherwise unused |
| cargo | 20 MB | `rust-docs` already removed |

The MCP servers are the larger half, and `@atlaskit` alone is a third of the
whole image — pulled **transitively** into
`@neverprepared/mcp-markdown-to-confluence`, whose own compiled output is 80 KB.
`@atlaskit` appears in none of that package's five declared dependencies.

## Placement criterion

The rule used to decide where an MCP server runs:

> A server runs **in-session** only if it needs the session's own filesystem,
> identity, or network namespace. Anything that merely calls a remote API with a
> profile-scoped credential belongs **gateway-side**, because the gateway
> already injects per-profile credentials and scopes tools per ADR-002.

This criterion is applied by hand in this work. It is **not** encoded anywhere,
and that gap is worth naming: `trust_zones.py` classifies the *destination* a
server talks to (`LOCAL` = unix/file schemes, then `INFRA < VENDOR < PUBLIC`)
for data residency. There is no field anywhere describing process *placement*.
Adding one is deliberately out of scope here — it is only needed if image
selection ever becomes per-language or per-role (see Out of scope).

## Decisions

### Delete from the image

| package | size | why |
|---|---|---|
| `@neverprepared/mcp-markdown-to-confluence` | 3.8 GB | Everything it does is reproducible with kroki (already a gateway server) plus local HTML generation and uploads through existing Atlassian tooling. See TODO-1. |
| `@neverprepared/mcp-cloudflare-dns` | 24 MB | Already served by the gateway — duplication today. |
| `@neverprepared/mcp-uptime-kuma` | 100 KB | Already served by the gateway — duplication today. |
| `@neverprepared/mcp-obsidian-second-brain` | 40 MB | Stays a local/host server; session containers do not need it. |
| `mcp-remote` | small | Its own description is "remote proxy for MCP, allowing local-only clients to connect to remote servers using oAuth" — precisely what the gateway is. Its `open` dependency also needs a browser for the OAuth flow, impossible in a headless session. |
| `chrome-devtools-mcp` | 15 MB | Declares zero dependencies, so it bundles no browser, and the session image has none — it has never worked in a container. Deleted rather than kept inert: an advertised-but-broken tool costs an agent a wasted turn discovering it. Re-add deliberately if in-session browser debugging becomes a real workflow, together with a provisioned browser. |

### Move to the gateway catalog

`@neverprepared/mcp-new-relic`, `argocd-mcp`, `kubernetes-mcp-server`,
`mssql-mcp`. All four are remote-API servers with brokered credentials.

Their combined size is **not individually measured**: some live under the
`@microsoft` (252 MB), `@azure` (94 MB) and `@azure-devops` (93 MB) scopes, so
the per-package attribution is approximate. Measure before relying on the
savings figure below.

**Two gates, per server, before the image stops shipping it:**

1. a catalog entry exists in `config/mcp-catalog.json` and the server is enabled
   for the profiles that use it; and
2. its credential is in phantom-credentials.

A server failing either gate **stays in the image** until it passes. Trading
image size for a broken tool is a worse outcome than a larger image.

### Keep

`@openai/codex` (390 MB) — not a tool server. It is the agent runtime for
`LLM_PROVIDER=codex` and the wrapper launches it directly.

### Prune toolchain duplicates

Node 20, Go 1.21.12, Python 3.11, and `$(brew --repo)/.git` (142 MB). Each is a
second version kept with no identified consumer; removal is reversible by
reinstating one line.

### Split the layers

Break the single `RUN` at `Dockerfile:154` into one layer per concern:

```
apt base │ brew CLI │ Python+uv │ Node+npm globals │ Go │ Rust │ config
```

This is what permanently removes the `413` class of failure, independent of
total size: once the MCP servers are gone no single concern approaches 2 GB. It
also means the next addition lands in one identifiable layer rather than the
monolith, which is the drift problem that produced the current state.

### Cache volumes

Mount as shared per-runner volumes, declared alongside the existing profile
mounts in `_resolve_profile_mounts` — in **phantom-router**, per the repo note
above:

- `~/.npm`
- `~/.cache/uv`
- `~/.cargo/registry`

**Caches only — no runtimes.** Nothing the image's reproducibility depends on
lives in a cache, so this costs none of the guarantees that mounting toolchains
would (see Out of scope).

`~/go/pkg/mod` is **excluded from the first cut**. These volumes are shared
across concurrent sessions on a runner; npm and cargo are concurrency-safe by
design, while Go's module cache has a history of races. Add it later behind
`GOFLAGS=-mod=readonly` if it proves worth it.

## Expected outcome

| | before | after (estimated) |
|---|---|---|
| image total | 9.22 GB (single layer) | ~3.5–4 GB |
| largest layer | 9.22 GB | ~1.5 GB (the apt base), everything else far smaller |
| pushes through the front door | fail (`413`) | succeed |

The size figures are an **estimate** derived from the measured baseline minus
the deletions, with the gateway-bound servers only roughly attributed. Step 1 of
Verification is what confirms them. The claim this design actually stands on is
the *largest layer*, not the total — that is what the `413` depends on.

## Verification

Each step has to be proven, not assumed:

1. **Size and layers** — `docker history` shows no layer above 2 GB, and the
   total is in the expected range. A layer over 2 GB means the split is wrong.
2. **Push through the front door** — push to `registry.neverprepared.com`
   *without* the SSH tunnel. This is the primary success condition; the tunnel
   workaround must become unnecessary.
3. **Gateway tools still reachable** — for each moved server, call one of its
   tools through the gateway from a session container, with the `channel_*`
   tools as the control. A server that lists but cannot be called has failed
   gate 1 or 2.
4. **A session still works end to end** — provision a session, confirm the agent
   starts, claims a task and completes it. The toolchain pruning is the risk
   here: anything pinned to Node 20, Go 1.21 or Python 3.11 surfaces as a
   session that starts and then fails.
5. **Cache volumes** — confirm a second session on the same runner reuses a
   warm cache, and that two concurrent sessions do not corrupt it.

## Rollback

`brainbox:pre-gateway-fix` → `sha256:afda133e1c64` is the pre-slimming base.
Retagging it to `:latest` and rebuilding profile images restores the current
state. Note that a failed push is inherently safe: the manifest is written last,
so a partial push leaves the existing tag serving the old image.

## Out of scope

- **Per-language base images** (a thin shared base plus `brainbox-python`,
  `brainbox-node`, `brainbox-go`). Considered and dropped: after the MCP removal
  the toolchains are a 2.2 GB problem rather than a 9 GB one, and the real cost
  was never the images — it was inventing a selector, since image choice is
  currently per-profile only.
- **A process-placement field.** Only needed by the above.
- **Mounting toolchains from volumes.** Considered and rejected: runner-hosted
  sessions run on other machines, so every runner would need a correctly seeded
  and versioned volume — trading a solved distribution problem (the registry,
  which pulls and verifies with cosign) for an unsolved one, and losing the
  property that the image fully describes the environment. That property is what
  keeps an agent run reproducible across m3/m1/m5.
- **Raising the proxy's `client_max_body_size`.** Staying under 2 GB is the
  better fix; raising the cap would only hide the drift.
- **Cosign and immutability** — unchanged.
- **Image selection** — unchanged, still per-profile.

## Follow-ups

- **TODO-1:** emulate `markdown-to-confluence` with kroki plus local HTML
  generation and uploads through existing Atlassian tooling. Its diagram plugin
  already called kroki, which is a gateway server, so the pieces exist.
- **TODO-2:** verify gateway-side `playwright` can actually launch a browser.
  The gateway container has no browser installed (runs as root, `~/.cache` holds
  only `uv`) and spawns playwright via `npx -y @playwright/mcp@0.0.75`, so a
  successful `tools/list` does not imply a working browser. An earlier note
  claiming "playwright works" may predate the standalone-gateway migration.
- **TODO-3:** fix `@neverprepared/mcp-markdown-to-confluence`'s dependency graph
  upstream regardless of TODO-1 — it ships 3.4 GB of React UI components to use
  one ADF traversal helper. Anything else that depends on it inherits the bloat.
- **TODO-4:** CI cannot build this image at all. `build-containers.yml` never
  supplies the named build contexts `phantombrain` and `prouterctl` that
  `Dockerfile:19,30` require, so BuildKit resolves them as Docker Hub images and
  fails with a misleading `insufficient_scope` error. Note `prouterctl` is its
  own repo at `code/prouterctl`, not inside phantom-router. Separately, CI
  publishes to `ghcr.io/neverprepared/brainbox` while deployments pull from
  `registry.neverprepared.com/brainbox` — there is no step bridging the two,
  which is the deeper reason the deployed base sat seven weeks stale.

## Not part of this work

A session container currently prompts for Bypass Permissions on the rebuilt
base. That is a live regression under separate investigation; `jq` presence,
both acceptance flags, the Claude Code version gate (2.1.263 → 2.1.316) and the
ADR-004 credential pull have each been ruled out.
