"""First-class Git worktree management for phantom-ink.

Worktrees are independent checkouts of a repo branch. They persist
beyond sessions so branches survive for PR review or continued work.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from .log import get_logger
from .models import Repository, Worktree

log = get_logger()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_worktrees: dict[str, Worktree] = {}
_listeners: list[Callable[[str, object], None]] = []


# ---------------------------------------------------------------------------
# SSE event bus
# ---------------------------------------------------------------------------


def on_event(fn: Callable[[str, object], None]) -> None:
    _listeners.append(fn)


def _emit(event: str, data: object) -> None:
    for fn in list(_listeners):
        try:
            fn(event, data)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Local path resolution
# ---------------------------------------------------------------------------


def resolved_local_path(repo: Repository) -> str:
    """Return the host filesystem path of the repo's working tree.

    Defaults to ``{workspace_home}/code/{repo.name}/``.
    Override via ``repo.local_path_override``.
    """
    if repo.local_path_override:
        return repo.local_path_override
    if repo.workspace_home:
        return os.path.join(repo.workspace_home, "code", repo.name)
    raise ValueError(
        f"Cannot resolve local path for '{repo.name}': "
        "set workspace_home when registering the repo, or add local_path_override"
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_worktree(repo_name: str, branch: str) -> Worktree:
    """Run ``git worktree add`` and register the result.

    Creates a new branch ``branch`` inside the repo's local checkout.
    The worktree directory is placed at::

        {repo_parent}/.worktrees/{repo_name}-{branch}-{id}/

    so it is a sibling of the repo directory, outside the source tree.
    """
    from .router import get_repo  # avoid circular import at module level

    repo = get_repo(repo_name)
    if repo is None:
        raise ValueError(f"Repository '{repo_name}' not found")

    local_path = resolved_local_path(repo)
    if not Path(local_path).exists():
        raise ValueError(
            f"Local path '{local_path}' does not exist. "
            "Check workspace_home or set local_path_override on the repo."
        )

    wt_id = uuid.uuid4().hex[:8]
    # Sanitise branch name for use in directory path
    safe_branch = branch.replace("/", "_").replace(" ", "_")
    wt_dir = Path(local_path).parent / ".worktrees" / f"{repo_name}-{safe_branch}-{wt_id}"
    wt_path = str(wt_dir)

    try:
        subprocess.run(
            ["git", "-C", local_path, "worktree", "add", "-B", branch, wt_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        log.warning("worktree.create_failed", metadata={"repo": repo_name, "branch": branch, "error": err})
        raise ValueError(f"git worktree add failed: {err}") from exc

    wt = Worktree(
        id=wt_id,
        repo_name=repo_name,
        branch=branch,
        worktree_path=wt_path,
    )
    _worktrees[wt_id] = wt
    log.info("worktree.created", metadata={"id": wt_id, "repo": repo_name, "branch": branch, "path": wt_path})
    _emit("worktree.created", wt)
    return wt


def list_worktrees(repo_name: str | None = None) -> list[Worktree]:
    if repo_name is None:
        return list(_worktrees.values())
    return [wt for wt in _worktrees.values() if wt.repo_name == repo_name]


def get_worktree(id: str) -> Worktree | None:
    return _worktrees.get(id)


def delete_worktree(id: str) -> None:
    """Remove the worktree from disk and deregister it."""
    wt = _worktrees.pop(id, None)
    if wt is None:
        raise ValueError(f"Worktree '{id}' not found")

    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt.worktree_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        log.warning("worktree.remove_failed", metadata={"id": id, "path": wt.worktree_path, "error": err})
        # Don't re-raise — the record is already gone; best-effort cleanup

    log.info("worktree.deleted", metadata={"id": id, "repo": wt.repo_name, "branch": wt.branch})
    _emit("worktree.deleted", {"id": id, "repo_name": wt.repo_name, "branch": wt.branch})


def attach_session(id: str, session_name: str) -> None:
    """Mark a worktree as in-use by a session."""
    wt = _worktrees.get(id)
    if wt is None:
        raise ValueError(f"Worktree '{id}' not found")
    wt.session_name = session_name
    wt.status = "in_use"
    _emit("worktree.updated", wt)


def detach_session(id: str) -> None:
    """Mark a worktree as available again after its session ends."""
    wt = _worktrees.get(id)
    if wt is None:
        return  # already gone, ignore
    wt.session_name = None
    wt.status = "ready"
    _emit("worktree.updated", wt)


# ---------------------------------------------------------------------------
# Hub state persistence
# ---------------------------------------------------------------------------


def get_state() -> dict:
    return {wt_id: wt.model_dump() for wt_id, wt in _worktrees.items()}


def restore_state(state: dict | None) -> None:
    if not state:
        return
    for wt_id, data in state.items():
        try:
            wt = Worktree(**data)
            # Only restore if the path still exists on disk
            if Path(wt.worktree_path).exists():
                _worktrees[wt_id] = wt
            else:
                log.warning(
                    "worktree.restore_skipped",
                    metadata={"id": wt_id, "path": wt.worktree_path, "reason": "path not found"},
                )
        except Exception as exc:
            log.warning("worktree.restore_error", metadata={"id": wt_id, "error": str(exc)})
