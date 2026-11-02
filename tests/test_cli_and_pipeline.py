"""Unit, doctest, Gherkin BDD, and CI integration tests for mote.core, mote.gui, and transcribe CLI."""

from __future__ import annotations

import contextlib
import doctest
import io
import json
import os
import shlex
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

# Add src/ to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe
from mote import core as mote_core
from mote import gui as mote_gui

FEATURE_FILE = Path(__file__).resolve().parent / "features" / "cli_and_pipeline.feature"


def parse_gherkin_feature(feature_path: Path) -> list[dict[str, object]]:
    """Minimal zero-dependency Gherkin .feature parser returning a list of scenarios with steps."""
    scenarios: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in feature_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Scenario:"):
            if current is not None:
                scenarios.append(current)
            current = {"name": line[len("Scenario:") :].strip(), "steps": []}
        elif current is not None and line.startswith(("Given ", "When ", "Then ", "And ")):
            steps = current["steps"]
            assert isinstance(steps, list)
            steps.append(line)
    if current is not None:
        scenarios.append(current)
    return scenarios


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, ignore: str | None
) -> unittest.TestSuite:
    """Register doctests from mote.core, mote.gui, and transcribe into standard unittest discovery."""
    tests.addTests(doctest.DocTestSuite(mote_core))
    tests.addTests(doctest.DocTestSuite(mote_gui))
    tests.addTests(doctest.DocTestSuite(transcribe))
    return tests


class TestDoctestsExplicit(unittest.TestCase):
    def test_mote_core_doctests(self) -> None:
        res = doctest.testmod(mote_core, verbose=False)
        self.assertEqual(res.failed, 0, f"mote.core doctests failed: {res}")
        self.assertGreater(res.attempted, 0)

    def test_mote_gui_doctests(self) -> None:
        res = doctest.testmod(mote_gui, verbose=False)
        self.assertEqual(res.failed, 0, f"mote.gui doctests failed: {res}")
        self.assertGreater(res.attempted, 0)

    def test_transcribe_doctests(self) -> None:
        res = doctest.testmod(transcribe, verbose=False)
        self.assertEqual(res.failed, 0, f"transcribe doctests failed: {res}")


class TestGherkinBDDScenarios(unittest.TestCase):
    def test_all_gherkin_scenarios_in_feature_file(self) -> None:
        self.assertTrue(FEATURE_FILE.is_file(), f"Missing Gherkin feature file: {FEATURE_FILE}")
        scenarios = parse_gherkin_feature(FEATURE_FILE)
        self.assertEqual(len(scenarios), 5)

        for sc in scenarios:
            name = str(sc["name"])
            steps = list(sc["steps"])  # type: ignore[arg-type]
            with self.subTest(scenario=name):
                self._execute_scenario(name, steps)

    def _execute_scenario(self, name: str, steps: list[str]) -> None:
        ctx: dict[str, object] = {}
        for step in steps:
            if step.startswith('Given the unified app entrypoint receives "'):
                arg_str = step.split('"', 2)[1]
                ctx["argv"] = shlex.split(arg_str)
            elif step == "When entrypoint dispatches the arguments":
                buf = io.StringIO()
                with mock.patch.object(mote_gui, "main") as mock_gui_main, contextlib.redirect_stdout(buf):
                    rc = mote_gui.entrypoint(list(ctx["argv"]))  # type: ignore[arg-type]
                ctx["rc"] = rc
                ctx["stdout"] = buf.getvalue()
                ctx["gui_called"] = mock_gui_main.called
            elif step == "Then it executes cli_main without opening the GUI window":
                self.assertFalse(ctx["gui_called"], "GUI main() should NOT be called for CLI flags")
                self.assertEqual(ctx["rc"], 0)
            elif step == 'And stdout contains JSON keys "hardware" and "resource_fit"':
                payload = json.loads(str(ctx["stdout"]))
                self.assertIn("hardware", payload)
                self.assertIn("resource_fit", payload)
            elif step == "Then it launches the GUI main function":
                self.assertTrue(ctx["gui_called"], "GUI main() should be called when only -psn_* is passed")
                self.assertEqual(ctx["rc"], 0)
            elif step == 'Then it returns exit code 0 and prints "SELF_TEST_OK"':
                self.assertFalse(ctx["gui_called"])
                self.assertEqual(ctx["rc"], 0)
                self.assertIn("SELF_TEST_OK", str(ctx["stdout"]))
            elif step.startswith('Given speaker_names_hint is "'):
                ctx["speaker_names_hint"] = step.split('"', 2)[1]
            elif step == "And min_speakers and max_speakers are empty":
                ctx["min_speakers"] = None
                ctx["max_speakers"] = None
            elif step == "When speaker bounds are resolved via resolve_speaker_bounds":
                min_s, max_s = mote_core.resolve_speaker_bounds(
                    ctx["min_speakers"],  # type: ignore[arg-type]
                    ctx["max_speakers"],  # type: ignore[arg-type]
                    str(ctx["speaker_names_hint"]),
                )
                ctx["resolved_min"] = min_s
                ctx["resolved_max"] = max_s
            elif step == "Then min_speakers is 3 and max_speakers is 3":
                self.assertEqual(ctx["resolved_min"], 3)
                self.assertEqual(ctx["resolved_max"], 3)
            elif step == "Given a generated transcript HTML file for a 2-speaker meeting":
                with tempfile.TemporaryDirectory() as tmp:
                    html_path = Path(tmp) / "mote_transkripsjon.html"
                    turns: list[mote_core.TurnDict] = [
                        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0, "text": "Hei"},
                        {"speaker": "SPEAKER_01", "start": 2.5, "end": 4.0, "text": "Hallo"},
                    ]
                    mote_core.build_transcript_html_and_text(
                        turns, {"SPEAKER_00": "Kari", "SPEAKER_01": "Ola"}, html_path, "Test"
                    )
                    ctx["html_content"] = html_path.read_text(encoding="utf-8")
            elif step == "When inspecting the generated transcript HTML content":
                self.assertTrue(bool(ctx.get("html_content")))
            elif step == 'Then it contains the "saveUpdatedHtml()" export function and "Lagre HTML" button':
                html_content = str(ctx["html_content"])
                self.assertIn("saveUpdatedHtml()", html_content)
                self.assertIn("Lagre HTML", html_content)
            else:
                self.fail(f"Unrecognized Gherkin step in '{name}': {step}")


