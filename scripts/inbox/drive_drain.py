# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-api-python-client>=2.140",
#   "google-auth>=2.33",
#   "pyyaml>=6.0",
#   "opentelemetry-sdk>=1.45",
#   "opentelemetry-exporter-otlp-proto-grpc>=1.45",
# ]
# ///
"""Stage A -- drain the Drive inbox into the local spool.

Deterministic by construction: no model call, no vault token, no Reminders
access. This stage can move files and write spool records; it cannot act on a
system of record. Every gate here is mechanical, so an adversarial document
cannot talk its way past one.

There is ONE flat inbox. What an item IS (reminder / todo / idea) is settled
during extraction, or declared by the author via frontmatter `type:` or a
filename marker -- never by which folder it sat in.

Run as a collect job (target_type "shell"):

    uv run --script scripts/inbox/drive_drain.py --profile "$WORKSPACE_PROFILE"

Stdout is the collect-job entries array (app/collect.go:793) and nothing else;
diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Any

import obslog
from common import (
    ALLOWED_MIMES,
    DRAIN_CAP,
    ENTRY_KIND,
    MAX_BODY_BYTES,
    STALE_PROCESSING_S,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_UPCOMING,
    Config,
    ConfigError,
    SpoolRecord,
    declared_kind,
    emit,
    entry,
    item_id_for,
    ledger_ids,
    load_config,
    log,
    scan_secrets,
    split_frontmatter,
    status_entry,
    utc_now_iso,
    write_spool,
)
from drive import Drive, InboxFolders, open_drive

# Files that live in the inbox as furniture, not as captures. A README the
# author put there to remind themselves of the format would otherwise be
# ingested as an idea on every single run.
#
# The agent-instruction names are here for a second reason. Claude Code reads
# CLAUDE.md from LOCAL filesystem paths, so a copy in Drive is inert -- but
# Drive is a multi-writer surface, and anyone a folder is shared with can
# create one. If such a file ever did reach an agent it would be untrusted
# content, never instructions. Skipping them keeps that question from arising
# and keeps them out of the vault.
SKIP_NAMES = frozenset(
    {
        "readme",
        "readme.md",
        "readme.txt",
        "claude.md",
        "agents.md",
        "gemini.md",
        "index.md",
        ".ds_store",
        "icon\r",
    }
)


def is_furniture(name: str) -> bool:
    """True for inbox files that are not captures and must be left alone."""
    lowered = name.strip().lower()
    return lowered in SKIP_NAMES or lowered.startswith((".", "_"))


class Rejected(Exception):
    """A mechanical gate refused the file. Carries the human-facing reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# -- the gates -----------------------------------------------------------------


def gate_owner(meta: dict[str, Any]) -> None:
    """The file must be owned by the authorised account.

    This is the primary prompt-injection vector: a document shared into the
    folder by someone else is content we never asked for, aimed at a pipeline
    with vault-write and calendar-write authority downstream. Ownership is a
    fact, not a judgement, so the check is exact and needs no model.
    """
    owners = meta.get("owners") or []
    if not owners:
        raise Rejected("no-owner", "Drive returned no owner for this file")
    if not owners[0].get("me"):
        who = owners[0].get("emailAddress", "unknown")
        raise Rejected("foreign-owner", f"owned by {who}, not the authorised account")


def gate_mime(meta: dict[str, Any]) -> None:
    mime = meta.get("mimeType", "")
    if mime not in ALLOWED_MIMES:
        raise Rejected("mime", f"{mime} is not an accepted capture format")


def gate_size(meta: dict[str, Any]) -> None:
    """Pre-check the declared size. Google Docs report none; read_text re-checks."""
    raw = meta.get("size")
    if raw is None:
        return
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return
    if size > MAX_BODY_BYTES:
        raise Rejected("too-large", f"{size} bytes exceeds the {MAX_BODY_BYTES} byte cap")


def gate_secrets(body: str) -> None:
    """Refuse bodies containing credential-shaped strings.

    A vault `learn` is durable, synthesised and recallable forever, so a
    credential that lands there is not a transient mistake. The detail
    deliberately names only the pattern, never the matched text.
    """
    hits = scan_secrets(body)
    if hits:
        raise Rejected("secret", f"credential-shaped content matched: {', '.join(hits)}")


def gate_body(body: str) -> None:
    if not body.strip():
        raise Rejected("empty", "no content after frontmatter")


# -- draining ------------------------------------------------------------------


