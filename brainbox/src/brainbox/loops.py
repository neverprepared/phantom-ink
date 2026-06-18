"""Loop / Node / Edge / HandoffEnvelope — Python mirrors of the Go types
in app/loops.go. Same JSON field names so both sides serialize to the same
wire format.

The Loop runner lives here in brainbox because it integrates with the queue,
the WAITING_* suspension primitive, and child-task observation. The Go side
owns authoring and display; brainbox owns execution. The JSON shape is the
contract; both sides mirror it.

See ~/.claude/plans/okay-the-idea-of-replicated-popcorn.md for the full
design and the Phase A breakdown.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants — kept aligned with app/loops.go
# ---------------------------------------------------------------------------

ENVELOPE_SCHEMA_VERSION = 1


class PermissionTier(str, Enum):
    """How a Loop's Nodes receive permissions from the profile env.

    See plan: profiles isolate projects; this is the second, finer boundary
    inside one profile that handles the reviewer-vs-worker prompt-injection
    surface within a single Loop.
    """

    INHERIT = "inherit"
    DEFAULT = "default"
    STRICT = "strict"


class NodeKind(str, Enum):
    AGENT = "agent"
    PLAYBOOK = "playbook"
    JOIN = "join"
    HUMAN = "human"
    SCHEDULE = "schedule"


class NodeExecutor(str, Enum):
    HOST_CLI = "host-cli"
    BRAINBOX_SESSION = "brainbox-session"
    A2A_REMOTE = "a2a-remote"


class EscalationAction(str, Enum):
    HUMAN = "human"
    SKIP = "skip"
    FAIL = "fail"


# ---------------------------------------------------------------------------
# Body — Nodes and Edges
# ---------------------------------------------------------------------------


class EdgeTransform(BaseModel):
    """Declarative envelope projection at a trust boundary. Select and Omit
    are mutually exclusive on a given edge; Merge can combine with either.
    """

    select: list[str] = Field(default_factory=list)
    omit: list[str] = Field(default_factory=list)
    merge: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """Connects two Nodes in a Loop body. Predicate (JMESPath, bool) decides
    whether the edge fires against the envelope emitted by ``from``;
    omitted predicate means always-fire.
    """

    from_: str = Field(alias="from")
    to: str
    predicate: str = ""
    transform: EdgeTransform | None = None

    model_config = {"populate_by_name": True}


class Node(BaseModel):
    """One unit of execution inside a Loop body. ``kind`` selects the runtime
    shape; ``executor`` selects the runtime backend. host-cli is wired today;
    brainbox-session and a2a-remote are forward-compat slots.

    ``requires`` lists permission scopes the Node needs (consulted in
    strict tier; in default tier, destructive scopes still require explicit
    listing here).
    """

    id: str
    kind: NodeKind = NodeKind.AGENT
    executor: NodeExecutor = NodeExecutor.HOST_CLI
    role: str = ""
    agent_id: str = ""
    playbook_id: str = ""
    prompt: str = ""
    requires: list[str] = Field(default_factory=list)
    timeout_ms: int = 0


class Body(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Intent and stop conditions
# ---------------------------------------------------------------------------


class EscalationClause(BaseModel):
    predicate: str  # JMESPath, bool
    action: EscalationAction = EscalationAction.HUMAN


class StopCondition(BaseModel):
    """Hard cap evaluated between iterations. If any matches, the Loop stops
    with the corresponding reason tag attached to the instance.
    """

    predicate: str  # JMESPath, bool against envelope
    reason: str = ""  # operator-facing tag, e.g. "diff_too_large"


class Intent(BaseModel):
    """Structured "done" definition for a Loop. ``convergence`` is the SAME
    expression as ``LoopSpec.convergence_predicate`` — one source of truth.
    A template that fails to declare convergence cannot load (enforced at
    LoopSpec validation time).
    """

    outcome: str = ""
    verification: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    convergence: str  # JMESPath, bool — REQUIRED
    escalation: list[EscalationClause] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LoopSpec — the on-disk Loop definition
# ---------------------------------------------------------------------------


class TemplateSnapshot(BaseModel):
    """Pinned onto each LoopInstance at creation. The runner reads only from
    the snapshot for the Loop's life; on-disk template changes after
    creation never affect in-flight instances. Role markdown referenced by
    Nodes does live-bind — see plan for the rationale.
    """

    name: str
    version: str = ""
    hash: str = ""
    body_json: str = ""  # the full spec at snapshot time


class LoopSpec(BaseModel):
    """Top-level Loop definition. Mirrors Loop in app/loops.go.

    Backwards compat: a 1-iteration LoopSpec with a trivially-true
    convergence_predicate is behaviorally identical to today's Chain run.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    intent: Intent
    body: Body
    max_iterations: int = 5
    convergence_predicate: str = ""  # JMESPath, bool — defaults to intent.convergence
    convergence_metric: str = ""  # JMESPath, number — charted per iteration
    stop_conditions: list[StopCondition] = Field(default_factory=list)
    permissions: PermissionTier = PermissionTier.DEFAULT
    template_snapshot: TemplateSnapshot | None = None
    cwd: str = ""
    workspace_profile: str = ""
    created_at: str = ""
    updated_at: str = ""

    def model_post_init(self, __context: Any) -> None:
        # Single source of truth: if the Loop didn't explicitly declare a
        # convergence_predicate, fall back to intent.convergence. Both being
        # empty is the validation-error case caught below.
        if not self.convergence_predicate:
            self.convergence_predicate = self.intent.convergence
        if not self.convergence_predicate:
            raise ValueError(
                "LoopSpec requires a convergence predicate "
                "(either Intent.convergence or LoopSpec.convergence_predicate)"
            )


