"""Agent registry and token issuance.

Extended to support markdown role prompts absorbed from multiclaude
(Dan Lorenc, github.com/dlorenc/multiclaude).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import stat
import time
import uuid

from .config import settings
from .log import get_logger
from .models import AgentDefinition, Token

log = get_logger()

# ---------------------------------------------------------------------------
# Capability catalog (T11)
# ---------------------------------------------------------------------------
# The small, closed set of capabilities a profile token may be granted. The
# dashboard reads this to render its multi-select; ``issue_profile_token``
# validates against it so a typo'd capability can never be minted. Keep this
# minimal — add entries only as routes are moved onto ``require_capability``.
CAPABILITY_CATALOG: tuple[str, ...] = (
    "agent_events:write",
    "agent_events:read",
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_agents: dict[str, AgentDefinition] = {}
_tokens: dict[str, Token] = {}
_last_token_sweep: float = 0.0
# Loaded role prompt content keyed by agent name
_role_prompts: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------------


def load_agents() -> dict[str, AgentDefinition]:
    _agents.clear()
    _role_prompts.clear()
    agents_dir = settings.agents_dir

    if not agents_dir.is_dir():
        log.warning("registry.no_agents_dir", metadata={"dir": str(agents_dir)})
        return _agents

    for f in sorted(agents_dir.iterdir()):
        if not f.suffix == ".json":
            continue
        try:
            # Check file permissions — warn if world-writable
            mode = f.stat().st_mode
            if mode & stat.S_IWOTH:
                log.warning(
                    "registry.agent_world_writable",
                    metadata={"file": f.name, "mode": oct(mode)},
                )
                # Enforce safe permissions — strip world-write bit
                f.chmod(mode & ~stat.S_IWOTH)

            raw = json.loads(f.read_text())

            # Validate required fields
            if not raw.get("name") or not raw.get("image"):
                log.warning(
                    "registry.agent_missing_fields",
                    metadata={
                        "file": f.name,
                        "has_name": bool(raw.get("name")),
                        "has_image": bool(raw.get("image")),
                    },
                )
                continue

            agent = AgentDefinition(**raw)
            _agents[agent.name] = agent

            # Load role prompt if specified
            if agent.role_prompt:
                _load_role_prompt(agent)

            log.info(
                "registry.agent_loaded",
                metadata={
                    "name": agent.name,
                    "file": f.name,
                    "has_role_prompt": agent.name in _role_prompts,
                    "persistent": agent.persistent,
                },
            )
        except Exception as exc:
            log.warning("registry.agent_load_failed", metadata={"file": f.name, "reason": str(exc)})

    return _agents


def _load_role_prompt(agent: AgentDefinition) -> None:
    """Load the markdown role prompt for an agent definition."""
    if not agent.role_prompt:
        return
    prompt_path = settings.agents_dir / agent.role_prompt
    if not prompt_path.is_file():
        log.warning(
            "registry.role_prompt_not_found",
            metadata={"agent": agent.name, "path": str(prompt_path)},
        )
        return
    try:
        _role_prompts[agent.name] = prompt_path.read_text()
    except Exception as exc:
        log.warning(
            "registry.role_prompt_load_failed",
            metadata={"agent": agent.name, "reason": str(exc)},
        )


def get_role_prompt(agent_name: str) -> str | None:
    """Get the loaded role prompt content for an agent."""
    return _role_prompts.get(agent_name)


def get_agent(name: str) -> AgentDefinition | None:
    return _agents.get(name)


def list_agents() -> list[AgentDefinition]:
    return list(_agents.values())


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

_BUILTIN_AGENTS: frozenset[str] = frozenset({"assistant"})


def create_agent(name: str, image: str, description: str, capabilities: list[str],
                 hardened: bool, persistent: bool, role_prompt_content: str | None,
                 claude_model: str | None = None, claude_effort: str | None = None,
                 codex_model: str | None = None, ollama_model: str | None = None,
                 category: str = "general", spawn_mode: str = "container") -> AgentDefinition:
    """Create a new agent definition and persist it to disk."""
    if name in _agents:
        raise ValueError(f"Agent '{name}' already exists")

    agents_dir = settings.agents_dir
    agents_dir.mkdir(parents=True, exist_ok=True)

    role_prompt: str | None = None
    if role_prompt_content:
        roles_dir = agents_dir / "roles"
        roles_dir.mkdir(exist_ok=True)
        prompt_file = roles_dir / f"{name}.md"
        prompt_file.write_text(role_prompt_content)
        role_prompt = f"roles/{name}.md"
        log.info("registry.role_prompt_written", metadata={"agent": name, "path": str(prompt_file)})

    agent = AgentDefinition(
        name=name,
        image=image,
        description=description,
        category=category,
        spawn_mode=spawn_mode,
        capabilities=capabilities,
        hardened=hardened,
        persistent=persistent,
        role_prompt=role_prompt,
        claude_model=claude_model or None,
        claude_effort=claude_effort or None,
        codex_model=codex_model or None,
        ollama_model=ollama_model or None,
    )

    json_path = agents_dir / f"{name}.json"
    json_path.write_text(json.dumps(agent.model_dump(exclude_none=True), indent=2))
    json_path.chmod(0o644)

    _agents[name] = agent
    if role_prompt_content:
        _role_prompts[name] = role_prompt_content

    log.info("registry.agent_created", metadata={"name": name})
    return agent


def update_agent(name: str, image: str | None, description: str | None,
                 capabilities: list[str] | None, hardened: bool | None,
                 persistent: bool | None, role_prompt_content: str | None,
                 claude_model: str | None = None, claude_effort: str | None = None,
                 codex_model: str | None = None, ollama_model: str | None = None,
                 category: str | None = None, spawn_mode: str | None = None) -> AgentDefinition:
    """Update an existing agent definition."""
    agent = _agents.get(name)
    if not agent:
        raise ValueError(f"Agent '{name}' not found")

    agents_dir = settings.agents_dir

    # Patch scalar fields (None = no change, "" = clear for model fields)
    patch: dict = {}
    if image is not None:
        patch["image"] = image
    if description is not None:
        patch["description"] = description
    if category is not None:
        patch["category"] = category
    if spawn_mode is not None:
        patch["spawn_mode"] = spawn_mode
    if capabilities is not None:
        patch["capabilities"] = capabilities
    if hardened is not None:
        patch["hardened"] = hardened
    if persistent is not None:
        patch["persistent"] = persistent
    # Model/effort fields: None = no change, "" = clear
    for field, val in [("claude_model", claude_model), ("claude_effort", claude_effort),
                       ("codex_model", codex_model), ("ollama_model", ollama_model)]:
        if val is not None:
            patch[field] = val if val != "" else None
    updated = agent.model_copy(update=patch)

    # Handle role_prompt_content:
    #   None      → no change
    #   ""        → clear prompt
    #   non-empty → write/overwrite
    if role_prompt_content is not None:
        if role_prompt_content == "":
            # Clear prompt
            if updated.role_prompt:
                prompt_path = agents_dir / updated.role_prompt
                if prompt_path.is_file():
                    prompt_path.unlink()
            updated = updated.model_copy(update={"role_prompt": None})
            _role_prompts.pop(name, None)
        else:
            roles_dir = agents_dir / "roles"
            roles_dir.mkdir(exist_ok=True)
            prompt_file = roles_dir / f"{name}.md"
            prompt_file.write_text(role_prompt_content)
            updated = updated.model_copy(update={"role_prompt": f"roles/{name}.md"})
            _role_prompts[name] = role_prompt_content

    json_path = agents_dir / f"{name}.json"
    json_path.write_text(json.dumps(updated.model_dump(exclude_none=True), indent=2))

    _agents[name] = updated
    log.info("registry.agent_updated", metadata={"name": name})
    return updated


def delete_agent(name: str) -> None:
    """Delete a custom agent. Built-in agents cannot be deleted."""
    if name in _BUILTIN_AGENTS:
        raise ValueError(f"Agent '{name}' is a built-in and cannot be deleted")
    if name not in _agents:
        raise ValueError(f"Agent '{name}' not found")

    agents_dir = settings.agents_dir

    json_path = agents_dir / f"{name}.json"
    if json_path.is_file():
        json_path.unlink()

    prompt_path = agents_dir / "roles" / f"{name}.md"
    if prompt_path.is_file():
        prompt_path.unlink()

    _agents.pop(name, None)
    _role_prompts.pop(name, None)
    log.info("registry.agent_deleted", metadata={"name": name})


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


def issue_token(agent_name: str, task_id: str, ttl: int = 3600) -> Token:
    agent = _agents.get(agent_name)
    if not agent:
        raise ValueError(f"Agent '{agent_name}' not registered")

    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=task_id,
        capabilities=list(agent.capabilities),
        issued=now,
        expiry=now + ttl * 1000,
    )

    _tokens[token.token_id] = token
    log.info(
        "registry.token_issued",
        metadata={
            "token_id": token.token_id,
            "agent_name": agent_name,
            "task_id": task_id,
            "ttl": ttl,
        },
    )
    return token


def issue_session_token(role: str, session_id: str) -> Token | None:
    """Issue a real token for an interactive session's agent identity.

    Every spawned session gets one (not just hub tasks) so the agent calls the
    hub/A2A API under its own token instead of a stub. Capabilities come from
    the role's agent definition — only roles with ``task_submit`` (supervisor,
    worker) can dispatch A2A. TTL is persistent-aware. Returns ``None`` if the
    role isn't a registered agent (caller falls back to a stub token).
    """
    from .config import settings

    agent = _agents.get(role)
    if agent is None:
        return None
    ttl = settings.hub.persistent_token_ttl if agent.persistent else settings.hub.token_ttl
    return issue_token(role, session_id, ttl=ttl)


def issue_bare_session_token(role: str, session_id: str) -> Token:
    """Mint a capability-less token for a session whose role is NOT a
    registered agent. Task-bearing sessions need a bearer identity to fetch
    their task from the session store; without an agent definition there are
    no capabilities to grant, but possession still proves session identity.
    """
    from .config import settings

    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=role,
        task_id=session_id,
        capabilities=[],
        issued=now,
        expiry=now + settings.hub.token_ttl * 1000,
    )
    _tokens[token.token_id] = token
    log.info(
        "registry.bare_session_token_issued",
        metadata={"token_id": token.token_id, "role": role, "session_id": session_id},
    )
    return token


def issue_gateway_token(
    profile: str,
    scope: list[str] | None = None,
    ttl: int = 3600,
    residency_ceiling: str = "",
) -> Token:
    """Mint a profile-bound MCP gateway token (ADR-002 phase 3, Tier-0).

    Unlike ``issue_token``, this requires neither a registered agent nor a
    task: a local client (e.g. opencode) gets a token scoped directly to a
    workspace profile with an explicit tool ``scope`` (empty = all tools) and an
    optional ``residency_ceiling`` (trust-zone name; empty = no restriction).
    The gateway's ``BrainboxTokenVerifier`` reads these straight off the token.
    """
    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name="gateway",
        task_id="",
        capabilities=[],
        issued=now,
        expiry=now + ttl * 1000,
        workspace_profile=profile,
        scope=list(scope or []),
        residency_ceiling=residency_ceiling or "",
    )
    _tokens[token.token_id] = token
    # Persist so the token survives a daemon restart — gateway tokens are baked
    # into long-lived container .mcp.json files, which would 401 otherwise.
    try:
        from . import store

        store.save_gateway_token(
            token.token_id, profile, token.scope, token.issued, token.expiry,
            token.residency_ceiling,
        )
    except Exception as exc:  # pragma: no cover - persistence is best-effort
        log.warning("registry.gateway_token_persist_failed", metadata={"reason": str(exc)})
    log.info(
        "registry.gateway_token_issued",
        metadata={
            "token_id": token.token_id,
            "profile": profile,
            "scope": token.scope,
            "ceiling": token.residency_ceiling,
            "ttl": ttl,
        },
    )
    return token


def load_persisted_gateway_tokens() -> int:
    """Rehydrate non-expired gateway tokens from the DB into memory (startup)."""
    from . import store

    count = 0
    for row in store.load_gateway_tokens():
        _tokens[row["token_id"]] = Token(
            token_id=row["token_id"],
            agent_name="gateway",
            task_id="",
            capabilities=[],
            issued=row["issued"],
            expiry=row["expiry"],
            workspace_profile=row["workspace_profile"],
            scope=list(row["scope"]),
            residency_ceiling=row.get("residency_ceiling", ""),
        )
        count += 1
    if count:
        log.info("registry.gateway_tokens_loaded", metadata={"count": count})
    return count


# ---------------------------------------------------------------------------
# Profile tokens (T11) — persistent, revocable, per-profile service tokens
# ---------------------------------------------------------------------------

# Profile tokens never expire (long-lived until revoked). validate_token builds
# a Token with this sentinel expiry so the in-memory expiry check is a no-op.
_NO_EXPIRY: int = 1 << 62


def _hash_token(raw: str) -> str:
    """sha256 hex digest of a raw bearer value — what we persist and look up."""
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_profile_token(
    workspace_profile: str,
    capabilities: list[str],
    label: str = "",
) -> tuple[str, Token]:
    """Mint a persistent, revocable profile-bound token (T11, Tier-0).

    Generates a high-entropy bearer secret (``secrets.token_hex(32)`` — NOT a
    bare uuid), persists ``sha256(raw)`` plus metadata in ``profile_tokens``,
    and returns ``(raw, Token)``. The raw value is returned ONCE and never
    stored; callers must surface it to the human immediately. The Token has no
    task binding and no expiry (works until revoked).
    """
    if not workspace_profile or not workspace_profile.strip():
        raise ValueError("workspace_profile is required")
    unknown = [c for c in capabilities if c not in CAPABILITY_CATALOG]
    if unknown:
        raise ValueError(f"Unknown capabilities: {unknown}")

    from . import store

    raw = secrets.token_hex(32)
    token_id = str(uuid.uuid4())
    now = int(time.time() * 1000)
    caps = list(dict.fromkeys(capabilities))  # dedupe, preserve order

    store.insert_profile_token(
        token_id=token_id,
        token_hash=_hash_token(raw),
        workspace_profile=workspace_profile,
        capabilities=caps,
        scope=[],
        label=label,
        issued=now,
    )
    token = Token(
        token_id=token_id,
        agent_name="profile",
        task_id="",
        capabilities=caps,
        issued=now,
        expiry=_NO_EXPIRY,
        workspace_profile=workspace_profile,
    )
    log.info(
        "registry.profile_token_issued",
        metadata={
            "token_id": token_id,
            "workspace_profile": workspace_profile,
            "capabilities": caps,
            "label": label,
        },
    )
    return raw, token


def _validate_profile_token(presented: str) -> Token | None:
    """Hashed-lookup fallback for validate_token: resolve a raw bearer value
    against ``profile_tokens``. Rejects revoked tokens; stamps last_used."""
    try:
        from . import store

        row = store.find_profile_token_by_hash(_hash_token(presented))
    except Exception as exc:  # pragma: no cover - DB unavailable is best-effort
        log.warning("registry.profile_token_lookup_failed", metadata={"reason": str(exc)})
        return None
    if row is None or row["revoked"]:
        return None
    now = int(time.time() * 1000)
    try:
        store.touch_profile_token_last_used(row["token_id"], now)
    except Exception:  # pragma: no cover - best-effort telemetry
        pass
    return Token(
        token_id=row["token_id"],
        agent_name="profile",
        task_id="",
        capabilities=list(row["capabilities"]),
        issued=row["issued"],
        expiry=_NO_EXPIRY,
        workspace_profile=row["workspace_profile"],
        scope=list(row["scope"]),
    )


def validate_token(token_id: str) -> Token | None:
    # 1. Legacy in-memory path (gateway/session/task tokens keyed by uuid).
    token = _tokens.get(token_id)
    if token:
        now = int(time.time() * 1000)
        if now > token.expiry:
            _tokens.pop(token_id, None)
            return None
        return token
    # 2. Persistent profile-token fallback (hashed lookup). Reached only when
    #    the presented value isn't a known in-memory token id — a profile token's
    #    bearer value is a 64-char hex secret, not a registered uuid, so the two
    #    namespaces never collide.
    return _validate_profile_token(token_id)


def revoke_token(token_id: str) -> bool:
    existed = token_id in _tokens
    _tokens.pop(token_id, None)
    try:
        from . import store

        store.delete_gateway_token(token_id)
    except Exception:  # pragma: no cover - best-effort
        pass
    if existed:
        log.info("registry.token_revoked", metadata={"token_id": token_id})
    return existed


def list_tokens() -> list[Token]:
    global _last_token_sweep
    if time.monotonic() - _last_token_sweep > 60 or len(_tokens) > 100:
        now = int(time.time() * 1000)
        expired = [tid for tid, t in _tokens.items() if now > t.expiry]
        for tid in expired:
            _tokens.pop(tid, None)
        _last_token_sweep = time.monotonic()
    return list(_tokens.values())


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------


def get_state() -> dict:
    return {"tokens": [(tid, t.model_dump()) for tid, t in _tokens.items()]}


def restore_state(state: dict | None) -> None:
    if not state or "tokens" not in state:
        return
    now = int(time.time() * 1000)
    for tid, data in state["tokens"]:
        token = Token(**data)
        if now <= token.expiry:
            _tokens[tid] = token
