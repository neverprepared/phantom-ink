"""brainbox-mcp — MCP server entry point.

Usage:
    brainbox-mcp                          # stdio transport (default)
    brainbox-mcp --url http://host:9999   # custom API URL
    python -m brainbox_mcp mcp [--url …]  # same, via module invocation
"""

from __future__ import annotations

import argparse
import os
import sys

from .log import setup_logging


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(prog="brainbox-mcp")
    parser.add_argument(
        "--url",
        default=None,
        help="Brainbox API URL (default: $BRAINBOX_URL or http://127.0.0.1:9999)",
    )
    args = parser.parse_args()

    if args.url:
        os.environ["BRAINBOX_URL"] = args.url

    from .mcp_server import run

    run()


if __name__ == "__main__":
    sys.exit(main())