# ---------------------------------------------------------------------------
# HandoffEnvelope — the unifying handoff primitive
# ---------------------------------------------------------------------------


class HandoffEnvelope(BaseModel):
    """The unifying handoff payload. Every Node-to-Node, iteration-to-iteration,
    Loop-to-Loop, and cross-runtime handoff carries one of these.

    Discipline (do not break):
      - Additive-only schema. Never remove or repurpose a field; only add
        optional ones. ``schema_version`` is stamped for cross-runtime
        compatibility checks (e.g. A2A remote agent declaring
        ``accepts_schema: ">=3"``). The runner does NOT do migrations on load.
      - Embed with overflow. Up to ~100 KB embedded; larger blobs go to
        artifact storage and the envelope carries a pointer in ``artifact_refs``.
      - JMESPath-queryable. Every predicate site in the runner shares the
        same expression language evaluated against this shape.
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
# LoopInstance — one Loop run, projected as a parent task + iteration children
# ---------------------------------------------------------------------------


class LoopStatus(str, Enum):
    """A Loop instance's lifecycle. PENDING / RUNNING are active; the rest are
    terminal. CONVERGED is the success exit; the four FAILED-ish terminals
    capture distinct failure shapes so an operator can triage at a glance.
    """

    PENDING = "pending"
    RUNNING = "running"
    CONVERGED = "converged"
    THRASHING = "thrashing"               # convergence metric not improving
    MAX_ITER = "max_iter"                 # iteration cap hit without convergence
    STOPPED_BY_CONDITION = "stopped_by_condition"  # a StopCondition predicate fired
    FAILED = "failed"                     # iteration child task failed / runner error
    CANCELLED = "cancelled"


class LoopInstance(BaseModel):
    """One execution of a LoopSpec. Each iteration is projected as a child
    task in the router; this instance holds the cross-iteration state the
    runner needs to decide whether to advance, converge, or stop.

    ``spec_snapshot`` is pinned at creation. The runner only reads from this
    snapshot; on-disk template changes never affect in-flight instances.
    ``metric_history`` is the per-iteration convergence-metric series that
    feeds the convergence trend chart and the thrash detector.
    """

    id: str
    spec_snapshot: LoopSpec
    parent_task_id: str
    status: LoopStatus = LoopStatus.PENDING
    iteration: int = 0
    envelope: HandoffEnvelope
    metric_history: list[float] = Field(default_factory=list)
    current_child_id: str | None = None
    workspace_profile: str | None = None
    created_at: int  # epoch ms
    updated_at: int  # epoch ms
    error: str | None = None
    stop_reason: str | None = None  # set when status == STOPPED_BY_CONDITION
