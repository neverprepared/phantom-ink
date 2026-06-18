---
name: pr-review-loop
version: "0.1.0"
description: |
  Review-driven repair loop. Reviewer reads the PR, emits structured findings
  (blockers + suggested fixes). Loop iterates until blockers reach zero and
  CI is green, or until iteration / diff caps trip. First fully-automated
  loop for phantom-ink — Phase B of the loop-engineering plan.
intent:
  outcome: This PR should be mergeable per repo conventions and the linked issue.
  verification:
    - gh pr checks $pr_number --watch
    - all reviewer blockers resolved
  non_goals:
    - modifying files outside the PR's changed paths
    - merging the PR (merge-queue handles that separately)
  convergence: "length(findings.blockers) == `0` && observations.ci_status == 'green'"
  escalation:
    - predicate: "iteration >= `3`"
      action: human
body:
  nodes:
    - id: reviewer
      kind: agent
      executor: brainbox-session
      role: reviewer
      prompt: |
        Review the pull request referenced in your handoff envelope's
        artifact_refs (pr_number, repo, head_sha). Emit a final
        HandoffEnvelope at the end of your session with the schema:

          findings:
            blockers: [{file, line, reason, suggested_fix?}, ...]
            approved: bool
          observations:
            ci_status: "green" | "red" | "pending"
            diff_lines: int

        See `brainbox/agents/roles/reviewer.md` for the full reviewer
        contract. Read prior iterations from the envelope's findings
        history if it's not iteration 1.
      requires:
        - repo:read
      timeout_ms: 600000
  edges: []
convergence_predicate: "length(findings.blockers) == `0` && observations.ci_status == 'green'"
convergence_metric: "length(findings.blockers)"
max_iterations: 3
stop_conditions:
  - predicate: "observations.diff_lines > `500`"
    reason: diff_too_large
permissions: default
required_refs:
  - name: pr_number
    type: int
    description: GitHub PR number on the target repository
  - name: repo
    type: string
    description: owner/name slug (e.g. neverprepared/phantom-ink)
  - name: head_sha
    type: sha
    description: PR head commit SHA — auto-populated by the webhook on PR events
    required: false
---

# pr-review-loop

A review-driven repair loop for incoming PRs. The first loop phantom-ink
runs end-to-end, and the first place we exercise loop engineering on real
software work.

## When to use this template

- A PR has opened or been updated on a repo the operator has opted into.
- The PR is small enough to be reviewable in one pass (diff under ~500 lines
  — past that, the `diff_too_large` stop condition fires by design).
- The reviewing agent has read access to the repo and to CI.

## What this loop does NOT do

- **Merge.** That's the merge-queue agent's job; this loop only converges
  on "should be mergeable" and hands off.
- **Repair.** Phase B1 ships a single-node review-only iteration. The
  worker node that applies suggested fixes lands when multi-node flowchart
  traversal lands (post-A3b).
- **Author commits.** No write access in `permissions: default`; the
  reviewer reads and emits findings only.

## Convergence and the metric

- **Convergence predicate** — `length(findings.blockers) == 0 && observations.ci_status == 'green'`. Both must be true. A reviewer who reports zero blockers on a PR with red CI does NOT converge; CI is the second signal that addresses Kilo's "overfitting to tests" failure mode in reverse — we don't trust the agent alone, we require the world to agree.
- **Convergence metric** — `length(findings.blockers)`. Charted per iteration in the future Loops Panel. Non-decreasing across two consecutive iterations triggers thrash detection.

## Stop conditions

- `observations.diff_lines > 500` → `diff_too_large`. A PR that grew past
  the cap during the loop's lifetime stops; the operator decides whether
  to split it and retry.

## Escalation

- `iteration >= 3` → `human`. The default cap is conservative; once we have
  data from real loops we'll tune it. Human escalation fires a
  `bus.attention` envelope through the existing attention pipeline.

## Permissions

`default` tier:
- Reviewer node inherits read-level profile scopes (`repo:read` is
  explicitly required).
- No write scopes inherited — the reviewer cannot push, merge, or
  modify files. This is the prompt-injection mitigation: the most
  attacker-exposed node (it reads the PR description) has the least
  blast radius.
