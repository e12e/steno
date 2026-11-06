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
        self.assertEqual(len(scenarios), 10)

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
            elif step == "Given the desktop GUI template app.html is loaded":
                ctx["app_html"] = mote_gui._load_app_html()
            elif step == "When inspecting the app.html layout structure":
                self.assertTrue(bool(ctx.get("app_html")))
            elif step == "Then advanced settings and context textareas are placed in collapsible details drawers with a live summary pill":
                app_html = str(ctx["app_html"])
                self.assertIn('id="advanced_settings_drawer"', app_html)
                self.assertIn('id="context_notes_drawer"', app_html)
                self.assertIn('id="settings_summary_pill"', app_html)
                self.assertIn("updateSettingsSummary()", app_html)
            elif step == "And all required pywebview DOM element IDs are preserved":
                app_html = str(ctx["app_html"])
                required_ids = [
                    "dropzone",
                    "selected-file",
                    "rec_btn",
                    "rec_timer",
                    "live_level_badge",
                    "auto_speak_guide",
                    "live_transcribe_chk",
                    "prompt_banner",
                    "live_check_panel",
                    "meeting_title",
                    "speaker_names_hint",
                    "agenda_text",
                    "manual_notes_text",
                    "min_speakers",
                    "max_speakers",
                    "note_mode",
                    "out_dir",
                    "html_name",
                    "notes_name",
                    "notes_html_name",
                    "model_profile",
                    "whisper_model",
                    "ollama_model",
                    "language",
                    "hf_token",
                    "hw_resource_banner",
                    "hw_resource_text",
                    "apply_rec_btn",
                    "ckpt_bar",
                    "ckpt_s1",
                    "ckpt_s2",
                    "ckpt_s3",
                    "ckpt_s4",
                    "ckpt_s5",
                    "task_mode",
                    "conflict-msg",
                    "check_audio_btn",
                    "stop_btn",
                    "run_btn",
                    "progress_label",
                    "progress_val",
                    "progress_bar",
                    "log",
                ]
                for dom_id in required_ids:
                    self.assertIn(f'id="{dom_id}"', app_html, f"Missing required DOM id='{dom_id}' in app.html")
            elif step == 'Given the CLI parser is built with ui_lang "en" and "no"':
                ctx["parser_en"] = mote_core.build_cli_parser(ui_lang="en")  # type: ignore[call-arg]
                ctx["parser_no"] = mote_core.build_cli_parser(ui_lang="no")  # type: ignore[call-arg]
            elif step == "When formatting CLI help output for both languages":
                ctx["help_en"] = ctx["parser_en"].format_help()  # type: ignore[attr-defined]
                ctx["help_no"] = ctx["parser_no"].format_help()  # type: ignore[attr-defined]
            elif step == 'Then the English help text contains "Steno - a modern annalist for your meetings - an automaton stenographer" and "100% local meeting transcription" and "--ui-lang"':
                self.assertIn("Steno - a modern annalist for your meetings - an automaton stenographer", str(ctx["help_en"]))
                self.assertIn("100% local meeting transcription", str(ctx["help_en"]))
                self.assertIn("--ui-lang", str(ctx["help_en"]))
            elif step == 'And the Norwegian help text contains "Steno - a modern annalist for your meetings - an automaton stenographer" and "100% lokal møtetranskripsjon" and "--ui-lang"':
                self.assertIn("Steno - a modern annalist for your meetings - an automaton stenographer", str(ctx["help_no"]))
                self.assertIn("100% lokal møtetranskripsjon", str(ctx["help_no"]))
                self.assertIn("--ui-lang", str(ctx["help_no"]))
            elif step == 'And app.html contains the English and Norwegian UI localization toggle "setUiLanguage"':
                app_html = mote_gui._load_app_html()
                self.assertIn("setUiLanguage", app_html)
                self.assertIn('id="ui_lang_select"', app_html)
            elif step == "Given cross-platform ffmpeg microphone commands are generated for darwin, win32, and linux":
                fn = getattr(mote_core, "build_ffmpeg_mic_record_cmd", None)
                self.assertIsNotNone(fn, "build_ffmpeg_mic_record_cmd missing in mote.core")
                assert callable(fn)
                ctx["cmd_darwin"] = fn("ffmpeg", Path("/tmp/rec.wav"), platform_str="darwin", os_name="posix")
                ctx["cmd_win32"] = fn("ffmpeg.exe", Path("C:/tmp/rec.wav"), platform_str="win32", os_name="nt")
                ctx["cmd_linux"] = fn("ffmpeg", Path("/tmp/rec.wav"), platform_str="linux", os_name="posix")
            elif step == "When inspecting the generated ffmpeg arguments and the www/rec release folder":
                root_dir = Path(__file__).resolve().parent.parent
                ctx["www_rec_index"] = root_dir / "www" / "rec" / "index.html"
                ctx["www_rec_script"] = root_dir / "www" / "rec" / "steno.py"
            elif step == 'Then darwin uses "avfoundation", win32 uses "dshow", and linux uses "pulse" or "alsa"':
                self.assertIn("avfoundation", list(ctx["cmd_darwin"]))  # type: ignore[arg-type]
                self.assertIn("dshow", list(ctx["cmd_win32"]))  # type: ignore[arg-type]
                cmd_linux = list(ctx["cmd_linux"])  # type: ignore[arg-type]
                self.assertTrue("pulse" in cmd_linux or "alsa" in cmd_linux)
            elif step == 'And www/rec/index.html and www/rec/steno.py exist with "Steno" and "an automaton stenographer" in The Chronicle broadsheet style':
                idx_path = ctx["www_rec_index"]
                scr_path = ctx["www_rec_script"]
                assert isinstance(idx_path, Path) and isinstance(scr_path, Path)
                self.assertTrue(idx_path.is_file(), f"Missing {idx_path}")
                self.assertTrue(scr_path.is_file(), f"Missing {scr_path}")
                idx_html = idx_path.read_text(encoding="utf-8")
                self.assertIn("The Chronicle", idx_html)
                self.assertIn("broadsheet-grid", idx_html)
                self.assertIn("steno.py", idx_html)
                self.assertIn("a modern annalist for your meetings", idx_html.lower())
                self.assertIn("an automaton stenographer", idx_html.lower())
                self.assertIn("macOS", idx_html)
                self.assertIn("Windows", idx_html)
                self.assertIn("Linux", idx_html)
            elif step == "Given a target machine without python3 pre-installed":
                root_dir = Path(__file__).resolve().parent.parent
                ctx["root_dir"] = root_dir
                ctx["dist_steno"] = root_dir / "dist" / "steno.py"
                ctx["www_rec_dir"] = root_dir / "www" / "rec"
            elif step == "When inspecting the polyglot header of dist/steno.py and the universal installers in www/rec":
                dist_steno = ctx["dist_steno"]
                assert isinstance(dist_steno, Path)
                ctx["steno_head"] = "\n".join(dist_steno.read_text(encoding="utf-8").splitlines()[:35])
            elif step == 'Then dist/steno.py uses "# 2>nul & goto :windows_boot" so both /bin/sh and cmd.exe boot without python3':
                head = str(ctx["steno_head"])
                self.assertIn("#!/bin/sh", head)
                self.assertIn("# 2>nul & goto :windows_boot", head)
                self.assertNotIn("goto :windows_boot >/dev/null", head)
                self.assertIn("wget -qO- https://astral.sh/uv/install.sh", head)
                self.assertIn("/etc/alpine-release", head)
            elif step == "And www/rec/install.sh installs the steno launcher into a custom prefix using /bin/sh":
                import subprocess

                www_rec_dir = ctx["www_rec_dir"]
                assert isinstance(www_rec_dir, Path)
                install_sh = www_rec_dir / "install.sh"
                self.assertTrue(install_sh.is_file(), f"Missing {install_sh}")
                with tempfile.TemporaryDirectory() as tmp:
                    env = os.environ.copy()
                    env["STENO_INSTALL_DIR"] = str(Path(tmp) / "share" / "steno")
                    env["STENO_BIN_DIR"] = str(Path(tmp) / "bin")
                    env["STENO_SOURCE_FILE"] = str(www_rec_dir / "steno.py")
                    env["STENO_SKIP_UV_WARMUP"] = "1"
                    proc = subprocess.run(
                        ["/bin/sh", str(install_sh)],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(proc.returncode, 0, msg=f"install.sh failed: {proc.stderr}\n{proc.stdout}")
                    installed_bin = Path(tmp) / "bin" / "steno"
                    installed_script = Path(tmp) / "share" / "steno" / "steno.py"
                    self.assertTrue(installed_bin.is_file(), "install.sh did not create bin/steno wrapper")
                    self.assertTrue(installed_script.is_file(), "install.sh did not copy steno.py")
            elif step == "And www/rec/install.ps1 and www/rec/steno.cmd provide zero-Python Windows installation and double-click launch":
                www_rec_dir = ctx["www_rec_dir"]
                assert isinstance(www_rec_dir, Path)
                install_ps1 = www_rec_dir / "install.ps1"
                steno_cmd = www_rec_dir / "steno.cmd"
                idx_html = (www_rec_dir / "index.html").read_text(encoding="utf-8")
                self.assertTrue(install_ps1.is_file(), f"Missing {install_ps1}")
                self.assertTrue(steno_cmd.is_file(), f"Missing {steno_cmd}")
                ps1_text = install_ps1.read_text(encoding="utf-8")
                cmd_text = steno_cmd.read_text(encoding="utf-8")
                self.assertIn("astral.sh/uv/install.ps1", ps1_text)
                self.assertIn("steno.py", ps1_text)
                self.assertIn("uv run --python 3.12", cmd_text)
                self.assertIn("install.sh", idx_html)
                self.assertIn("install.ps1", idx_html)
                self.assertIn("steno.cmd", idx_html)
            elif step == "Given the Cosmopolitan APE binary dist/steno.com and www/rec/steno.com":
                root_dir = Path(__file__).resolve().parent.parent
                ctx["dist_com"] = root_dir / "dist" / "steno.com"
                ctx["www_com"] = root_dir / "www" / "rec" / "steno.com"
                ctx["www_idx"] = root_dir / "www" / "rec" / "index.html"
            elif step == "When running install_steno_app and uninstall_steno_app in an isolated prefix and inspecting the GUI File menu":
                install_fn = getattr(mote_core, "install_steno_app", None)
                uninstall_fn = getattr(mote_core, "uninstall_steno_app", None)
                self.assertTrue(callable(install_fn), "mote.core.install_steno_app is missing")
                self.assertTrue(callable(uninstall_fn), "mote.core.uninstall_steno_app is missing")
                ctx["install_fn"] = install_fn
                ctx["uninstall_fn"] = uninstall_fn
                ctx["gui_html"] = mote_gui.HTML
            elif step == "Then dist/steno.com has a valid MZqFpD APE + 64-bit PE32+ header and embeds the steno.py payload":
                import struct

                dist_com = ctx["dist_com"]
                www_com = ctx["www_com"]
                www_idx = ctx["www_idx"]
                assert isinstance(dist_com, Path) and isinstance(www_com, Path) and isinstance(www_idx, Path)
                self.assertTrue(dist_com.is_file(), f"Missing {dist_com}")
                self.assertTrue(www_com.is_file(), f"Missing {www_com}")
                com_bytes = dist_com.read_bytes()
                self.assertEqual(com_bytes, www_com.read_bytes(), "www/rec/steno.com is out of sync with dist/steno.com")
                self.assertTrue(com_bytes.startswith(b"MZqFpD="), "steno.com must start with Cosmopolitan APE MZqFpD= magic")
                e_lfanew = struct.unpack_from("<I", com_bytes, 0x3C)[0]
                self.assertEqual(com_bytes[e_lfanew : e_lfanew + 4], b"PE\x00\x00", "Invalid PE signature at e_lfanew")
                machine = struct.unpack_from("<H", com_bytes, e_lfanew + 4)[0]
                self.assertEqual(machine, 0x8664, "PE header must target x86_64 (0x8664)")
                opt_magic = struct.unpack_from("<H", com_bytes, e_lfanew + 24)[0]
                self.assertEqual(opt_magic, 0x020B, "PE optional header must be PE32+ (0x020B)")
                self.assertIn(b"# === STENO_PAYLOAD_BEGIN ===", com_bytes)
                self.assertIn("steno.com", www_idx.read_text(encoding="utf-8"))
            elif step == "And install_steno_app creates the cached app directory, CLI launcher, and desktop entry, and uninstall_steno_app removes them cleanly":
                install_fn = ctx["install_fn"]
                uninstall_fn = ctx["uninstall_fn"]
                assert callable(install_fn) and callable(uninstall_fn)
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_root = Path(tmp)
                    res_inst = install_fn(target_root=tmp_root, platform_str="darwin")
                    self.assertTrue(res_inst["ok"])
                    app_dir = Path(res_inst["app_dir"])
                    launcher = Path(res_inst["launcher_path"])
                    desktop_entry = Path(res_inst["desktop_entry"])
                    mote_cache = tmp_root / ".local" / "share" / "mote_transcribe"
                    mote_cache.mkdir(parents=True, exist_ok=True)
                    (mote_cache / "dummy_model.bin").write_bytes(b"1234")
                    self.assertTrue(app_dir.is_dir())
                    self.assertTrue(launcher.is_file())
                    self.assertTrue(desktop_entry.exists())
                    res_uninst = uninstall_fn(target_root=tmp_root, platform_str="darwin")
                    self.assertTrue(res_uninst["ok"])
                    self.assertFalse(app_dir.exists(), "app_dir should be removed on uninstall")
                    self.assertFalse(mote_cache.exists(), "mote_transcribe cache should be removed on uninstall")
                    self.assertFalse(launcher.exists(), "CLI launcher should be removed on uninstall")
                    self.assertFalse(desktop_entry.exists(), "Desktop entry should be removed on uninstall")
            elif step == "And the desktop GUI includes a File menu with an Uninstall option and a Yes/No confirmation modal connected to MoteApi.uninstall_app":
                gui_html = str(ctx["gui_html"])
                self.assertIn("file_menu_btn", gui_html)
                self.assertIn("menu_uninstall_btn", gui_html)
                self.assertIn("uninstall_modal", gui_html)
                self.assertIn("confirm_uninstall_yes_btn", gui_html)
                self.assertIn("confirm_uninstall_no_btn", gui_html)
                api = mote_gui.MoteApi()
                self.assertTrue(hasattr(api, "uninstall_app"), "MoteApi.uninstall_app is missing")
                self.assertTrue(hasattr(api, "install_app"), "MoteApi.install_app is missing")
                parser = mote_core.build_cli_parser("en", ["--help"])
                help_en = parser.format_help()
                self.assertIn("--install", help_en)
                self.assertIn("--uninstall", help_en)
            else:
                self.fail(f"Unrecognized Gherkin step in '{name}': {step}")


class TestAARImprovements(unittest.TestCase):
    def test_dist_single_file_is_in_sync_with_sources(self) -> None:
        from scripts import build_single_file

        self.assertEqual(build_single_file.DIST_FILE.name, "steno.py")
        expected = build_single_file.generate_single_file_content()
        actual = build_single_file.DIST_FILE.read_text(encoding="utf-8")
        self.assertEqual(
            actual,
            expected,
            "dist/steno.py is out of sync with src/mote/* — run scripts/build_single_file.py",
        )
        www_rec_script = build_single_file.ROOT_DIR / "www" / "rec" / "steno.py"
        self.assertTrue(www_rec_script.is_file(), "www/rec/steno.py is missing")
        self.assertEqual(
            www_rec_script.read_text(encoding="utf-8"),
            expected,
            "www/rec/steno.py is out of sync with dist/steno.py — run scripts/build_single_file.py",
        )

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
