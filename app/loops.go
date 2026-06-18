package main

// Loop is the top-level construct that replaces Chain in the loop-engineering
// model. A Loop has structured Intent, a Body that is a flowchart of Nodes
// connected by Edges, a convergence predicate that decides when to stop, and
// stop conditions that bound runaway iteration.
//
// A 1-iteration Loop with a trivially-true convergence predicate is
// behaviorally identical to today's Chain run — backwards compat is automatic
// once the runner learns to consume Loop.
//
// These types are scaffolding (Phase A1): defined but not wired into the
// runner yet. See the plan at
// ~/.claude/plans/okay-the-idea-of-replicated-popcorn.md for the full
// design and phase breakdown.
type Loop struct {
	ID                   string          `json:"id"`
	Name                 string          `json:"name"`
	Description          string          `json:"description"`
	Intent               Intent          `json:"intent"`
	Body                 LoopBody        `json:"body"`
	MaxIterations        int             `json:"max_iterations"`
	ConvergencePredicate string          `json:"convergence_predicate"` // JMESPath, bool
	ConvergenceMetric    string          `json:"convergence_metric"`    // JMESPath, number
	StopConditions       []StopCondition `json:"stop_conditions"`
	Permissions          string          `json:"permissions"` // "inherit" | "default" | "strict"
	TemplateSnapshot     *TemplateSnapshot `json:"template_snapshot,omitempty"`
	Cwd                  string          `json:"cwd"`
	WorkspaceProfile     string          `json:"workspace_profile"`
	CreatedAt            string          `json:"created_at"`
	UpdatedAt            string          `json:"updated_at"`
}

// Intent is the structured "what does done look like" attached to a Loop.
// Convergence is the same expression used by Loop.ConvergencePredicate —
// they're the same data viewed from two angles. A template that fails to
// declare Convergence cannot be loaded.
type Intent struct {
	Outcome      string             `json:"outcome"`
	Verification []string           `json:"verification"`
	NonGoals     []string           `json:"non_goals"`
	Convergence  string             `json:"convergence"` // JMESPath, bool
	Escalation   []EscalationClause `json:"escalation"`
}

// EscalationClause routes an out-of-bounds Loop state to a handler: emit a
// human-attention envelope, skip the iteration, or fail the Loop outright.
type EscalationClause struct {
	Predicate string `json:"predicate"` // JMESPath, bool
	Action    string `json:"action"`    // "human" | "skip" | "fail"
}

// StopCondition is a hard cap evaluated between iterations. If any matches,
// the Loop stops with a corresponding status.
type StopCondition struct {
	Predicate string `json:"predicate"` // JMESPath, bool against envelope
	Reason    string `json:"reason"`    // operator-facing tag, e.g. "diff_too_large"
}

// LoopBody is the flowchart that runs each iteration. Nodes are units of
// execution; Edges connect them and may carry predicates that select branches
// based on the envelope emitted by the source Node.
type LoopBody struct {
	Nodes []Node `json:"nodes"`
	Edges []Edge `json:"edges"`
}

// Node is one unit of execution inside a Loop body. Kind selects the runtime
// shape (agent invocation, brainbox playbook, join of upstream branches, human
// review pause, scheduled wait). Executor selects the runtime backend for
// agent/playbook nodes — host-cli is wired today; brainbox-session and
// a2a-remote are forward-compat slots.
//
// Requires lists permission scopes the Node needs (consulted only in the
// "strict" permission tier; in "default" tier, destructive scopes still
// require explicit listing here).
type Node struct {
	ID         string   `json:"id"`
	Kind       string   `json:"kind"`     // "agent" | "playbook" | "join" | "human" | "schedule"
	Executor   string   `json:"executor"` // "host-cli" | "brainbox-session" | "a2a-remote"
	Role       string   `json:"role"`    // references brainbox/agents/roles/*.md by name
	AgentID    string   `json:"agent_id"`
	PlaybookID string   `json:"playbook_id"`
	Prompt     string   `json:"prompt"`
	Requires   []string `json:"requires"`
	TimeoutMs  int      `json:"timeout_ms"`
}

