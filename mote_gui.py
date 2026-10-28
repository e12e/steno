#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
# ]
# ///
"""Minimal native macOS GUI for WhisperX + Ollama meeting transcription."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Iterator
import urllib.request
import webview
from webview.dom import DOMEventHandler

OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"


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


def fetch_ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=1.0) as resp:
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
        time.sleep(0.5)
    return False


@contextmanager
def managed_ollama_server(log_fn) -> Iterator[None]:
    if fetch_ollama_models() is not None:
        log_fn("Ollama server kjører allerede.")
        yield
        return

    log_fn("Starter ollama serve midlertidig...")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_ollama():
            raise RuntimeError("Klarte ikke å starte ollama serve.")
        yield
    finally:
        if proc.poll() is None:
            log_fn(f"Stopper midlertidig ollama serve (PID {proc.pid})...")
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

    def pick_audio_file(self) -> dict | None:
        if not self.window:
            return None
        file_types = ("Audio Files (*.wav;*.m4a;*.mp3;*.ogg;*.opus;*.flac)", "All files (*.*)")
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types
        )
        if result and len(result) > 0:
            p = Path(result[0])
            return {"audio_path": str(p), "out_dir": str(p.parent)}
        return None

    def pick_output_dir(self) -> str | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return str(result[0])
        return None

    def reveal_in_finder(self, folder_path: str) -> None:
        if folder_path and Path(folder_path).is_dir():
            subprocess.Popen(["open", folder_path])

    def start_job(
        self,
        audio_path: str,
        out_dir: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
    ) -> None:
        threading.Thread(
            target=self._worker,
            args=(audio_path, out_dir, whisper_model, ollama_model, language, hf_token),
            daemon=True,
        ).start()

    def _log(self, msg: str) -> None:
        if self.window:
            escaped = json.dumps(msg)
            self.window.evaluate_js(f"appendLog({escaped})")

    def _set_running(self, running: bool) -> None:
        if self.window:
            self.window.evaluate_js(f"setRunning({'true' if running else 'false'})")

    def _worker(
        self,
        audio_path_str: str,
        out_dir_str: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
    ) -> None:
        self._set_running(True)
        try:
            audio_path = Path(audio_path_str).resolve()
            out_dir = Path(out_dir_str).resolve() if out_dir_str else audio_path.parent
            out_dir.mkdir(parents=True, exist_ok=True)

            if not audio_path.is_file():
                self._log(f"FEIL: Fant ikke lydfilen {audio_path}")
                return

            if hf_token.strip():
                save_hf_token(hf_token)
            elif not is_diarization_cached():
                self._log("FEIL: Pyannote er ikke bufret ennå. Lim inn et Hugging Face Read-token.")
                return

            txt_path = out_dir / f"{audio_path.stem}.txt"
            notes_path = out_dir / f"{audio_path.stem}_referat.md"

            # 1/2 WhisperX
            self._log(f"==> [1/2] Kjører WhisperX ({whisper_model}) på {audio_path.name}...")
            env = {**os.environ, "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io"}
            # Ensure ~/.local/bin and /opt/homebrew/bin are in PATH when launched from Finder
            env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"

            cmd = [
                "whisperx",
                str(audio_path),
                "--output_dir", str(out_dir),
                "--model", whisper_model,
                "--language", language,
                "--diarize",
                "--compute_type", "int8",
                "--output_format", "txt",
            ]
            if hf_token.strip():
                cmd.extend(["--hf_token", hf_token.strip()])

            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.strip():
                    self._log(line.rstrip())
            if proc.wait() != 0:
                self._log("FEIL: WhisperX feilet.")
                return

            self._log(f"==> Transkripsjon lagret: {txt_path}")

            # 2/2 Ollama
            with managed_ollama_server(self._log):
                installed = fetch_ollama_models() or []
                if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
                    self._log(f"==> Laster ned '{ollama_model}' med ollama pull...")
                    subprocess.run(["ollama", "pull", ollama_model], env=env, check=True)

                self._log(f"==> [2/2] Genererer møtereferat med {ollama_model}...")
                transcript = txt_path.read_text(encoding="utf-8")
                prompt = (
                    "Lag et strukturert møtereferat på norsk basert på denne transkripsjonen med taler-IDer. "
                    "Inkluder:\n1. Kort oppsummering\n2. Hovedpunkter per tema\n3. Beslutninger\n4. Aksjonspunkter\n\n"
                    f"Transkripsjon:\n{transcript}"
                )
                with notes_path.open("w", encoding="utf-8") as out_file:
                    subprocess.run(
                        ["ollama", "run", ollama_model],
                        input=prompt,
                        text=True,
                        stdout=out_file,
                        env=env,
                        check=True,
                    )

            self._log(f"==> FERDIG!\n    Transkripsjon: {txt_path}\n    Møtereferat  : {notes_path}")
        except Exception as exc:
            self._log(f"FEIL: {exc}")
        finally:
            self._set_running(False)


HTML = """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<style>
  :root {
    color-scheme: light dark;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  }
  body {
    margin: 0; padding: 18px;
    background: Canvas; color: CanvasText;
    font-size: 13px; user-select: none;
    display: flex; flex-direction: column; gap: 12px; height: calc(100vh - 36px);
  }
  #dropzone {
    border: 2px dashed GrayText; border-radius: 10px;
    padding: 22px; text-align: center; cursor: pointer;
    transition: all 0.15s ease;
  }
  #dropzone.dragover {
    border-color: #0a84ff; background: rgba(10, 132, 255, 0.1);
  }
  #dropzone strong { font-size: 14px; display: block; margin-bottom: 4px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row label { width: 95px; flex-shrink: 0; font-weight: 500; }
  input[type="text"], input[type="password"], select {
    flex: 1; padding: 6px 8px; border-radius: 6px;
    border: 1px solid rgba(128,128,128,0.4); background: Field; color: FieldText;
    font-size: 13px;
  }
  button {
    padding: 6px 12px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.4);
    background: ButtonFace; color: ButtonText; cursor: pointer; font-size: 13px;
  }
  button.primary {
    background: #0a84ff; color: white; border: none; font-weight: 600; padding: 8px 16px;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #log {
    flex: 1; background: rgba(128,128,128,0.1); border-radius: 8px;
    padding: 10px; font-family: "SF Mono", Menlo, monospace; font-size: 11.5px;
    overflow-y: auto; white-space: pre-wrap; user-select: text;
    border: 1px solid rgba(128,128,128,0.25);
  }