def reject(
    drive: Drive, folders: InboxFolders, meta: dict[str, Any], exc: Rejected, *, dry: bool
) -> dict[str, Any]:
    """Move a refused file to rejected/ and leave a note saying why.

    Refused files are MOVED, never disposed of: the capture itself may be
    perfectly good -- a note that happened to quote a token, say -- and
    deleting it would destroy the author's only copy.
    """
    if not dry:
        try:
            drive.move(meta["id"], to=folders["rejected"], from_=folders["inbox"])
            drive.write_note(
                folders["rejected"],
                f"{meta['name']}.rejected.txt",
                f"reason: {exc.reason}\ndetail: {exc.detail}\nat: {utc_now_iso()}\n",
            )
        # Never lose the queue over a failed tidy-up move.
        except Exception as move_err:
            log(f"reject-move failed for {meta.get('name')!r}: {move_err}")
    return entry(
        f"rejected-{meta['id']}",
        ENTRY_KIND,
        f"Rejected: {meta.get('name', meta['id'])}",
        description=f"{exc.reason} — {exc.detail}",
        status=STATUS_FAILED,
        tags=["inbox", "rejected", exc.reason],
    )


def drain(
    drive: Drive,
    cfg: Config,
    applied: set[str],
    *,
    dry: bool,
    cap: int,
    hard_delete: bool = False,
) -> list[dict[str, Any]]:
    started = time.monotonic()
    folders = drive.resolve()
    entries: list[dict[str, Any]] = []
    counts = {"spooled": 0, "rejected": 0, "already filed": 0, "errors": 0, "skipped": 0}

    for meta in drive.list_inbox(folders, cap):
        name = meta.get("name", meta["id"])

        if is_furniture(name):
            log(f"skipping {name!r} (not a capture)")
            counts["skipped"] += 1
            continue

        try:
            gate_owner(meta)
            gate_mime(meta)
            gate_size(meta)
            try:
                text = drive.read_text(meta, MAX_BODY_BYTES)
            except ValueError as exc:
                raise Rejected("too-large", str(exc)) from exc
            fm, body = split_frontmatter(text)
            gate_body(body)
            gate_secrets(text)
        except Rejected as exc:
            log(f"reject {name!r}: {exc.reason} ({exc.detail})")
            counts["rejected"] += 1
            entries.append(reject(drive, folders, meta, exc, dry=dry))
            continue
        # One bad file must not stall the rest of the queue.
        except Exception as exc:
            log(f"error on {name!r}: {exc}")
            counts["errors"] += 1
            entries.append(
                entry(
                    f"error-{meta['id']}",
                    ENTRY_KIND,
                    f"Drain failed: {name}",
                    description=str(exc)[:500],
                    status=STATUS_FAILED,
                    tags=["inbox", "error"],
                )
            )
            continue

        iid = item_id_for(body)
        if iid in applied:
            # Same content already filed -- a re-drop, not new work. Dispose of
            # it rather than creating a duplicate reminder or todo.
            log(f"{name!r} already filed as {iid}; disposing")
            # Also no per-item row: a re-drop is noise, and like the pending
            # row above it would never clear. It shows in the status counts.
            counts["already filed"] += 1
            if not dry:
                drive.dispose(meta["id"], hard_delete=hard_delete)
            continue

        kind = declared_kind(fm, name)
        rec = SpoolRecord(
            item_id=iid,
            profile=cfg.profile,
            declared_kind=kind,
            drive_file_id=meta["id"],
            drive_name=name,
            mime=meta.get("mimeType", ""),
            captured_at=meta.get("createdTime", ""),
            drained_at=utc_now_iso(),
            body=body.strip(),
            frontmatter=fm,
        )
        if dry:
            log(f"would spool {iid} from {name!r} (declared kind: {kind or 'none'})")
        else:
            # Spool first, then move. A crash between the two leaves a spool
            # record whose Drive file is still in inbox/, which re-drains to the
            # same content-addressed id and overwrites it. The reverse order
            # would strand a file in processing/ with nothing to process it.
            write_spool(cfg, rec)
            drive.move(meta["id"], to=folders["processing"], from_=folders["inbox"])

        # Deliberately NO per-item row here. The two stages are separate
        # collect jobs and collected_entries upserts per (job_id, entry_id), so
        # a "pending" row written under the DRAIN job can never be superseded
        # by the apply job's "applied" row -- it sat at `upcoming` forever, one
        # stale row per item ever captured. The status row below carries the
        # spooled count instead, and apply emits the durable per-item record.
        counts["spooled"] += 1

    # One upserted status row, so a run that found nothing is still visible.
    verb = "would spool" if dry else "spooled"
    summary = f"{verb} {counts['spooled']}" if counts["spooled"] else "idle"
    entries.insert(
        0,
        status_entry(
            "drain",
            ok=True,
            summary=summary,
            counts=counts,
            attention=bool(counts["rejected"] or counts["errors"]),
        ),
    )

    # ADR-007: one record per run, counts folded in rather than one per item.
    # The status row above is current state; this is the history behind it.
    obslog.emit_run(
        workspace=cfg.profile,
        event_name="inbox.drain.run",
        stage="drain",
        counts=counts,
        duration_ms=int((time.monotonic() - started) * 1000),
        attributes={"dry": dry},
    )
    return entries


