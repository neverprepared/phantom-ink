#!/usr/bin/env python3
"""One-time migration of user-configured tables from the legacy SQLite store
into Postgres.

Only the operator-configured config tables are migrated — gateway_servers
(on/off toggles), profile_server_override, trust_rules, and
trust_profile_config. Ephemeral tables (sessions, gateway_tokens, runners,
agent_*, loop_*) are intentionally NOT migrated: they self-reconcile (runners
re-register, tokens are re-minted on session provision, sessions get reaped).

The migration goes through the store's own writers, so it exercises the exact
validated Postgres path and is idempotent (every write is an upsert).

Usage:
    CL_DATABASE_URL=postgresql://user:pass@host:5432/brainbox \\
        uv run python scripts/migrate_sqlite_to_pg.py [SQLITE_PATH]

SQLITE_PATH defaults to ~/.config/phantom-ink/brainbox/brainbox.db
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def _default_sqlite_path() -> Path:
    return Path.home() / ".config" / "phantom-ink" / "brainbox" / "brainbox.db"


def _rows(cur: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """Return all rows of a table, or [] if the table doesn't exist."""
    try:
        return cur.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - fixed table names
    except sqlite3.OperationalError:
        return []


def main() -> int:
    if not os.environ.get("CL_DATABASE_URL"):
        print("error: CL_DATABASE_URL (Postgres DSN) must be set", file=sys.stderr)
        return 2

    sqlite_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_sqlite_path()
    if not sqlite_path.exists():
        print(f"error: SQLite DB not found: {sqlite_path}", file=sys.stderr)
        return 2

    # Import after the env check so the store binds to the right DSN.
    from brainbox import store

    store.init_db()  # ensure the Postgres schema exists

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row

    counts: dict[str, int] = {}

    # gateway_servers — the on/off toggles
    n = 0
    for r in _rows(src, "gateway_servers"):
        store.set_gateway_server_enabled(r["name"], bool(r["enabled"]))
        n += 1
    counts["gateway_servers"] = n

    # profile_server_override — per-(profile, server) manual on/off
    n = 0
    for r in _rows(src, "profile_server_override"):
        store.set_profile_server_override(r["profile"], r["server"], bool(r["enabled"]))
        n += 1
    counts["profile_server_override"] = n

    # trust_rules — per-profile destination -> zone
    n = 0
    for r in _rows(src, "trust_rules"):
        store.set_trust_rule(r["profile"], r["pattern"], r["zone"])
        n += 1
    counts["trust_rules"] = n

    # trust_profile_config — per-profile default residency ceiling
    n = 0
    for r in _rows(src, "trust_profile_config"):
        store.set_profile_default_ceiling(r["profile"], r["default_ceiling"])
        n += 1
    counts["trust_profile_config"] = n

    src.close()

    print(f"migrated from {sqlite_path} -> Postgres:")
    for table, c in counts.items():
        print(f"  {table:26} {c} rows")
    print("done. (sessions/tokens/runners/agent_*/loop_* intentionally not migrated — they self-reconcile)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
