"""Desktop pywebview GUI API, native OS menus, self-test, and unified entrypoint for Steno."""

from __future__ import annotations

import json
import os
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

from mote.audio import (  # noqa: E402
    LIVE_CHUNK_WORKER_CODE,
    MACOS_COREAUDIO_RECORD_RUNNER_CODE,
    LiveAudioMonitor,
    detect_audio_language,
    download_media_url_audio,
    ensure_pregenerated_voiceover,
    find_wav_data_offset,
    get_system_input_volume,
    get_voiceover_script,
    list_audio_input_devices,
    open_system_sound_settings,
    resolve_moved_audio_path,
    run_live_check_on_file,
    run_mic_test,
    set_system_input_volume,
    start_mic_record_process,
)
from mote.constants import (  # noqa: E402
    LANGUAGE_LABELS,
    MODEL_PROFILES,
    build_runtime_env,
    build_whisper_initial_prompt,
    clean_base_stem,
    detect_text_language,
    get_checkpoint_dir,
    get_language_display_name,
    is_media_url,
    is_termux,
    is_transcript_file,
    load_user_preferences,
    normalize_audio_language,
    resolve_speaker_bounds,
    resolve_ui_lang,
    sanitize_text,
    save_user_preferences,
    unique_filename,
)
from mote.context import extract_context_from_file, fetch_context_from_url  # noqa: E402
from mote.core import build_cli_parser, cli_main  # noqa: E402
from mote.gui_bridge import (  # noqa: F401, E402
    BrowserWindowBridge,
    create_browser_gui_server,
    has_native_webview_backend,
    inject_browser_bridge,
    run_browser_gui,
)
from mote.hardware import (  # noqa: E402
    detect_hardware_resources,
    ensure_ffmpeg,
    evaluate_model_resource_fit,
    get_initial_hf_token,
    install_steno_app,
    is_diarization_cached,
    recommend_models_for_language,
    uninstall_steno_app,
)
from mote.pipeline import (  # noqa: E402
    WHISPERX_STAGE_RUNNER_CODE,
    get_checkpoint_status_dict,
    list_resumable_sessions,
    run_pipeline,
)
from mote.template_loader import assemble_app_html, load_template  # noqa: E402

try:
    import webview  # noqa: E402
    from webview import FileDialog  # noqa: E402
    from webview.dom import DOMEventHandler  # noqa: E402
except ImportError:
    webview = None  # type: ignore[assignment]
    FileDialog = None  # type: ignore[assignment, misc]
    DOMEventHandler = None  # type: ignore[assignment, misc]

# Keep compatibility imports referenced so linters preserve public re-exports used by GUI scripts
_GUI_EXPORTS = (
    find_wav_data_offset,
    get_voiceover_script,
    is_media_url,
)


def _get_project_base_dir() -> Path:
    here = Path(__file__).resolve().parent
    if here.name == "mote" and here.parent.name == "src":
        return here.parent.parent
    return here


