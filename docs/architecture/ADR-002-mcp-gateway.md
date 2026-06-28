# ADR-002: Shared MCP Gateway (per-profile, token-scoped, in brainbox)

- **Status:** Accepted (phase 1 implemented)
- **Date:** 2026-06-28
- **Deciders:** Platform owner (mindmorass)
- **Related:** [ADR-001](./ADR-001-agent-orchestration.md), [`ORCHESTRATION.md`](./ORCHESTRATION.md)

---

## Context

The platform vision is **local-first / private isolation first**: a Tier-0 local entry (opencode + ollama) where private data stays put, and Tier-1 containerized public agents (claude/codex) launched on demand via the phantom-ink API. For these heterogeneous agents to share tools, we need **one MCP endpoint** they all connect to that:

1. aggregates many MCP servers behind a single endpoint;
2. wraps stdio (`npx`/`uvx`) servers so they can be shared;
3. injects **per-profile credentials** server-side (Tier-1 containers never hold secrets);
4. enforces **per-agent tool scope** ("narrow-by-default, broaden only when the user deploys a public task").

We evaluated the field directly (mcp-proxy, IBM ContextForge, Docker MCP Gateway, MetaMCP, Bifrost, obot). **None does per-token tool-level scoping tied to an external identity model** — that boundary is inherently platform-specific. Each full gateway also brings a competing identity/governance/credential model that collides with phantom-ink's foundational **profile + credential** model. (Full landscape eval stored in phantom-brain: `mcp-gateway-for-phantom-ink-landscape-eval-go-build-decision`.)

## Decision

**Build a thin MCP gateway, hosted in brainbox (FastAPI/Python).** Adopt no platform. Use the **MCP SDK for the commodity transport**, and **own the policy/scope/credential layer** — because it's a security boundary that must be native to profiles. Same philosophy as ADR-001's A2A façade: thin, native, SDK for the commodity protocol, own the boundary.

**Host = central, in brainbox.** The gateway lives where brainbox runs (the control plane). "Local-first / private" is satisfied by running brainbox locally for Tier-0; a standalone edge binary is only needed if the gateway must run independently of brainbox, which we don't require. (FastAPI also wins on dependency locality: the catalog, per-profile creds, the hub token model, and config-gen are already Python in brainbox — a separate Go service would reach back over the network or duplicate them.)

## Architecture

- **MCP plane (commodity, via the Python MCP SDK):** the gateway is an MCP **server** over streamable-HTTP that agents (opencode/claude/codex) connect to, *and* an MCP **client** to the downstream catalog servers (stdio + http). Mounted as an ASGI sub-app in the brainbox FastAPI app.
- **Identity:** an agent connects with a Bearer token carrying **`profile`** + **`scope`**. Reuses the hub `Token` / `require_capability` substrate. Tier-1 tokens are minted at task/session creation with `profile` = the task's `workspace_profile`; Tier-0 opencode gets a profile-bound token.
- **Per-(profile × server) instances:** MCP servers read credentials at **startup**, so a single shared process can't serve multiple profiles. The gateway runs **one instance per (profile, credentialed server)**, started with that profile's env — lazy-spawned, idle-reaped (the OllamaPool capability-pool pattern, keyed by profile). No-cred/stateless servers are shared singletons; servers that accept per-request auth can be shared + per-call.
- **Credentials = per-profile encrypted env store (no new cred *scheme* invented):** each profile's env (the vars MCP servers read at startup) is stored **encrypted at rest** with one operator-held key. Decrypted in-memory only, injected into the per-profile server subprocess at spawn. Edited through the app (select profile → edit env vars). Adding a profile/secret = an app edit; **no server config change, no restart.**
- **Enforcement per request:** `tools/list` → `catalog ∩ scope`; `tools/call` → assert tool ∈ scope, route to the profile's instance, proxy. **Default-narrow; the "public task" grant broadens the token's scope.**

### Trust model
- **No cross-profile leakage:** the token→profile binding is the single enforcement point; creds are strictly profile-keyed; a profile-X token can never reach profile-Y instances or creds.
- **Creds out of containers:** Tier-1 holds only a scoped token; the gateway holds creds.
- **Operator key** lives only in the gateway env/memory (`CL_GATEWAY__SECRET_KEY`); the store is useless at rest without it. One key unlocks all profiles (by design, operator-controlled). The gateway is the concentrated trust point — mitigate with per-profile process isolation + audit logging on `tools/call` and cred edits.
- The **app never holds the key**: it sends plaintext env to brainbox over the authed/TLS channel; encryption is at-rest + at-runtime inside the gateway.

### Crypto
**age** via `pyrage` (passphrase mode) — the operator key is an age passphrase. A vetted library, already a brainbox dependency; **we do not roll our own crypto.** age keypair / SOPS are drop-in alternatives behind the same `gateway_secrets` interface if that interop is later wanted.

## Phases

**Phase 1 — per-profile encrypted env store (implemented in this PR).**
- `config.GatewaySettings` (`secret_key`, `secrets_dir`).
- `gateway_secrets.py`: age-encrypted (pyrage passphrase) per-profile env blobs; `set/get/list/delete`, `is_unlocked`; atomic 0600 writes; locked/wrong-key errors.
- `api.py`: operator-only CRUD — `GET/PUT/DELETE /api/gateway/profiles/{profile}/env`, `GET /api/gateway/profiles` (all `require_api_key`).
- Tests: round-trip, ciphertext-at-rest, list/delete, locked, wrong-key, invalid profile, API CRUD + 409/400.

**Phase 2 — the MCP plane (next).** Verify the Python MCP SDK's streamable-HTTP server + stdio client APIs and 3.14 compat; build the per-(profile × server) instance manager (decrypt env → spawn → idle-reap) and the MCP server/client mounted in FastAPI; extend the hub token to carry `profile` + `scope`; permissive scope to start.

**Phase 3 — app UI + scope hardening.** Per-profile Environment editor in the Profiles panel (Go binding → the phase-1 CRUD). Per-tool scope grants ("public task" broadening). First servers behind it: phantom-brain (already shared-daemon) + one credential-bearing server (slack or gh) as the pilot.

## Verification

- Phase 1: `just bb-test` (`tests/test_gateway_secrets.py`) + `just bb-lint`.
- Manual: set `CL_GATEWAY__SECRET_KEY=<a strong passphrase>`, then `PUT/GET/DELETE /api/gateway/profiles/personal/env`; confirm the on-disk blob contains no plaintext.
- Phase 2/3 verification defined when those land.

## Cross-links
- ADR-001 (A2A façade + ModelTarget) — same thin-façade philosophy; the gateway's token model extends the same hub `Token`/`require_capability` substrate.
- Reuses `reflex/plugins/reflex/mcp-catalog.json`, per-profile creds, the OllamaPool capability-pool pattern.
