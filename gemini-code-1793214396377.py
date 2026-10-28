#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
# ]
# ///
"""Native macOS GUI for WhisperX + Ollama meeting transcription with Rich HTML and Clean Notes."""

from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
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


def unique_filename(directory: Path, stem: str, suffix: str) -> str:
    candidate = f"{stem}{suffix}"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def format_seconds(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


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


def clean_llm_output(raw_output: str) -> str:
    """Removes <think>...</think> reasoning traces and cleans problematic control characters."""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_output, flags=re.DOTALL)
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", cleaned)
    return cleaned.strip()


def build_rich_html(json_path: Path, html_path: Path, title: str) -> str:
    """Creates a standalone HTML transcript and returns clean plain-text for Ollama."""
    with open(json_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    colors = ["#007AFF", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5856D6"]
    speaker_color_map = {}

    llm_lines = []
    html_items = []

    for seg in segments:
        speaker = seg.get("speaker", "Ukjent taler")
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()

        # Sanitize corrupt symbols
        clean_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
        if not clean_text:
            continue

        time_str = f"{format_seconds(start)} - {format_seconds(end)}"
        llm_lines.append(f"[{time_str}] {speaker}: {clean_text}")

        if speaker not in speaker_color_map:
            speaker_color_map[speaker] = colors[len(speaker_color_map) % len(colors)]
        spk_color = speaker_color_map[speaker]

        html_items.append(f"""
        <div class="turn">
          <div class="meta">
            <span class="badge" style="background-color: {spk_color};">{html.escape(speaker)}</span>
            <span class="timestamp">{time_str}</span>
          </div>
          <div class="text">{html.escape(clean_text)}</div>
        </div>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="no">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} - Transkripsjon</title>
  <style>
    :root {{
      --bg: #f8f9fa; --card-bg: #ffffff; --text: #212529; --sub: #6c757d; --border: #dee2e6;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #121212; --card-bg: #1e1e1e; --text: #e0e0e0; --sub: #9e9e9e; --border: #333333;
      }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text);
      max-width: 860px; margin: 30px auto; padding: 0 20px 60px 20px;
      line-height: 1.5;
    }}
    header {{
      margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
    }}
    h1 {{ font-size: 1.4rem; margin: 0 0 8px 0; }}
    #filter {{
      width: 100%; box-sizing: border-box; padding: 10px 14px;
      border: 1px solid var(--border); border-radius: 8px;
      background: var(--card-bg); color: var(--text); font-size: 14px;
      margin-bottom: 20px;
    }}
    .turn {{
      background: var(--card-bg); border: 1px solid var(--border);
      border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;
    }}
    .meta {{
      display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 12px;
    }}
    .badge {{
      color: #fff; font-weight: 600; padding: 2px 8px; border-radius: 12px;
    }}
    .timestamp {{
      color: var(--sub); font-family: ui-monospace, monospace;
    }}
    .text {{ font-size: 14.5px; }}
  </style>
</head>
<body>
  <header>
    <h1>Transkripsjon: {html.escape(title)}</h1>
    <input type="text" id="filter" placeholder="Søk i transkripsjon eller taler..." oninput="filterTurns()">
  </header>
  <main id="container">
    {''.join(html_items)}
  </main>
  <script>
    function filterTurns() {{
      const q = document.getElementById('filter').value.toLowerCase();
      document.querySelectorAll('.turn').forEach(turn => {{
        turn.style.display = turn.innerText.toLowerCase().includes(q) ? '' : 'none';
      }});
    }}
  </script>
</body>
</html>
"""
    html_path.write_text(html_content, encoding="utf-8")
    return "\n".join(llm_lines)


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
            "html_name": unique_filename(out_dir, f"{stem}_transkripsjon", ".html"),
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

    def check_conflicts(self, out_dir_str: str, html_name: str, notes_name: str) -> list[str]:
        out_dir = Path(out_dir_str).resolve()
        conflicts = []
        for name in (html_name.strip(), notes_name.strip()):
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
        html_name: str,
        notes_name: str,
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
        min_speakers: str,
        max_speakers: str,
    ) -> None:
        threading.Thread(
            target=self._worker,
            args=(
                audio_path,
                out_dir,
                html_name,
                notes_name,
                whisper_model,
                ollama_model,
                language,
                hf_token,
                min_speakers,
                max_speakers,
            ),
            daemon=True,
        ).start()

    def _log(self, msg: str) -> None:
        if self.window:
            escaped = json.dumps(msg)
            self.window.evaluate_js(f"appendLog({escaped})")

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
        whisper_model: str,
        ollama_model: str,
        language: str,
        hf_token: str,
        min_speakers_str: str,
        max_speakers_str: str,
    ) -> None:
        self._set_running(True)
        self._set_progress(5, "Forbereder miljø og filer...")
        try:
            audio_path = Path(audio_path_str).resolve()
            out_dir = Path(out_dir_str).resolve() if out_dir_str else audio_path.parent
            out_dir.mkdir(parents=True, exist_ok=True)

            if not audio_path.is_file():
                self._log(f"FEIL: Fant ikke lydfilen {audio_path}")
                self._set_progress(0, "Feil: Lydfil mangler")
                return

            html_name = Path(html_name_str.strip()).name
            notes_name = Path(notes_name_str.strip()).name
            if not html_name or not notes_name:
                self._log("FEIL: Du må oppgi filnavn for både transkripsjon og møtereferat.")
                return
            if html_name == notes_name:
                self._log("FEIL: Transkripsjon og referat må ha ulike filnavn.")
                return

            html_path = out_dir / html_name
            notes_path = out_dir / notes_name

            for target in (html_path, notes_path):
                if target.exists():
                    self._log(f"AVBRUTT: Filen finnes allerede:\n  {target}")
                    self._set_progress(0, "Avbrutt pga filkonflikt")
                    return

            if hf_token.strip():
                save_hf_token(hf_token)
            elif not is_diarization_cached():
                self._log("FEIL: Pyannote er ikke bufret ennå. Lim inn HF-token.")
                self._set_progress(0, "Mangler HF-token")
                return

            env = {**os.environ, "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io"}
            env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"

            self._set_progress(15, f"Kjører WhisperX ({whisper_model})...")
            self._log(f"==> [1/2] Kjører WhisperX på {audio_path.name}...")

            with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
                cmd = [
                    "whisperx",
                    str(audio_path),
                    "--output_dir", tmp_dir,
                    "--model", whisper_model,
                    "--language", language,
                    "--diarize",
                    "--compute_type", "int8",
                    "--output_format", "json",
                ]
                if hf_token.strip():
                    cmd.extend(["--hf_token", hf_token.strip()])

                if min_speakers_str.isdigit():
                    cmd.extend(["--min_speakers", min_speakers_str.strip()])
                if max_speakers_str.isdigit():
                    cmd.extend(["--max_speakers", max_speakers_str.strip()])

                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    clean = line.rstrip()
                    if clean:
                        self._log(clean)
                        if "%" in clean:
                            match = re.search(r"(\d+)%", clean)
                            if match:
                                p_pct = int(match.group(1))
                                mapped = int(15 + (p_pct * 0.4))
                                self._set_progress(mapped, f"Whisper transkribering: {p_pct}%")
                        elif "diariz" in clean.lower() or "pyannote" in clean.lower():
                            self._set_progress(60, "Identifiserer talere (Diarization)...")

                if proc.wait() != 0:
                    self._log("FEIL: WhisperX feilet.")
                    self._set_progress(0, "Feil under WhisperX")
                    return

                raw_json = Path(tmp_dir) / f"{audio_path.stem}.json"
                if not raw_json.is_file():
                    self._log("FEIL: Fant ikke generert JSON fra WhisperX.")
                    self._set_progress(0, "JSON-fil mangler")
                    return

                self._set_progress(70, "Bygger interaktiv HTML-transkripsjon...")
                clean_transcript = build_rich_html(raw_json, html_path, audio_path.stem)

            self._log(f"==> Rik HTML-transkripsjon lagret: {html_path}")

            with managed_ollama_server(self._log):
                installed = fetch_ollama_models() or []
                if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
                    self._set_progress(75, f"Laster ned {ollama_model}...")
                    self._log(f"==> Laster ned '{ollama_model}'...")
                    subprocess.run(["ollama", "pull", ollama_model], env=env, check=True)

                self._set_progress(82, f"Genererer møtereferat via {ollama_model}...")
                self._log(f"==> [2/2] Genererer strukturert møtereferat...")

                prompt = (
                    "Du er en assistent som skriver presise og ryddige møtereferater på norsk basert på transkripsjonen. "
                    "Ikke inkluder interne resonnementer eller tankeprosess i svaret.\n\n"
                    "Bruk nøyaktig denne strukturen:\n"
                    "# Møtereferat\n"
                    "## 1. Oppsummering\n"
                    "## 2. Diskusjoner og temaer (referér til hvem som sa hva)\n"
                    "## 3. Beslutninger\n"
                    "## 4. Aksjonspunkter (Hvem / Hva / Tidsfrist)\n\n"
                    f"Transkripsjon:\n{clean_transcript}"
                )

                proc = subprocess.run(
                    ["ollama", "run", ollama_model],
                    input=prompt,
                    text=True,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=True,
                )

                cleaned_notes = clean_llm_output(proc.stdout)
                notes_path.write_text(cleaned_notes, encoding="utf-8")

            self._set_progress(100, "Fullført!")
            self._log(f"==> FERDIG!\n    HTML-transkripsjon: {html_path}\n    Møtereferat       : {notes_path}")
        except Exception as exc:
            self._log(f"FEIL: {exc}")
            self._set_progress(0, f"Feilet: {exc}")
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
    margin: 0; padding: 14px;
    background: Canvas; color: CanvasText;
    font-size: 13px; user-select: none;
    display: flex; flex-direction: column; gap: 8px; height: calc(100vh - 28px);
  }
  #dropzone {
    border: 2px dashed GrayText; border-radius: 10px;
    padding: 10px 14px; display: flex; align-items: center; justify-content: space-between;
    gap: 12px; transition: all 0.15s ease;
  }
  #dropzone.dragover { border-color: #0a84ff; background: rgba(10, 132, 255, 0.1); }
  .dz-text { flex: 1; overflow: hidden; }
  .dz-text strong { font-size: 13px; display: block; margin-bottom: 2px; }
  #selected-file {
    color: GrayText; display: block; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-size: 11.5px;
  }
  .row { display: flex; gap: 8px; align-items: center; }
  .row label { width: 110px; flex-shrink: 0; font-weight: 500; font-size: 12.5px; }
  input[type="text"], input[type="password"], input[type="number"], select {
    flex: 1; padding: 5px 8px; border-radius: 6px;
    border: 1px solid rgba(128,128,128,0.4); background: Field; color: FieldText;
    font-size: 12.5px;
  }
  input.conflict { border-color: #ff453a; background: rgba(255, 69, 58, 0.08); }
  button {
    padding: 5px 11px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.4);
    background: ButtonFace; color: ButtonText; cursor: pointer; font-size: 12.5px;
    white-space: nowrap;
  }
  button.primary {
    background: #0a84ff; color: white; border: none; font-weight: 600; padding: 7px 15px;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #conflict-msg { color: #ff453a; font-size: 12px; min-height: 14px; flex: 1; }

  .progress-section { display: flex; flex-direction: column; gap: 3px; }
  .progress-header { display: flex; justify-content: space-between; font-size: 11px; color: GrayText; }
  .progress-bg {
    width: 100%; height: 7px; background: rgba(128,128,128,0.25);
    border-radius: 4px; overflow: hidden;
  }
  .progress-fill { height: 100%; width: 0%; background: #0a84ff; border-radius: 4px; transition: width 0.3s ease; }

  #log {
    flex: 1; background: rgba(128,128,128,0.1); border-radius: 8px;
    padding: 9px; font-family: "SF Mono", Menlo, monospace; font-size: 11px;
    overflow-y: auto; white-space: pre-wrap; user-select: text;
    border: 1px solid rgba(128,128,128,0.25);
  }
</style>
</head>
<body>

  <div id="dropzone">
    <div class="dz-text">
      <strong>Slipp lydfil her (.m4a, .wav, .opus, .mp3)</strong>
      <span id="selected-file">Ingen lydfil valgt ennå</span>
    </div>
    <button onclick="browseAudio()">Velg fil...</button>
  </div>

  <div class="row">
    <label>Mappe:</label>
    <input type="text" id="out_dir" placeholder="Velg mappe..." oninput="validateNames()">
    <button onclick="browseFolder()">Velg...</button>
    <button onclick="openFinder()" title="Vis i Finder">Åpne</button>
  </div>

  <div class="row">
    <label>Transkripsjon:</label>
    <input type="text" id="html_name" placeholder="mote_transkripsjon.html" oninput="validateNames()">
    <button onclick="autoIncrementNames()">Ledig navn</button>
  </div>

  <div class="row">
    <label>Referat:</label>
    <input type="text" id="notes_name" placeholder="mote_referat.md" oninput="validateNames()">
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
    <select id="language" style="max-width: 65px;">
      <option value="no" selected>NO</option>
      <option value="en">EN</option>
      <option value="sv">SV</option>
      <option value="da">DA</option>
    </select>
  </div>

  <div class="row">
    <label>Antall talere:</label>
    <input type="number" id="min_speakers" placeholder="Min (f.eks 2)" min="1" max="20" style="max-width: 110px;">
    <input type="number" id="max_speakers" placeholder="Maks (f.eks 2)" min="1" max="20" style="max-width: 110px;">
    <span style="color: GrayText; font-size: 11px;">(Lås talere for å unngå feilidentifisering)</span>
  </div>

  <div class="row">
    <label>HF Token:</label>
    <input type="password" id="hf_token" placeholder="hf_... (kun ved første gangs oppsett)">
  </div>

  <div class="row">
    <span id="conflict-msg"></span>
    <button class="primary" id="run_btn" onclick="runPipeline()" disabled>Transkriber & Lag Referat</button>
  </div>

  <div class="progress-section">
    <div class="progress-header">
      <span id="progress_label">Klar</span>
      <span id="progress_val">0%</span>
    </div>
    <div class="progress-bg">
      <div id="progress_bar" class="progress-fill"></div>
    </div>
  </div>

  <div id="log">Klar. Dra inn en lydfil for å begynne.</div>

<script>
  let currentAudioPath = "";
  let isJobRunning = false;

  window.addEventListener('pywebviewready', async () => {
    const defs = await pywebview.api.get_defaults();
    if (defs.hf_token) document.getElementById('hf_token').value = defs.hf_token;
    if (defs.diarization_cached && !defs.hf_token) {
      document.getElementById('hf_token').placeholder = "Pyannote bufret lokalt (token valgfri)";
    }
    document.getElementById('out_dir').value = defs.default_out_dir;
  });

  const dz = document.getElementById('dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('dragover'); });

  async function applySelection(info) {
    currentAudioPath = info.audio_path;
    document.getElementById('selected-file').textContent = info.audio_path;
    if (info.out_dir) document.getElementById('out_dir').value = info.out_dir;
    if (info.html_name) document.getElementById('html_name').value = info.html_name;
    if (info.notes_name) document.getElementById('notes_name').value = info.notes_name;
    await validateNames();
  }

  async function handleDroppedAudio(fullPath) {
    const info = await pywebview.api.suggest_output_names(
      fullPath,
      document.getElementById('out_dir').value
    );
    await applySelection(info);
  }

  async function browseAudio() {
    const info = await pywebview.api.pick_audio_file();
    if (info) await applySelection(info);
  }

  async function browseFolder() {
    const res = await pywebview.api.pick_output_dir(currentAudioPath);
    if (res) {
      document.getElementById('out_dir').value = res.out_dir;
      if (res.html_name) document.getElementById('html_name').value = res.html_name;
      if (res.notes_name) document.getElementById('notes_name').value = res.notes_name;
      await validateNames();
    }
  }

  async function autoIncrementNames() {
    if (!currentAudioPath) return;
    const info = await pywebview.api.suggest_output_names(
      currentAudioPath,
      document.getElementById('out_dir').value
    );
    document.getElementById('html_name').value = info.html_name;
    document.getElementById('notes_name').value = info.notes_name;
    await validateNames();
  }

  async function validateNames() {
    const outDir = document.getElementById('out_dir').value;
    const htmlInput = document.getElementById('html_name');
    const notesInput = document.getElementById('notes_name');
    const msg = document.getElementById('conflict-msg');
    const btn = document.getElementById('run_btn');

    const htmlName = htmlInput.value.trim();
    const notesName = notesInput.value.trim();

    if (!currentAudioPath || !htmlName || !notesName) {
      btn.disabled = true;
      return;
    }

    if (htmlName === notesName) {
      msg.textContent = "Filene må ha ulike navn.";
      htmlInput.classList.add('conflict');
      notesInput.classList.add('conflict');
      btn.disabled = true;
      return;
    }

    const conflicts = await pywebview.api.check_conflicts(outDir, htmlName, notesName);
    const hasHtmlConflict = conflicts.some(p => p.endsWith("/" + htmlName));
    const hasNotesConflict = conflicts.some(p => p.endsWith("/" + notesName));

    htmlInput.classList.toggle('conflict', hasHtmlConflict);
    notesInput.classList.toggle('conflict', hasNotesConflict);

    if (conflicts.length > 0) {
      msg.textContent = "Filen finnes allerede! Klikk «Ledig navn».";
      btn.disabled = true;
    } else {
      msg.textContent = "";
      btn.disabled = isJobRunning;
    }
  }

  function openFinder() {
    pywebview.api.reveal_in_finder(document.getElementById('out_dir').value);
  }

  function appendLog(line) {
    const log = document.getElementById('log');
    log.textContent += "\\n" + line;
    log.scrollTop = log.scrollHeight;
  }

  function updateProgress(percent, label) {
    const clamped = Math.max(0, Math.min(100, percent));
    document.getElementById('progress_bar').style.width = clamped + "%";
    document.getElementById('progress_val').textContent = clamped + "%";
    if (label) document.getElementById('progress_label').textContent = label;
  }

  function setRunning(running) {
    isJobRunning = running;
    const btn = document.getElementById('run_btn');
    btn.textContent = running ? "Arbeider..." : "Transkriber & Lag Referat";
    validateNames();
  }

  async function runPipeline() {
    await validateNames();
    if (document.getElementById('run_btn').disabled) return;

    updateProgress(0, "Starter jobb...");
    document.getElementById('log').textContent = "Starter jobb...";
    pywebview.api.start_job(
      currentAudioPath,
      document.getElementById('out_dir').value,
      document.getElementById('html_name').value,
      document.getElementById('notes_name').value,
      document.getElementById('whisper_model').value,
      document.getElementById('ollama_model').value,
      document.getElementById('language').value,
      document.getElementById('hf_token').value,
      document.getElementById('min_speakers').value,
      document.getElementById('max_speakers').value
    );
  }
</script>
</body>
</html>
"""


def bind_drag_and_drop(window: webview.Window) -> None:
    def on_drop(e: dict) -> None:
        files = e.get("dataTransfer", {}).get("files", [])
        if files:
            full_path = files[0].get("pywebviewFullPath")
            if full_path:
                window.evaluate_js(f"handleDroppedAudio({json.dumps(str(Path(full_path)))})")

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


def main() -> None:
    api = MoteApi()
    window = webview.create_window(
        "Møtereferat (WhisperX + Ollama)",
        html=HTML,
        js_api=api,
        width=650,
        height=650,
    )
    api.window = window
    webview.start(bind_drag_and_drop, window)


if __name__ == "__main__":
    main()