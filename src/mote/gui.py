# GUI for Meeting Recording, Guided Voice Enrollment, Agenda Context, WhisperX & Ollama.
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mote.core import (  # noqa: E402
    MODEL_PROFILES,
    WHISPERX_STAGE_RUNNER_CODE,
    LiveAudioMonitor,
    build_cli_parser,
    build_ffmpeg_mic_record_cmd,
    build_runtime_env,
    build_whisper_initial_prompt,
    clean_base_stem,
    cli_main,
    detect_hardware_resources,
    ensure_ffmpeg,
    evaluate_model_resource_fit,
    extract_context_from_file,
    get_checkpoint_dir,
    get_checkpoint_status_dict,
    get_initial_hf_token,
    is_diarization_cached,
    is_transcript_file,
    resolve_moved_audio_path,
    resolve_speaker_bounds,
    resolve_ui_lang,
    run_live_check_on_file,
    run_pipeline,
    unique_filename,
)
from mote.template_loader import load_template  # noqa: E402

try:
    import webview  # noqa: E402
    from webview import FileDialog  # noqa: E402
    from webview.dom import DOMEventHandler  # noqa: E402
except ImportError:
    webview = None  # type: ignore[assignment]
    FileDialog = None  # type: ignore[assignment, misc]
    DOMEventHandler = None  # type: ignore[assignment, misc]


def _get_project_base_dir() -> Path:
    here = Path(__file__).resolve().parent
    if here.name == "mote" and here.parent.name == "src":
        return here.parent.parent
    return here


