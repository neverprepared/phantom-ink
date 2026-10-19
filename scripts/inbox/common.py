"""Shared core for the Drive inbox pipeline.

Three queues (reminders / todo / ideas) per workspace profile. A Drive folder is
the capture front door; this module holds everything both stages need: config,
the spool, the idempotency ledger, frontmatter parsing, the mechanical secret
scan, and the collect-job stdout contract.

The pipeline is split so that the component which reads untrusted Drive content
(stage B, extract) holds no credentials and has no tools. See
docs/architecture/ADR-006-drive-inbox-queues.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Shape of a queue ───────────────────────────────────────────────────────────

KIND_REMINDER = "reminder"
KIND_TODO = "todo"
KIND_IDEA = "idea"

# What an item IS, not where it lives. There is one flat inbox; the kind is
# detected during extraction (or declared explicitly, see declared_kind below)
# rather than encoded in a folder path, so capture needs no taxonomy.
KINDS = (KIND_REMINDER, KIND_TODO, KIND_IDEA)

# Accepted spellings for an explicit override, mapped onto the canonical kind.
KIND_ALIASES = {
    "reminder": KIND_REMINDER,
    "reminders": KIND_REMINDER,
    "r": KIND_REMINDER,
    "todo": KIND_TODO,
    "todos": KIND_TODO,
    "task": KIND_TODO,
    "t": KIND_TODO,
    "idea": KIND_IDEA,
    "ideas": KIND_IDEA,
    "note": KIND_IDEA,
    "i": KIND_IDEA,
}

# One flat tree per profile. A filed item is TRASHED, not archived, so there is
# no `done`: Drive is transit, not storage. `rejected` and `quarantine` remain
# real folders and are never deleted -- a refused capture that got deleted
# would simply be lost. They are distinct because the fixes differ: `rejected`
# means the FILE was unacceptable (a stage-A mechanical gate), `quarantine`
# that its CONTENT could not be parsed into a valid item (stage B).
LIFECYCLE = ("inbox", "processing", "quarantine", "rejected")

SCHEMA_VERSION = 1

# Per-run caps. Both stages run as collect jobs under a hard 60s subprocess
# timeout (app/collect.go:793), so neither may be unbounded.
DRAIN_CAP = 25
APPLY_CAP = 10

# Fail the model call inside the 60s budget, so a slow extract cannot get the
# process killed partway through stage C.
LLM_TIMEOUT_S = 35.0

MAX_BODY_BYTES = 256 * 1024

# Google Docs export as markdown; the other two are read verbatim.
MIME_DOC = "application/vnd.google-apps.document"
ALLOWED_MIMES = {MIME_DOC, "text/markdown", "text/plain", "text/x-markdown"}

STALE_PROCESSING_S = 3600


# ── Config ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """Per-profile configuration, resolved from the environment.

    The app delivers the profile .env to a collect job via resolveProfileEnv
    (app/app_dashboard.go:138), so these are simply environment reads with
    defaults; there is no new config system.
    """

    profile: str
    workspace_home: Path
    google_account: str
    drive_root: str
    reminders_list: str
    router_api: str
    router_key: str
    brain_api: str
    brain_token: str
    todo_token: str

    @property
    def spool_dir(self) -> Path:
        return self.workspace_home / ".phantom-ink" / "inbox"

    @property
    def pending_dir(self) -> Path:
        return self.spool_dir / "pending"

    @property
    def ledger_path(self) -> Path:
        return self.spool_dir / "ledger.jsonl"


def load_config(profile: str | None = None, env: dict[str, str] | None = None) -> Config:
    e = dict(os.environ if env is None else env)
    prof = profile or e.get("WORKSPACE_PROFILE") or e.get("CL_WORKSPACE_PROFILE") or ""
    if not prof:
        raise ConfigError("no profile: pass --profile or set WORKSPACE_PROFILE")

    home = e.get("WORKSPACE_HOME", "")
    if not home:
        # Convention path. Keeps the script runnable from a plain shell where
        # only --profile was given.
        home = str(Path.home() / "workspaces" / "profiles" / prof)
    return Config(
        profile=prof,
        workspace_home=Path(home),
        google_account=e.get("CL_INBOX_GOOGLE_ACCOUNT", "neuralcolony@gmail.com"),
        drive_root=e.get("CL_INBOX_DRIVE_ROOT", "phantom-ink"),
        reminders_list=e.get("CL_INBOX_REMINDERS_LIST", f"phantom-{prof}"),
        router_api=e.get("CL_ROUTER_API", "").rstrip("/"),
        router_key=e.get("CL_API_KEY", ""),
        brain_api=e.get("CL_BRAIN_API", "").rstrip("/"),
        brain_token=e.get("CL_BRAIN_API_TOKEN", ""),
        todo_token=e.get("CL_TODO_API_TOKEN", ""),
    )


class ConfigError(RuntimeError):
    """Configuration is missing or unusable; the job should fail loudly."""


# ── Secret scan ────────────────────────────────────────────────────────────────

# Mechanical, not clever. The point is that a credential pasted into a capture
# note can never be `learn`ed into a vault, where it would be synthesised,
# indexed and recalled forever.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("64-hex", re.compile(r"\b[0-9a-fA-F]{64}\b")),
    ("openai-sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("github-pat", re.compile(r"\b(?:ghp|ghs|gho|ghu)_[A-Za-z0-9]{20,}")),
    ("github-fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws-akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
)


def scan_secrets(text: str) -> list[str]:
    """Return the names of every secret pattern present in text."""
    return [name for name, pat in SECRET_PATTERNS if pat.search(text)]


# ── Frontmatter ────────────────────────────────────────────────────────────────

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)

# Only these are honoured. Frontmatter the human wrote is TRUSTED and wins over
# anything the model extracts, so the accepted set is closed on purpose.
TRUSTED_FM_KEYS = frozenset({"title", "due", "priority", "tags", "actionable", "type"})

# A filename may declare the kind: "r: call the accountant" or
# "idea - nightly rollup". This exists because frontmatter is awkward from a
# phone share-sheet, where the filename is often the only thing you get to type.
#
# The separator must be a colon, or a hyphen with spaces around it. A bare
# hyphen or underscore is far too permissive: it turns the ordinary filename
# "ideas-are-great.md" into kind=idea plus the title "are-great".
_ALIAS_ALT = "|".join(sorted(KIND_ALIASES, key=len, reverse=True))
_FILENAME_KIND_RE = re.compile(rf"^\s*({_ALIAS_ALT})\s*(?::|\s-\s)\s*", re.IGNORECASE)


def normalise_kind(value: Any) -> str:
    """Map a declared kind onto a canonical one, or "" if unrecognised."""
    if not isinstance(value, str):
        return ""
    return KIND_ALIASES.get(value.strip().lower().lstrip("#"), "")


def kind_from_filename(name: str) -> tuple[str, str]:
    """Split a leading kind marker off a filename.

    Returns (kind, remaining-name). An unmarked filename yields ("", name), so
    the model decides -- which is the common case.
    """
    stem = re.sub(r"\.(md|markdown|txt)$", "", name, flags=re.IGNORECASE)
    m = _FILENAME_KIND_RE.match(stem)
    if not m:
        return "", stem.strip()
    return normalise_kind(m.group(1)), stem[m.end() :].strip()


def declared_kind(frontmatter: dict[str, Any], filename: str) -> str:
    """The kind the human declared, if any. Frontmatter beats the filename."""
    return normalise_kind(frontmatter.get("type")) or kind_from_filename(filename)[0]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML frontmatter from the body.

    Returns (frontmatter, body). Unparseable or absent frontmatter yields an
    empty dict and the original text — a malformed header must not lose the
    note, which is the whole point of the capture.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end() :]
    try:
        import yaml

        raw = yaml.safe_load(m.group(1))
    # A malformed YAML header must not lose the note -- treat the whole
    # text as the body instead.
    except Exception:
        return {}, text
    if not isinstance(raw, dict):
        return {}, text
    fm = {k: v for k, v in raw.items() if k in TRUSTED_FM_KEYS and v is not None}
    return fm, body


# ── Item identity and the spool ────────────────────────────────────────────────


def item_id_for(body: str) -> str:
    """Stable id for an item: first 16 hex of the sha256 of its body.

    Content-addressed so re-dropping the same note is recognised as the same
    item even under a different filename.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


