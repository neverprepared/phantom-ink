"""The validated record that stage C acts on.

This module is the whole trust boundary in code form. Stage B hands over
whatever the model produced; nothing downstream ever sees the original prose.
Every field is range-checked here, so the worst an injected instruction can
achieve is a wrong-but-well-formed title -- never an action.

Frontmatter the human wrote is trusted and takes precedence over any extracted
value. An item whose frontmatter supplies everything needed skips the model
entirely.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_TITLE = 200
MAX_SUMMARY = 500
MAX_TAGS = 8
MAX_TAG_LEN = 32
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

PRIORITIES = ("low", "medium", "high")

# A due date must be plausible: in the future, and not so far out that a
# hallucinated year ("2525") silently creates a reminder nobody will ever see.
MAX_DUE_HORIZON = dt.timedelta(days=730)
# Small grace so an item captured minutes ago with a "now"-ish time still lands.
DUE_PAST_GRACE = dt.timedelta(minutes=10)


class ValidationError(ValueError):
    """The verdict is unusable. The item is quarantined, not applied."""


def local_zone() -> dt.tzinfo:
    """The machine's IANA time zone, not a snapshot of its current offset.

    This has to be a real zone rather than `datetime.now().astimezone().tzinfo`,
    which is a FIXED offset for today. Stamping a naive "2026-11-14T09:00" with
    today's PDT (-07:00) when the target date is in PST (-08:00) shifts the
    reminder an hour earlier -- a 9am reminder that fires at 8am. A ZoneInfo
    computes the offset for the target date instead.
    """
    tz_env = os.environ.get("TZ")
    if tz_env:
        try:
            return ZoneInfo(tz_env)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    # macOS and most Linux distros symlink /etc/localtime into the tz database,
    # so the trailing path components are the IANA key.
    try:
        target = Path("/etc/localtime").resolve()
        parts = target.parts
        if "zoneinfo" in parts:
            key = "/".join(parts[parts.index("zoneinfo") + 1 :])
            if key:
                return ZoneInfo(key)
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    # Last resort: today's offset. Wrong across a DST boundary, but better than
    # treating the value as UTC.
    return dt.datetime.now().astimezone().tzinfo or dt.UTC


@dataclass
class Verdict:
    item_id: str
    title: str
    due_at: dt.datetime | None = None
    priority: str | None = None
    tags: list[str] = field(default_factory=list)
    actionable: bool = False
    summary: str = ""

    def due_iso(self) -> str | None:
        """ISO-8601 with an explicit offset, which is what reminders_add wants."""
        if self.due_at is None:
            return None
        return self.due_at.isoformat()


# -- field coercion ------------------------------------------------------------


def _as_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"title must be a string, got {type(value).__name__}")
    title = " ".join(value.split())
    if not title:
        raise ValidationError("title is empty")
    if len(title) > MAX_TITLE:
        raise ValidationError(f"title exceeds {MAX_TITLE} chars")
    return title


def _as_summary(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"summary must be a string, got {type(value).__name__}")
    return value.strip()[:MAX_SUMMARY]


def _as_priority(value: Any) -> str | None:
    if value is None or value == "" or value == "none":
        return None
    if not isinstance(value, str):
        raise ValidationError(f"priority must be a string, got {type(value).__name__}")
    low = value.strip().lower()
    if low not in PRIORITIES:
        raise ValidationError(f"priority {value!r} not in {PRIORITIES}")
    return low


def _as_tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [t for t in re.split(r"[,\s]+", value) if t]
    if not isinstance(value, list):
        raise ValidationError(f"tags must be a list, got {type(value).__name__}")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValidationError("tags must all be strings")
        tag = raw.strip().lower().replace(" ", "-").replace("_", "-")
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise ValidationError(f"tag {tag!r} exceeds {MAX_TAG_LEN} chars")
        if not TAG_RE.match(tag):
            raise ValidationError(f"tag {tag!r} is not [a-z0-9-]")
        if tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValidationError(f"more than {MAX_TAGS} tags")
    return out


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    # A model asked for JSON routinely answers "true"/"yes"; accept the obvious
    # spellings and reject everything else rather than guessing truthiness.
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes"):
            return True
        if low in ("false", "no", ""):
            return False
    if value is None:
        return False
    raise ValidationError(f"{field_name} must be a boolean, got {value!r}")


def parse_due(value: Any, *, now: dt.datetime | None = None) -> dt.datetime | None:
    """Parse and sanity-check a due date.

    Accepts ISO-8601 with or without an offset; a naive value is interpreted in
    the machine's local zone, which is what someone writing "2026-10-23T09:00"
    in a note means.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time(9, 0))
    elif isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(f"due_at {value!r} is not ISO-8601") from exc
    else:
        raise ValidationError(f"due_at must be a string or date, got {type(value).__name__}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_zone())

    ref = now or dt.datetime.now(dt.UTC)
    if parsed < ref - DUE_PAST_GRACE:
        raise ValidationError(f"due_at {parsed.isoformat()} is in the past")
    if parsed > ref + MAX_DUE_HORIZON:
        raise ValidationError(
            f"due_at {parsed.isoformat()} is beyond the {MAX_DUE_HORIZON.days}d horizon"
        )
    return parsed


# -- assembly ------------------------------------------------------------------


def needs_model(queue: str, frontmatter: dict[str, Any]) -> bool:
    """True when frontmatter alone cannot produce a valid verdict.

    Keeping this honest is what lets a disciplined capture ("due:" written by
    hand) bypass the model and its failure modes entirely.
    """
    if not frontmatter.get("title"):
        return True
    return bool(queue == "reminders" and not frontmatter.get("due"))


def from_frontmatter(
    item_id: str, queue: str, frontmatter: dict[str, Any], *, now: dt.datetime | None = None
) -> Verdict:
    """Build a verdict from trusted frontmatter only. No model involved."""
    due = parse_due(frontmatter.get("due"), now=now)
    if queue == "reminders" and due is None:
        raise ValidationError("reminders require a due date")
    return Verdict(
        item_id=item_id,
        title=_as_title(frontmatter.get("title")),
        due_at=due,
        priority=_as_priority(frontmatter.get("priority")),
        tags=_as_tags(frontmatter.get("tags")),
        # An explicitly captured idea with frontmatter is taken at its word;
        # absent the key, default to not-actionable so nothing is promoted to a
        # todo by accident.
        actionable=_as_bool(frontmatter.get("actionable"), field_name="actionable"),
        summary="",
    )


def validate(
    raw: Any,
    *,
    queue: str,
    allowed_ids: set[str],
    frontmatter: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> Verdict:
    """Validate one model-produced verdict against a pending item.

    `allowed_ids` is the set of item_ids actually in the spool: it rejects a
    verdict for an item the model invented, which is the cheapest guard against
    a batch response that has drifted from the request.
    """
    if not isinstance(raw, dict):
        raise ValidationError(f"verdict must be an object, got {type(raw).__name__}")

    item_id = raw.get("item_id")
    if not isinstance(item_id, str) or item_id not in allowed_ids:
        raise ValidationError(f"item_id {item_id!r} is not a pending item")

    fm = frontmatter or {}

    # Frontmatter wins, field by field.
    title = _as_title(fm["title"]) if fm.get("title") else _as_title(raw.get("title"))
    due_src = fm.get("due") if fm.get("due") else raw.get("due_at")
    due = parse_due(due_src, now=now)
    priority = _as_priority(fm.get("priority") if fm.get("priority") else raw.get("priority"))
    tags = _as_tags(fm.get("tags") if fm.get("tags") else raw.get("tags"))
    actionable = _as_bool(
        fm["actionable"] if "actionable" in fm else raw.get("actionable"),
        field_name="actionable",
    )

    if queue == "reminders" and due is None:
        # A reminder with no time is not a reminder. Quarantine rather than
        # creating a dateless one that will never surface.
        raise ValidationError("reminders require a due date; none supplied or parseable")

    return Verdict(
        item_id=item_id,
        title=title,
        due_at=due,
        priority=priority,
        tags=tags,
        actionable=actionable,
        summary=_as_summary(raw.get("summary")),
    )
