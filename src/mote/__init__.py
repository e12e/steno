"""mote: 100% lokal norsk møtetranskripsjon, taler-separasjon og referatgenerering."""

from __future__ import annotations

from mote.core import (
    LiveAudioMonitor,
    PipelineResult,
    TurnDict,
    build_notes_html,
    build_transcript_html_and_text,
    cli_main,
    run_live_check_on_file,
    run_pipeline,
)

__all__ = [
    "LiveAudioMonitor",
    "PipelineResult",
    "TurnDict",
    "build_notes_html",
    "build_transcript_html_and_text",
    "cli_main",
    "run_live_check_on_file",
    "run_pipeline",
]