</style>
</head>
<body>

  <div id="dropzone" onclick="browseAudio()">
    <strong>Slipp lydfil her (.m4a, .wav, .opus, .mp3)</strong>
    <span id="selected-file" style="color: GrayText;">Eller klikk for å velge i Finder...</span>
  </div>

  <div class="row">
    <label>Lagre i mappe:</label>
    <input type="text" id="out_dir" placeholder="Velg mappe...">
    <button onclick="browseFolder()">Velg...</button>
    <button onclick="openFinder()" title="Vis i Finder">Åpne</button>
  </div>

  <div class="row">
    <label>Modeller:</label>
    <select id="whisper_model">
      <option value="large-v3-turbo" selected>Whisper: large-v3-turbo</option>
      <option value="large-v3">Whisper: large-v3</option>
      <option value="medium">Whisper: medium</option>
      <option value="small">Whisper: small</option>
    </select>
    <input type="text" id="ollama_model" value="gemma4:e4b" placeholder="Ollama-modell">
    <select id="language" style="max-width: 75px;">
      <option value="no" selected>NO</option>
      <option value="en">EN</option>
      <option value="sv">SV</option>
      <option value="da">DA</option>
    </select>
  </div>

  <div class="row">
    <label>HF Token:</label>
    <input type="password" id="hf_token" placeholder="hf_... (kun nødvendig første gang)">
  </div>

  <div class="row" style="justify-content: flex-end;">
    <button class="primary" id="run_btn" onclick="runPipeline()" disabled>Transkriber & Lag Referat</button>
  </div>

  <div id="log">Klar. Slipp en lydfil over for å starte.</div>

<script>
  let currentAudioPath = "";

  window.addEventListener('pywebviewready', async () => {
    const defs = await pywebview.api.get_defaults();
    if (defs.hf_token) document.getElementById('hf_token').value = defs.hf_token;
    if (defs.diarization_cached && !defs.hf_token) {
      document.getElementById('hf_token').placeholder = "Pyannote er allerede bufret lokalt (token valgfri)";
    }
    document.getElementById('out_dir').value = defs.default_out_dir;
  });

  const dz = document.getElementById('dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dragover');
  });

  function setAudioFile(fullPath, parentDir) {
    currentAudioPath = fullPath;
    document.getElementById('selected-file').textContent = fullPath;
    if (parentDir) document.getElementById('out_dir').value = parentDir;
    document.getElementById('run_btn').disabled = false;
  }

  async function browseAudio() {
    const res = await pywebview.api.pick_audio_file();
    if (res) setAudioFile(res.audio_path, res.out_dir);
  }

  async function browseFolder() {
    const folder = await pywebview.api.pick_output_dir();
    if (folder) document.getElementById('out_dir').value = folder;
  }

  function openFinder() {
    pywebview.api.reveal_in_finder(document.getElementById('out_dir').value);
  }

  function appendLog(line) {
    const log = document.getElementById('log');
    log.textContent += "\\n" + line;
    log.scrollTop = log.scrollHeight;
  }

  function setRunning(isRunning) {
    const btn = document.getElementById('run_btn');
    btn.disabled = isRunning || !currentAudioPath;
    btn.textContent = isRunning ? "Arbeider..." : "Transkriber & Lag Referat";
  }

  function runPipeline() {
    document.getElementById('log').textContent = "Starter jobb...";
    pywebview.api.start_job(
      currentAudioPath,
      document.getElementById('out_dir').value,
      document.getElementById('whisper_model').value,
      document.getElementById('ollama_model').value,
      document.getElementById('language').value,
      document.getElementById('hf_token').value
    );
  }
</script>
</body>
</html>
"""


def bind_drag_and_drop(window: webview.Window) -> None:
    """Bind native macOS file drop handler to extract full filesystem paths."""
    def on_drop(e: dict) -> None:
        files = e.get("dataTransfer", {}).get("files", [])
        if files:
            full_path = files[0].get("pywebviewFullPath")
            if full_path:
                p = Path(full_path)
                window.evaluate_js(
                    f"setAudioFile({json.dumps(str(p))}, {json.dumps(str(p.parent))})"
                )

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


def main() -> None:
    api = MoteApi()
    window = webview.create_window(
        "Møtereferat (WhisperX + Ollama)",
        html=HTML,
        js_api=api,
        width=600,
        height=520,
    )
    api.window = window
    webview.start(bind_drag_and_drop, window)


if __name__ == "__main__":
    main()