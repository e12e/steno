#!/bin/sh
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
#   "markdown>=3.5",
#   "static-ffmpeg>=2.7",
# ]
# ///
"true" '''\'
goto :windows_boot >/dev/null 2>&1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installerer uv (frittstaaende Python-motor)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
exec uv run --python 3.12 --script "$0" "$@"
:windows_boot
@echo off
cls
where uv >nul 2>nul || (
  echo Installerer uv ^(frittstaaende Python-motor^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
uv run --python 3.12 --script "%~f0" %*
exit /b %ERRORLEVEL%
'''
# Cross-platform single-file GUI for Meeting Recording, Guided Voice Enrollment, Agenda Context, WhisperX & Ollama.
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import html
import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
from typing import Iterator
import urllib.request
import zipfile


def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python mote_app.py' without uv, auto-install uv and re-exec."""
    try:
        import markdown  # noqa: F401
        import static_ffmpeg  # noqa: F401
        import webview  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    print("Forbereder frittstående Python-miljø via uv...")
    search_paths = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        str(Path.home() / ".local" / "share" / "mise" / "shims"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.environ.get("PATH", ""),
    ]
    os.environ["PATH"] = os.pathsep.join(p for p in search_paths if p)
    uv_bin = shutil.which("uv")

    if not uv_bin:
        print("Laster ned uv...")
        if os.name == "nt":
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    "irm https://astral.sh/uv/install.ps1 | iex",
                ],
                check=True,
            )
        else:
            subprocess.run(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                shell=True,
                check=True,
            )
        os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"
        uv_bin = shutil.which("uv")

    if not uv_bin:
        raise RuntimeError("Klarte ikke å finne eller installere 'uv'.")

    cmd = [uv_bin, "run", "--python", "3.12", "--script", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


_self_bootstrap_if_needed()

import markdown  # noqa: E402
import static_ffmpeg  # noqa: E402
import webview  # noqa: E402
from webview import FileDialog  # noqa: E402
from webview.dom import DOMEventHandler  # noqa: E402

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE}/api/tags"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE}/api/chat"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"
MOTE_BIN_DIR = Path.home() / ".cache" / "mote" / "bin"


def build_runtime_env() -> dict[str, str]:
    """Build cross-platform environment dictionary with tool directories in PATH."""
    env = {
        **os.environ,
        "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    if os.name != "nt":
        env.setdefault("LC_ALL", "en_US.UTF-8")

    extra_dirs = [
        str(MOTE_BIN_DIR),
        str(Path.home() / ".local" / "share" / "uv" / "tools" / "whisperx" / "bin"),
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        str(Path.home() / ".local" / "share" / "mise" / "shims"),
    ]
    if sys.platform == "darwin":
        extra_dirs.extend([
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/Applications/Ollama.app/Contents/Resources",
        ])
    elif os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            extra_dirs.append(str(Path(local_app) / "Programs" / "Ollama"))
            extra_dirs.append(str(Path(local_app) / "Microsoft" / "WinGet" / "Links"))

    current_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*extra_dirs, current_path])
    return env


def ensure_ffmpeg(env: dict[str, str], log_fn) -> str:
    """Ensure ffmpeg and ffprobe are available on PATH, using static-ffmpeg if missing."""
    ff = shutil.which("ffmpeg", path=env.get("PATH"))
    if ff:
        return ff
    log_fn("==> Laster ned innebygd FFmpeg for lydbehandling...")
    static_ffmpeg.add_paths(weak=True)
    env["PATH"] = os.environ.get("PATH", "") + os.pathsep + env.get("PATH", "")
    return shutil.which("ffmpeg", path=env.get("PATH")) or "ffmpeg"


