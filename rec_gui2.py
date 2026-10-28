#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
# ]
# ///
"""Minimal native macOS GUI for WhisperX + Ollama with dual progress bars."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Iterator
import urllib.request
import webview
from webview.dom import DOMEventHandler

OLLAMA_BASE = "http://127.0.0.1:11434"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"
PROGRESS_RE = re.compile(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%")


def is_diarization_cached() -> bool:
    hub_dir = HF_CACHE_DIR / "hub"
    if not hub_dir.is_dir():
        return False
    return any(
        any(p.glob("snapshots/*/*"))
        for p in hub_dir.glob("models--pyannote--speaker-diarization*")
    )


def get_initial_hf_token() -> str:
    if tok := os.environ.get("HF_TOKEN", "").strip():
        return tok
    if HF_TOKEN_FILE.is_file():
        return HF_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_hf_token(token: str) -> None:
    token = token.strip()
    if token:
        HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        HF_TOKEN_FILE.write_text(token, encoding="utf-8")
        HF_TOKEN_FILE.chmod(0o600)


def unique_filename(directory: Path, stem: str, suffix: str) -> str:
    candidate = f"{stem}{suffix}"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def fetch_ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=1.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None
    return None


def wait_for_ollama(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fetch_ollama_models() is not None:
            return True
        time.sleep(0.4)
    return False


@contextmanager
def managed_ollama_server(log_fn: Callable[[str], None], env: dict) -> Iterator[None]:
    """Start ollama serve on-demand if not running; stop it on exit if started by us."""
    if fetch_ollama_models() is not None:
        log_fn("==> Ollama kjører allerede i bakgrunnen (lar den kjøre etterpå).")
        yield
        return

    log_fn("==> Starter 'ollama serve' midlertidig ved behov...")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_ollama():
            raise RuntimeError("Klarte ikke å starte 'ollama serve' innen 15 sekunder.")
        log_fn(f"==> Midlertidig 'ollama serve' startet (PID {proc.pid}).")
        yield
    finally:
        if proc.poll() is None:
            log_fn(f"==> Stopper midlertidig 'ollama serve' (PID {proc.pid})...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


class MoteApi:
    def __init__(self) -> None:
        self.window: webview.Window | None = None

    def get_defaults(self) -> dict:
        return {
            "hf_token": get_initial_hf_token(),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(Path.home() / "Downloads"),
        }

    def suggest_output_names(self, audio_path_str: str, out_dir_str: str) -> dict:
        audio_path = Path(audio_path_str)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        stem = audio_path.stem
        return {
            "audio_path": str(audio_path),
            "out_dir": str(out_dir),
            "txt_name": unique_filename(out_dir, f"{stem}_transkripsjon", ".txt"),
            "notes_name": unique_filename(out_dir, f"{stem}_referat", ".md"),
        }

    def pick_audio_file(self) -> dict | None:
        if not self.window:
            return None
        file_types = ("Audio Files (*.wav;*.m4a;*.mp3;*.ogg;*.opus;*.flac)", "All files (*.*)")
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types
        )
        if result and len(result) > 0:
            p = Path(result[0])
            return self.suggest_output_names(str(p), str(p.parent))
        return None

    def pick_output_dir(self, current_audio_path: str) -> dict | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            folder = str(result[0])
            if current_audio_path:
                return self.suggest_output_names(current_audio_path, folder)
            return {"out_dir": folder}
        return None

    def check_conflicts(self, out_dir_str: str, txt_name: str, notes_name: str) -> list[str]:
        out_dir = Path(out_dir_str).resolve()
        conflicts = []
        for name in (txt_name.strip(), notes_name.strip()):
            if name:
                candidate = out_dir / Path(name).name
                if candidate.exists():
                    conflicts.append(str(candidate))
        return conflicts

    def reveal_in_finder(self, folder_path: str) -> None:
        if folder_path and Path(folder_path).is_dir():
            subprocess.Popen(["open", folder_path])

    def start_job(
        self,
        audio_path: str,
        out_dir: str,
        txt_name: str,
        notes_name: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
    ) -> None:
        threading.Thread(
            target=self._worker,
            args=(
                audio_path,
                out_dir,
                txt_name,
                notes_name,
                whisper_model,
                ollama_model,
                language,
                hf_token,
            ),
            daemon=True,
        ).start()

    def _log(self, msg: str) -> None:
        if self.window:
            self.window.evaluate_js(f"appendLog({json.dumps(msg)})")

    def _progress(self, task_pct: float, total_pct: float, task_label: str) -> None:
        if self.window:
            t_pct = max(0.0, min(100.0, task_pct))
            tot_pct = max(0.0, min(100.0, total_pct))
            self.window.evaluate_js(
                f"updateProgress({t_pct:.1f}, {tot_pct:.1f}, {json.dumps(task_label)})"
            )

    def _set_running(self, running: bool) -> None:
        if self.window:
            self.window.evaluate_js(f"setRunning({'true' if running else 'false'})")

    def _pull_ollama_model_with_progress(self, model: str) -> None:
        self._log(f"==> Laster ned Ollama-modell '{model}'...")
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/pull",
            data=json.dumps({"name": model, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            for raw_line in resp:
                if not raw_line.strip():
                    continue
                msg = json.loads(raw_line.decode("utf-8"))
                status = msg.get("status", "Laster ned...")
                completed = msg.get("completed", 0)
                total = msg.get("total", 0)
                if total > 0:
                    pct = (completed / total) * 100.0
                    self._progress(pct, 80.0 + (pct * 0.05), f"Laster ned {model}: {status}")

    def _generate_notes_with_progress(
        self, model: str, transcript: str, notes_path: Path
    ) -> None:
        prompt = (
            "Lag et strukturert møtereferat på norsk basert på denne transkripsjonen med taler-IDer. "
            "Inkluder:\n1. Kort oppsummering\n2. Hovedpunkter per tema\n3. Beslutninger\n4. Aksjonspunkter\n\n"
            f"Transkripsjon:\n{transcript}"
        )

        # Estimate output tokens (~25% of input words, clamped between 350 and 1600 tokens)
        est_tokens = max(350, min(1600, int(len(transcript.split()) * 0.35)))

        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        token_count = 0
        with notes_path.open("x", encoding="utf-8") as out_file:
            with urllib.request.urlopen(req) as resp:
                for raw_line in resp:
                    if not raw_line.strip():
                        continue
                    chunk = json.loads(raw_line.decode("utf-8"))
                    text_piece = chunk.get("response", "")
                    if text_piece:
                        out_file.write(text_piece)
                        out_file.flush()
                        token_count += 1
                        # Asymptotic progress up to 98% until 'done' flag arrives
                        task_pct = min(98.0, (token_count / est_tokens) * 100.0)
                        total_pct = 85.0 + (task_pct * 0.15)
                        self._progress(
                            task_pct,
                            total_pct,
                            f"2/2 Genererer møtereferat ({token_count} tokens skrevet)...",
                        )
                    if chunk.get("done"):
                        break

    def _worker(
        self,
        audio_path_str: str,
        out_dir_str: str,
        txt_name_str: str,
        notes_name_str: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
    ) -> None:
        self._set_running(True)
        self._progress(0.0, 0.0, "Forbereder kjøring...")
        try:
            audio_path = Path(audio_path_str).resolve()
            out_dir = Path(out_dir_str).resolve() if out_dir_str else audio_path.parent
            out_dir.mkdir(parents=True, exist_ok=True)

            if not audio_path.is_file():
                self._log(f"FEIL: Fant ikke lydfilen {audio_path}")
                return

            txt_name = Path(txt_name_str.strip()).name
            notes_name = Path(notes_name_str.strip()).name
            if not txt_name or not notes_name or txt_name == notes_name:
                self._log("FEIL: Ugyldige utdata-filnavn.")
                return

            txt_path = out_dir / txt_name
            notes_path = out_dir / notes_name

            for target in (txt_path, notes_path):
                if target.exists():
                    self._log(f"AVBRUTT: Filen finnes allerede:\n  {target}")
                    return

            if hf_token.strip():
                save_hf_token(hf_token)
            elif not is_diarization_cached():
                self._log("FEIL: Pyannote er ikke bufret ennå. Lim inn et Hugging Face Read-token.")
                return

            env = {
                **os.environ,
                "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io",
                "PYTHONUNBUFFERED": "1",
            }
            env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"

            # 1/2 WhisperX with --print_progress True
            self._log(f"==> [1/2] Starter WhisperX ({whisper_model}) på {audio_path.name}...")
            self._progress(2.0, 2.0, "1/2 Laster Whisper-modell...")

            with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
                cmd = [
                    "whisperx",
                    str(audio_path),
                    "--output_dir", tmp_dir,
                    "--model", whisper_model,
                    "--language", language,
                    "--diarize",
                    "--compute_type", "int8",
                    "--output_format", "txt",
                    "--print_progress", "True",
                ]
                if hf_token.strip():
                    cmd.extend(["--hf_token", hf_token.strip()])

                proc = subprocess.Popen(
                    cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
                )
                assert proc.stdout is not None

                # WhisperX runs 3 sub-phases that each emit Progress: 0% -> 100%:
                # Phase 0: ASR Transcription (Total 0% -> 50%)
                # Phase 1: Wav2Vec2 Alignment (Total 50% ->