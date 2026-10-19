# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-api-python-client>=2.140",
#   "google-auth>=2.33",
#   "pyyaml>=6.0",
# ]
# ///
"""Stages B and C -- extract fields from spooled items, then apply them.

Two stages in one job, with the trust boundary between them held in code: stage
B (extract.py) reads untrusted capture text and holds no credentials; stage C
(targets.py) holds the credentials and reads only a validated Verdict.

Run as a collect job (target_type "shell"):

    uv run --script scripts/inbox/extract_apply.py --profile "$WORKSPACE_PROFILE"

Stdout is the collect-job entries array (app/collect.go:793) and nothing else.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from common import (
    APPLY_CAP,
    ENTRY_KIND,
    KIND_REMINDER,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_NEEDS_ACTION,
    Config,
    ConfigError,
    SpoolRecord,
    drop_spool,
    emit,
    entry,
    ledger_append,
    ledger_ids,
    load_config,
    log,
    read_pending,
    status_entry,
)
from drive import Drive, open_drive
from extract import extract
from targets import Applied, ApplyError, Reminders, apply, drive_url
from verdict import Verdict


def quarantine_item(
    drive: Drive | None, cfg: Config, rec: SpoolRecord, reason: str
) -> dict[str, Any]:
    """Park an item that could not be extracted. Nothing is written anywhere.

    The Drive file moves out of processing/ so it stops being retried, and the
    reason is written beside it so the fix is obvious from the folder alone.
    """
    log(f"{rec.item_id} quarantine: {reason}")
    if drive is not None:
        try:
            folders = drive.resolve()
            drive.move(rec.drive_file_id, to=folders["quarantine"], from_=folders["processing"])
            drive.write_note(
                folders["quarantine"],
                f"{rec.drive_name}.quarantine.txt",
                f"item_id: {rec.item_id}\n"
                f"declared_kind: {rec.declared_kind or 'none'}\n"
                f"reason: {reason}\n",
            )
        # Visibility matters more than tidiness: the item is already
        # quarantined in the spool even if the Drive move fails.
        except Exception as exc:
            log(f"{rec.item_id}: quarantine move failed: {exc}")
    drop_spool(cfg, rec.item_id)
    return entry(
        rec.item_id,
        ENTRY_KIND,
        rec.drive_name,
        description=reason[:500],
        status=STATUS_NEEDS_ACTION,
        tags=["inbox", "quarantined"],
        url=drive_url(rec.drive_file_id),
    )


def apply_item(
    drive: Drive | None,
    cfg: Config,
    rec: SpoolRecord,
    v: Verdict,
    *,
    reminders: Reminders | None = None,
    hard_delete: bool = False,
) -> dict[str, Any]:
    """Apply one verdict, then dispose of its Drive file."""
    try:
        result: Applied = apply(cfg, rec, v, reminders=reminders)
    except ApplyError as exc:
        # Leave the spool record and the Drive file in place: a target that is
        # down (EventKit not granted, brain daemon restarting) should be retried
        # on the next tick, not quarantined as if the item were bad.
        log(f"{rec.item_id} [{v.kind}] apply failed, will retry: {exc}")
        return entry(
            rec.item_id,
            ENTRY_KIND,
            v.title,
            description=f"apply failed, will retry: {exc}"[:500],
            status=STATUS_BLOCKED,
            tags=["inbox", v.kind, "retry"],
            url=drive_url(rec.drive_file_id),
        )

    # Ledger BEFORE disposing of the file. A kill between the two leaves the
    # file in processing/ already applied, which --requeue-stale resolves by
    # disposing of it. The reverse order would double-apply on the next run.
    ledger_append(
        cfg,
        {
            "item_id": rec.item_id,
            "profile": cfg.profile,
            "kind": v.kind,
            "declared_kind": rec.declared_kind,
            "drive_file_id": rec.drive_file_id,
            "target": result.target,
            "result": result.result,
            "extra": result.extra,
            "title": v.title,
        },
    )
    drop_spool(cfg, rec.item_id)

    if drive is not None:
        try:
            drive.dispose(rec.drive_file_id, hard_delete=hard_delete)
        except Exception as exc:
            # Already applied and ledgered; a file left in processing/ is
            # recoverable by --requeue-stale, which consults the ledger.
            log(f"{rec.item_id}: disposal failed (already applied): {exc}")

    detail = f"{result.target} {result.result}"
    if result.extra:
        detail += f" ({result.extra})"
    log(f"{rec.item_id} [{v.kind}] applied -> {detail}")
    return entry(
        rec.item_id,
        ENTRY_KIND,
        v.title,
        description=detail[:500],
        status=STATUS_DONE,
        tags=["inbox", v.kind, "applied", *v.tags],
        url=drive_url(rec.drive_file_id),
        metadata={
            "kind": v.kind,
            "target": result.target,
            "result": result.result,
            "due_at": v.due_iso() or "",
            "actionable": v.actionable,
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract and apply spooled inbox items.")
    ap.add_argument(
        "--profile", default=None, help="workspace profile (default $WORKSPACE_PROFILE)"
    )
    ap.add_argument("--cap", type=int, default=APPLY_CAP, help=f"max items per run ({APPLY_CAP})")
    ap.add_argument(
        "--no-drive",
        action="store_true",
        help="apply without touching Drive (leaves files in processing/)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="extract and print verdicts without applying or moving anything",
    )
    ap.add_argument(
        "--hard-delete",
        action="store_true",
        help="permanently delete filed items instead of trashing them (not recoverable)",
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.profile)
    except ConfigError as exc:
        log(f"config error: {exc}")
        emit([status_entry("apply", ok=False, summary=f"config error: {exc}"[:120])])
        return 1

    records = read_pending(cfg, limit=max(args.cap, 1))
    if not records:
        emit([status_entry("apply", ok=True, summary="idle")])
        return 0

    # Content-addressed replay guard: a spool record for already-applied content
    # must not be applied twice.
    applied_ids = ledger_ids(cfg)
    fresh: list[SpoolRecord] = []
    for rec in records:
        if rec.item_id in applied_ids:
            log(f"{rec.item_id}: already in the ledger; dropping spool record")
            drop_spool(cfg, rec.item_id)
        else:
            fresh.append(rec)
    if not fresh:
        emit(
            [
                status_entry(
                    "apply", ok=True, summary="idle", counts={"replays dropped": len(records)}
                )
            ]
        )
        return 0

    drive: Drive | None = None
    if not (args.no_drive or args.dry_run):
        try:
            drive = open_drive(cfg)
        except Exception as exc:
            # Without Drive we cannot dispose of anything, and applying would
            # leave items that re-drain later. Stop rather than half-work.
            log(f"drive unavailable, not applying this run: {exc}")
            emit([status_entry("apply", ok=False, summary=f"drive unavailable: {exc}"[:120])])
            return 1

    try:
        verdicts, quarantined = extract(cfg, fresh)
    except Exception as exc:
        log(f"extract stage crashed: {exc}")
        log(traceback.format_exc())
        emit([status_entry("apply", ok=False, summary=f"extract crashed: {exc}"[:120])])
        return 1

    by_id = {r.item_id: r for r in fresh}
    entries: list[dict[str, Any]] = []

    if args.dry_run:
        for item_id, v in verdicts.items():
            log(
                f"{item_id} [{v.kind}] would apply: title={v.title!r} "
                f"due={v.due_iso()} priority={v.priority} tags={v.tags} "
                f"actionable={v.actionable}"
            )
        for item_id, reason in quarantined.items():
            log(f"{item_id} would quarantine: {reason}")
        emit([])
        return 0

    counts = {"filed": 0, "quarantined": 0, "retrying": 0}

    for item_id, reason in quarantined.items():
        counts["quarantined"] += 1
        entries.append(quarantine_item(drive, cfg, by_id[item_id], reason))

    # One MCP session for every reminder in the run: starting the Swift server
    # costs seconds, and this job runs under a 60s timeout.
    want_reminders = any(v.kind == KIND_REMINDER for v in verdicts.values())
    reminders = Reminders(cfg) if want_reminders else None
    try:
        for item_id, v in verdicts.items():
            entries.append(
                apply_item(
                    drive,
                    cfg,
                    by_id[item_id],
                    v,
                    reminders=reminders,
                    hard_delete=args.hard_delete,
                )
            )
    finally:
        if reminders is not None:
            reminders.close()

    for e in entries:
        tags = e.get("tags") or []
        if "applied" in tags:
            counts["filed"] += 1
        elif "retry" in tags:
            counts["retrying"] += 1

    summary = f"filed {counts['filed']}" if counts["filed"] else "idle"
    entries.insert(
        0,
        status_entry(
            "apply",
            ok=True,
            summary=summary,
            counts=counts,
            attention=bool(counts["quarantined"] or counts["retrying"]),
        ),
    )
    emit(entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
