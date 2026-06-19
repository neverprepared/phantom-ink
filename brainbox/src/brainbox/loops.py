"""Loop runtime types — HandoffEnvelope, LoopInstance, LoopStatus,
RequiredRef, PermissionTier.

The on-disk loop *definition* lives in ``loop_md`` as ``LoopMarkdown``.
This module holds the runtime shapes the runner reads and writes.

History note: an earlier version held a full ``LoopSpec`` Pydantic
model with embedded JMESPath predicates, Node/Edge graphs, and
StopCondition lists. That format was replaced with the markdown format
(prose stop/escalate sections evaluated by a judge agent each
iteration). See ``loop_md`` for the parser and ``loop_judge`` for the
evaluator.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants — kept aligned with app/loops.go
# ---------------------------------------------------------------------------

ENVELOPE_SCHEMA_VERSION = 1


class PermissionTier(str, Enum):
    """How a Loop's worker receives permissions from the profile env.

    Profiles isolate projects; this is the second, finer boundary inside
    one profile that handles the reviewer-vs-worker prompt-injection
    surface within a single loop.
    """

    INHERIT = "inherit"
    DEFAULT = "default"
    STRICT = "strict"


class RequiredRefType(str, Enum):
    """Operator-facing type hint for a loop's required artifact_refs.

    Drives the Trigger form's input rendering (number input for int,
    text input for string, fixed-width font + length hint for sha) and
    documents intent for AI Assist when generating templates. The
    runner does NOT enforce type — the start_loop check is presence-only.
    """

    INT = "int"
    STRING = "string"
    SHA = "sha"


class RequiredRef(BaseModel):
    """Declares an artifact_refs key that the operator (or webhook
    handler) must populate before start_loop accepts the trigger.

    Surfaced in the desktop Trigger tab as a form field; pre-populated
    by the GitHub webhook handler from the PR payload.
    """

    name: str
    type: RequiredRefType = RequiredRefType.STRING
    description: str = ""
    required: bool = True


# ---------------------------------------------------------------------------
# HandoffEnvelope — the unifying handoff primitive
# ---------------------------------------------------------------------------


class HandoffEnvelope(BaseModel):
    """The unifying handoff payload. Every iteration-to-iteration handoff
    and every cross-runtime handoff carries one of these.

    Discipline (do not break):
      - Additive-only schema. Never remove or repurpose a field; only
        add optional ones. ``schema_version`` is stamped for cross-runtime
        compatibility checks (e.g. A2A remote agent declaring
        ``accepts_schema: ">=3"``). The runner does NOT do migrations
        on load.
      - Embed with overflow. Up to ~100 KB embedded; larger blobs go to
        artifact storage and the envelope carries a pointer in
        ``artifact_refs``.
      - Judge-readable. The judge agent reads this shape directly when
        evaluating prose stop/escalate sections, so keep keys descriptive.
    """

    schema_version: int = ENVELOPE_SCHEMA_VERSION
    loop_id: str = ""
    iteration: int = 0
    from_node: str = ""
    to_node: str = ""
    artifact_refs: dict[str, Any] = Field(default_factory=dict)
    observations: dict[str, Any] = Field(default_factory=dict)
    findings: dict[str, Any] = Field(default_factory=dict)
    memory_refs: list[str] = Field(default_factory=list)
    trace_id: str = ""
    scope_grants: dict[str, Any] = Field(default_factory=dict)
    context_carry: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# LoopInstance — one loop run
# ---------------------------------------------------------------------------


class LoopStatus(str, Enum):
    """A loop instance's lifecycle. PENDING / RUNNING are active; the rest
    are terminal. CONVERGED is the success exit; the four FAILED-ish
    terminals capture distinct failure shapes for triage.
    """

    PENDING = "pending"
    RUNNING = "running"
    CONVERGED = "converged"
    THRASHING = "thrashing"
    MAX_ITER = "max_iter"
    STOPPED_BY_CONDITION = "stopped_by_condition"  # set when judge says escalate
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoopInstance(BaseModel):
    """One execution of a parsed loop template. Each iteration is
    projected as a child task in the router; this instance holds the
    cross-iteration state the runner needs to decide whether to advance,
    converge, or stop.

    ``template_text`` is the full raw markdown frozen at creation. The
    runner only reads from this snapshot for the loop's life; on-disk
    template changes never affect in-flight instances. ``mermaid`` is
    generated alongside it and survives template edits.

    ``cost_history`` is the per-iteration USD cost series; ``cost_usd``
    is the running total. Used to enforce ``budget_usd`` from the
    template frontmatter.
    """

    id: str
    template_name: str = ""
    template_text: str = ""        # raw markdown — the spec snapshot
    template_hash: str = ""        # short content hash for telemetry
    mermaid: str = ""              # rendered at create time
    parent_task_id: str
    status: LoopStatus = LoopStatus.PENDING
    iteration: int = 0
    envelope: HandoffEnvelope
    cost_history: list[float] = Field(default_factory=list)
    cost_usd: float = 0.0
    current_child_id: str | None = None
    workspace_profile: str | None = None
    created_at: int  # epoch ms
    updated_at: int  # epoch ms
    error: str | None = None
    stop_reason: str | None = None  # judge "reason" string when terminal