@dataclass
class SpoolRecord:
    item_id: str
    profile: str
    # The kind the human DECLARED, via frontmatter `type:` or a filename
    # marker. Empty is the normal case: there is no folder to read it from any
    # more, so an unmarked item's kind is settled during extraction.
    declared_kind: str
    drive_file_id: str
    drive_name: str
    mime: str
    captured_at: str
    drained_at: str
    body: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    schema: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpoolRecord:
        known = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**known)


def write_spool(cfg: Config, rec: SpoolRecord) -> Path:
    """Write a pending record atomically (temp + rename in the same dir)."""
    cfg.pending_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.pending_dir / f"{rec.item_id}.json"
    fd, tmp = tempfile.mkstemp(dir=str(cfg.pending_dir), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rec.to_json())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def read_pending(cfg: Config, limit: int | None = None) -> list[SpoolRecord]:
    """Oldest-first pending records, so a backlog drains in capture order."""
    if not cfg.pending_dir.is_dir():
        return []
    paths = sorted(
        (p for p in cfg.pending_dir.glob("*.json") if not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
    )
    out: list[SpoolRecord] = []
    for p in paths if limit is None else paths[:limit]:
        try:
            out.append(SpoolRecord.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            # A corrupt spool file is not worth stalling the queue over; move it
            # aside so it is visible rather than retried forever.
            p.rename(p.with_suffix(".corrupt"))
    return out


def drop_spool(cfg: Config, item_id: str) -> None:
    (cfg.pending_dir / f"{item_id}.json").unlink(missing_ok=True)


# ── Ledger ─────────────────────────────────────────────────────────────────────


def ledger_ids(cfg: Config) -> set[str]:
    """Every item_id already applied. Cheap enough to read per run."""
    if not cfg.ledger_path.is_file():
        return set()
    ids: set[str] = set()
    for line in cfg.ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line)["item_id"])
        # A torn ledger line must not hide every other applied item.
        except Exception:
            continue
    return ids


