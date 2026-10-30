"""Unit, doctest, and CI integration tests for mote.core, transcribe CLI, and context/checkpoint handling."""

from __future__ import annotations

import doctest
import io
import os
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

# Add src/ to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe
from mote import core as mote_core


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, ignore: str | None
) -> unittest.TestSuite:
    """Register doctests from mote.core and transcribe into standard unittest discovery."""
    tests.addTests(doctest.DocTestSuite(mote_core))
    tests.addTests(doctest.DocTestSuite(transcribe))
    return tests


class TestDoctestsExplicit(unittest.TestCase):
    def test_mote_core_doctests(self) -> None:
        res = doctest.testmod(mote_core, verbose=False)
        self.assertEqual(res.failed, 0, f"mote.core doctests failed: {res}")
        self.assertGreater(res.attempted, 0)

    def test_transcribe_doctests(self) -> None:
        res = doctest.testmod(transcribe, verbose=False)
        self.assertEqual(res.failed, 0, f"transcribe doctests failed: {res}")


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
            buf = io.StringIO()
            old_stdout = os.dup(1)
            try:
                st = mote_core.get_checkpoint_status_dict(out_dir, html_path)
            finally:
                os.close(old_stdout)
            self.assertTrue(st["is_transcript_input"])
            self.assertTrue(st["s1_asr"])
            self.assertTrue(st["s2_align"])
            self.assertTrue(st["s3_diarize"])
            self.assertTrue(st["s4_names"])
            self.assertFalse(st["s5_notes"])
            _ = buf

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
        opus_path = base_dir / "synthetic_test" / "syntetisk_mote_4_talere.opus"
        agenda_path = base_dir / "synthetic_test" / "fasit_agenda.md"
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
