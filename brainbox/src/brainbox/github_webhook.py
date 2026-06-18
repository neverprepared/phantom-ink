"""GitHub webhook handling — Phase C of the loop-engineering plan.

Pure logic, no FastAPI dependency. The HTTP route in api.py orchestrates:

  body = await request.read()
  if not verify_signature(body, header, secret): → 401
  if not allow_repo(payload, allowed): → 403
  trigger = extract_loop_trigger(event_type, payload)
  if trigger is None: → 422 ("event understood but not a loop trigger")
  else: start_loop(template, envelope_from(trigger))

Three trigger shapes we care about for the pr-review-loop today:

  - ``pull_request.opened``           → trigger on every new PR
  - ``pull_request.synchronize``      → trigger when a PR's HEAD moves
  - ``issue_comment.created`` with    → operator opt-in: comment "/loop"
    body containing "/loop"             on a PR re-fires the loop

Other events (push, fork, star, etc.) return None and the route replies
422 — the webhook is intentionally registered for the broad surface, but
we only act on the narrow subset.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_signature(body: bytes, header_value: str, secret: str) -> bool:
    """Verify a GitHub X-Hub-Signature-256 header.

    GitHub formats the header as ``sha256=<hex_digest>``. Constant-time
    comparison via hmac.compare_digest. Empty secret rejects everything;
    operators must explicitly configure github_webhook_secret to enable.

    Returns True only when the secret is non-empty AND the digest matches.
    """
    if not secret:
        return False
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = header_value[len("sha256="):]
    return hmac.compare_digest(expected, provided)


# ---------------------------------------------------------------------------
# Repo allowlist
# ---------------------------------------------------------------------------


def allow_repo(payload: dict[str, Any], allowed: list[str]) -> bool:
    """Check whether the payload's repository is in the allowed list.

    An empty allowed list means "accept any signed payload" — the operator
    trusts the webhook secret rotation. A non-empty list narrows to a
    specific set of ``owner/name`` strings.

    Missing repository.full_name in the payload (rare, but possible for
    org-level events) returns False when the allowlist is non-empty.
    """
    if not allowed:
        return True
    repo = (payload.get("repository") or {}).get("full_name") or ""
    return repo in allowed


# ---------------------------------------------------------------------------
# Trigger extraction
# ---------------------------------------------------------------------------


def extract_loop_trigger(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return an ``artifact_refs``-shaped dict to feed into a Loop's initial
    HandoffEnvelope, or None when the event isn't something we trigger on.

    The returned dict is the operator-visible record of why the loop fired
    — it identifies the PR by number, repo, and HEAD SHA. The loop runner
    stamps loop_id and iteration on top.
    """
    if event_type == "pull_request":
        return _trigger_from_pull_request(payload)
    if event_type == "issue_comment":
        return _trigger_from_issue_comment(payload)
    return None


_PR_TRIGGER_ACTIONS = {"opened", "synchronize", "reopened"}


def _trigger_from_pull_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    action = payload.get("action") or ""
    if action not in _PR_TRIGGER_ACTIONS:
        return None
    pr = payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name") or ""
    pr_number = pr.get("number") or payload.get("number")
    if not pr_number or not repo:
        return None
    return {
        "pr_number": pr_number,
        "repo": repo,
        "head_sha": (pr.get("head") or {}).get("sha") or "",
        "base_sha": (pr.get("base") or {}).get("sha") or "",
        "title": pr.get("title") or "",
        "trigger_event": f"pull_request.{action}",
    }


_LOOP_COMMENT_MARKER = "/loop"


def _trigger_from_issue_comment(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("action") != "created":
        return None
    issue = payload.get("issue") or {}
    # issue_comment events on issues that aren't PRs have no pull_request field.
    if "pull_request" not in issue:
        return None
    body = (payload.get("comment") or {}).get("body") or ""
    if _LOOP_COMMENT_MARKER not in body:
        return None
    repo = (payload.get("repository") or {}).get("full_name") or ""
    pr_number = issue.get("number")
    if not pr_number or not repo:
        return None
    return {
        "pr_number": pr_number,
        "repo": repo,
        # Comment events don't carry head SHA; the loop's reviewer will
        # resolve it from the live PR. Empty is acceptable — convergence
        # doesn't read base/head SHAs, only the per-iteration findings.
        "head_sha": "",
        "trigger_event": "issue_comment.loop",
        "comment_id": (payload.get("comment") or {}).get("id"),
    }
