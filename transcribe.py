#!/usr/bin/env python3
"""Entrypoint for transcribe CLI."""

from __future__ import annotations

import sys
from pathlib import Path

# Add src/ to sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mote.core import cli_main

if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))