class TestAARImprovements(unittest.TestCase):
    def test_fallback_templates_are_compact_and_not_duplicated_bloat(self) -> None:
        self.assertLessEqual(
            len(mote_core._FALLBACK_TRANSCRIPT_TPL.splitlines()),
            35,
            "_FALLBACK_TRANSCRIPT_TPL in core.py should be a compact fallback, not 500+ lines of duplicated HTML",
        )
        self.assertLessEqual(
            len(mote_core._FALLBACK_NOTES_TPL.splitlines()),
            35,
            "_FALLBACK_NOTES_TPL in core.py should be a compact fallback, not 270+ lines of duplicated HTML",
        )

    def test_gui_worker_delegates_to_run_pipeline(self) -> None:
        api = mote_gui.MoteApi()
        with tempfile.TemporaryDirectory() as tmp:
            audio_file = Path(tmp) / "test.wav"
            audio_file.write_bytes(mote_core.build_wav_bytes_from_pcm16(b"\x00\x00" * 1600, 16000))
            with mock.patch.object(mote_gui, "run_pipeline") as mock_run_pipeline:
                mock_run_pipeline.return_value = {
                    "ok": True,
                    "input_path": audio_file,
                    "out_dir": Path(tmp),
                    "ckpt_dir": Path(tmp) / ".mote_checkpoints" / "test",
                    "html_path": Path(tmp) / "test_transkripsjon.html",
                    "notes_path": Path(tmp) / "test_referat.md",
                    "notes_html_path": Path(tmp) / "test_referat.html",
                    "executed_stages": ["1_asr"],
                    "effective_mode": "mote",
                    "speaker_map": {},
                    "turn_count": 1,
                }
                api._worker(
                    audio_path_str=str(audio_file),
                    out_dir_str=str(tmp),
                    html_name_str="test_transkripsjon.html",
                    notes_name_str="test_referat.md",
                    notes_html_name_str="test_referat.html",
                    whisper_model="NbAiLab/nb-whisper-small",
                    ollama_model="gemma4:e4b",
                    language="no",
                    hf_token="",
                    min_speakers_str="",
                    max_speakers_str="",
                    speaker_names_hint="Kari Nordmann, Ola Hansen",
                    meeting_title="Testmøte",
                    agenda_text="Sak 1",
                    note_mode="auto",
                    task_mode="only_1_asr",
                    manual_notes_text="Manuelle notater",
                )
                self.assertTrue(
                    mock_run_pipeline.called,
                    "MoteApi._worker should delegate pipeline execution to run_pipeline (DRY)",
                )


