"""Put the scripts/inbox directory on sys.path.

`uv run --script` puts the script's own directory on sys.path, which is how the
stage scripts import their siblings. pytest collects from the repo root instead,
so the same path has to be added explicitly here.
"""

from __future__ import annotations

import sys
from pathlib import Path

INBOX_DIR = Path(__file__).resolve().parent.parent
if str(INBOX_DIR) not in sys.path:
    sys.path.insert(0, str(INBOX_DIR))