def ensure_whisperx(env: dict[str, str], log_fn, progress_fn) -> str:
    """Ensure whisperx CLI is installed; auto-install via uv tool under Python 3.12 if missing."""
    wx = shutil.which("whisperx", path=env.get("PATH"))
    if wx:
        return wx

    uv_bin = shutil.which("uv", path=env.get("PATH"))
    if not uv_bin:
        raise RuntimeError("Fant ikke 'uv' for automatisk installasjon av WhisperX.")

    progress_fn(8, "Installerer WhisperX automatisk (kun første gang, tar 1–3 min)...")
    log_fn("==> WhisperX er ikke installert ennå. Installerer automatisk via 'uv tool install --python 3.12 whisperx'...")
    proc = subprocess.Popen(
        [uv_bin, "tool", "install", "--python", "3.12", "whisperx"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        clean = line.strip()
        if clean:
            log_fn(f"    [uv] {clean}")
    if proc.wait() != 0:
        raise RuntimeError("Automatisk installasjon av WhisperX feilet.")

    try:
        bin_dir = subprocess.check_output(
            [uv_bin, "tool", "dir", "--bin"], env=env, text=True
        ).strip()
        if bin_dir:
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    except Exception:
        pass

    wx = shutil.which("whisperx", path=env.get("PATH"))
    if not wx:
        raise RuntimeError("WhisperX ble installert, men ble ikke funnet i PATH.")
    return wx


def ensure_ollama_binary(env: dict[str, str], log_fn, progress_fn) -> str:
    """Ensure ollama binary is available; download standalone binary into ~/.cache/mote/bin if missing."""
    ollama_bin = shutil.which("ollama", path=env.get("PATH"))
    if ollama_bin:
        return ollama_bin

    MOTE_BIN_DIR.mkdir(parents=True, exist_ok=True)
    progress_fn(73, "Laster ned frittstående Ollama-motor (kun første gang)...")
    log_fn("==> Fant ikke Ollama på maskinen. Laster ned frittstående Ollama-binærfil...")

    if sys.platform == "darwin":
        archive_path = MOTE_BIN_DIR / "ollama-darwin.tgz"
        urllib.request.urlretrieve("https://ollama.com/download/ollama-darwin.tgz", archive_path)
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if Path(member.name).name == "ollama" and member.isfile():
                    member.name = "ollama"
                    tar.extract(member, MOTE_BIN_DIR)
                    break
        archive_path.unlink(missing_ok=True)
        target = MOTE_BIN_DIR / "ollama"
        if target.is_file():
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    elif os.name == "nt":
        winget = shutil.which("winget")
        if winget:
            subprocess.run(
                [
                    winget, "install", "--id", "Ollama.Ollama", "-e",
                    "--silent", "--accept-package-agreements", "--accept-source-agreements",
                ],
                check=False,
            )
        ollama_bin = shutil.which("ollama", path=env.get("PATH"))
        if not ollama_bin:
            zip_path = MOTE_BIN_DIR / "ollama-windows-amd64.zip"
            urllib.request.urlretrieve(
                "https://ollama.com/download/ollama-windows-amd64.zip", zip_path
            )
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(MOTE_BIN_DIR)
            zip_path.unlink(missing_ok=True)
    else:
        arch = "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "amd64"
        archive_path = MOTE_BIN_DIR / f"ollama-linux-{arch}.tgz"
        urllib.request.urlretrieve(
            f"https://ollama.com/download/ollama-linux-{arch}.tgz", archive_path
        )
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(MOTE_BIN_DIR)
        archive_path.unlink(missing_ok=True)
        nested = MOTE_BIN_DIR / "bin" / "ollama"
        if nested.is_file() and not (MOTE_BIN_DIR / "ollama").exists():
            shutil.move(str(nested), str(MOTE_BIN_DIR / "ollama"))

    env["PATH"] = f"{MOTE_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    ollama_bin = shutil.which("ollama", path=env.get("PATH"))
    if not ollama_bin:
        raise RuntimeError("Klarte ikke å installere Ollama automatisk. Installer fra https://ollama.com")
    return ollama_bin


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
        if os.name != "nt":
            HF_TOKEN_FILE.chmod(0o600)


def unique_filename(directory: Path, stem: str, suffix: str) -> str:
    candidate = f"{stem}{suffix}"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def sanitize_text(text: str) -> str:
    """Normalize Unicode (NFC), strip ANSI codes, replacement chars, and control bytes."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = text.replace("\ufffd", "")
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return text.strip()


def clean_llm_output(raw_output: str) -> str:
    """Strip reasoning/thinking blocks from Gemma 4, DeepSeek, Qwen, etc."""
    cleaned = sanitize_text(raw_output)
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thought>[\s\S]*?</thought>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\|thought\|>[\s\S]*?(?:<\|/thought\|>|<\|end_thought\|>)", "", cleaned, flags=re.IGNORECASE)
    if "<think>" in cleaned.lower() and "# " in cleaned:
        idx = cleaned.find("# ")
        cleaned = cleaned[idx:]
    return cleaned.strip()


def fetch_ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as resp:
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
def managed_ollama_server(
    log_fn, progress_fn, env: dict[str, str]
) -> Iterator[str]:
    if fetch_ollama_models() is not None:
        log_fn("Ollama server kjører allerede.")
        ollama_bin = shutil.which("ollama", path=env.get("PATH")) or "ollama"
        yield ollama_bin
        return

    ollama_bin = ensure_ollama_binary(env, log_fn, progress_fn)
    log_fn("Starter ollama serve midlertidig...")
    proc = subprocess.Popen(
        [ollama_bin, "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_ollama():
            raise RuntimeError("Klarte ikke å starte ollama serve.")
        yield ollama_bin
    finally:
        if proc.poll() is None:
            log_fn(f"Stopper midlertidig ollama serve (PID {proc.pid})...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def extract_turns_from_whisperx(json_path: Path) -> list[dict]:
    """Extract word-level speaker turns so speaker handoffs mid-segment are not lost."""
    raw_bytes = json_path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    raw_segments = data.get("segments", [])

    atomic_items: list[dict] = []
    last_speaker = "SPEAKER_00"

    for seg in raw_segments:
        seg_speaker = seg.get("speaker") or last_speaker
        words = seg.get("words") or []

        if words and any(w.get("speaker") for w in words):
            for w in words:
                w_text = sanitize_text(str(w.get("word", "")))
                if not w_text:
                    continue
                w_spk = w.get("speaker") or seg_speaker or last_speaker
                last_speaker = w_spk
                w_start = float(w.get("start", seg.get("start", 0.0)))
                w_end = float(w.get("end", w_start))
                atomic_items.append(
                    {"speaker": w_spk, "start": w_start, "end": w_end, "text": w_text}
                )
        else:
            text = sanitize_text(seg.get("text", ""))
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            last_speaker = seg_speaker
            atomic_items.append(
                {"speaker": seg_speaker, "start": start, "end": end, "text": text}
            )

    merged: list[dict] = []
    for item in atomic_items:
        spk = item["speaker"]
        start = item["start"]
        end = item["end"]
        text = item["text"]
        if merged and merged[-1]["speaker"] == spk and (start - merged[-1]["end"]) < 2.0:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["text"] += " " + text
        else:
            merged.append({"speaker": spk, "start": start, "end": end, "text": text})

    return merged


def infer_speaker_names_via_api(
    turns: list[dict],
    model: str,
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    log_fn,
) -> dict[str, str]:
    """Use Ollama to detect self-introductions (name + optional title) and map SPEAKER_XX."""
    unique_speakers = sorted({t["speaker"] for t in turns})
    if not unique_speakers:
        return {}

    intro_keywords = (
        "heter", "jeg er", "mitt navn", "fra ", "representerer",
        "leder", "tittel", "rolle", "ansvarlig", "velkommen", "møteleder",
    )
    sample_lines: list[str] = []

    for idx, t in enumerate(turns):
        low = t["text"].lower()
        if idx < 110 or any(k in low for k in intro_keywords):
            t_str = f"{format_seconds(t['start'])}–{format_seconds(t['end'])}"
            sample_lines.append(f"[{t_str}] {t['speaker']}: {t['text']}")

    context_parts = []
    if meeting_title.strip():
        context_parts.append(f"Møtetittel/Dato: {meeting_title.strip()}")
    if known_names_hint.strip():
        context_parts.append(f"Forhåndsoppgitte deltakere (tilstede): {known_names_hint.strip()}")
    if agenda_text.strip():
        context_parts.append(f"Agenda / Kontekst:\n{agenda_text.strip()[:1500]}")
    context_str = "\n".join(context_parts) if context_parts else "Ingen forhåndsoppgitt deltakerliste."

    prompt = (
        "Analyser utdraget fra møtetranskripsjonen under og finn ut hvem de ulike taler-IDene "
        f"({', '.join(unique_speakers)}) er, basert på at møteleder og deltakere presenterer seg "
        "(navn og eventuell tittel/rolle) eller tiltaler hverandre.\n\n"
        f"{context_str}\n\n"
        "Regler:\n"
        "1. Returner KUN et gyldig JSON-objekt som knytter hver taler-ID til personens navn (og tittel/rolle hvis kjent).\n"
        "2. Hvis du IKKE finner navnet til en taler-ID med sikkerhet, behold original ID, f.eks. "
        '{"SPEAKER_00": "SPEAKER_00"}. Ikke gjett tilfeldige navn.\n'
        '3. Eksempel på format: {"SPEAKER_00": "Ola Nordmann, Møteleder", "SPEAKER_01": "Kari Hansen, Arkitekt"}\n\n'
        "Utdrag fra transkripsjon:\n" + "\n".join(sample_lines[:180])
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"num_ctx": 16384, "temperature": 0.0},
    }

    try:
        req = urllib.request.Request(
            OLLAMA_CHAT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = clean_llm_output(body.get("message", {}).get("content", "{}"))
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                mapping: dict[str, str] = {}
                for spk in unique_speakers:
                    val = str(parsed.get(spk, spk)).strip()
                    if val and val != spk:
                        mapping[spk] = f"{val} ({spk})" if spk not in val else val
                    else:
                        mapping[spk] = spk
                return mapping
    except Exception as exc:
        log_fn(f"Merk: Automatisk navnegjenkjenning hoppet over ({exc}).")
    return {spk: spk for spk in unique_speakers}


def build_transcript_html_and_text(
    turns: list[dict],
    speaker_map: dict[str, str],
    html_path: Path,
    title: str,
) -> str:
    """Build rich searchable HTML transcript and return plain-text transcript for LLM."""
    colors = ["#007AFF", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5856D6", "#00C7BE"]
    speaker_color_map: dict[str, str] = {}
    llm_lines: list[str] = []
    html_items: list[str] = []

    for item in turns:
        orig_spk = item["speaker"]
        display_spk = speaker_map.get(orig_spk, orig_spk)
        if orig_spk not in speaker_color_map:
            speaker_color_map[orig_spk] = colors[len(speaker_color_map) % len(colors)]
        color = speaker_color_map[orig_spk]
        t_str = f"{format_seconds(item['start'])} – {format_seconds(item['end'])}"

        llm_lines.append(f"[{t_str}] {display_spk}: {item['text']}")
        html_items.append(
            f'<div class="turn" data-speaker="{html.escape(orig_spk)}">'
            f'  <div class="meta">'
            f'    <span class="badge" style="background:{color}" data-spk-label="{html.escape(orig_spk)}">{html.escape(display_spk)}</span>'
            f'    <span class="timestamp">{t_str}</span>'
            f'  </div>'
            f'  <div class="text">{html.escape(item["text"])}</div>'
            f'</div>'
        )

    speaker_inputs = "".join(
        f'<div class="spk-edit">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <input type="text" value="{html.escape(speaker_map.get(spk, spk))}" data-orig="{html.escape(spk)}" '
        f'         oninput="renameSpeaker(this.dataset.orig, this.value)" title="Endre navn i visningen ({html.escape(spk)})">'
        f'</div>'
        for spk, col in speaker_color_map.items()
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="no">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} – Transkripsjon</title>
  <style>
    :root {{
      --bg: #f5f6f8; --card: #ffffff; --text: #1d1d1f; --sub: #6e6e73; --border: #e5e5ea;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #121212; --card: #1c1c1e; --text: #f5f5f7; --sub: #98989d; --border: #2c2c2e;
      }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text);
      max-width: 860px; margin: 28px auto; padding: 0 20px 60px; line-height: 1.55;
    }}
    header {{
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 16px 20px; margin-bottom: 18px; position: sticky; top: 10px; z-index: 10;
      box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 12px 0; }}
    .controls {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
    #filter {{
      flex: 1; min-width: 200px; padding: 8px 12px; border: 1px solid var(--border);
      border-radius: 8px; background: var(--bg); color: var(--text); font-size: 13.5px;
    }}
    .speakers-bar {{
      display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; padding-top: 10px;
      border-top: 1px solid var(--border); font-size: 12px; align-items: center;
    }}
    .spk-edit {{ display: flex; align-items: center; gap: 5px; }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
    .spk-edit input {{
      padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border);
      background: var(--bg); color: var(--text); font-size: 12px; width: 165px; font-weight: 600;
    }}
    .turn {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;
    }}
    .meta {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 12px; }}
    .badge {{ color: #fff; font-weight: 600; padding: 2px 9px; border-radius: 12px; }}
    .timestamp {{ color: var(--sub); font-family: ui-monospace, "SF Mono", monospace; }}
    .text {{ font-size: 14.5px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)} – Transkripsjon</h1>
    <div class="controls">
      <input type="text" id="filter" placeholder="Søk i tekst, tidspunkt eller taler..." oninput="filterTurns()">
    </div>
    <div class="speakers-bar">
      <span style="color:var(--sub)">Talere (kan redigeres):</span>
      {speaker_inputs}
    </div>
  </header>
  <main id="container">
    {"".join(html_items)}
  </main>
  <script>
    const STORAGE_KEY = "mote_spk_" + document.title;
    function filterTurns() {{
      const q = document.getElementById('filter').value.toLowerCase();
      document.querySelectorAll('.turn').forEach(el => {{
        el.style.display = el.innerText.toLowerCase().includes(q) ? '' : 'none';
      }});
    }}
    function renameSpeaker(orig, newName, skipSave) {{
      const label = newName.trim() || orig;
      document.querySelectorAll(`[data-spk-label="${{orig}}"]`).forEach(el => {{
        el.textContent = label;
      }});
      if (!skipSave) {{
        try {{
          const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
          saved[orig] = newName;
          localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
        }} catch (e) {{}}
      }}
      filterTurns();
    }}
    window.addEventListener('DOMContentLoaded', () => {{
      try {{
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
        document.querySelectorAll('.spk-edit input').forEach(inp => {{
          const orig = inp.dataset.orig;
          if (saved[orig]) {{
            inp.value = saved[orig];
            renameSpeaker(orig, saved[orig], true);
          }}
        }});
      }} catch (e) {{}}
    }});
  </script>
</body>
</html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return "\n".join(llm_lines)


def build_notes_html(notes_md: str, notes_html_path: Path, title: str) -> None:
    """Render Markdown meeting notes into a styled, print-ready HTML document."""
    rendered_body = markdown.markdown(
        notes_md,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    html_doc = f"""<!DOCTYPE html>
<html lang="no">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} – Møtereferat</title>
  <style>
    :root {{
      --bg: #f5f6f8; --card: #ffffff; --text: #1d1d1f; --sub: #6e6e73;
      --border: #e5e5ea; --accent: #007AFF; --th-bg: #f0f2f5;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #121212; --card: #1c1c1e; --text: #f5f5f7; --sub: #98989d;
        --border: #2c2c2e; --accent: #0a84ff; --th-bg: #262629;
      }}
    }}
    @media print {{
      :root {{
        --bg: #ffffff; --card: #ffffff; --text: #000000; --sub: #555555;
        --border: #cccccc; --th-bg: #f0f0f0;
      }}
      body {{ margin: 0; padding: 0; }}
      .no-print {{ display: none !important; }}
      article {{ border: none !important; box-shadow: none !important; padding: 0 !important; }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text);
      max-width: 820px; margin: 32px auto; padding: 0 20px 60px; line-height: 1.65;
    }}
    .toolbar {{
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 14px; font-size: 13px; color: var(--sub);
    }}
    .toolbar button {{
      padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
      background: var(--card); color: var(--text); cursor: pointer; font-size: 12.5px;
      font-weight: 500;
    }}
    .toolbar button:hover {{ border-color: var(--accent); }}
    article {{
      background: var(--card); border: 1px solid var(--border); border-radius: 14px;
      padding: 36px 44px; box-shadow: 0 4px 16px rgba(0,0,0,0.05);
    }}
    h1 {{
      font-size: 1.75rem; margin: 0 0 18px 0; padding-bottom: 12px;
      border-bottom: 2px solid var(--border); letter-spacing: -0.02em;
    }}
    h2 {{
      font-size: 1.25rem; margin: 28px 0 10px 0; color: var(--accent);
      padding-bottom: 6px; border-bottom: 1px solid var(--border);
    }}
    h3 {{ font-size: 1.05rem; margin: 20px 0 6px 0; }}
    p {{ margin: 0 0 12px 0; }}
    ul, ol {{ margin: 0 0 14px 0; padding-left: 22px; }}
    li {{ margin-bottom: 6px; }}
    table {{
      width: 100%; border-collapse: collapse; margin: 14px 0 20px 0; font-size: 14px;
    }}
    th, td {{
      text-align: left; padding: 10px 12px; border: 1px solid var(--border);
    }}
    th {{ background: var(--th-bg); font-weight: 600; }}
    blockquote {{
      margin: 12px 0; padding: 10px 16px; border-left: 4px solid var(--accent);
      background: var(--bg); border-radius: 0 8px 8px 0; color: var(--sub);
    }}
    code {{
      font-family: ui-monospace, "SF Mono", monospace; font-size: 0.9em;
      background: var(--bg); padding: 2px 5px; border-radius: 4px;
    }}
  </style>
