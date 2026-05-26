#!/usr/bin/env python3
"""
LangFuse trace sender for Claude Code tool calls.

Receives tool call data from stdin (JSON) and sends it to LangFuse.
Designed to be called from langfuse-hook.sh as a PostToolUse hook.

Environment variables:
  LANGFUSE_BASE_URL    - LangFuse server URL (default: http://localhost:3000)
  LANGFUSE_PUBLIC_KEY  - LangFuse public key (required)
  LANGFUSE_SECRET_KEY  - LangFuse secret key (required)
  LANGFUSE_SESSION_ID  - Optional session ID for grouping traces
"""

import json
import logging.handlers
import os
import sys
from datetime import datetime, timezone, timedelta

# API pricing per million tokens (used as proxy for subscription cost estimation)
MODEL_PRICING = {
    "claude-opus-4-7":          {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-6":        {"input":  3.00, "output": 15.00, "cache_read": 0.30,  "cache_write":  3.75},
    "claude-haiku-4-5":         {"input":  0.80, "output":  4.00, "cache_read": 0.08,  "cache_write":  1.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output":  4.00, "cache_read": 0.08,  "cache_write":  1.00},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}


def read_transcript_usage(transcript_path: str) -> dict | None:
    """Read the most recent assistant turn's token usage from the transcript JSONL."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        last_usage = None
        with open(transcript_path, "rb") as fh:
            # Scan from end to find last assistant message with usage
            fh.seek(0, 2)
            size = fh.tell()
            chunk_size = min(32_768, size)
            fh.seek(max(0, size - chunk_size))
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not usage:
                continue
            model = msg.get("model", "")
            pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
            input_tok     = usage.get("input_tokens", 0)
            output_tok    = usage.get("output_tokens", 0)
            cache_read    = usage.get("cache_read_input_tokens", 0)
            cache_write   = usage.get("cache_creation_input_tokens", 0)
            cost = (
                input_tok   * pricing["input"]       / 1_000_000 +
                output_tok  * pricing["output"]      / 1_000_000 +
                cache_read  * pricing["cache_read"]  / 1_000_000 +
                cache_write * pricing["cache_write"] / 1_000_000
            )
            return {
                "model":                    model,
                "turn_input_tokens":        input_tok,
                "turn_output_tokens":       output_tok,
                "turn_cache_read_tokens":   cache_read,
                "turn_cache_write_tokens":  cache_write,
                "turn_cost_estimate_usd":   round(cost, 6),
                "turn_cost_note":           "per-assistant-turn; duplicated across batched tool calls",
            }
        return None
    except Exception:
        return None

# Check for langfuse package
try:
    from langfuse import get_client, propagate_attributes
except ImportError:
    # Silently exit if langfuse not installed
    sys.exit(0)


def get_session_id() -> str:
    """Get or generate a session ID for trace grouping."""
    # Use provided session ID or generate from timestamp
    return os.environ.get(
        "LANGFUSE_SESSION_ID",
        f"claude-code-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )


def parse_tool_data(data: dict) -> dict:
    """Extract relevant fields from Claude Code tool call data."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {"tool_name": "unknown", "tool_input": {}, "tool_response": {}, "session_id": None, "tool_use_id": None, "success": True, "error": None}

    # Claude Code PostToolUse hook sends: tool_name, tool_input, tool_response
    tool_response = data.get("tool_response", {})

    # tool_response may be a plain string (raw tool output) rather than a dict
    if isinstance(tool_response, str):
        tool_response = {"output": tool_response}

    # Determine if there was an error
    error = tool_response.get("stderr") if tool_response.get("stderr") else None

    return {
        "tool_name": data.get("tool_name", "unknown"),
        "tool_input": data.get("tool_input", {}),
        "tool_response": tool_response,
        "session_id": data.get("session_id"),
        "tool_use_id": data.get("tool_use_id"),
        "transcript_path": data.get("transcript_path"),
        "cwd": data.get("_cwd"),
        "workspace_profile": data.get("_workspace_profile") or None,
        "success": not bool(error),
        "error": error,
    }


def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    log_path = os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")),
        "reflex", "langfuse-debug.log"
    )
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3
    )
    logger = logging.getLogger("langfuse_debug")
    if not logger.handlers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    logger.debug("[PYTHON] %s", msg)


def send_trace(tool_data: dict) -> None:
    """Send tool call trace to LangFuse using SDK v3 API."""
    host = os.environ.get("LANGFUSE_BASE_URL") or "http://localhost:3000"
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    debug_log(f"host={host}")
    debug_log(f"public_key={'<set>' if public_key else '<not set>'}")
    debug_log(f"secret_key={'<set>' if secret_key else '<not set>'}")

    if not public_key or not secret_key:
        debug_log("Missing credentials, returning")
        return

    try:
        # Set environment variables for get_client() to use
        os.environ["LANGFUSE_HOST"] = host

        debug_log("Getting Langfuse client...")
        langfuse = get_client()
        debug_log("Langfuse client obtained")

        parsed = parse_tool_data(tool_data)
        # Prefer LANGFUSE_SESSION_ID env var (set by containers to the container name),
        # then fall back to Claude Code's per-session UUID, then generate one
        session_id = os.environ.get("LANGFUSE_SESSION_ID") or parsed.get("session_id") or get_session_id()

        # SDK v3 uses start_as_current_observation with context manager
        # Use propagate_attributes for session_id and user_id
        user_id = os.environ.get("LANGFUSE_USER_ID") or os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
        debug_log(f"Creating span for tool:{parsed['tool_name']}")
        debug_log(f"user_id={user_id}")

        # Use hook-stamped time as the span timestamp so queue delay doesn't skew traces.
        # _queued_at is set by langfuse-hook.sh immediately after the tool completes.
        # queued_at reflects when the hook fired (immediately after tool completion).
        # Real span duration tracking would require capturing tool start time in the hook.
        queued_at_raw = tool_data.get("_queued_at")
        if queued_at_raw:
            try:
                queued_at = datetime.fromisoformat(queued_at_raw.replace("Z", "+00:00"))
            except ValueError:
                queued_at = datetime.now(timezone.utc)
        else:
            queued_at = datetime.now(timezone.utc)

        metadata = {
            "source": "claude-code",
            "hook_event": "PostToolUse",
            "plugin": "reflex",
            "tool_name": parsed["tool_name"],
            "success": parsed["success"],
        }
        if parsed.get("workspace_profile"):
            metadata["workspace_profile"] = parsed["workspace_profile"]
        if parsed.get("cwd"):
            metadata["cwd"] = parsed["cwd"]
        if parsed.get("transcript_path"):
            metadata["transcript_path"] = parsed["transcript_path"]

        if parsed.get("tool_use_id"):
            metadata["tool_use_id"] = parsed["tool_use_id"]

        turn_usage = read_transcript_usage(parsed.get("transcript_path"))
        if turn_usage:
            metadata.update(turn_usage)
            debug_log(f"usage: model={turn_usage['model']} in={turn_usage['turn_input_tokens']} out={turn_usage['turn_output_tokens']} cost=${turn_usage['turn_cost_estimate_usd']}")
        else:
            debug_log("usage: no transcript data found")

        with propagate_attributes(session_id=session_id, user_id=user_id):
            with langfuse.start_as_current_observation(
                as_type="span",
                name=f"tool:{parsed['tool_name']}",
                input=parsed["tool_input"],
                metadata=metadata,
            ) as span:
                update_kwargs = {"output": parsed["tool_response"], "start_time": queued_at, "end_time": queued_at}
                if turn_usage:
                    update_kwargs["usage"] = {
                        "input":      turn_usage["turn_input_tokens"],
                        "output":     turn_usage["turn_output_tokens"],
                        "unit":       "TOKENS",
                        "total_cost": turn_usage["turn_cost_estimate_usd"],
                    }
                span.update(**update_kwargs)

                if parsed["error"]:
                    span.update(level="ERROR", status_message=str(parsed["error"]))

        debug_log(f"Span created: tool:{parsed['tool_name']}")

        # Flush to ensure data is sent
        debug_log("Flushing...")
        langfuse.flush()
        debug_log("Flush complete")

    except Exception as e:
        # Log error for debugging
        debug_log(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        debug_log(f"Traceback: {traceback.format_exc()}")


def main():
    """Read tool data from stdin (or a --batch JSONL file) and send trace(s)."""
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--batch", metavar="FILE", default=None)
    args, _ = parser.parse_known_args()

    if args.batch:
        try:
            with open(args.batch) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        send_trace(json.loads(line))
                    except (json.JSONDecodeError, Exception):
                        pass
        except Exception:
            pass
        return

    try:
        raw_input = sys.stdin.read().strip()
        if not raw_input:
            return

        tool_data = json.loads(raw_input)
        send_trace(tool_data)

    except json.JSONDecodeError:
        # Invalid JSON - skip silently
        pass
    except Exception:
        # Any other error - skip silently
        pass


if __name__ == "__main__":
    main()
