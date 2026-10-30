#!/usr/bin/env python3
"""Command-line interface (CLI) for local meeting transcription, speaker diarization, and notes."""

from __future__ import annotations

import sys

from mote_core import (
    VALID_NOTE_MODES,
    VALID_TASK_MODES,
    build_cli_parser,
    cli_main,
    run_pipeline,
)

__all__ = [
    "VALID_NOTE_MODES",
    "VALID_TASK_MODES",
    "build_cli_parser",
    "cli_main",
    "run_pipeline",
]


def _doctest_cli_parser() -> None:
    """Verify CLI argument parsing via doctest.

    >>> parser = build_cli_parser()
    >>> args = parser.parse_args(["mote.wav", "-n", "4", "--mode", "mote", "--task", "from_3_diarize"])
    >>> (args.input_file.name, args.speakers, args.mode, args.task)
    ('mote.wav', 4, 'mote', 'from_3_diarize')
    >>> args2 = parser.parse_args(["foredrag.opus", "-n", "1", "-c", "slides.pptx", "-c", "agenda.md"])
    >>> (args2.speakers, [p.name for p in args2.context_file])
    (1, ['slides.pptx', 'agenda.md'])
    >>> args3 = parser.parse_args(["--live-check", "--live-seconds", "15"])
    >>> (args3.input_file, args3.live_check, args3.live_seconds)
    (None, True, 15.0)
    """


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))