class TestContextAndTranscriptExtraction(unittest.TestCase):
    def test_extract_context_from_pptx_and_docx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Create synthetic .pptx with slide text and speaker notes
            pptx_path = tmp_path / "presentasjon.pptx"
            with zipfile.ZipFile(pptx_path, "w") as zf:
                zf.writestr(
                    "ppt/slides/slide1.xml",
                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<a:p><a:r><a:t>Sikkerhetsarkitektur 2026</a:t></a:r></a:p>"
                    "<a:p><a:r><a:t>OAuth2 og Zero Trust</a:t></a:r></a:p>"
                    "</p:sld>",
                )
                zf.writestr(
                    "ppt/notesSlides/notesSlide1.xml",
                    '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<a:p><a:r><a:t>Husk å nevne fristen 15. november.</a:t></a:r></a:p>"
                    "</p:notes>",
                )

            pptx_text = mote_core.extract_context_from_file(pptx_path)
            self.assertIn("[Lysbilde 1]", pptx_text)
            self.assertIn("Sikkerhetsarkitektur 2026", pptx_text)
            self.assertIn("OAuth2 og Zero Trust", pptx_text)
            self.assertIn("Foredragsnotat: Husk å nevne fristen 15. november.", pptx_text)

            # Create synthetic .docx
            docx_path = tmp_path / "agenda.docx"
            with zipfile.ZipFile(docx_path, "w") as zf:
                zf.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body><w:p><w:r><w:t>Agenda punkt 1: Budsjett</w:t></w:r></w:p></w:body>"
                    "</w:document>",
                )
            docx_text = mote_core.extract_context_from_file(docx_path)
            self.assertIn("Agenda punkt 1: Budsjett", docx_text)

    def test_html_transcript_roundtrip_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            html_path = out_dir / "mote_transkripsjon.html"
            turns: list[mote_core.TurnDict] = [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.2, "text": "Hei, jeg heter Kari Nordmann."},
                {"speaker": "SPEAKER_01", "start": 4.5, "end": 9.0, "text": "Hei Kari, jeg er Ola Hansen."},
            ]
            speaker_map = {
                "SPEAKER_00": "Kari Nordmann, Prosjektleder (SPEAKER_00)",
                "SPEAKER_01": "Ola Hansen, Utvikler (SPEAKER_01)",
            }

            llm_txt = mote_core.build_transcript_html_and_text(
                turns, speaker_map, html_path, "Testmøte"
            )
            self.assertTrue(html_path.is_file())
            self.assertIn("Kari Nordmann", llm_txt)

            parsed_turns, parsed_map = mote_core.load_turns_from_transcript_file(html_path)
            self.assertEqual(len(parsed_turns), 2)
            self.assertEqual(parsed_turns[0]["speaker"], "SPEAKER_00")
            self.assertEqual(parsed_turns[1]["text"], "Hei Kari, jeg er Ola Hansen.")
            self.assertEqual(parsed_map["SPEAKER_00"], speaker_map["SPEAKER_00"])

            # Verify CLI --status on transcript input
            st = mote_core.get_checkpoint_status_dict(out_dir, html_path)
            self.assertTrue(st["is_transcript_input"])
            self.assertTrue(st["s1_asr"])
            self.assertTrue(st["s2_align"])
            self.assertTrue(st["s3_diarize"])
            self.assertTrue(st["s4_names"])
            self.assertFalse(st["s5_notes"])

    def test_live_audio_monitor_wav_tailing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "growing.wav"
            # Write 2.5 seconds of -18.3 dBFS 16kHz mono PCM
            pcm_2_5s = struct.pack("<h", 4000) * (16000 * 2 + 8000)
            wav_path.write_bytes(mote_core.build_wav_bytes_from_pcm16(pcm_2_5s, 16000))

            levels: list[dict[str, object]] = []
            chunks: list[str] = []
            mon = mote_core.LiveAudioMonitor(
                wav_path=wav_path,
                chunk_seconds=2.0,
                enable_asr=False,
                on_level=lambda lvl: levels.append(lvl),
                on_chunk=lambda line, _lvl, _txt: chunks.append(line),
            )
            mon.start()
            try:
                for _ in range(15):
                    if levels and chunks:
                        break
                    time.sleep(0.15)
            finally:
                mon.stop()

            self.assertGreaterEqual(len(levels), 1)
            self.assertEqual(levels[0]["status"], "godt")
            self.assertGreaterEqual(len(chunks), 1)
            self.assertIn("00:00–00:02 | -18 dBFS (godt)", chunks[0])


class TestSyntheticE2E(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_E2E") == "1",
        "Sett RUN_E2E=1 for å kjøre full WhisperX + Ollama ende-til-ende-test på syntetisk møte.",
    )
    def test_synthetic_4_speaker_meeting_e2e(self) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        m_dir = base_dir / "synthetic_test" / "meetings" / "mote_1_sikkerhet"
        opus_path = m_dir / "mote_1_sikkerhet.opus"
        agenda_path = m_dir / "fasit_agenda.md"
        self.assertTrue(opus_path.is_file(), f"Mangler {opus_path}")

        res = mote_core.run_pipeline(
            input_path=opus_path,
            out_dir=opus_path.parent,
            min_speakers=4,
            max_speakers=4,
            speaker_names_hint=(
                "Kari Nordmann (Prosjektleder), Ola Hansen (Systemutvikler), "
                "Ingrid Berg (Sikkerhetsarkitekt), Per Johansen (Økonomiansvarlig)"
            ),
            meeting_title="Statusmøte Sikkerhetsplattform 2026",
            context_files=[agenda_path],
            note_mode="mote",
            task_mode="auto_resume",
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["html_path"].is_file())
        self.assertTrue(res["notes_path"].is_file())
        self.assertTrue(res["notes_html_path"].is_file())


if __name__ == "__main__":
    unittest.main()