class MoteApi:
    def __init__(self) -> None:
        self.window: Any = None
        self._rec_proc: subprocess.Popen[str] | None = None
        self._rec_path: Path | None = None
        self._live_monitor: LiveAudioMonitor | None = None
        self._tts_proc: subprocess.Popen[bytes] | None = None
        self._active_proc: subprocess.Popen[str] | None = None
        self._cancel_requested: bool = False
        self._log_lines: list[str] = []

    def shutdown_children(self) -> None:
        """Stop any running background recording, TTS, or WhisperX/Ollama child processes when GUI closes."""
        self._cancel_requested = True
        if self._live_monitor is not None:
            try:
                self._live_monitor.stop()
            except Exception:
                pass
            self._live_monitor = None

        for proc in (self._active_proc, self._rec_proc, self._tts_proc):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def get_defaults(self) -> dict[str, Any]:
        today = datetime.now().strftime("%Y-%m-%d")
        base_dir = _get_project_base_dir()
        default_dir = base_dir / "data" if (base_dir / "data").is_dir() else (Path.home() / "Downloads")
        hw = detect_hardware_resources(default_dir)
        fit = evaluate_model_resource_fit(
            hw["recommended_whisper"], hw["recommended_ollama"], hw
        )
        return {
            "hf_token": get_initial_hf_token(),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(default_dir),
            "default_meeting_title": f"Møte {today}",
            "hardware": hw,
            "resource_fit": fit,
            "profiles": MODEL_PROFILES,
            "ui_lang": resolve_ui_lang(),
        }

    def check_model_resources(
        self,
        whisper_model: str,
        ollama_model: str,
        out_dir_str: str = "",
    ) -> dict[str, Any]:
        """Evaluate whether the selected Whisper + Ollama model combination fits local hardware."""
        out_dir = Path(out_dir_str) if out_dir_str else None
        hw = detect_hardware_resources(out_dir)
        fit = evaluate_model_resource_fit(whisper_model, ollama_model, hw)
        return {"hardware": hw, "resource_fit": fit}

    def stop_job(self) -> dict[str, Any]:
        """Stop/pause the active transcription or LLM job while keeping completed checkpoints."""
        self._cancel_requested = True
        proc = self._active_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self._log(
            "==> STOPPET AV BRUKER: Fullførte steg er lagret i sjekkpunkt. "
            "Trykk «Start / Fortsett» når du vil fortsette der jobben slapp."
        )
        self._set_progress(0, "Pauset / Stoppet (kan fortsettes)")
        return {"ok": True}

    def get_checkpoint_status(
        self,
        out_dir_str: str,
        audio_path_str: str,
        html_name_str: str = "",
        notes_name_str: str = "",
    ) -> dict[str, Any]:
        if not audio_path_str:
            return {
                "has_input": False,
                "is_transcript_input": False,
                "s1_asr": False,
                "s2_align": False,
                "s3_diarize": False,
                "s4_names": False,
                "s5_notes": False,
            }
        base_dir = _get_project_base_dir()
        audio_path = resolve_moved_audio_path(Path(audio_path_str), base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        return get_checkpoint_status_dict(out_dir, audio_path, html_name_str, notes_name_str)

    def save_log_dialog(self, log_text: str, out_dir_str: str, audio_path_str: str) -> str | None:
        if not self.window:
            return None
        base_dir = _get_project_base_dir()
        stem = clean_base_stem(Path(audio_path_str)) if audio_path_str else "mote"
        out_dir = Path(out_dir_str) if out_dir_str else base_dir
        if not out_dir.is_dir():
            out_dir = base_dir / "data" if (base_dir / "data").is_dir() else base_dir
        default_name = unique_filename(out_dir, f"{stem}_logg", ".txt")
        result = self.window.create_file_dialog(
            FileDialog.SAVE,
            directory=str(out_dir),
            save_filename=default_name,
        )
        if not result:
            return None
        target_str = result if isinstance(result, str) else (result[0] if len(result) > 0 else "")
        if not target_str:
            return None
        target_path = Path(target_str)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = log_text.strip() or "\n".join(self._log_lines)
        target_path.write_text(content + "\n", encoding="utf-8")
        self._log(f"==> Logg lagret til: {target_path}")
        return str(target_path)

    def speak_intro_prompt(self, ui_lang: str = "no") -> None:
        """Speak the guided voice introduction prompt using local OS TTS (Norwegian or English)."""
        is_en = str(ui_lang).strip().lower().startswith("en")
        prompt_text = (
            (
                "Recording is ready. "
                "Step one: The meeting chair briefly states the meeting topic and today's date. "
                "Step two: Go around the table so each participant states their full name and role in a complete sentence."
            )
            if is_en
            else (
                "Opptaket er klart. "
                "Steg en: Møteleder sier kort hva møtet gjelder og dagens dato. "
                "Steg to: Ta en runde rundt bordet der hver deltaker sier sitt navn og sin tittel eller rolle."
            )
        )
        if self._tts_proc and self._tts_proc.poll() is None:
            self._tts_proc.terminate()
            return

        if sys.platform == "darwin":
            self._tts_proc = subprocess.Popen(["say", prompt_text])
        elif os.name == "nt":
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{prompt_text.replace(chr(39), '')}')"
            )
            self._tts_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            voice = "en" if is_en else "no"
            if shutil.which("espeak-ng"):
                self._tts_proc = subprocess.Popen(["espeak-ng", "-v", voice, prompt_text])
            elif shutil.which("espeak"):
                self._tts_proc = subprocess.Popen(["espeak", "-v", voice, prompt_text])

    def _on_live_level(self, lvl: dict[str, Any]) -> None:
        if self.window:
            payload = json.dumps(lvl, ensure_ascii=False)
            self.window.evaluate_js(f"updateLiveLevel({payload})")

    def _on_live_chunk(self, line: str, lvl: dict[str, Any], _text: str) -> None:
        self._log(f"[SANNTIDS-LYDSJEKK] {line}")
        if self.window:
            escaped_line = json.dumps(line, ensure_ascii=False)
            escaped_lvl = json.dumps(lvl, ensure_ascii=False)
            self.window.evaluate_js(f"appendLiveChunk({escaped_line}, {escaped_lvl})")

    def start_recording(
        self,
        out_dir_str: str,
        speak_guide: bool,
        live_transcribe: bool = True,
        whisper_model: str = "NbAiLab/nb-whisper-small",
        language: str = "no",
        meeting_title: str = "",
        speaker_names_hint: str = "",
        agenda_text: str = "",
    ) -> dict[str, Any]:
        """Start recording 16kHz mono WAV audio from the default microphone via ffmpeg + optional live ASR check."""
        if self._rec_proc and self._rec_proc.poll() is None:
            return {"ok": False, "error": "Opptak pågår allerede."}

        env = build_runtime_env()
        ffmpeg_bin = ensure_ffmpeg(env, self._log)

        out_dir = Path(out_dir_str).resolve() if out_dir_str else (Path.home() / "Downloads")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        rec_file = out_dir / unique_filename(out_dir, f"moteopptak_{stamp}", ".wav")
        cmd = build_ffmpeg_mic_record_cmd(ffmpeg_bin, rec_file, env=env)

        try:
            self._rec_proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._rec_path = rec_file
            self._log(f"==> OPPTAK STARTET: Lagrer mikrofon-lyd til {rec_file.name}")
            if speak_guide:
                self.speak_intro_prompt()

            try:
                init_prompt = build_whisper_initial_prompt(meeting_title, speaker_names_hint, agenda_text)
                mon = LiveAudioMonitor(
                    wav_path=rec_file,
                    whisper_model=whisper_model,
                    language=language,
                    initial_prompt=init_prompt,
                    chunk_seconds=5.0,
                    enable_asr=bool(live_transcribe),
                    on_level=self._on_live_level,
                    on_chunk=self._on_live_chunk,
                    log_fn=self._log,
                )
                mon.start()
                self._live_monitor = mon
                if live_transcribe:
                    self._log(
                        f"==> Grov sanntidstranskripsjon aktivert ({mon.whisper_model}, hvert 5. sekund) for lydsjekk."
                    )
            except Exception as mon_exc:
                self._log(f"Merk: Kunne ikke starte sanntids-lydsjekk ({mon_exc}).")

            return {"ok": True, "audio_path": str(rec_file)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_recording(self, out_dir_str: str) -> dict[str, Any] | None:
        """Gracefully stop the active ffmpeg microphone recording and return suggested file names."""
        if self._live_monitor is not None:
            try:
                self._live_monitor.stop()
            except Exception:
                pass
            self._live_monitor = None

        if not self._rec_proc:
            return None

        proc = self._rec_proc
        self._rec_proc = None
        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    proc.send_signal(signal.SIGINT)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

        if self._rec_path and self._rec_path.is_file():
            self._log(f"==> OPPTAK STOPPET: {self._rec_path} ({self._rec_path.stat().st_size // 1024} KB)")
            return self.suggest_output_names(str(self._rec_path), out_dir_str)
        self._log("==> Merk: Opptaksfilen ble ikke opprettet (sjekk mikrofontilgang i Systeminnstillinger).")
        return None

    def run_quick_audio_check(
        self,
        audio_path_str: str,
        whisper_model: str = "NbAiLab/nb-whisper-small",
        language: str = "no",
        meeting_title: str = "",
        speaker_names_hint: str = "",
        agenda_text: str = "",
    ) -> None:
        """Run a 20-second coarse transcription + dBFS audio check on the selected audio file in a background thread."""
        if not audio_path_str:
            return

        def _check_worker() -> None:
            try:
                init_prompt = build_whisper_initial_prompt(meeting_title, speaker_names_hint, agenda_text)
                res = run_live_check_on_file(
                    input_path=Path(audio_path_str),
                    whisper_model=whisper_model,
                    language=language,
                    chunk_seconds=5.0,
                    max_seconds=20.0,
                    initial_prompt=init_prompt,
                    log_fn=self._log,
                )
                avg_db = res.get("avg_rms_dbfs", -96.0)
                st_val = "godt" if res.get("good_chunks", 0) > 0 else "lavt"
                lvl_payload = json.dumps(
                    {"status": st_val, "rms_dbfs": avg_db, "label": f"Snitt: {avg_db} dBFS"},
                    ensure_ascii=False,
                )
                for ln in res.get("lines", []):
                    if self.window:
                        ln_payload = json.dumps(ln, ensure_ascii=False)
                        self.window.evaluate_js(f"appendLiveChunk({ln_payload}, {lvl_payload})")
            except Exception as exc:
                self._log(f"FEIL under lydsjekk: {exc}")

        threading.Thread(target=_check_worker, daemon=True).start()

    def suggest_output_names(self, audio_path_str: str, out_dir_str: str) -> dict[str, Any]:
        base_dir = _get_project_base_dir()
        audio_path = resolve_moved_audio_path(Path(audio_path_str), base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        stem = clean_base_stem(audio_path)

        ckpt_dir = get_checkpoint_dir(out_dir, audio_path)
        std_html = f"{stem}_transkripsjon.html"
        std_md = f"{stem}_referat.md"
        std_rhtml = f"{stem}_referat.html"
        has_partial_ckpt = ckpt_dir.is_dir() and any(ckpt_dir.glob("*.json"))

        if has_partial_ckpt and not ((out_dir / std_md).exists() and (out_dir / std_rhtml).exists()):
            html_name = (
                std_html
                if std_html != audio_path.name
                else unique_filename(out_dir, f"{stem}_transkripsjon", ".html")
            )
            notes_name = std_md
            notes_html_name = std_rhtml
        else:
            html_name = unique_filename(out_dir, f"{stem}_transkripsjon", ".html")
            notes_name = unique_filename(out_dir, f"{stem}_referat", ".md")
            notes_html_name = unique_filename(out_dir, f"{stem}_referat", ".html")

        return {
            "audio_path": str(audio_path),
            "out_dir": str(out_dir),
            "html_name": html_name,
            "notes_name": notes_name,
            "notes_html_name": notes_html_name,
            "is_transcript_input": is_transcript_file(audio_path),
        }

    def pick_audio_file(self) -> dict[str, Any] | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(FileDialog.OPEN, allow_multiple=False)
        if result and len(result) > 0:
            p = Path(result[0])
            return self.suggest_output_names(str(p), str(p.parent))
        return None

    def pick_agenda_file(self) -> str | None:
        """Let user pick one or more agenda, slide (.pptx/.pdf/.odp), or document (.docx/.md/.txt) files."""
        if not self.window:
            return None
        result = self.window.create_file_dialog(FileDialog.OPEN, allow_multiple=True)
        if result and len(result) > 0:
            chunks: list[str] = []
            for item in result:
                p = Path(item)
                if p.is_file():
                    extracted = extract_context_from_file(p)
                    if extracted:
                        chunks.append(extracted)
                        self._log(f"==> Lastet inn kontekst fra {p.name} ({len(extracted)} tegn)")
            if chunks:
                return "\n\n".join(chunks)
        return None

    def load_context_file_path(self, file_path_str: str) -> str | None:
        """Extract context from a dropped slide/document file."""
        p = Path(file_path_str)
        if p.is_file():
            extracted = extract_context_from_file(p)
            if extracted:
                self._log(f"==> Lastet inn kontekst fra {p.name} ({len(extracted)} tegn)")
                return extracted
        return None

    def pick_output_dir(self, current_audio_path: str) -> dict[str, Any] | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(FileDialog.FOLDER)
        if result and len(result) > 0:
            folder = str(result[0])
            if current_audio_path:
                return self.suggest_output_names(current_audio_path, folder)
            return {"out_dir": folder}
        return None

    def check_conflicts(
        self, out_dir_str: str, html_name: str, notes_name: str, notes_html_name: str
    ) -> list[str]:
        out_dir = Path(out_dir_str).resolve()
        conflicts = []
        for name in (html_name.strip(), notes_name.strip(), notes_html_name.strip()):
            if name:
                candidate = out_dir / Path(name).name
                if candidate.exists():
                    conflicts.append(candidate.name)
        return conflicts

    def reveal_in_finder(self, folder_path: str) -> None:
        if not folder_path or not Path(folder_path).is_dir():
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder_path])
        elif os.name == "nt":
            os.startfile(folder_path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", folder_path])

    def start_job(
        self,
        audio_path: str,
        out_dir: str,
        html_name: str,
        notes_name: str,
        notes_html_name: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
        min_speakers: str,
        max_speakers: str,
        speaker_names_hint: str,
        meeting_title: str,
        agenda_text: str,
        note_mode: str = "auto",
        task_mode: str = "auto_resume",
        manual_notes_text: str = "",
    ) -> None:
        threading.Thread(
            target=self._worker,
            args=(
                audio_path,
                out_dir,
                html_name,
                notes_name,
                notes_html_name,
                whisper_model,
                ollama_model,
                language,
                hf_token,
                min_speakers,
                max_speakers,
                speaker_names_hint,
                meeting_title,
                agenda_text,
                note_mode,
                task_mode,
                manual_notes_text,
            ),
            daemon=True,
        ).start()

    def _log(self, msg: str) -> None:
        self._log_lines.append(msg)
        if self.window:
            escaped = json.dumps(msg)
            self.window.evaluate_js(f"appendLog({escaped})")

    def _refresh_checkpoints_ui(self) -> None:
        if self.window:
            self.window.evaluate_js("refreshCheckpointBar()")

    def _auto_save_error_log(self, out_dir: Path, stem: str) -> None:
        try:
            base_dir = _get_project_base_dir()
            target_dir = (
                out_dir
                if out_dir.is_dir()
                else (base_dir / "data" if (base_dir / "data").is_dir() else base_dir)
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            log_file = target_dir / unique_filename(target_dir, f"{stem}_feillogg", ".txt")
            log_file.write_text("\n".join(self._log_lines) + "\n", encoding="utf-8")
            self._log(f"==> Feillogg lagret automatisk: {log_file}")
        except Exception:
            pass

    def _set_progress(self, percent: int, label: str) -> None:
        if self.window:
            lbl = json.dumps(label)
            self.window.evaluate_js(f"updateProgress({percent}, {lbl})")

    def _set_running(self, running: bool) -> None:
        if self.window:
            self.window.evaluate_js(f"setRunning({'true' if running else 'false'})")

    def _set_active_proc(self, proc: subprocess.Popen[str] | None) -> None:
        self._active_proc = proc

    def _worker(
        self,
        audio_path_str: str,
        out_dir_str: str,
        html_name_str: str,
        notes_name_str: str,
        notes_html_name_str: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
        min_speakers_str: str,
        max_speakers_str: str,
        speaker_names_hint: str,
        meeting_title: str,
        agenda_text: str,
        note_mode: str = "auto",
        task_mode: str = "auto_resume",
        manual_notes_text: str = "",
    ) -> None:
        self._cancel_requested = False
        self._log_lines = [f"Starter jobb (oppgavemodus: {task_mode})..."]
        self._set_running(True)
        self._set_progress(5, "Forbereder miljø og sjekkpunkter...")
        base_dir = _get_project_base_dir()
        orig_audio_path = Path(audio_path_str)
        audio_path = resolve_moved_audio_path(orig_audio_path, base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        try:
            out_dir = out_dir.resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            if not audio_path.is_file():
                self._log(f"FEIL: Fant ikke inndatafilen {audio_path}")
                self._set_progress(0, "Feil: Fil mangler")
                self._auto_save_error_log(out_dir, orig_audio_path.stem or "mote")
                return
            if audio_path != orig_audio_path.resolve():
                self._log(f"Merk: Filen ble funnet på ny sti: {audio_path}")

            min_spk, max_spk = resolve_speaker_bounds(
                min_speakers_str, max_speakers_str, speaker_names_hint
            )
            run_pipeline(
                input_path=audio_path,
                out_dir=out_dir,
                html_name=html_name_str,
                notes_name=notes_name_str,
                notes_html_name=notes_html_name_str,
                whisper_model=whisper_model,
                ollama_model=ollama_model,
                language=language,
                hf_token=hf_token,
                min_speakers=min_spk,
                max_speakers=max_spk,
                speaker_names_hint=speaker_names_hint,
                meeting_title=meeting_title,
                agenda_text=agenda_text,
                manual_notes_text=manual_notes_text,
                note_mode=note_mode,
                task_mode=task_mode,
                log_fn=self._log,
                progress_fn=self._set_progress,
                cancel_check_fn=lambda: self._cancel_requested,
                on_proc_fn=self._set_active_proc,
                on_checkpoint_fn=self._refresh_checkpoints_ui,
            )
        except Exception as exc:
            if not self._cancel_requested:
                self._log(f"FEIL: {exc}")
                self._set_progress(0, f"Feilet (kan gjenopptas): {exc}")
                self._auto_save_error_log(out_dir, clean_base_stem(audio_path) or "mote")
        finally:
            self._active_proc = None
            self._set_running(False)
            self._refresh_checkpoints_ui()


def _load_app_html() -> str:
    return load_template("app.html")


HTML = _load_app_html()


def bind_drag_and_drop(window: Any) -> None:
    def on_drop(e: dict[str, Any]) -> None:
        files = e.get("dataTransfer", {}).get("files", [])
        if files:
            full_path = files[0].get("pywebviewFullPath")
            if full_path:
                window.evaluate_js(f"handleDroppedAudio({json.dumps(str(Path(full_path)))})")

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


def main() -> None:
    api = MoteApi()
    window = webview.create_window(
        "Steno — A modern annalist for your meetings — an automaton stenographer",
        html=HTML,
        js_api=api,
        width=820,
        height=790,
    )
    api.window = window
    try:
        window.events.closed += api.shutdown_children
    except Exception:
        pass
    webview.start(bind_drag_and_drop, window)


def run_self_test() -> int:
    """Verify embedded stage runner script, HTML templates, and MoteApi without opening a blocking GUI window."""
    compile(WHISPERX_STAGE_RUNNER_CODE, "wx_stage_runner.py", "exec")
    ns: dict[str, object] = {}
    header_lines = []
    for line in WHISPERX_STAGE_RUNNER_CODE.splitlines():
        if line.startswith("import torch") or line.startswith("import whisperx"):
            break
        header_lines.append(line)
    exec("\n".join(header_lines), ns)
    assert "sys" in ns and "Path" in ns, "Stage runner missing sys or Path"
    assert "<html" in HTML.lower(), "App HTML template failed to load"
    assert "<html" in load_template("transcript.html").lower(), "Transcript HTML template failed to load"
    assert "<html" in load_template("notes.html").lower(), "Notes HTML template failed to load"
    api = MoteApi()
    defs = api.get_defaults()
    assert "default_out_dir" in defs
    print("SELF_TEST_OK")
    return 0


def strip_macos_psn_args(argv: list[str]) -> list[str]:
    """Filter out macOS LaunchServices process serial number (-psn_0_...) arguments.

    >>> strip_macos_psn_args(["-psn_0_123456"])
    []
    >>> strip_macos_psn_args(["-psn_0_123456", "--check-hw"])
    ['--check-hw']
    >>> strip_macos_psn_args(["--profile", "light", "mote.opus"])
    ['--profile', 'light', 'mote.opus']
    """
    return [arg for arg in argv if not arg.startswith("-psn_")]


def entrypoint(argv: list[str] | None = None) -> int:
    """Unified CLI and GUI dispatcher driven by build_cli_parser()."""
    raw_args = sys.argv[1:] if argv is None else list(argv)
    clean_args = strip_macos_psn_args(raw_args)
    if not clean_args:
        main()
        return 0

    if "-h" in clean_args or "--help" in clean_args:
        return cli_main(clean_args)

    parser = build_cli_parser()
    parsed, _unknown = parser.parse_known_args(clean_args)
    if parsed.self_test:
        return run_self_test()
    if parsed.gui and not parsed.cli:
        main()
        return 0

    cli_args = [a for a in clean_args if a != "--cli"]
    return cli_main(cli_args)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