class MoteApi:
    """pywebview / browser-bridge RPC surface exposed as window.pywebview.api."""

    def __init__(self) -> None:
        self.window: Any = None
        self._rec_proc: subprocess.Popen[str] | None = None
        self._rec_path: Path | None = None
        self._live_monitor: LiveAudioMonitor | None = None
        self._tts_proc: subprocess.Popen[bytes] | None = None
        self._play_proc: subprocess.Popen[bytes] | None = None
        self._active_proc: subprocess.Popen[str] | None = None
        self._cancel_requested: bool = False
        self._log_lines: list[str] = []
        self._last_waveform_emit: float = 0.0
        self._state_lock: threading.Lock = threading.Lock()
        self._job_running: bool = False
        self._mic_test_running: bool = False

    def _emit(self, event_type: str, payload: Any) -> None:
        win = self.window
        if not win:
            return
        if hasattr(win, "emit_event"):
            win.emit_event(event_type, payload)
            return
        try:
            t_json = json.dumps(event_type, ensure_ascii=False)
            p_json = json.dumps(payload, ensure_ascii=False)
            win.evaluate_js(f"window.__stenoDispatchEvent && window.__stenoDispatchEvent({t_json}, {p_json})")
        except Exception:
            pass

    def _set_rec_path(self, path: Path) -> None:
        self._rec_path = path

    def shutdown_children(self) -> None:
        """Stop any running background recording, TTS, playback, or WhisperX/Ollama child processes when GUI closes."""
        self._cancel_requested = True
        if self._live_monitor is not None:
            try:
                self._live_monitor.stop()
            except Exception:
                pass
            self._live_monitor = None

        for proc in (self._active_proc, self._rec_proc, self._tts_proc, self._play_proc):
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
        prefs = load_user_preferences()
        audio_lang = normalize_audio_language(str(prefs.get("audio_language", "no") or "no"))
        saved_ui_lang = str(prefs.get("ui_lang", "") or "").strip().lower()
        ui_lang = saved_ui_lang if saved_ui_lang in ("no", "en") else resolve_ui_lang()
        hw = detect_hardware_resources(default_dir, language=audio_lang)
        lang_models = prefs.get("language_models") if isinstance(prefs.get("language_models"), dict) else {}
        saved_entry = lang_models.get(audio_lang) if isinstance(lang_models, dict) else None
        rec_whisper = (
            str(saved_entry.get("whisper_model") or hw["recommended_whisper"])
            if isinstance(saved_entry, dict)
            else str(hw["recommended_whisper"])
        )
        rec_ollama = (
            str(saved_entry.get("ollama_model") or hw["recommended_ollama"])
            if isinstance(saved_entry, dict)
            else str(hw["recommended_ollama"])
        )
        hw["recommended_whisper"] = rec_whisper
        hw["recommended_ollama"] = rec_ollama
        fit = evaluate_model_resource_fit(rec_whisper, rec_ollama, hw, language=audio_lang)
        return {
            "hf_token": "",
            "hf_token_configured": bool(get_initial_hf_token()),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(default_dir),
            "default_meeting_title": f"Møte {today}",
            "hardware": hw,
            "resource_fit": fit,
            "profiles": MODEL_PROFILES,
            "ui_lang": ui_lang,
            "audio_language": audio_lang,
            "color_scheme": str(prefs.get("color_scheme", "system")),
            "user_prefs": prefs,
            "supported_languages": [
                {"code": k, "label_no": v[0], "label_en": v[1]}
                for k, v in LANGUAGE_LABELS.items()
            ],
            "language_recommendations": recommend_models_for_language(
                audio_lang,
                profile=str(hw.get("recommended_profile", "recommended")),
                ram_gb=float(hw.get("ram_gb", 16.0)),
            ),
            "audio_inputs": list_audio_input_devices(),
            "system_input_volume": get_system_input_volume(),
            "resumable_sessions": list_resumable_sessions(default_dir, base_dir),
        }

    def save_preferences(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist user preferences (ui_lang, color_scheme, audio_language, per-language models)."""
        merged = save_user_preferences(updates if isinstance(updates, dict) else {})
        return {"ok": True, "user_prefs": merged}

    def get_language_recommendations(
        self,
        language: str,
        profile: str = "recommended",
        out_dir_str: str = "",
    ) -> dict[str, Any]:
        """Return recommended & saved Whisper/Ollama models for a selected audio speech language."""
        norm_lang = normalize_audio_language(language)
        prefs = load_user_preferences()
        out_dir = Path(out_dir_str) if out_dir_str else None
        hw = detect_hardware_resources(out_dir, language=norm_lang)
        eff_profile = (
            profile
            if profile in ("light", "recommended", "heavy")
            else str(hw.get("recommended_profile", "recommended"))
        )
        recs = recommend_models_for_language(
            norm_lang,
            profile=eff_profile,
            ram_gb=float(hw.get("ram_gb", 16.0)),
        )
        lang_models = prefs.get("language_models") if isinstance(prefs.get("language_models"), dict) else {}
        saved_entry = lang_models.get(norm_lang) if isinstance(lang_models, dict) else None
        chosen_whisper = (
            str(saved_entry.get("whisper_model") or recs["default_whisper"])
            if isinstance(saved_entry, dict) and saved_entry.get("whisper_model")
            else str(recs["default_whisper"])
        )
        chosen_ollama = (
            str(saved_entry.get("ollama_model") or recs["default_ollama"])
            if isinstance(saved_entry, dict) and saved_entry.get("ollama_model")
            else str(recs["default_ollama"])
        )
        fit = evaluate_model_resource_fit(chosen_whisper, chosen_ollama, hw, language=norm_lang)
        return {
            "ok": True,
            "language": norm_lang,
            "label_no": get_language_display_name(norm_lang, "no"),
            "label_en": get_language_display_name(norm_lang, "en"),
            "recommendations": recs,
            "chosen_whisper": chosen_whisper,
            "chosen_ollama": chosen_ollama,
            "has_saved_model": bool(isinstance(saved_entry, dict) and saved_entry.get("whisper_model")),
            "hardware": hw,
            "resource_fit": fit,
        }

    def detect_language(self, audio_path_str: str = "") -> dict[str, Any]:
        """Detect spoken or written language from the selected audio/transcript file or latest mic recording."""
        raw = (audio_path_str or "").strip()
        if not raw and self._rec_path and self._rec_path.is_file():
            raw = str(self._rec_path)
        if not raw:
            mictest_wav = Path(tempfile.gettempdir()) / "steno_mictest_latest.wav"
            if mictest_wav.is_file():
                raw = str(mictest_wav)
        if not raw:
            return {
                "ok": False,
                "error": "Velg en lydfil eller kjør mikrofontest først for å oppdage språk.",
            }
        base_dir = _get_project_base_dir()
        target = resolve_moved_audio_path(Path(raw), base_dir)
        if not target.is_file():
            return {"ok": False, "error": f"Fant ikke filen: {target.name}"}

        if is_transcript_file(target):
            try:
                txt = target.read_text(encoding="utf-8", errors="replace")
                txt_det = detect_text_language(txt[:8000])
                t_lang = str(txt_det.get("detected_language", "no"))
                t_prob = float(txt_det.get("confidence", 0.0) or 0.0)
                norm = normalize_audio_language(t_lang or "no")
                recs = recommend_models_for_language(norm)
                self._log(
                    f"==> SPRÅKDETEKSJON ({target.name}): Oppdaget {get_language_display_name(norm, 'no')} "
                    f"({t_prob * 100:.0f}% sikkerhet)"
                )
                return {
                    "ok": bool(t_lang),
                    "detected_language": norm,
                    "language_probability": round(t_prob, 3),
                    "label_no": get_language_display_name(norm, "no"),
                    "label_en": get_language_display_name(norm, "en"),
                    "recommended_whisper": recs["default_whisper"],
                    "snippet": sanitize_text(txt[:160]),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        self._log(f"==> Analyserer talespråk i {target.name}...")
        res = detect_audio_language(target, sample_seconds=15.0, log_fn=self._log)
        if res.get("ok") and res.get("detected_language"):
            dl = str(res["detected_language"])
            prob = float(res.get("language_probability", 0.0) or 0.0)
            self._log(
                f"==> SPRÅKDETEKSJON ({target.name}): Oppdaget {get_language_display_name(dl, 'no')} "
                f"({prob * 100:.0f}% sikkerhet) — anbefalt modell: {res.get('recommended_whisper', '')}"
            )
        return res

    def get_audio_input_status(self) -> dict[str, Any]:
        """Return available audio input devices and current OS microphone volume (0-100)."""
        return {
            "devices": list_audio_input_devices(),
            "volume": get_system_input_volume(),
        }

    def set_mic_volume(self, volume_pct: int) -> dict[str, Any]:
        """Set the OS microphone input volume (0-100) and return the new status."""
        vol = max(0, min(100, int(volume_pct)))
        res = set_system_input_volume(vol)
        actual = get_system_input_volume()
        actual_vol = int(actual.get("volume", vol)) if isinstance(actual, dict) else vol
        self._log(f"==> Systemets mikrofonvolum satt til {actual_vol}%")
        return {"ok": bool(res.get("ok", True)) if isinstance(res, dict) else True, "volume": actual_vol}

    def open_sound_settings(self) -> dict[str, Any]:
        """Open the OS Sound / Microphone input settings pane."""
        return {"ok": bool(open_system_sound_settings().get("ok", False))}

    def _emit_mic_test_state(self, state: dict[str, Any]) -> None:
        self._emit("mic_test", state)

    def _on_live_level(self, lvl: dict[str, Any]) -> None:
        self._emit("live_level", lvl)

    def _on_live_waveform(self, samples: list[float], lvl: dict[str, Any]) -> None:
        now = time.monotonic()
        if (now - self._last_waveform_emit) < 0.075:
            return
        self._last_waveform_emit = now
        self._emit("waveform", {"samples": samples, "level": lvl})

    def _on_live_chunk(self, line: str, lvl: dict[str, Any], text: str) -> None:
        self._log(f"[SANNTIDS-LYDSJEKK] {line}")
        self._emit("live_chunk", {"line": line, "level": lvl, "text": text})

    def test_microphone(
        self,
        mic_device: str | dict[str, Any] = "default",
        gain_db: float = 6.0,
        auto_gain: bool = True,
        duration_sec: float = 4.0,
        whisper_model: str = "NbAiLab/nb-whisper-small",
        language: str = "no",
    ) -> dict[str, Any]:
        """Run a 4s microphone oscilloscope + level + Speech-to-Text (STT) verification in a background thread."""
        if self._rec_proc and self._rec_proc.poll() is None:
            return {"ok": False, "error": "Opptak pågår allerede."}

        if isinstance(mic_device, dict):
            cfg = mic_device
            eff_dev = str(cfg.get("mic_device", "default"))
            eff_gain = float(cfg.get("gain_db", gain_db))
            eff_auto = bool(cfg.get("auto_gain", auto_gain))
            eff_dur = float(cfg.get("duration_sec", duration_sec))
            eff_whisper = str(cfg.get("whisper_model", whisper_model))
            eff_lang = str(cfg.get("language", language))
        else:
            eff_dev = mic_device
            eff_gain = gain_db
            eff_auto = auto_gain
            eff_dur = duration_sec
            eff_whisper = whisper_model
            eff_lang = language

        with self._state_lock:
            if self._mic_test_running:
                return {"ok": False, "error": "Mikrofontest pågår allerede."}
            self._mic_test_running = True

        def _worker_fn() -> None:
            try:
                run_mic_test(
                    mic_device=eff_dev,
                    gain_db=eff_gain,
                    auto_gain=eff_auto,
                    duration_sec=eff_dur,
                    whisper_model=eff_whisper,
                    language=eff_lang,
                    on_state=self._emit_mic_test_state,
                    on_level=self._on_live_level,
                    on_waveform=self._on_live_waveform,
                    on_audio_ready=self._set_rec_path,
                    log_fn=self._log,
                )
            finally:
                with self._state_lock:
                    self._mic_test_running = False

        try:
            threading.Thread(target=_worker_fn, daemon=True).start()
        except Exception as exc:
            with self._state_lock:
                self._mic_test_running = False
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def fetch_context_url(self, url: str) -> dict[str, Any]:
        """Download and extract agenda, slides, or draft notes text from a remote URL or shared document link."""
        try:
            res = fetch_context_from_url(url)
        except Exception as exc:
            err_msg = str(exc)
            self._log(f"==> Kunne ikke hente kontekst fra lenke: {err_msg}")
            return {"ok": False, "error": err_msg}
        if res.get("ok"):
            txt = str(res.get("text", ""))
            src = str(res.get("source_name", url))
            self._log(f"==> Hentet kontekst fra lenke ({src}, {len(txt)} tegn)")
        else:
            self._log(f"==> Kunne ikke hente kontekst fra lenke: {res.get('error', 'Ukjent feil')}")
        return res

    def list_resumable_sessions(self, out_dir_str: str = "") -> list[dict[str, Any]]:
        """List saved transcription sessions and checkpoints in out_dir and project data folder."""
        base_dir = _get_project_base_dir()
        out_dir = Path(out_dir_str) if out_dir_str else None
        return list_resumable_sessions(out_dir=out_dir, base_dir=base_dir)

    def check_model_resources(
        self,
        whisper_model: str,
        ollama_model: str,
        out_dir_str: str = "",
        language: str = "no",
    ) -> dict[str, Any]:
        """Evaluate whether the selected Whisper + Ollama model combination fits local hardware."""
        out_dir = Path(out_dir_str) if out_dir_str else None
        norm_lang = normalize_audio_language(language)
        hw = detect_hardware_resources(out_dir, language=norm_lang)
        fit = evaluate_model_resource_fit(whisper_model, ollama_model, hw, language=norm_lang)
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
        notes_html_name_str: str = "",
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
                "html_exists": False,
                "notes_exists": False,
                "notes_html_exists": False,
            }
        base_dir = _get_project_base_dir()
        audio_path = resolve_moved_audio_path(Path(audio_path_str), base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        return get_checkpoint_status_dict(
            out_dir, audio_path, html_name_str, notes_name_str, notes_html_name_str
        )

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

    def install_app(self, ui_lang: str = "no") -> dict[str, Any]:
        """Install Steno into the user's local app directory with CLI and desktop shortcuts."""
        is_en = str(ui_lang).strip().lower().startswith("en")
        res = install_steno_app()
        msg = res["message_en"] if is_en else res["message_no"]
        self._log(f"==> {msg}")
        return {"ok": True, "message": msg, **res}

    def uninstall_app(self, ui_lang: str = "no") -> dict[str, Any]:
        """Shutdown child processes, remove all cached Steno runtime/model directories and shortcuts, and close window."""
        is_en = str(ui_lang).strip().lower().startswith("en")
        self.shutdown_children()
        res = uninstall_steno_app()
        msg = res["message_en"] if is_en else res["message_no"]
        self._log(f"==> {msg}")
        if self.window:

            def _close_later() -> None:
                time.sleep(0.8)
                try:
                    if self.window:
                        self.window.destroy()
                except Exception:
                    pass

            threading.Thread(target=_close_later, daemon=True).start()
        return {"ok": True, "message": msg, **res}

    def get_voiceover_data_uri(self, ui_lang: str = "no", note_mode: str = "mote") -> str | None:
        """Return a data:audio/mpeg;base64 URI for the pre-generated neural voiceover guide."""
        import base64

        vo_path = ensure_pregenerated_voiceover(ui_lang, note_mode)
        if vo_path and vo_path.is_file():
            raw = vo_path.read_bytes()
            mime = "audio/wav" if vo_path.suffix.lower() == ".wav" else "audio/mpeg"
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
        return None

    def speak_intro_prompt(self, ui_lang: str = "no", note_mode: str = "mote") -> dict[str, Any]:
        """Play the pre-generated high-quality neural voiceover guide (or toggle stop if already playing)."""
        if self._tts_proc and self._tts_proc.poll() is None:
            try:
                self._tts_proc.terminate()
            except Exception:
                pass
            return {"ok": True, "playing": False}

        is_en = str(ui_lang).strip().lower().startswith("en")
        vo_path = ensure_pregenerated_voiceover(ui_lang, note_mode)
        if vo_path and vo_path.is_file():
            if sys.platform == "darwin" and shutil.which("afplay"):
                self._tts_proc = subprocess.Popen(
                    ["afplay", str(vo_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "playing": True, "audio_path": str(vo_path)}
            if shutil.which("ffplay"):
                self._tts_proc = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(vo_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "playing": True, "audio_path": str(vo_path)}

        prompt_text = get_voiceover_script(ui_lang, note_mode)
        if sys.platform == "darwin":
            voice_flag = "Samantha" if is_en else "Nora"
            self._tts_proc = subprocess.Popen(
                ["say", "-v", voice_flag, "-r", "180", prompt_text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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
            if shutil.which("termux-tts-speak"):
                self._tts_proc = subprocess.Popen(["termux-tts-speak", "-l", voice, prompt_text])
            elif shutil.which("espeak-ng"):
                self._tts_proc = subprocess.Popen(["espeak-ng", "-v", voice, prompt_text])
            elif shutil.which("espeak"):
                self._tts_proc = subprocess.Popen(["espeak", "-v", voice, prompt_text])
        return {"ok": True, "playing": True}

    def start_recording(
        self,
        out_dir_str: str | dict[str, Any] = "",
        speak_guide: bool = False,
        live_transcribe: bool = True,
        whisper_model: str = "NbAiLab/nb-whisper-small",
        language: str = "no",
        meeting_title: str = "",
        speaker_names_hint: str = "",
        agenda_text: str = "",
        mic_device: str = "default",
        gain_db: float = 0.0,
        auto_gain: bool = False,
        ui_lang: str = "no",
        note_mode: str = "mote",
    ) -> dict[str, Any]:
        """Start recording 16kHz mono WAV audio from the selected microphone via ffmpeg + live oscilloscope & ASR."""
        if self._rec_proc and self._rec_proc.poll() is None:
            return {"ok": False, "error": "Opptak pågår allerede."}

        if isinstance(out_dir_str, dict):
            cfg = out_dir_str
            out_dir_val = str(cfg.get("out_dir", ""))
            speak_guide = bool(cfg.get("speak_guide", False))
            live_transcribe = bool(cfg.get("live_transcribe", True))
            whisper_model = str(cfg.get("whisper_model", whisper_model))
            language = str(cfg.get("language", language))
            meeting_title = str(cfg.get("meeting_title", meeting_title))
            speaker_names_hint = str(cfg.get("speaker_names_hint", speaker_names_hint))
            agenda_text = str(cfg.get("agenda_text", agenda_text))
            mic_device = str(cfg.get("mic_device", mic_device))
            gain_db = float(cfg.get("gain_db", gain_db))
            auto_gain = bool(cfg.get("auto_gain", auto_gain))
            ui_lang = str(cfg.get("ui_lang", ui_lang))
            note_mode = str(cfg.get("note_mode", note_mode))
        else:
            out_dir_val = out_dir_str

        if self._play_proc and self._play_proc.poll() is None:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
            self._play_proc = None

        env = build_runtime_env()
        ffmpeg_bin = ensure_ffmpeg(env, self._log)

        out_dir = Path(out_dir_val).resolve() if out_dir_val else (Path.home() / "Downloads")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        rec_file = out_dir / unique_filename(out_dir, f"moteopptak_{stamp}", ".wav")

        try:
            self._rec_proc = start_mic_record_process(
                ffmpeg_bin,
                rec_file,
                env=env,
                mic_device=mic_device,
                gain_db=float(gain_db),
                auto_gain=bool(auto_gain),
            )
            self._rec_path = rec_file
            gain_info = f", boost=+{float(gain_db):.0f}dB" if float(gain_db) > 0 else ""
            if auto_gain:
                gain_info += " + auto-gain"
            self._log(
                f"==> OPPTAK STARTET: Lagrer mikrofon-lyd ({mic_device}{gain_info}) til {rec_file.name}"
            )
            if speak_guide:
                self.speak_intro_prompt(ui_lang=ui_lang, note_mode=note_mode)

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
                    on_waveform=self._on_live_waveform,
                    poll_interval=0.075,
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
                lvl_obj = {"status": st_val, "rms_dbfs": avg_db, "label": f"Snitt: {avg_db} dBFS"}
                for ln in res.get("lines", []):
                    self._emit("live_chunk", {"line": ln, "level": lvl_obj, "text": ""})
                if res.get("detected_language"):
                    self._emit(
                        "language_detected",
                        {
                            "detected_language": res.get("detected_language"),
                            "language_probability": res.get("language_probability", 0.0),
                            "label_no": res.get("detected_label_no", ""),
                            "label_en": res.get("detected_label_en", ""),
                            "recommended_whisper": res.get("recommended_whisper", ""),
                            "source": "quick_check",
                        },
                    )
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

    def fetch_media_url(self, url: str, out_dir: str | None = None) -> dict[str, Any] | None:
        """Download or stream media from a URL, extract 16kHz audio, and suggest meeting output names."""
        clean_url = (url or "").strip()
        if not is_media_url(clean_url):
            return None
        target_dir = Path(out_dir).resolve() if out_dir else None
        extracted_wav = download_media_url_audio(
            url=clean_url,
            out_dir=target_dir,
            log_fn=self._log,
        )
        return self.suggest_output_names(str(extracted_wav), str(extracted_wav.parent))

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
        elif shutil.which("termux-open"):
            subprocess.Popen(["termux-open", folder_path])
        else:
            subprocess.Popen(["xdg-open", folder_path])

    def open_file_in_os(self, file_path_str: str, out_dir_str: str = "") -> dict[str, Any]:
        """Open a generated output file (Transcript HTML, Notes HTML, Notes Markdown, or audio file) in the OS default app."""
        raw = (file_path_str or "").strip()
        if not raw:
            return {"ok": False, "error": "Ingen fil angitt."}

        base_dir = _get_project_base_dir()
        candidates: list[Path] = []
        raw_path = Path(raw)
        if raw_path.is_absolute():
            candidates.append(raw_path)
            candidates.append(resolve_moved_audio_path(raw_path, base_dir))
        if out_dir_str:
            candidates.append(Path(out_dir_str) / raw_path.name)
        if self._rec_path:
            candidates.append(self._rec_path.parent / raw_path.name)
        candidates.append(base_dir / "data" / raw_path.name)
        candidates.append(Path.home() / "Downloads" / raw_path.name)

        chosen = next((c for c in candidates if c.is_file()), None)
        if chosen is None:
            msg = f"Filen finnes ikke ennå: {raw_path.name}"
            self._log(f"==> Merk: {msg}")
            return {"ok": False, "error": msg}

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(chosen)])
            elif os.name == "nt":
                os.startfile(str(chosen))  # type: ignore[attr-defined]
            elif shutil.which("termux-open"):
                subprocess.Popen(["termux-open", str(chosen)])
            else:
                subprocess.Popen(["xdg-open", str(chosen)])
            self._log(f"==> Åpnet fil: {chosen.name}")
            return {"ok": True, "path": str(chosen)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def toggle_play_audio(self, audio_path_str: str = "") -> dict[str, Any]:
        """Play or stop the currently selected or recorded audio file directly inside Steno."""
        if self._play_proc and self._play_proc.poll() is None:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
            self._play_proc = None
            self._log("==> Avspilling stoppet.")
            self._emit("playback_state", {"playing": False})
            return {"ok": True, "playing": False}

        raw = (audio_path_str or "").strip() or (str(self._rec_path) if self._rec_path else "")
        if not raw:
            msg = "Velg en lydfil eller gjør et opptak først for å spille av."
            self._log(f"==> {msg}")
            return {"ok": False, "playing": False, "error": msg}

        base_dir = _get_project_base_dir()
        target = resolve_moved_audio_path(Path(raw), base_dir)
        if not target.is_file() or is_transcript_file(target):
            msg = f"Fant ikke spillbar lydfil: {target.name}"
            self._log(f"==> {msg}")
            return {"ok": False, "playing": False, "error": msg}

        play_cmd: list[str] | None = None
        if sys.platform == "darwin" and shutil.which("afplay"):
            play_cmd = ["afplay", str(target)]
        elif shutil.which("ffplay"):
            play_cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(target)]

        if play_cmd is None:
            res = self.open_file_in_os(str(target))
            return {
                "ok": bool(res.get("ok")),
                "playing": False,
                "opened_external": True,
                "audio_path": str(target),
            }

        try:
            proc = subprocess.Popen(
                play_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._play_proc = proc
            self._log(f"==> Spiller av lydfil: {target.name}")

            def _watch_playback(p: subprocess.Popen[bytes]) -> None:
                try:
                    p.wait()
                except Exception:
                    pass
                if self._play_proc is p:
                    self._play_proc = None
                    self._emit("playback_state", {"playing": False})

            threading.Thread(target=_watch_playback, args=(proc,), daemon=True).start()
            return {"ok": True, "playing": True, "audio_path": str(target)}
        except Exception as exc:
            return {"ok": False, "playing": False, "error": str(exc)}

    def start_job(
        self,
        audio_path: str | dict[str, Any] = "",
        out_dir: str = "",
        html_name: str = "",
        notes_name: str = "",
        notes_html_name: str = "",
        whisper_model: str = "NbAiLab/nb-whisper-small",
        ollama_model: str = "gemma4:e4b",
        language: str = "no",
        hf_token: str = "",
        min_speakers: str = "",
        max_speakers: str = "",
        speaker_names_hint: str = "",
        meeting_title: str = "",
        agenda_text: str = "",
        note_mode: str = "auto",
        task_mode: str = "auto_resume",
        manual_notes_text: str = "",
    ) -> dict[str, Any]:
        if isinstance(audio_path, dict):
            cfg = audio_path
            args_tuple = (
                str(cfg.get("audio_path", "")),
                str(cfg.get("out_dir", out_dir)),
                str(cfg.get("html_name", html_name)),
                str(cfg.get("notes_name", notes_name)),
                str(cfg.get("notes_html_name", notes_html_name)),
                str(cfg.get("whisper_model", whisper_model)),
                str(cfg.get("ollama_model", ollama_model)),
                str(cfg.get("language", language)),
                str(cfg.get("hf_token", hf_token)),
                str(cfg.get("min_speakers", min_speakers)),
                str(cfg.get("max_speakers", max_speakers)),
                str(cfg.get("speaker_names_hint", speaker_names_hint)),
                str(cfg.get("meeting_title", meeting_title)),
                str(cfg.get("agenda_text", agenda_text)),
                str(cfg.get("note_mode", note_mode)),
                str(cfg.get("task_mode", task_mode)),
                str(cfg.get("manual_notes_text", manual_notes_text)),
            )
        else:
            args_tuple = (
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
            )
        with self._state_lock:
            if self._job_running:
                return {"ok": False, "error": "En jobb kjører allerede."}
            self._job_running = True
        try:
            threading.Thread(
                target=self._worker,
                args=args_tuple,
                daemon=True,
            ).start()
        except Exception as exc:
            with self._state_lock:
                self._job_running = False
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _log(self, msg: str) -> None:
        self._log_lines.append(msg)
        self._emit("log", {"line": msg})

    def _refresh_checkpoints_ui(self) -> None:
        self._emit("checkpoints_dirty", {})

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
        self._emit("progress", {"percent": percent, "label": label})

    def _set_running(self, running: bool) -> None:
        self._emit("running", {"running": running})

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
            with self._state_lock:
                self._job_running = False
            self._set_running(False)
            self._refresh_checkpoints_ui()


def _load_app_html() -> str:
    return assemble_app_html()


HTML = _load_app_html()


def bind_drag_and_drop(window: Any) -> None:
    def on_drop(e: dict[str, Any]) -> None:
        files = e.get("dataTransfer", {}).get("files", [])
        if files:
            full_path = files[0].get("pywebviewFullPath")
            if full_path:
                window.evaluate_js(f"handleDroppedAudio({json.dumps(str(Path(full_path)))})")

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


def build_native_app_menu(window: Any, ui_lang: str = "no") -> list[Any] | None:
    """Build native OS menu bar (macOS / desktop pywebview) for File, Session, and Mode actions."""
    try:
        from webview.menu import Menu, MenuAction, MenuSeparator  # type: ignore[import-not-found]
    except Exception:
        return None

    is_en = ui_lang == "en"
    return [
        Menu(
            "File" if is_en else "Fil",
            [
                MenuAction(
                    "Open Audio File / Resume Session..." if is_en else "Åpne lydfil / Gjenoppta økt...",
                    lambda: window.evaluate_js("selectModeTab('transcribe'); browseAudio()"),
                ),
                MenuAction(
                    "Fetch from Stream / URL..." if is_en else "Hent fra strøm / nettadresse (URL)...",
                    lambda: window.evaluate_js("selectModeTab('transcribe'); promptAudioUrl()"),
                ),
                MenuSeparator(),
                MenuAction(
                    "Open Transcript (.html)" if is_en else "Åpne transkripsjon (.html)",
                    lambda: window.evaluate_js("openGeneratedFile('html')"),
                ),
                MenuAction(
                    "Open Meeting Notes (.html)" if is_en else "Åpne møtereferat (.html)",
                    lambda: window.evaluate_js("openGeneratedFile('notes_html')"),
                ),
                MenuAction(
                    "Open Output Folder" if is_en else "Åpne utdatamappe",
                    lambda: window.evaluate_js("openFinder()"),
                ),
                MenuSeparator(),
                MenuAction(
                    "Install Desktop Shortcut" if is_en else "Installer snarvei på maskinen",
                    lambda: window.evaluate_js("installStenoShortcut()"),
                ),
                MenuAction(
                    "Uninstall Steno..." if is_en else "Avinstaller Steno...",
                    lambda: window.evaluate_js("openUninstallDialog()"),
                ),
            ],
        ),
        Menu(
            "Session" if is_en else "Økt",
            [
                MenuAction(
                    "Meeting (Multi-speaker)" if is_en else "Møte (Flere talere)",
                    lambda: window.evaluate_js("setSessionFormat('mote')"),
                ),
                MenuAction(
                    "Lecture / Talk (1 speaker)" if is_en else "Forelesning / Foredrag (1 taler)",
                    lambda: window.evaluate_js("setSessionFormat('foredrag')"),
                ),
                MenuSeparator(),
                MenuAction(
                    "Record" if is_en else "Ta opp",
                    lambda: window.evaluate_js("selectModeTab('record')"),
                ),
                MenuAction(
                    "Transcribe & Notes" if is_en else "Transkriber & Referat",
                    lambda: window.evaluate_js("selectModeTab('transcribe')"),
                ),
                MenuAction(
                    "Settings" if is_en else "Innstillinger",
                    lambda: window.evaluate_js("selectModeTab('settings')"),
                ),
            ],
        ),
    ]


def main() -> None:
    api = MoteApi()
    if not has_native_webview_backend():
        if is_termux():
            cli_main(["--help"])
            return
        run_browser_gui(api, HTML)
        return

    window = webview.create_window(
        "Steno — A modern annalist for your meetings — an automaton stenographer",
        html=HTML,
        js_api=api,
        width=1080,
        height=760,
        min_size=(840, 620),
    )
    api.window = window
    try:
        window.events.closed += api.shutdown_children
    except Exception:
        pass

    prefs = load_user_preferences()
    ui_lang = str(prefs.get("ui_lang") or resolve_ui_lang())
    app_menu = build_native_app_menu(window, ui_lang)

    try:
        if app_menu:
            try:
                webview.start(bind_drag_and_drop, window, menu=app_menu)
                return
            except TypeError:
                pass
        webview.start(bind_drag_and_drop, window)
    except Exception:
        run_browser_gui(api, HTML)


def run_self_test() -> int:
    """Verify extracted runner scripts, HTML templates, and MoteApi without opening a blocking GUI window."""
    compile(MACOS_COREAUDIO_RECORD_RUNNER_CODE, "macos_coreaudio_record.py", "exec")
    compile(WHISPERX_STAGE_RUNNER_CODE, "whisperx_stage_runner.py", "exec")
    compile(LIVE_CHUNK_WORKER_CODE, "live_chunk_worker.py", "exec")
    ns: dict[str, object] = {}
    header_lines = []
    for line in WHISPERX_STAGE_RUNNER_CODE.splitlines():
        if line.startswith("import torch") or line.startswith("import whisperx"):
            break
        header_lines.append(line)
    exec("\n".join(header_lines), ns)
    assert "sys" in ns and "Path" in ns, "Stage runner missing sys or Path"
    app_html = _load_app_html()
    assert "<html" in app_html.lower(), "App HTML template failed to load"
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