def ledger_append(cfg: Config, entry: dict[str, Any]) -> None:
    """Append one applied record, durably.

    Ordering is load-bearing: stage C appends here BEFORE moving the Drive file.
    A kill between the two leaves a file in processing/ that is already applied,
    which --requeue-stale resolves by sending it to done/. The reverse order
    would double-apply.
    """
    cfg.spool_dir.mkdir(parents=True, exist_ok=True)
    entry.setdefault("applied_at", utc_now_iso())
    with cfg.ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ── Time ───────────────────────────────────────────────────────────────────────


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_bucket() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


# ── collect-job stdout contract ────────────────────────────────────────────────

# runCollectCommand (app/collect.go:793) requires stdout to be a JSON array of
# entries, each with at least id/kind/title, and drops anything else. Emitting
# these is how drained items show up on the app timeline.

# The six display statuses the contract allows
# (app/internal/contract/timeline-entry.schema.json, $defs/EnvelopeStatus).
# Producers map their own lifecycle onto these six -- there is no "pending" and
# no "error". runCollectCommand does NOT police this, so an invented status is
# accepted here and only breaks downstream, where the attention system reads it.
# tests/test_contract.py asserts these against the in-repo schema so the mapping
# cannot drift.
STATUS_UPCOMING = "upcoming"  # drained, queued for extraction
STATUS_ACTIVE = "active"  # in progress
STATUS_DONE = "done"  # filed successfully (terminal)
STATUS_FAILED = "failed"  # terminal: a mechanical gate refused the file
STATUS_BLOCKED = "blocked"  # a target is down; the item will be retried
STATUS_NEEDS_ACTION = "needs_action"  # quarantined: the human must fix the note

ENTRY_STATUSES = frozenset(
    {
        STATUS_UPCOMING,
        STATUS_ACTIVE,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_BLOCKED,
        STATUS_NEEDS_ACTION,
    }
)


def entry(
    item_id: str,
    kind: str,
    title: str,
    *,
    description: str = "",
    status: str = STATUS_ACTIVE,
    tags: list[str] | None = None,
    url: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "title": title[:200],
        "description": description[:500],
        "status": status,
    }
    if tags:
        e["tags"] = tags
    if url:
        e["url"] = url
    if metadata:
        e["metadata"] = metadata
    return e


# A stage's status row. collected_entries is keyed PRIMARY KEY (job_id,
# entry_id) and upserted ON CONFLICT (app/collect.go:504-508), so a STABLE
# entry_id gives one always-current row per job rather than 96 rows a day.
#
# This exists because a successful collect job that emits no items is
# completely invisible: stdout is parsed and discarded, and stderr only
# surfaces in last_error on a non-zero exit. "Ran, nothing to do" looked
# identical to "never ran", which caused two misdiagnoses while building this.
STATUS_ENTRY_PREFIX = "inbox-status"


def status_entry(
    stage: str,
    *,
    ok: bool,
    summary: str,
    counts: dict[str, int] | None = None,
    attention: bool = False,
) -> dict[str, Any]:
    """One upserted row describing this stage's most recent run.

    `ok=False` marks a run that failed outright; `attention` marks a successful
    run that produced items a human needs to look at (rejected or quarantined),
    so the timeline surfaces it without anyone scanning the item list.
    """
    counts = {k: v for k, v in (counts or {}).items() if v}
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to do"
    if not ok:
        status = STATUS_FAILED
    elif attention:
        status = STATUS_NEEDS_ACTION
    else:
        status = STATUS_DONE
    return entry(
        f"{STATUS_ENTRY_PREFIX}-{stage}",
        "inbox_run",
        f"Drive inbox {stage}: {summary}",
        description=f"last run {utc_now_iso()} — {detail}",
        status=status,
        tags=["inbox", "run", stage],
        metadata={"stage": stage, "ok": ok, **counts},
    )


def emit(entries: list[dict[str, Any]]) -> None:
    """Print the entries array on stdout. Must be the ONLY thing on stdout."""
    json.dump(entries, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for the entries contract.

    Stage A surfaces stderr in the job last_error only on a non-zero exit, so
    this is for interactive runs and post-mortems.
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[inbox {ts}] {msg}", file=sys.stderr)
