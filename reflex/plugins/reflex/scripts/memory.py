#!/usr/bin/env python3
"""
Reflex memory system — SQLite + FTS5 event log.

Commands:
  ingest              Read PostToolUse JSON from stdin, write event to SQLite
  recent [--hours N]  Print recent events as JSON lines
  search <query>      FTS5 keyword search, print matching events as JSON lines
  summarize [--day YYYY-MM-DD]  Generate or fetch daily summary
  stats               Print event counts grouped by action_type and day

Environment:
  REFLEX_HOME   Override for $HOME/.config/reflex (directory, not file)
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def db_path() -> Path:
    base = os.environ.get("REFLEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".config", "reflex"
    )
    return Path(base) / "memory.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    session_id    TEXT,
    action_type   TEXT NOT NULL,
    query_text    TEXT,
    url           TEXT,
    domain        TEXT,
    title         TEXT,
    note          TEXT,
    status        TEXT,
    tags_json     TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts          ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session_id  ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_domain      ON events(domain);
CREATE INDEX IF NOT EXISTS idx_events_action_type ON events(action_type);

CREATE VIRTUAL TABLE IF NOT EXISTS event_fts USING fts5(
    query_text,
    title,
    note,
    content='events',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO event_fts(rowid, query_text, title, note)
    VALUES (new.id, new.query_text, new.title, new.note);
END;

CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO event_fts(event_fts, rowid, query_text, title, note)
    VALUES ('delete', old.id, old.query_text, old.title, old.note);
    INSERT INTO event_fts(rowid, query_text, title, note)
    VALUES (new.id, new.query_text, new.title, new.note);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO event_fts(event_fts, rowid, query_text, title, note)
    VALUES ('delete', old.id, old.query_text, old.title, old.note);
END;

CREATE TABLE IF NOT EXISTS daily_summaries (
    day                TEXT PRIMARY KEY,  -- YYYY-MM-DD
    summary_text       TEXT,
    key_topics         TEXT,              -- JSON array of strings
    source_event_count INTEGER,
    created_at         DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type           TEXT,
    title              TEXT,
    body               TEXT,
    source_range_start DATETIME,
    source_range_end   DATETIME,
    created_at         DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


_db_initialized = False


def connect() -> sqlite3.Connection:
    global _db_initialized
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    if not _db_initialized:
        conn.executescript(SCHEMA)
        _db_initialized = True
    return conn


# ---------------------------------------------------------------------------
# Parsers for each tool type
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or ""
    except Exception:
        return ""


def parse_web_search(data: dict) -> dict:
    tool_input = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}

    query = tool_input.get("query") or ""

    # tool_response may be a string (raw text) or a dict with results
    results = []
    if isinstance(tool_response, dict):
        results = tool_response.get("results") or []
    elif isinstance(tool_response, str):
        # Store raw snippet as note
        pass

    first_title = results[0].get("title") if results else None
    first_url   = results[0].get("url")   if results else None

    return {
        "action_type":   "web_search",
        "query_text":    query,
        "url":           first_url,
        "domain":        _domain(first_url) if first_url else None,
        "title":         first_title,
        "metadata_json": json.dumps({"result_count": len(results), "results": results[:5]}),
    }


def parse_web_fetch(data: dict) -> dict:
    tool_input    = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}

    url = tool_input.get("url") or ""

    title = None
    if isinstance(tool_response, dict):
        title = tool_response.get("title")
    elif isinstance(tool_response, str):
        # Try to extract <title> from HTML snippet
        import re
        m = re.search(r"<title[^>]*>([^<]+)</title>", tool_response, re.IGNORECASE)
        title = m.group(1).strip() if m else None

    return {
        "action_type":   "web_fetch",
        "query_text":    tool_input.get("prompt") or None,
        "url":           url,
        "domain":        _domain(url),
        "title":         title,
        "metadata_json": json.dumps({"prompt": tool_input.get("prompt")}),
    }


PARSERS = {
    "WebSearch": parse_web_search,
    "WebFetch":  parse_web_fetch,
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    """Read PostToolUse JSON from stdin and insert an event row."""
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return

    tool_name = data.get("tool_name", "")
    parser = PARSERS.get(tool_name)
    if not parser:
        return  # not a tool we track

    try:
        event = parser(data)
    except Exception:
        return

    event["session_id"] = data.get("session_id") or None
    event["status"]     = "ok"

    try:
        conn = connect()
        conn.execute(
            """
            INSERT INTO events
                (session_id, action_type, query_text, url, domain, title, note, status, tags_json, metadata_json)
            VALUES
                (:session_id, :action_type, :query_text, :url, :domain, :title, :note, :status, :tags_json, :metadata_json)
            """,
            {
                "session_id":    event.get("session_id"),
                "action_type":   event["action_type"],
                "query_text":    event.get("query_text"),
                "url":           event.get("url"),
                "domain":        event.get("domain"),
                "title":         event.get("title"),
                "note":          event.get("note"),
                "status":        event.get("status"),
                "tags_json":     event.get("tags_json"),
                "metadata_json": event.get("metadata_json"),
            },
        )
        conn.commit()
        conn.close()
    except Exception as e:
        sys.stderr.write(f"memory-hook: error: {e}\n")  # never block the hook


def cmd_recent(args):
    """Print recent events as JSON lines."""
    hours = getattr(args, "hours", 24)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        conn = connect()
        rows = conn.execute(
            "SELECT * FROM events WHERE ts >= ? ORDER BY ts DESC",
            (since,),
        ).fetchall()
        conn.close()
        for row in rows:
            print(json.dumps(dict(row)))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)


def cmd_search(args):
    """FTS5 keyword search over query_text, title, note."""
    query = " ".join(args.query)
    if not query.strip():
        print(json.dumps({"error": "no query provided"}), file=sys.stderr)
        return
    try:
        conn = connect()
        rows = conn.execute(
            """
            SELECT e.*
            FROM events e
            JOIN event_fts f ON f.rowid = e.id
            WHERE event_fts MATCH ?
            ORDER BY rank, e.ts DESC
            LIMIT 50
            """,
            (query,),
        ).fetchall()
        conn.close()
        for row in rows:
            print(json.dumps(dict(row)))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)


def cmd_summarize(args):
    """
    Generate or fetch a daily summary.

    Uses ollama (nomic-embed-text host) if available for LLM summarization,
    otherwise produces a plain-text aggregate from raw events.
    """
    day = getattr(args, "day", None) or datetime.now().strftime("%Y-%m-%d")

    try:
        conn = connect()

        # Return cached summary if it exists
        cached = conn.execute(
            "SELECT * FROM daily_summaries WHERE day = ?", (day,)
        ).fetchone()
        if cached:
            print(json.dumps(dict(cached)))
            conn.close()
            return

        # Fetch events for the day
        rows = conn.execute(
            """
            SELECT action_type, query_text, url, domain, title, ts
            FROM events
            WHERE date(ts) = ?
            ORDER BY ts
            """,
            (day,),
        ).fetchall()

        if not rows:
            print(json.dumps({"day": day, "message": "no events found"}))
            conn.close()
            return

        events_list = [dict(r) for r in rows]
        event_count = len(events_list)

        # Extract key topics (unique query_text values, non-null)
        topics = list(
            dict.fromkeys(
                r["query_text"]
                for r in events_list
                if r.get("query_text")
            )
        )[:20]

        # Try ollama for LLM-assisted summary
        summary_text = _ollama_summarize(day, events_list)

        # Fallback: plain aggregate
        if not summary_text:
            searches = [e for e in events_list if e["action_type"] == "web_search"]
            fetches  = [e for e in events_list if e["action_type"] == "web_fetch"]
            lines = [
                f"Day: {day}",
                f"Events: {event_count} total ({len(searches)} searches, {len(fetches)} fetches)",
            ]
            if topics:
                lines.append(f"Searches: {', '.join(topics[:10])}")
            domains = list(dict.fromkeys(e["domain"] for e in fetches if e.get("domain")))
            if domains:
                lines.append(f"Sites visited: {', '.join(domains[:10])}")
            summary_text = "\n".join(lines)

        # Persist
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_summaries
                (day, summary_text, key_topics, source_event_count)
            VALUES (?, ?, ?, ?)
            """,
            (day, summary_text, json.dumps(topics), event_count),
        )
        conn.commit()
        conn.close()

        print(json.dumps({
            "day":                day,
            "summary_text":       summary_text,
            "key_topics":         topics,
            "source_event_count": event_count,
        }))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)


