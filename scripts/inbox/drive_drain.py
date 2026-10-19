# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-api-python-client>=2.140",
#   "google-auth>=2.33",
#   "pyyaml>=6.0",
# ]
# ///
"""Stage A -- drain the Drive inbox queues into the local spool.

Deterministic by construction: no model call, no vault token, no Reminders
access. This stage can move files and write spool records; it cannot act on a
system of record. Every gate here is mechanical, so an adversarial document
cannot talk its way past one.

Run as a collect job (target_type "shell"):

    uv run --script scripts/inbox/drive_drain.py --profile "$WORKSPACE_PROFILE"

Stdout is the collect-job entries array (app/collect.go:793) and nothing else;
diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from common import (
    ALLOWED_MIMES,
    DRAIN_CAP,
    MAX_BODY_BYTES,
    QUEUES,
    STALE_PROCESSING_S,
    STATUS_FAILED,
    STATUS_UPCOMING,
    Config,
    ConfigError,
    SpoolRecord,
    emit,
    entry,
    item_id_for,
    ledger_ids,
    load_config,
    log,
    scan_secrets,
    split_frontmatter,
    utc_now_iso,
    write_spool,
)
from drive import Drive, QueueFolders, open_drive


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
    drive: Drive, folders: QueueFolders, meta: dict[str, Any], exc: Rejected, *, dry: bool
) -> dict[str, Any]:
    """Move a refused file to rejected/ and leave a note saying why."""
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
        "inbox_rejected",
        f"Rejected: {meta.get('name', meta['id'])}",
        description=f"{exc.reason} — {exc.detail}",
        status=STATUS_FAILED,
        tags=["inbox", "rejected", exc.reason],
    )


def drain_queue(
    drive: Drive, cfg: Config, queue: str, applied: set[str], *, dry: bool, cap: int
) -> list[dict[str, Any]]:
    folders = drive.resolve_queue(queue)
    entries: list[dict[str, Any]] = []

    for meta in drive.list_inbox(folders, cap):
        name = meta.get("name", meta["id"])
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
            log(f"[{queue}] reject {name!r}: {exc.reason} ({exc.detail})")
            entries.append(reject(drive, folders, meta, exc, dry=dry))
            continue
        # One bad file must not stall the rest of the queue.
        except Exception as exc:
            log(f"[{queue}] error on {name!r}: {exc}")
            entries.append(
                entry(
                    f"error-{meta['id']}",
                    "inbox_error",
                    f"Drain failed: {name}",
                    description=str(exc)[:500],
                    status=STATUS_FAILED,
                    tags=["inbox", "error"],
                )
            )
            continue

        iid = item_id_for(body)
        if iid in applied:
            # Same content already applied — a re-drop, not new work. Archive it
            # rather than creating a duplicate reminder or todo.
            log(f"[{queue}] {name!r} already applied as {iid}; archiving")
            if not dry:
                drive.move(meta["id"], to=folders["processing"], from_=folders["inbox"])
                drive.move_to_done(folders, meta["id"])
            continue

        rec = SpoolRecord(
            item_id=iid,
            profile=cfg.profile,
            queue=queue,
            drive_file_id=meta["id"],
            drive_name=name,
            mime=meta.get("mimeType", ""),
            captured_at=meta.get("createdTime", ""),
            drained_at=utc_now_iso(),
            body=body.strip(),
            frontmatter=fm,
        )
        if dry:
            log(f"[{queue}] would spool {iid} from {name!r}")
        else:
            # Spool first, then move. A crash between the two leaves a spool
            # record whose Drive file is still in inbox/, which re-drains to the
            # same content-addressed id and overwrites it. The reverse order
            # would strand a file in processing/ with nothing to process it.
            write_spool(cfg, rec)
            drive.move(meta["id"], to=folders["processing"], from_=folders["inbox"])

        entries.append(
            entry(
                iid,
                "inbox_pending",
                f"{queue}: {fm.get('title') or name}",
                description=rec.body[:300],
                status=STATUS_UPCOMING,
                tags=["inbox", queue, "pending"],
                metadata={"queue": queue, "drive_file_id": meta["id"]},
            )
        )

    return entries


def requeue_stale(
    drive: Drive,
    cfg: Config,
    applied: set[str],
    *,
    dry: bool,
    older_than_s: int = STALE_PROCESSING_S,
) -> list[dict[str, Any]]:
    """Recover items stranded in processing/ by a crash or a killed job.

    The ledger is authoritative: a stranded file whose item was already applied
    goes to done/, never back to inbox/, because stage C appends to the ledger
    before moving the file and a re-queue would otherwise double-apply.
    """
    entries: list[dict[str, Any]] = []
    for queue in QUEUES:
        folders = drive.resolve_queue(queue)
        for meta in drive.stale_processing(folders, older_than_s):
            name = meta.get("name", meta["id"])
            try:
                text = drive.read_text(meta, MAX_BODY_BYTES)
            # A stale file we cannot read is left for the next pass.
            except Exception as exc:
                log(f"[{queue}] stale read failed for {name!r}: {exc}")
                continue
            _, body = split_frontmatter(text)
            iid = item_id_for(body)
            if iid in applied:
                log(f"[{queue}] stale {name!r} already applied; -> done")
                if not dry:
                    drive.move_to_done(folders, meta["id"])
                verdict, status = "archived (already applied)", "active"
            else:
                log(f"[{queue}] stale {name!r} -> inbox for retry")
                if not dry:
                    drive.move(meta["id"], to=folders["inbox"], from_=folders["processing"])
                verdict, status = "requeued", "pending"
            entries.append(
                entry(
                    f"stale-{meta['id']}",
                    "inbox_requeued",
                    f"{queue}: {name} {verdict}",
                    status=status,
                    tags=["inbox", queue, "requeued"],
                )
            )
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drain Drive inbox queues into the local spool.")
    ap.add_argument(
        "--profile", default=None, help="workspace profile (default $WORKSPACE_PROFILE)"
    )
    ap.add_argument("--queue", choices=QUEUES, default=None, help="limit to one queue")
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
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.profile)
        drive = open_drive(cfg)
    except ConfigError as exc:
        log(f"config error: {exc}")
        emit([])
        return 1
    except Exception as exc:
        log(f"drive init failed: {exc}")
        log(traceback.format_exc())
        emit([])
        return 1

    try:
        if args.init:
            tree = drive.init_tree()
            for q, folders in tree.items():
                log(f"{cfg.drive_root}/{cfg.profile}/{q} -> {folders['_queue_root']}")
            emit([])
            return 0

        applied = ledger_ids(cfg)

        if args.requeue_stale:
            emit(
                requeue_stale(
                    drive, cfg, applied, dry=args.dry_run, older_than_s=args.stale_seconds
                )
            )
            return 0

        queues = [args.queue] if args.queue else list(QUEUES)
        entries: list[dict[str, Any]] = []
        for q in queues:
            entries.extend(drain_queue(drive, cfg, q, applied, dry=args.dry_run, cap=args.cap))
        emit(entries)
        return 0
    except ConfigError as exc:
        log(f"config error: {exc}")
        emit([])
        return 1
    except Exception as exc:
        log(f"drain failed: {exc}")
        log(traceback.format_exc())
        emit([])
        return 1


if __name__ == "__main__":
    sys.exit(main())
