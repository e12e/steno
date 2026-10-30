#!/usr/bin/env python3
"""Run CI checks (doctests, unit tests, ruff, mypy) and coarse real-time --live-check E2E test."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def run_step(title: str, cmd: list[str]) -> None:
    print(f"\n{'=' * 76}", flush=True)
    print(f"==> {title}", flush=True)
    print(f"    Kommando: {' '.join(cmd)}", flush=True)
    print(f"{'=' * 76}", flush=True)
    res = subprocess.run(cmd, cwd=BASE_DIR)
    if res.returncode != 0:
        raise SystemExit(f"FEIL i steg '{title}' (returkode {res.returncode})")


def main() -> int:
    os.chdir(BASE_DIR)
    src_str = str(BASE_DIR / "src")
    os.environ["PYTHONPATH"] = f"{src_str}:{os.environ.get('PYTHONPATH', '')}"

    # 1. Doctests, Unit tests, Linting (ruff), and Static Types (mypy)
    run_step(
        "1/5: Doctests (src/mote/core.py & src/mote/cli.py)",
        [sys.executable, "-m", "doctest", "src/mote/core.py", "src/mote/cli.py"],
    )
    run_step(
        "2/5: Enhetstester, sanntidsmonitor og sjekkpunkt-/konteksttester",
        [sys.executable, "tests/test_cli_and_pipeline.py", "-v"],
    )
    run_step(
        "3/5: Linting med Ruff",
        [
            "uvx",
            "ruff",
            "check",
            "src/",
            "scripts/",
            "tests/",
            "transcribe.py",
            "mote_app.py",
        ],
    )
    run_step(
        "4/5: Statisk typesjekk med Mypy",
        ["uvx", "mypy", "src/mote/core.py", "src/mote/cli.py", "transcribe.py", "tests/test_cli_and_pipeline.py"],
    )

    # 2. Verify coarse real-time transcription & audio level check (--live-check) on synthetic meeting
    synth_audio = BASE_DIR / "synthetic_test" / "syntetisk_mote_4_talere.opus"
    run_step(
        "5/5: Grov sanntids-transkripsjon og lydsjekk (--live-check 15s) på syntetisk møte",
        [
            sys.executable,
            "transcribe.py",
            str(synth_audio),
            "--live-check",
            "--live-seconds",
            "15",
            "--speaker-names",
            "Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
