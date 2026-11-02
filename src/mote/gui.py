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
    TurnDict,
    build_notes_html,
    build_runtime_env,
    build_transcript_html_and_text,
    build_whisper_initial_prompt,
    clean_base_stem,
    cli_main,
    detect_hardware_resources,
    determine_stages_to_run,
    ensure_ffmpeg,
    ensure_whisperx,
    evaluate_model_resource_fit,
    extract_context_from_file,
    extract_turns_from_whisperx,
    fetch_ollama_models,
    format_seconds,
    generate_notes_via_api,
    get_checkpoint_dir,
    get_checkpoint_status_dict,
    get_initial_hf_token,
    get_whisperx_python,
    infer_speaker_names_via_api,
    is_diarization_cached,
    is_transcript_file,
    load_turns_from_transcript_file,
    managed_ollama_server,
    resolve_effective_note_mode,
    resolve_moved_audio_path,
    run_live_check_on_file,
    sanitize_text,
    save_hf_token,
    stage_audio_input,
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

    def speak_intro_prompt(self) -> None:
        """Speak the guided voice introduction prompt using local OS TTS."""
        prompt_text = (
            "Opptaket er klart. "
            "Steg en: Møteleder sier kort hva møtet gjelder og dagens dato. "
            "Steg to: Ta en runde rundt bordet der hver deltaker sier sitt navn og sin tittel eller rolle."
        )
        if self._tts_proc and self._tts_proc.poll() is None:
            self._tts_proc.terminate()
            return

        if sys.platform == "darwin":
            self._tts_proc = subprocess.Popen(["say", prompt_text])
        elif os.name == "nt":
            ps_cmd = (
                "Add-Type -AssemblyName System.Speech; "
                f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{prompt_text}')"
            )
            self._tts_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            if shutil.which("espeak-ng"):
                self._tts_proc = subprocess.Popen(["espeak-ng", "-v", "no", prompt_text])
            elif shutil.which("espeak"):
                self._tts_proc = subprocess.Popen(["espeak", "-v", "no", prompt_text])

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

        if sys.platform == "darwin":
            cmd = [
                ffmpeg_bin, "-y", "-f", "avfoundation", "-i", ":default",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rec_file),
            ]
        elif os.name == "nt":
            cmd = [
                ffmpeg_bin, "-y", "-f", "dshow", "-i", "audio=default",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rec_file),
            ]
        else:
            cmd = [
                ffmpeg_bin, "-y", "-f", "pulse", "-i", "default",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rec_file),
            ]

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

            html_name = Path(html_name_str.strip()).name
            notes_name = Path(notes_name_str.strip()).name
            notes_html_name = Path(notes_html_name_str.strip()).name

            html_path = out_dir / html_name
            notes_path = out_dir / notes_name
            notes_html_path = out_dir / notes_html_name

            ckpt_dir = get_checkpoint_dir(out_dir, audio_path)
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            asr_json = ckpt_dir / "1_asr.json"
            align_json = ckpt_dir / "2_align.json"
            diarize_json = ckpt_dir / "3_diarize.json"
            speakers_json = ckpt_dir / "4_speakers.json"

            is_trans_input = is_transcript_file(audio_path)
            run_s1, run_s2, run_s3, run_s4, run_s5 = determine_stages_to_run(
                task_mode=task_mode,
                is_trans_input=is_trans_input,
                has_s1=asr_json.is_file(),
                has_s2=align_json.is_file(),
                has_s3=diarize_json.is_file(),
                has_s4=speakers_json.is_file(),
                has_html=html_path.is_file(),
            )

            if is_trans_input and task_mode in ("only_1_asr", "only_2_align", "only_3_diarize"):
                self._log(
                    f"==> Merk: {audio_path.name} er allerede en transkripsjonsfil. "
                    "Lydsteg 1–3 hoppes over."
                )
                self._set_progress(100, "Inndata er allerede transkribert")
                return

            env = build_runtime_env()

            wx_stages: list[str] = []
            if run_s1:
                wx_stages.append("asr")
            if run_s2:
                wx_stages.append("align")
            if run_s3:
                wx_stages.append("diarize")

            if wx_stages:
                if "diarize" in wx_stages:
                    if hf_token.strip():
                        save_hf_token(hf_token)
                    elif not is_diarization_cached():
                        self._log("FEIL: Pyannote er ikke bufret ennå. Lim inn HF-token.")
                        self._set_progress(0, "Mangler HF-token")
                        return

                ensure_ffmpeg(env, self._log)
                whisperx_bin = ensure_whisperx(env, self._log, self._set_progress)
                wx_python = get_whisperx_python(whisperx_bin)

                initial_prompt = build_whisper_initial_prompt(
                    meeting_title, speaker_names_hint, agenda_text, manual_notes_text
                )
                if initial_prompt:
                    self._log(f"==> Kontekst-ordliste til Whisper ({len(initial_prompt)} tegn) aktivert.")

                skipped = [s for s in ("asr", "align", "diarize") if s not in wx_stages]
                if skipped:
                    self._log(
                        f"==> Sjekkpunkt-gjenopptak: Gjenbruker ferdige steg ({', '.join(skipped)}) "
                        f"fra {ckpt_dir.name}/ og kjører: {', '.join(wx_stages)}"
                    )

                with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
                    tmp_dir_path = Path(tmp_dir)
                    staged_audio = stage_audio_input(audio_path, tmp_dir_path)
                    runner_script = tmp_dir_path / "wx_stage_runner.py"
                    runner_script.write_text(WHISPERX_STAGE_RUNNER_CODE, encoding="utf-8")

                    cfg_file = tmp_dir_path / "wx_config.json"
                    cfg_data = {
                        "audio_path": str(staged_audio),
                        "ckpt_dir": str(ckpt_dir),
                        "stages": wx_stages,
                        "model": whisper_model,
                        "language": language,
                        "compute_type": "int8",
                        "hf_token": hf_token.strip(),
                        "min_speakers": int(min_speakers_str.strip()) if min_speakers_str.strip().isdigit() else None,
                        "max_speakers": int(max_speakers_str.strip()) if max_speakers_str.strip().isdigit() else None,
                        "initial_prompt": initial_prompt,
                    }
                    cfg_file.write_text(json.dumps(cfg_data, ensure_ascii=False), encoding="utf-8")

                    cmd = [wx_python, "-u", str(runner_script), str(cfg_file)]
                    proc = subprocess.Popen(
                        cmd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
                    self._active_proc = proc
                    assert proc.stdout is not None
                    phase = 0 if "asr" in wx_stages else (1 if "align" in wx_stages else 2)
                    diarization_start: float | None = None
                    last_diarize_pct: float = 0.0
                    stop_heartbeat = threading.Event()

                    def _diarization_heartbeat() -> None:
                        while not stop_heartbeat.wait(1.0):
                            if phase == 2 and diarization_start is not None:
                                elapsed = int(time.time() - diarization_start)
                                mm, ss = divmod(elapsed, 60)
                                if last_diarize_pct > 0:
                                    mapped = int(60 + (last_diarize_pct * 0.14))
                                    self._set_progress(
                                        mapped,
                                        f"[Steg 3/5] Pyannote taler-ID: {last_diarize_pct:.0f}% ({mm:02d}:{ss:02d})...",
                                    )
                                else:
                                    creep = min(13, elapsed // 15)
                                    self._set_progress(
                                        60 + creep,
                                        f"[Steg 3/5] Identifiserer talere med Pyannote ({mm:02d}:{ss:02d})...",
                                    )

                    hb_thread = threading.Thread(target=_diarization_heartbeat, daemon=True)
                    hb_thread.start()

                    try:
                        for line in proc.stdout:
                            clean = sanitize_text(line)
                            if not clean:
                                continue

                            if clean == "STAGE:ASR:START":
                                phase = 0
                                self._set_progress(12, f"[Steg 1/5] Transkriberer med {whisper_model}...")
                                self._log(f"==> [Steg 1/5] Kjører WhisperX ASR ({whisper_model}) på {audio_path.name}...")
                                continue
                            if clean == "STAGE:ASR:DONE":
                                self._log("==> [Steg 1/5] Rå-transkripsjon lagret i sjekkpunkt (1_asr.json).")
                                self._refresh_checkpoints_ui()
                                continue
                            if clean == "STAGE:ALIGN:START":
                                phase = 1
                                self._set_progress(48, "[Steg 2/5] Tidsjusterer ord (Alignment): 0%")
                                self._log("==> [Steg 2/5] Starter ord-tidsjustering (Alignment)...")
                                continue
                            if clean == "STAGE:ALIGN:DONE":
                                self._log("==> [Steg 2/5] Tidsjustering lagret i sjekkpunkt (2_align.json).")
                                self._refresh_checkpoints_ui()
                                continue
                            if clean == "STAGE:DIARIZE:START":
                                phase = 2
                                last_diarize_pct = 0.0
                                diarization_start = time.time()
                                self._set_progress(60, "[Steg 3/5] Identifiserer talere med Pyannote (00:00)...")
                                self._log("==> [Steg 3/5] Starter Pyannote taler-identifisering...")
                                continue
                            if clean == "STAGE:DIARIZE:DONE":
                                self._log("==> [Steg 3/5] Taler-identifisering lagret i sjekkpunkt (3_diarize.json).")
                                self._refresh_checkpoints_ui()
                                continue

                            d_match = re.search(r"DiarizeProgress:\s*([0-9]+(?:\.[0-9]+)?)%", clean)
                            if d_match:
                                last_diarize_pct = float(d_match.group(1))
                                mapped = int(60 + (last_diarize_pct * 0.14))
                                elapsed = int(time.time() - (diarization_start or time.time()))
                                mm, ss = divmod(elapsed, 60)
                                self._set_progress(
                                    mapped,
                                    f"[Steg 3/5] Pyannote taler-ID: {last_diarize_pct:.0f}% ({mm:02d}:{ss:02d})...",
                                )
                                continue

                            self._log(clean)

                            match = re.search(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%", clean)
                            if match:
                                p_pct = float(match.group(1))
                                if phase == 0:
                                    mapped = int(12 + (p_pct * 0.35))
                                    self._set_progress(mapped, f"[Steg 1/5] Transkriberer tale: {p_pct:.0f}%")
                                elif phase == 1:
                                    mapped = int(48 + (p_pct * 0.11))
                                    self._set_progress(mapped, f"[Steg 2/5] Tidsjusterer ord (Alignment): {p_pct:.0f}%")
                    finally:
                        stop_heartbeat.set()
                        self._active_proc = None

                    rc = proc.wait()
                    if self._cancel_requested:
                        self._refresh_checkpoints_ui()
                        return
                    if rc != 0:
                        self._log("FEIL: WhisperX-steget feilet. Fullførte tidligere steg er lagret i sjekkpunkt.")
                        self._set_progress(0, "Feil under WhisperX (kan gjenopptas)")
                        self._auto_save_error_log(out_dir, clean_base_stem(audio_path))
                        self._refresh_checkpoints_ui()
                        return

            if self._cancel_requested:
                return

            if not run_s4 and not run_s5:
                self._refresh_checkpoints_ui()
                self._set_progress(100, f"Steg ({', '.join(wx_stages)}) fullført og lagret i sjekkpunkt")
                self._log(f"==> Valgt deloppgave ({task_mode}) er ferdig og lagret i {ckpt_dir}")
                return

            turns: list[TurnDict] = []
            speaker_map: dict[str, str] = {}

            if is_trans_input:
                self._log(f"==> Leser inn eksisterende transkripsjon fra {audio_path.name}...")
                turns, speaker_map = load_turns_from_transcript_file(audio_path)
                if not turns:
                    self._log(f"FEIL: Klarte ikke hente ut replikker fra {audio_path.name}.")
                    self._set_progress(0, "Kunne ikke lese transkripsjon")
                    return
                self._log(f"==> Lastet {len(turns)} replikker fra transkripsjonsfil.")
            elif not run_s4 and speakers_json.is_file():
                saved_s4 = json.loads(speakers_json.read_text(encoding="utf-8"))
                turns = list(saved_s4.get("turns", []))
                speaker_map = dict(saved_s4.get("speaker_map", {}))
                self._log(f"==> Gjenbruker taler-navnekobling fra sjekkpunkt (4_speakers.json, {len(turns)} replikker).")
            elif not run_s4 and html_path.is_file():
                turns, speaker_map = load_turns_from_transcript_file(html_path)
                self._log(f"==> Gjenbruker ferdig HTML-transkripsjon ({html_path.name}, {len(turns)} replikker).")
            else:
                source_json = (
                    diarize_json
                    if diarize_json.is_file()
                    else (align_json if align_json.is_file() else asr_json)
                )
                if not source_json.is_file():
                    self._log("FEIL: Fant ingen transkripsjonsdata å bygge videre på.")
                    self._set_progress(0, "Mangler transkripsjonsdata")
                    return
                turns = extract_turns_from_whisperx(source_json)

            doc_title = meeting_title.strip() or clean_base_stem(audio_path)

            with managed_ollama_server(self._log, self._set_progress, env=env) as ollama_bin:
                installed = fetch_ollama_models() or []
                if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
                    self._set_progress(75, f"Laster ned {ollama_model}...")
                    self._log(f"==> Laster ned '{ollama_model}'...")
                    subprocess.run([ollama_bin, "pull", ollama_model], env=env, check=True)

                if self._cancel_requested:
                    return

                if run_s4:
                    self._set_progress(78, "[Steg 4/5] Gjenkjenner deltakernavn og titler...")
                    self._log("==> [Steg 4/5] Analyserer introduksjoner, deltakerliste og kontekst for å knytte taler-ID til navn...")
                    inferred_map = infer_speaker_names_via_api(
                        turns,
                        ollama_model,
                        speaker_names_hint,
                        meeting_title,
                        agenda_text,
                        self._log,
                        manual_notes_text=manual_notes_text,
                    )
                    merged_map = dict(speaker_map)
                    merged_map.update(inferred_map)
                    speaker_map = merged_map
                    for spk_id, resolved_name in speaker_map.items():
                        if resolved_name != spk_id:
                            self._log(f"    Gjenkjent {spk_id} -> {resolved_name}")

                    clean_transcript = build_transcript_html_and_text(
                        turns, speaker_map, html_path, doc_title
                    )
                    tmp_s4 = speakers_json.with_suffix(".json.tmp")
                    tmp_s4.write_text(
                        json.dumps({"speaker_map": speaker_map, "turns": turns}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    tmp_s4.replace(speakers_json)
                    self._refresh_checkpoints_ui()
                    self._log(f"==> [Steg 4/5] Rik HTML-transkripsjon lagret: {html_path}")
                else:
                    if not html_path.is_file():
                        clean_transcript = build_transcript_html_and_text(
                            turns, speaker_map, html_path, doc_title
                        )
                    else:
                        lines_out: list[str] = []
                        for t in turns:
                            spk = speaker_map.get(t["speaker"], t["speaker"])
                            ts = f"{format_seconds(t['start'])} – {format_seconds(t['end'])}"
                            lines_out.append(f"[{ts}] {spk}: {t['text']}")
                        clean_transcript = "\n\n".join(lines_out)

                if not run_s5 or self._cancel_requested:
                    self._refresh_checkpoints_ui()
                    if not self._cancel_requested:
                        self._set_progress(100, "Steg 4 (Taler-ID & HTML-transkripsjon) fullført!")
                        self._log(f"==> FERDIG med Steg 4: {html_path}")
                    return

                effective_mode = resolve_effective_note_mode(
                    note_mode, min_speakers_str, max_speakers_str, speaker_map
                )
                mode_desc = (
                    "foredragsnotater (1-talers modus / faglige hovedpoenger)"
                    if effective_mode == "foredrag"
                    else "møtereferat (flere deltakere & aksjonspunkter)"
                )
                self._set_progress(84, f"[Steg 5/5] Skriver {mode_desc} med {ollama_model}...")
                self._log(f"==> [Steg 5/5] Genererer {mode_desc} ({ollama_model})...")

                notes_md = generate_notes_via_api(
                    clean_transcript,
                    ollama_model,
                    speaker_map,
                    speaker_names_hint,
                    meeting_title,
                    agenda_text,
                    progress_fn=self._set_progress,
                    note_mode=effective_mode,
                    manual_notes_text=manual_notes_text,
                )
                if self._cancel_requested:
                    return

                notes_path.write_text(notes_md + "\n", encoding="utf-8")

                self._set_progress(99, "Bygger HTML-utgave av referat/notater...")
                build_notes_html(notes_md, notes_html_path, doc_title)
                self._refresh_checkpoints_ui()

            self._set_progress(100, "Fullført!")
            self._log(
                f"==> FERDIG!\n"
                f"    HTML-transkripsjon: {html_path}\n"
                f"    Referat/notat (MD): {notes_path}\n"
                f"    Referat/notat(HTML): {notes_html_path}"
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
        "Møteopptak, Transkripsjon & Referat (WhisperX + Ollama)",
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


def entrypoint(argv: list[str] | None = None) -> int:
    """Unified CLI and GUI dispatcher."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args:
        if args[0] == "--self-test":
            return run_self_test()
        if args[0] == "--cli":
            return cli_main(args[1:])
        if args[0] in ("-h", "--help", "--status", "--live-check") or not args[0].startswith("--"):
            return cli_main(args)
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