</head>
<body>
  <div class="toolbar no-print">
    <span>{html.escape(title)}</span>
    <button onclick="window.print()">Skriv ut / Lagre som PDF</button>
  </div>
  <article>
    {rendered_body}
  </article>
</body>
</html>"""
    notes_html_path.write_text(html_doc, encoding="utf-8")


def generate_notes_via_api(
    transcript: str,
    model: str,
    speaker_map: dict[str, str],
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    progress_fn=None,
) -> str:
    """Use Ollama HTTP /api/chat with agenda & attendee grounding to generate meeting notes."""
    detected_list = ", ".join(sorted(set(speaker_map.values())))
    meta_lines = [f"- **Identifiserte stemmer i opptaket**: {detected_list}"]
    if meeting_title.strip():
        meta_lines.insert(0, f"- **Møtetittel og dato**: {meeting_title.strip()}")
    if known_names_hint.strip():
        meta_lines.append(f"- **Oppgitte deltakere / roller**: {known_names_hint.strip()}")
    if agenda_text.strip():
        meta_lines.append(f"- **Forhåndsoppgitt agenda / saksliste**:\n{agenda_text.strip()}")
    meta_block = "\n".join(meta_lines)

    heading = f"# Møtereferat: {meeting_title.strip()}" if meeting_title.strip() else "# Møtereferat"

    system_prompt = (
        "Du er en profesjonell norsk møtesekretær. Du skriver kun selve møtereferatet i ren Markdown. "
        "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
        "Bruk korrekt norsk (bokmål) og organiser innholdet tematisk (gjerne etter punktene i agendaen hvis oppgitt). "
        "Bruk deltakernes faktiske navn og tittel/rolle der det fremgår av møtestart-runden eller deltakerlisten."
    )
    user_prompt = (
        "Lag et grundig og strukturert møtereferat basert på møtekonteksten og den tidsstemplede transkripsjonen under.\n\n"
        "### MØTEKONTEKST / AGENDA:\n"
        f"{meta_block}\n\n"
        "Bruk denne strukturen i referatet:\n"
        f"{heading}\n\n"
        "## 1. Møteinfo og kort oppsummering\n"
        "(Dato, formål med møtet og hovedkonklusjoner)\n\n"
        "## 2. Tilstede (Deltakere og roller)\n"
        "(Liste over deltakerne med navn, tittel/rolle og kort om deres bidrag)\n\n"
        "## 3. Gjennomgang av agenda / hovedtemaer\n"
        "(Strukturert etter agenda-punktene og temaene som ble diskutert, hvem som fremmet hvilke synspunkter, og tidsstempler)\n\n"
        "## 4. Beslutninger og vedtak\n"
        "(Konkrete vedtak eller enigheter)\n\n"
        "## 5. Aksjonspunkter\n"
        "(Markdown-tabell med kolonnene: Ansvarlig | Oppgave | Frist / Status)\n\n"
        f"--- TRANSKRIPSJON START ---\n{transcript}\n--- TRANSKRIPSJON SLUTT ---"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
        "think": False,
        "options": {
            "num_ctx": 32768,
            "temperature": 0.2,
        },
    }

    req = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    chunks: list[str] = []
    token_count = 0
    est_tokens = max(400, min(1800, int(len(transcript.split()) * 0.35)))

    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw_line in resp:
            if not raw_line.strip():
                continue
            body = json.loads(raw_line.decode("utf-8", errors="replace"))
            piece = body.get("message", {}).get("content", "")
            if piece:
                chunks.append(piece)
                token_count += 1
                if progress_fn and (token_count == 1 or token_count % 4 == 0):
                    pct = min(98, int(84 + min(14.0, (token_count / est_tokens) * 14.0)))
                    progress_fn(pct, f"[4/4] Skriver møtereferat ({token_count} tokens)...")
            if body.get("done"):
                break

    return clean_llm_output("".join(chunks))


class MoteApi:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self._rec_proc: subprocess.Popen | None = None
        self._rec_path: Path | None = None
        self._tts_proc: subprocess.Popen | None = None

    def get_defaults(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        return {
            "hf_token": get_initial_hf_token(),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(Path.home() / "Downloads"),
            "default_meeting_title": f"Møte {today}",
        }

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

    def start_recording(self, out_dir_str: str, speak_guide: bool) -> dict:
        """Start recording 16kHz mono WAV audio from the default microphone via ffmpeg."""
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
            return {"ok": True, "audio_path": str(rec_file)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_recording(self, out_dir_str: str) -> dict | None:
        """Gracefully stop the active ffmpeg microphone recording and return suggested file names."""
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

    def suggest_output_names(self, audio_path_str: str, out_dir_str: str) -> dict:
        audio_path = Path(audio_path_str)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        stem = audio_path.stem
        return {
            "audio_path": str(audio_path),
            "out_dir": str(out_dir),
            "html_name": unique_filename(out_dir, f"{stem}_transkripsjon", ".html"),
            "notes_name": unique_filename(out_dir, f"{stem}_referat", ".md"),
            "notes_html_name": unique_filename(out_dir, f"{stem}_referat", ".html"),
        }

    def pick_audio_file(self) -> dict | None:
        if not self.window:
            return None
        file_types = ("Audio Files (*.wav;*.m4a;*.mp3;*.ogg;*.opus;*.flac)", "All files (*.*)")
        result = self.window.create_file_dialog(
            FileDialog.OPEN, allow_multiple=False, file_types=file_types
        )
        if result and len(result) > 0:
            p = Path(result[0])
            return self.suggest_output_names(str(p), str(p.parent))
        return None

    def pick_agenda_file(self) -> str | None:
        """Let user pick a .txt or .md agenda file to populate the agenda box."""
        if not self.window:
            return None
        file_types = ("Text / Markdown (*.txt;*.md)", "All files (*.*)")
        result = self.window.create_file_dialog(
            FileDialog.OPEN, allow_multiple=False, file_types=file_types
        )
        if result and len(result) > 0:
            p = Path(result[0])
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        return None

    def pick_output_dir(self, current_audio_path: str) -> dict | None:
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
            notes_html_name = Path(notes_html_name_str.strip()).name

            html_path = out_dir / html_name
            notes_path = out_dir / notes_name
            notes_html_path = out_dir / notes_html_name

            for target in (html_path, notes_path, notes_html_path):
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

            env = build_runtime_env()
            ensure_ffmpeg(env, self._log)
            whisperx_bin = ensure_whisperx(env, self._log, self._set_progress)

            # Build initial_prompt from meeting title, attendees, and agenda for better ASR spelling
            vocab_parts = []
            if meeting_title.strip():
                vocab_parts.append(meeting_title.strip())
            if speaker_names_hint.strip():
                vocab_parts.append(f"Deltakere: {speaker_names_hint.strip()}.")
            if agenda_text.strip():
                vocab_parts.append(agenda_text.strip()[:250].replace("\n", " "))
            initial_prompt = " ".join(vocab_parts).strip()

            self._set_progress(12, f"[1/4] Laster inn {whisper_model}...")
            self._log(f"==> [1/4] Kjører WhisperX ({whisper_model}) på {audio_path.name}...")

            with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
                cmd = [
                    whisperx_bin,
                    str(audio_path),
                    "--output_dir", tmp_dir,
                    "--model", whisper_model,
                    "--language", language,
                    "--diarize",
                    "--compute_type", "int8",
                    "--output_format", "json",
                    "--print_progress", "True",
                ]
                if hf_token.strip():
                    cmd.extend(["--hf_token", hf_token.strip()])
                if min_speakers_str.strip().isdigit():
                    cmd.extend(["--min_speakers", min_speakers_str.strip()])
                if max_speakers_str.strip().isdigit():
                    cmd.extend(["--max_speakers", max_speakers_str.strip()])
                if initial_prompt:
                    cmd.extend(["--initial_prompt", initial_prompt])

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
                assert proc.stdout is not None
                phase = 0
                last_pct = 0.0
                diarization_start: float | None = None
                stop_heartbeat = threading.Event()

                def _diarization_heartbeat() -> None:
                    while not stop_heartbeat.wait(1.0):
                        if phase == 2 and diarization_start is not None:
                            elapsed = int(time.time() - diarization_start)
                            mm, ss = divmod(elapsed, 60)
                            creep = min(12, elapsed // 15)
                            self._set_progress(
                                60 + creep,
                                f"[3/4] Identifiserer talere med Pyannote ({mm:02d}:{ss:02d})...",
                            )

                hb_thread = threading.Thread(target=_diarization_heartbeat, daemon=True)
                hb_thread.start()

                try:
                    for line in proc.stdout:
                        clean = sanitize_text(line)
                        if not clean:
                            continue
                        self._log(clean)
                        low = clean.lower()

                        if "performing alignment" in low:
                            phase = 1
                            last_pct = 0.0
                            self._set_progress(50, "[2/4] Tidsjusterer ord (Alignment): 0%")
                            continue
                        if "performing diarization" in low or "assigning speaker" in low:
                            if phase < 2:
                                phase = 2
                                diarization_start = time.time()
                                self._log(
                                    "Pyannote analyserer stemmer i bakgrunnen (tar vanligvis 2–6 min uten logg-utskrift)..."
                                )
                            last_pct = 0.0
                            self._set_progress(60, "[3/4] Identifiserer talere med Pyannote (00:00)...")
                            continue

                        match = re.search(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%", clean)
                        if match:
                            p_pct = float(match.group(1))
                            if p_pct < last_pct - 30.0 and last_pct > 50.0:
                                phase = min(2, phase + 1)
                            last_pct = p_pct
                            if phase == 0:
                                mapped = int(15 + (p_pct * 0.35))
                                self._set_progress(mapped, f"[1/4] Transkriberer tale: {p_pct:.0f}%")
                            elif phase == 1:
                                mapped = int(50 + (p_pct * 0.10))
                                self._set_progress(mapped, f"[2/4] Tidsjusterer ord (Alignment): {p_pct:.0f}%")
                                if p_pct >= 99.9:
                                    phase = 2
                                    diarization_start = time.time()
                                    self._log(
                                        "Ord-tidsjustering ferdig. Starter Pyannote taler-identifisering "
                                        "(jobber stille på CPU i 2–6 minutter)..."
                                    )
                                    self._set_progress(
                                        60, "[3/4] Identifiserer talere med Pyannote (00:00)..."
                                    )
                            else:
                                mapped = int(60 + (p_pct * 0.12))
                                self._set_progress(mapped, f"[3/4] Identifiserer talere (Diarization): {p_pct:.0f}%")
                finally:
                    stop_heartbeat.set()

                if proc.wait() != 0:
                    self._log("FEIL: WhisperX feilet.")
                    self._set_progress(0, "Feil under WhisperX")
                    return

                raw_json = Path(tmp_dir) / f"{audio_path.stem}.json"
                if not raw_json.is_file():
                    self._log("FEIL: Fant ikke JSON-utdata fra WhisperX.")
                    self._set_progress(0, "JSON mangler")
                    return

                self._set_progress(73, "[3/4] Ekstraherer ord-baserte talerskifter...")
                turns = extract_turns_from_whisperx(raw_json)

            doc_title = meeting_title.strip() or audio_path.stem

            with managed_ollama_server(self._log, self._set_progress, env=env) as ollama_bin:
                installed = fetch_ollama_models() or []
                if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
                    self._set_progress(75, f"[4/4] Laster ned {ollama_model}...")
                    self._log(f"==> Laster ned '{ollama_model}'...")
                    subprocess.run([ollama_bin, "pull", ollama_model], env=env, check=True)

                self._set_progress(78, "[4/4] Gjenkjenner deltakernavn og titler fra introduksjoner...")
                self._log("==> [2/3] Analyserer introduksjoner og agenda for å knytte taler-ID til navn...")
                speaker_map = infer_speaker_names_via_api(
                    turns,
                    ollama_model,
                    speaker_names_hint,
                    meeting_title,
                    agenda_text,
                    self._log,
                )
                for spk_id, resolved_name in speaker_map.items():
                    if resolved_name != spk_id:
                        self._log(f"    Gjenkjent {spk_id} -> {resolved_name}")

                clean_transcript = build_transcript_html_and_text(
                    turns, speaker_map, html_path, doc_title
                )
                self._log(f"==> Rik HTML-transkripsjon lagret: {html_path}")

                self._set_progress(84, f"[4/4] Analyserer transkripsjon med {ollama_model}...")
                self._log(f"==> [3/3] Genererer møtereferat med agenda-kontekst ({ollama_model})...")

                notes_md = generate_notes_via_api(
                    clean_transcript,
                    ollama_model,
                    speaker_map,
                    speaker_names_hint,
                    meeting_title,
                    agenda_text,
                    progress_fn=self._set_progress,
                )
                notes_path.write_text(notes_md + "\n", encoding="utf-8")

                self._set_progress(99, "Bygger HTML-møtereferat...")
                build_notes_html(notes_md, notes_html_path, doc_title)

            self._set_progress(100, "Fullført!")
            self._log(
                f"==> FERDIG!\n"
                f"    HTML-transkripsjon: {html_path}\n"
                f"    Møtereferat (MD)  : {notes_path}\n"
                f"    Møtereferat (HTML): {notes_html_path}"
            )
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
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  }
  body {
    margin: 0; padding: 12px 14px;
    background: Canvas; color: CanvasText;
    font-size: 12.5px; user-select: none;
    display: flex; flex-direction: column; gap: 6px; height: calc(100vh - 24px);
  }
  .top-cards {
    display: flex; gap: 8px; align-items: stretch;
  }
  #dropzone {
    flex: 1.2; border: 2px dashed GrayText; border-radius: 10px;
    padding: 8px 12px; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; transition: all 0.15s ease;
  }
  #dropzone.dragover { border-color: #0a84ff; background: rgba(10, 132, 255, 0.1); }
  .dz-text { flex: 1; overflow: hidden; }
  .dz-text strong { font-size: 12.5px; display: block; margin-bottom: 2px; }
  #selected-file {
    color: GrayText; display: block; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-size: 11px;
  }
  .rec-card {
    flex: 1; border: 1px solid rgba(128,128,128,0.35); border-radius: 10px;
    padding: 8px 12px; display: flex; flex-direction: column; justify-content: center; gap: 4px;
    background: rgba(128,128,128,0.06);
  }
  .rec-controls { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  #rec_btn.recording { background: #ff453a; color: white; border-color: #ff453a; font-weight: 600; }
  #rec_timer { font-family: "SF Mono", Menlo, monospace; font-weight: 600; font-size: 12px; color: #ff453a; }

  #prompt_banner {
    display: none; background: rgba(10, 132, 255, 0.1); border: 1px solid #0a84ff;
    border-radius: 8px; padding: 8px 12px; font-size: 11.5px; line-height: 1.45;
  }
  #prompt_banner strong { color: #0a84ff; }

  .row { display: flex; gap: 7px; align-items: center; }
  .row label { width: 115px; flex-shrink: 0; font-weight: 500; font-size: 12px; }
  input[type="text"], input[type="password"], input[type="number"], select, textarea {
    flex: 1; padding: 4px 8px; border-radius: 6px;
    border: 1px solid rgba(128,128,128,0.4); background: Field; color: FieldText;
    font-size: 12px; font-family: inherit;
  }
  textarea { resize: vertical; min-height: 46px; max-height: 110px; }
  input.conflict { border-color: #ff453a; background: rgba(255, 69, 58, 0.08); }
  button {
    padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.4);
    background: ButtonFace; color: ButtonText; cursor: pointer; font-size: 12px;
    white-space: nowrap;
  }
  button.primary {
    background: #0a84ff; color: white; border: none; font-weight: 600; padding: 6px 14px;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #conflict-msg { color: #ff453a; font-size: 11.5px; min-height: 14px; flex: 1; }

  .progress-section { display: flex; flex-direction: column; gap: 2px; }
  .progress-header { display: flex; justify-content: space-between; font-size: 11px; color: GrayText; }
  .progress-bg {
    width: 100%; height: 7px; background: rgba(128,128,128,0.25);
    border-radius: 4px; overflow: hidden;
  }
  .progress-fill { height: 100%; width: 0%; background: #0a84ff; border-radius: 4px; transition: width 0.3s ease; }

  #log {
    flex: 1; background: rgba(128,128,128,0.1); border-radius: 8px;
    padding: 8px; font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 11px;
    overflow-y: auto; white-space: pre-wrap; user-select: text;
    border: 1px solid rgba(128,128,128,0.25); min-height: 80px;
  }
</style>
</head>
<body>

  <div class="top-cards">
    <div id="dropzone">
      <div class="dz-text">
        <strong>Slipp lydfil her (.m4a, .wav, .mp3)</strong>
        <span id="selected-file">Ingen lydfil valgt ennå</span>
      </div>
      <button onclick="browseAudio()">Velg fil...</button>
    </div>

    <div class="rec-card">
      <div class="rec-controls">
        <button id="rec_btn" onclick="toggleRecording()">Spill inn møte</button>
        <span id="rec_timer">00:00</span>
        <button onclick="speakGuide()" title="Les opp møtestart-instruks høyt">Tale-instruks</button>
      </div>
      <label style="font-size: 11px; color: GrayText; display: flex; align-items: center; gap: 4px;">
        <input type="checkbox" id="auto_speak_guide" checked>
        Vis og les opp navnerunde-guide ved opptaksstart
      </label>
    </div>
  </div>

  <div id="prompt_banner">
    <strong>Møtestart-guide for god talergjenkjenning:</strong><br>
    1. <b>Møteleder:</b> Si kort hva møtet gjelder og dagens dato.<br>
    2. <b>Navnerunde rundt bordet:</b> Hver deltaker sier eget navn og tittel/rolle i én hel setning (f.eks. <i>«Hei, jeg heter Ola Nordmann og er prosjektleder»</i>).
  </div>

  <div class="row">
    <label>Møtetittel & Dato:</label>
    <input type="text" id="meeting_title" placeholder="F.eks. Prosjektmøte – 2026-10-29">
    <button onclick="loadAgendaFile()" title="Last inn agenda fra .txt eller .md-fil">Last inn agenda...</button>
  </div>

  <div class="row">
    <label>Tilstede (Navn/tittel):</label>
    <input type="text" id="speaker_names_hint" placeholder="F.eks. Ola Nordmann (Møteleder), Kari Hansen (Arkitekt), Per Olsen, Anne Berg" oninput="autoCountSpeakersFromHint()">
  </div>

  <div class="row" style="align-items: flex-start;">
    <label style="padding-top: 4px;">Agenda / Kontekst:</label>
    <textarea id="agenda_text" placeholder="Valgfritt: Lim inn saksliste, agenda eller stikkord for møtet her (forbedrer både stavemåte i transkripsjon og struktur i møtereferatet)..."></textarea>
  </div>

  <div class="row">
    <label>Antall talere:</label>
    <input type="number" id="min_speakers" placeholder="Min (f.eks 4)" min="1" max="20" style="max-width: 90px;">
    <input type="number" id="max_speakers" placeholder="Maks (f.eks 4)" min="1" max="20" style="max-width: 90px;">
    <button onclick="setExactSpeakers(2)">2</button>
    <button onclick="setExactSpeakers(3)">3</button>
    <button onclick="setExactSpeakers(4)">4</button>
    <button onclick="setExactSpeakers(5)">5</button>
    <span style="color: GrayText; font-size: 11px;">(Låser stemmeseparasjon)</span>
  </div>

  <div class="row">
    <label>Lagre i mappe:</label>
    <input type="text" id="out_dir" placeholder="Velg mappe..." oninput="validateNames()">
    <button onclick="browseFolder()">Velg...</button>
    <button onclick="openFinder()" title="Vis i filutforsker">Åpne</button>
  </div>

  <div class="row">
    <label>Utdatafiler:</label>
    <input type="text" id="html_name" placeholder="transkripsjon.html" oninput="validateNames()" title="HTML Transkripsjon">
    <input type="text" id="notes_name" placeholder="referat.md" oninput="validateNames()" title="Markdown Referat">
    <input type="text" id="notes_html_name" placeholder="referat.html" oninput="validateNames()" title="HTML Referat">
    <button onclick="autoIncrementNames()">Ledig navn</button>
  </div>

  <div class="row">
    <label>Modeller & Språk:</label>
    <select id="whisper_model">
      <option value="NbAiLab/nb-whisper-large" selected>NB-Whisper Large (Anbefalt Norsk)</option>
      <option value="large-v3-turbo">Whisper: large-v3-turbo</option>
      <option value="large-v3">Whisper: large-v3</option>
      <option value="NbAiLab/nb-whisper-medium">NB-Whisper Medium (Raskere)</option>
    </select>
    <input type="text" id="ollama_model" value="gemma4:e4b" placeholder="Ollama-modell" style="max-width: 120px;">
    <select id="language" style="max-width: 60px;">
      <option value="no" selected>NO</option>
      <option value="en">EN</option>
      <option value="sv">SV</option>
      <option value="da">DA</option>
    </select>
    <input type="password" id="hf_token" placeholder="HF Token (hf_...)" style="max-width: 125px;">
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

  <div id="log">Klar. Start et nytt opptak, eller dra inn en eksisterende lydfil.</div>

<script>
  let currentAudioPath = "";
  let isJobRunning = false;
  let isRecording = false;
  let recStartTime = 0;
  let recTimerInterval = null;

  window.addEventListener('pywebviewready', async () => {
    const defs = await pywebview.api.get_defaults();
    if (defs.hf_token) document.getElementById('hf_token').value = defs.hf_token;
    if (defs.diarization_cached && !defs.hf_token) {
      document.getElementById('hf_token').placeholder = "HF bufret lokalt";
    }
    document.getElementById('out_dir').value = defs.default_out_dir;
    if (defs.default_meeting_title) {
      document.getElementById('meeting_title').value = defs.default_meeting_title;
    }
  });

  const dz = document.getElementById('dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('dragover'); });

  function setExactSpeakers(n) {
    document.getElementById('min_speakers').value = n;
    document.getElementById('max_speakers').value = n;
  }

  function autoCountSpeakersFromHint() {
    const raw = document.getElementById('speaker_names_hint').value.trim();
    if (!raw) return;
    const parts = raw.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
    if (parts.length >= 2 && parts.length <= 20) {
      document.getElementById('min_speakers').value = parts.length;
      document.getElementById('max_speakers').value = parts.length;
    }
  }

  function speakGuide() {
    document.getElementById('prompt_banner').style.display = 'block';
    pywebview.api.speak_intro_prompt();
  }

  async function toggleRecording() {
    const btn = document.getElementById('rec_btn');
    const outDir = document.getElementById('out_dir').value;
    if (!isRecording) {
      const autoSpeak = document.getElementById('auto_speak_guide').checked;
      document.getElementById('prompt_banner').style.display = 'block';
      const res = await pywebview.api.start_recording(outDir, autoSpeak);
      if (!res || !res.ok) {
        appendLog("FEIL ved opptaksstart: " + (res ? res.error : "Ukjent feil"));
        return;
      }
      isRecording = true;
      btn.textContent = "Stopp opptak";
      btn.classList.add('recording');
      recStartTime = Date.now();
      recTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - recStartTime) / 1000);
        const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
        const secs = String(elapsed % 60).padStart(2, '0');
        document.getElementById('rec_timer').textContent = mins + ":" + secs;
      }, 500);
    } else {
      clearInterval(recTimerInterval);
      isRecording = false;
      btn.textContent = "Spill inn møte";
      btn.classList.remove('recording');
      const info = await pywebview.api.stop_recording(outDir);
      if (info) {
        await applySelection(info);
      }
    }
  }

  async function loadAgendaFile() {
    const content = await pywebview.api.pick_agenda_file();
    if (content) {
      document.getElementById('agenda_text').value = content;
    }
  }

  async function applySelection(info) {
    currentAudioPath = info.audio_path;
    document.getElementById('selected-file').textContent = info.audio_path;
    if (info.out_dir) document.getElementById('out_dir').value = info.out_dir;
    if (info.html_name) document.getElementById('html_name').value = info.html_name;
    if (info.notes_name) document.getElementById('notes_name').value = info.notes_name;
    if (info.notes_html_name) document.getElementById('notes_html_name').value = info.notes_html_name;
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
      if (res.notes_html_name) document.getElementById('notes_html_name').value = res.notes_html_name;
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
    document.getElementById('notes_html_name').value = info.notes_html_name;
    await validateNames();
  }

  async function validateNames() {
    const outDir = document.getElementById('out_dir').value;
    const htmlInput = document.getElementById('html_name');
    const notesInput = document.getElementById('notes_name');
    const notesHtmlInput = document.getElementById('notes_html_name');
    const msg = document.getElementById('conflict-msg');
    const btn = document.getElementById('run_btn');

    const htmlName = htmlInput.value.trim();
    const notesName = notesInput.value.trim();
    const notesHtmlName = notesHtmlInput.value.trim();

    if (!currentAudioPath || !htmlName || !notesName || !notesHtmlName) {
      btn.disabled = true;
      return;
    }

    const uniqueSet = new Set([htmlName, notesName, notesHtmlName]);
    if (uniqueSet.size < 3) {
      msg.textContent = "Alle tre utdatafiler må ha ulike filnavn.";
      htmlInput.classList.add('conflict');
      notesInput.classList.add('conflict');
      notesHtmlInput.classList.add('conflict');
      btn.disabled = true;
      return;
    }

    const conflicts = await pywebview.api.check_conflicts(outDir, htmlName, notesName, notesHtmlName);
    htmlInput.classList.toggle('conflict', conflicts.includes(htmlName));
    notesInput.classList.toggle('conflict', conflicts.includes(notesName));
    notesHtmlInput.classList.toggle('conflict', conflicts.includes(notesHtmlName));

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
      document.getElementById('notes_html_name').value,
      document.getElementById('whisper_model').value,
      document.getElementById('ollama_model').value,
      document.getElementById('language').value,
      document.getElementById('hf_token').value,
      document.getElementById('min_speakers').value,
      document.getElementById('max_speakers').value,
      document.getElementById('speaker_names_hint').value,
      document.getElementById('meeting_title').value,
      document.getElementById('agenda_text').value
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
        "Møteopptak, Transkripsjon & Referat (WhisperX + Ollama)",
        html=HTML,
        js_api=api,
        width=760,
        height=760,
    )
    api.window = window
    webview.start(bind_drag_and_drop, window)


if __name__ == "__main__":
    main()