def requeue_stale(
    drive: Drive,
    cfg: Config,
    applied: set[str],
    *,
    dry: bool,
    older_than_s: int = STALE_PROCESSING_S,
    hard_delete: bool = False,
) -> list[dict[str, Any]]:
    """Recover items stranded in processing/ by a crash or a killed job.

    The ledger is authoritative: a stranded file whose item was already filed is
    disposed of, never returned to inbox/, because stage C appends to the ledger
    before disposing of the file and a re-queue would otherwise double-apply.
    """
    entries: list[dict[str, Any]] = []
    folders = drive.resolve()
    for meta in drive.stale_processing(folders, older_than_s):
        name = meta.get("name", meta["id"])
        try:
            text = drive.read_text(meta, MAX_BODY_BYTES)
        # A stale file we cannot read is left for the next pass.
        except Exception as exc:
            log(f"stale read failed for {name!r}: {exc}")
            continue
        _, body = split_frontmatter(text)
        iid = item_id_for(body)
        if iid in applied:
            log(f"stale {name!r} already filed; disposing")
            if not dry:
                drive.dispose(meta["id"], hard_delete=hard_delete)
            outcome, status = "disposed (already filed)", STATUS_DONE
        else:
            log(f"stale {name!r} -> inbox for retry")
            if not dry:
                drive.move(meta["id"], to=folders["inbox"], from_=folders["processing"])
            outcome, status = "requeued", STATUS_UPCOMING
        entries.append(
            entry(
                f"stale-{meta['id']}",
                ENTRY_KIND,
                f"{name} {outcome}",
                status=status,
                tags=["inbox", "requeued"],
            )
        )
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drain the Drive inbox into the local spool.")
    ap.add_argument(
        "--profile", default=None, help="workspace profile (default $WORKSPACE_PROFILE)"
    )
    ap.add_argument("--cap", type=int, default=DRAIN_CAP, help=f"max files per run ({DRAIN_CAP})")
    ap.add_argument("--init", action="store_true", help="create the Drive folder tree and exit")
    ap.add_argument("--dry-run", action="store_true", help="report without moving or spooling")
    ap.add_argument(
        "--requeue-stale",
        action="store_true",
        help="recover items stranded in processing/ and exit",
    )
    ap.add_argument(
        "--stale-seconds",
        type=int,
        default=STALE_PROCESSING_S,
        help=f"age at which a processing/ item counts as stranded ({STALE_PROCESSING_S})",
    )
    ap.add_argument(
        "--hard-delete",
        action="store_true",
        help="permanently delete filed items instead of trashing them (not recoverable)",
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.profile)
        drive = open_drive(cfg)
    except ConfigError as exc:
        log(f"config error: {exc}")
        emit([status_entry("drain", ok=False, summary=f"config error: {exc}"[:120])])
        return 1
    except Exception as exc:
        log(f"drive init failed: {exc}")
        log(traceback.format_exc())
        emit([status_entry("drain", ok=False, summary=f"drive unavailable: {exc}"[:120])])
        return 1

    try:
        if args.init:
            folders = drive.init_tree()
            log(f"{cfg.drive_root}/{cfg.profile} -> {folders['_root']}")
            for stage in ("inbox", "processing", "quarantine", "rejected"):
                log(f"  {stage} -> {folders[stage]}")
            emit([])
            return 0

        applied = ledger_ids(cfg)

        if args.requeue_stale:
            emit(
                requeue_stale(
                    drive,
                    cfg,
                    applied,
                    dry=args.dry_run,
                    older_than_s=args.stale_seconds,
                    hard_delete=args.hard_delete,
                )
            )
            return 0

        emit(
            drain(
                drive,
                cfg,
                applied,
                dry=args.dry_run,
                cap=args.cap,
                hard_delete=args.hard_delete,
            )
        )
        return 0
    except ConfigError as exc:
        log(f"config error: {exc}")
        emit([status_entry("drain", ok=False, summary=f"config error: {exc}"[:120])])
        return 1
    except Exception as exc:
        log(f"drain failed: {exc}")
        log(traceback.format_exc())
        emit([status_entry("drain", ok=False, summary=f"failed: {exc}"[:120])])
        return 1


if __name__ == "__main__":
    sys.exit(main())