def _ollama_summarize(day: str, events: list) -> str | None:
    """
    Request a summary from ollama. Returns None if ollama is unavailable.
    Uses MEMORY_LLM_MODEL (default: llama3.2) or MEMORY_LLM_URL for overrides.
    """
    try:
        import urllib.request

        base_url = os.environ.get("MEMORY_LLM_URL", "http://localhost:11434")
        model    = os.environ.get("MEMORY_LLM_MODEL", "llama3.2")

        bullet_lines = []
        for e in events[:40]:  # cap context
            if e["action_type"] == "web_search" and e.get("query_text"):
                bullet_lines.append(f"- searched: {e['query_text']}")
            elif e["action_type"] == "web_fetch" and e.get("url"):
                label = e.get("title") or e["url"]
                bullet_lines.append(f"- visited: {label}")

        prompt = (
            f"Summarize the following Claude Code session activity for {day} "
            f"in 3-5 concise sentences. Focus on topics researched and goals inferred.\n\n"
            + "\n".join(bullet_lines)
        )

        payload = json.dumps({
            "model":  model,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("response", "").strip() or None

    except Exception:
        return None


def cmd_stats(args):
    """Print event counts grouped by action_type and day."""
    try:
        conn = connect()
        rows = conn.execute(
            """
            SELECT date(ts) AS day, action_type, COUNT(*) AS count
            FROM events
            GROUP BY day, action_type
            ORDER BY day DESC, count DESC
            LIMIT 100
            """
        ).fetchall()
        conn.close()
        for row in rows:
            print(json.dumps(dict(row)))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reflex memory system")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("ingest", help="Ingest PostToolUse event from stdin")

    p_recent = sub.add_parser("recent", help="Show recent events")
    p_recent.add_argument("--hours", type=int, default=24, help="Look-back window in hours")

    p_search = sub.add_parser("search", help="FTS5 keyword search")
    p_search.add_argument("query", nargs="+", help="Search terms")

    p_summary = sub.add_parser("summarize", help="Generate or fetch daily summary")
    p_summary.add_argument("--day", default=None, help="Date in YYYY-MM-DD format (default: today)")

    sub.add_parser("stats", help="Event counts by type and day")

    args = parser.parse_args()

    dispatch = {
        "ingest":    cmd_ingest,
        "recent":    cmd_recent,
        "search":    cmd_search,
        "summarize": cmd_summarize,
        "stats":     cmd_stats,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