// Edge connects two Nodes in a Loop body. Predicate (JMESPath, bool) decides
// whether the edge fires against the envelope emitted by From; omitted
// predicate means always-fire. Transform projects/strips fields from the
// envelope before handing off to To — useful at trust boundaries (e.g. strip
// scope_grants before crossing a2a-remote).
type Edge struct {
	From      string         `json:"from"`
	To        string         `json:"to"`
	Predicate string         `json:"predicate,omitempty"`
	Transform *EdgeTransform `json:"transform,omitempty"`
}

// EdgeTransform is a declarative envelope projection. Day-1 supports:
//
//   - Select: keep only the listed JMESPath-rooted fields
//   - Omit:   drop the listed fields
//   - Merge:  merge a literal JSON object into the envelope
//
// Selecting and omitting are mutually exclusive on a given edge; Merge can
// combine with either.
type EdgeTransform struct {
	Select []string               `json:"select,omitempty"`
	Omit   []string               `json:"omit,omitempty"`
	Merge  map[string]interface{} `json:"merge,omitempty"`
}

// TemplateSnapshot is pinned onto each LoopInstance at creation. The runner
// reads only from the snapshot for the duration of the Loop's life; on-disk
// template changes after creation never affect in-flight instances. Role
// markdown referenced by Nodes does live-bind — see the plan for the
// rationale (templates change freely, roles change deliberately).
type TemplateSnapshot struct {
	Name     string `json:"name"`
	Version  string `json:"version"`
	Hash     string `json:"hash"`
	BodyJSON string `json:"body_json"` // full Loop config at snapshot time
}

// HandoffEnvelope is the unifying handoff primitive. Every Node-to-Node,
// iteration-to-iteration, Loop-to-Loop, and cross-runtime handoff carries one
// of these. The envelope is additive-only — never remove or repurpose a
// field, only add optional ones. SchemaVersion is for cross-runtime
// compatibility checks (e.g. an A2A remote agent declaring "accepts schema
// >=3"); the runner does not do migrations on load.
//
// Embed-with-overflow: blobs over ~100KB go to artifact storage and the
// envelope carries a pointer in ArtifactRefs.
//
// JMESPath-queryable: edge predicates, the convergence predicate, stop
// conditions, join conditions, and the convergence metric all evaluate
// against this shape using the same language.
type HandoffEnvelope struct {
	SchemaVersion int                    `json:"schema_version"`
	LoopID        string                 `json:"loop_id"`
	Iteration     int                    `json:"iteration"`
	FromNode      string                 `json:"from_node"`
	ToNode        string                 `json:"to_node"`
	ArtifactRefs  map[string]interface{} `json:"artifact_refs,omitempty"`
	Observations  map[string]interface{} `json:"observations,omitempty"`
	Findings      map[string]interface{} `json:"findings,omitempty"`
	MemoryRefs    []string               `json:"memory_refs,omitempty"`
	TraceID       string                 `json:"trace_id,omitempty"`
	ScopeGrants   map[string]interface{} `json:"scope_grants,omitempty"`
	ContextCarry  map[string]interface{} `json:"context_carry,omitempty"`
}

// EnvelopeSchemaVersion is the current additive schema version stamped onto
// freshly-emitted envelopes. Bump only when adding fields, never on
// repurpose or removal. Day-1: v1.
const EnvelopeSchemaVersion = 1

// Permission tiers — see plan for semantics. Default applies when a Loop
// declares no tier.
const (
	PermissionInherit = "inherit"
	PermissionDefault = "default"
	PermissionStrict  = "strict"
)

// Node kinds.
const (
	NodeKindAgent    = "agent"
	NodeKindPlaybook = "playbook"
	NodeKindJoin     = "join"
	NodeKindHuman    = "human"
	NodeKindSchedule = "schedule"
)

// Node executors.
const (
	ExecutorHostCLI         = "host-cli"
	ExecutorBrainboxSession = "brainbox-session"
	ExecutorA2ARemote       = "a2a-remote"
)

// Escalation actions.
const (
	EscalationHuman = "human"
	EscalationSkip  = "skip"
	EscalationFail  = "fail"
)
