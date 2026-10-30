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

import html
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


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


def _maybe_run_cli_from_args() -> None:
    """If CLI arguments are passed to mote_app.py, run headless CLI via mote_core without launching GUI."""
    if __name__ != "__main__":
        return
    argv = sys.argv[1:]
    if not argv:
        return
    if argv[0] == "--cli":
        argv = argv[1:]
    from mote_core import cli_main

    raise SystemExit(cli_main(argv))


_maybe_run_cli_from_args()
_self_bootstrap_if_needed()

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
        context_parts.append(f"Agenda / Lysbilder / Kontekst:\n{agenda_text.strip()[:4500]}")
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
            m_json = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if m_json:
                content = m_json.group(0)
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


from mote_core import build_notes_html, build_transcript_html_and_text  # noqa: E402


def generate_notes_via_api(
    transcript: str,
    model: str,
    speaker_map: dict[str, str],
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    progress_fn=None,
    note_mode: str = "mote",
) -> str:
    """Use Ollama HTTP /api/chat with agenda & attendee grounding to generate meeting or lecture notes."""
    detected_list = ", ".join(sorted(set(speaker_map.values())))
    meta_lines = [f"- **Identifiserte stemmer i opptaket**: {detected_list}"]
    if meeting_title.strip():
        meta_lines.insert(0, f"- **Tittel og dato**: {meeting_title.strip()}")
    if known_names_hint.strip():
        meta_lines.append(f"- **Oppgitte personer / roller**: {known_names_hint.strip()}")
    if agenda_text.strip():
        meta_lines.append(f"- **Forhåndsoppgitt agenda / lysbilder / kontekst**:\n{agenda_text.strip()[:12000]}")
    meta_block = "\n".join(meta_lines)

    if note_mode == "foredrag":
        heading = (
            f"# Foredragsnotater: {meeting_title.strip()}"
            if meeting_title.strip()
            else "# Foredragsnotater og hovedpoenger"
        )
        system_prompt = (
            "Du er en dyktig norsk fagredaktør og notatskriver. Du skriver kun selve oppsummeringen i ren Markdown. "
            "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
            "Opptaket er et foredrag, en faglig presentasjon eller en monolog (ikke et møte med vedtak og aksjonstabell). "
            "Bruk korrekt norsk (bokmål) og trekk ut de viktigste innsiktene, hovedpoengene, faglige sammenhengene og konkrete læringspunktene."
        )
        user_prompt = (
            "Lag et grundig og strukturert faglig sammendrag av foredraget/presentasjonen basert på konteksten og den tidsstemplede transkripsjonen under.\n\n"
            "### KONTEKST / TEMA:\n"
            f"{meta_block}\n\n"
            "Bruk denne strukturen i notatene:\n"
            f"{heading}\n\n"
            "## 1. Oversikt og hovedbudskap\n"
            "(Foredragsholder hvis kjent, tema, formål og kjernebudskapet oppsummert kort)\n\n"
            "## 2. Sentrale innsikter og hovedpoenger (Key Takeaways)\n"
            "(Kulepunktliste med de viktigste konklusjonene, innsiktene og lærdommene fra presentasjonen)\n\n"
            "## 3. Tematisk gjennomgang\n"
            "(Strukturert etter hovedtemaene i foredraget med underoverskrifter, sentrale argumenter, eksempler, tall/fakta og tidsstempler)\n\n"
            "## 4. Viktige begreper, modeller eller verktøy\n"
            "(Korte forklaringer av sentrale fagbegreper, metoder, teknologier eller referanser som ble trukket frem)\n\n"
            "## 5. Anbefalinger og videre refleksjon\n"
            "(Konkrete råd til tilhørerne, åpne spørsmål eller forslag til videre oppfølging nevnt i foredraget — uten kunstig møte-aksjonstabell)\n\n"
            f"--- TRANSKRIPSJON START ---\n{transcript}\n--- TRANSKRIPSJON SLUTT ---"
        )
    else:
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


def resolve_moved_audio_path(audio_path: Path, base_dir: Path) -> Path:
    """Locate audio file if moved into data/ or relocated with the project folder (handles NFD/NFC)."""
    if audio_path.is_file():
        return audio_path
    target_nfc = unicodedata.normalize("NFC", audio_path.name)
    search_dirs = [
        audio_path.parent,
        audio_path.parent / "data",
        base_dir,
        base_dir / "data",
    ]
    for d in search_dirs:
        if not d.is_dir():
            continue
        direct = d / audio_path.name
        if direct.is_file():
            return direct.resolve()
        for child in d.iterdir():
            if child.is_file() and unicodedata.normalize("NFC", child.name) == target_nfc:
                return child.resolve()
    return audio_path


def stage_audio_input(audio_path: Path, tmp_dir: Path) -> Path:
    """Create a hardlink (or copy) in tmp_dir so moving/renaming files mid-run never breaks WhisperX/Pyannote."""
    ext = audio_path.suffix or ".wav"
    staged = tmp_dir / f"input_audio{ext}"
    try:
        os.link(audio_path, staged)
    except OSError:
        shutil.copy2(audio_path, staged)
    return staged


TRANSCRIPT_EXTENSIONS = {".html", ".htm", ".json", ".txt", ".md", ".srt", ".vtt"}


def is_transcript_file(path: Path) -> bool:
    return path.suffix.lower() in TRANSCRIPT_EXTENSIONS


def clean_base_stem(path: Path) -> str:
    stem = path.stem
    for suf in (
        "_transkripsjon",
        "_referat",
        "_1_asr",
        "_2_align",
        "_3_diarize",
        "_4_speakers",
        "1_asr",
        "2_align",
        "3_diarize",
        "4_speakers",
    ):
        if stem.endswith(suf) and len(stem) > len(suf):
            stem = stem[: -len(suf)]
            break
    return stem


def get_checkpoint_dir(out_dir: Path, input_or_stem: Path | str) -> Path:
    stem = clean_base_stem(input_or_stem) if isinstance(input_or_stem, Path) else clean_base_stem(Path(input_or_stem))
    return out_dir / ".mote_checkpoints" / stem


def parse_time_str(t_str: str) -> float:
    t_str = t_str.strip().replace(",", ".")
    parts = t_str.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def load_turns_from_transcript_file(path: Path) -> tuple[list[dict], dict[str, str]]:
    """Parse turns and optional speaker_map from .html, .json, .srt, .vtt, .txt, or .md transcript files."""
    ext = path.suffix.lower()
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    turns: list[dict] = []
    speaker_map: dict[str, str] = {}

    if ext in (".html", ".htm"):
        # Parse our rich HTML transcript format (<div class="turn" data-speaker="...">)
        pattern = re.compile(
            r'data-speaker="([^"]+)".*?'
            r'data-spk-label="[^"]*">(.*?)</span>.*?'
            r'class="timestamp">([^<]+)</span>.*?'
            r'class="text">(.*?)</div>',
            re.DOTALL,
        )
        for m in pattern.finditer(raw_text):
            orig_spk, disp_spk, ts_range, txt_html = m.groups()
            orig_spk = html.unescape(orig_spk).strip()
            disp_spk = html.unescape(disp_spk).strip()
            if disp_spk and disp_spk != orig_spk:
                speaker_map[orig_spk] = disp_spk
            ts_parts = re.split(r"\s*[–\-]\s*", ts_range.strip())
            start_s = parse_time_str(ts_parts[0]) if ts_parts else 0.0
            end_s = parse_time_str(ts_parts[1]) if len(ts_parts) > 1 else start_s
            turns.append(
                {
                    "speaker": orig_spk or "SPEAKER_00",
                    "start": start_s,
                    "end": end_s,
                    "text": sanitize_text(html.unescape(txt_html)),
                }
            )
        if turns:
            return turns, speaker_map
        # Fallback: strip HTML tags and treat as text lines
        raw_text = re.sub(r"<[^>]+>", "\n", raw_text)

    if ext == ".json":
        data = json.loads(raw_text)
        if isinstance(data, dict) and "segments" in data:
            return extract_turns_from_whisperx(path), {}
        if isinstance(data, dict) and "turns" in data and isinstance(data["turns"], list):
            s_map = data.get("speaker_map") if isinstance(data.get("speaker_map"), dict) else {}
            return data["turns"], s_map
        if isinstance(data, list):
            cur_t = 0.0
            for item in data:
                if isinstance(item, dict) and item.get("text"):
                    spk = str(item.get("speaker", "SPEAKER_00"))
                    st = float(item.get("start", cur_t))
                    en = float(item.get("end", st + 5.0))
                    cur_t = en
                    turns.append(
                        {"speaker": spk, "start": st, "end": en, "text": sanitize_text(str(item["text"]))}
                    )
            return turns, {}

    if ext in (".srt", ".vtt"):
        blocks = re.split(r"\n\s*\n", raw_text)
        for blk in blocks:
            m_ts = re.search(
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)",
                blk,
            )
            if not m_ts:
                continue
            st = parse_time_str(m_ts.group(1))
            en = parse_time_str(m_ts.group(2))
            lines = [
                ln.strip()
                for ln in blk[m_ts.end() :].splitlines()
                if ln.strip() and not ln.strip().isdigit()
            ]
            txt = sanitize_text(" ".join(lines))
            if not txt:
                continue
            spk = "SPEAKER_00"
            m_spk = re.match(r"^([A-Za-zÆØÅæøå0-9_ ()\-]{2,35}):\s+(.+)$", txt)
            if m_spk:
                spk, txt = m_spk.group(1).strip(), m_spk.group(2).strip()
            turns.append({"speaker": spk, "start": st, "end": en, "text": txt})
        if turns:
            return turns, {}

    # Plain text / Markdown parsing
    cur_t = 0.0
    for line in raw_text.splitlines():
        clean = sanitize_text(line)
        if not clean:
            continue
        # Match [00:12 – 00:25] Speaker: Text
        m_br = re.match(
            r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[–\-]\s*(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):\s*(.+)$",
            clean,
        )
        if m_br:
            st = parse_time_str(m_br.group(1))
            en = parse_time_str(m_br.group(2))
            spk = m_br.group(3).strip()
            txt = m_br.group(4).strip()
            cur_t = en
            turns.append({"speaker": spk, "start": st, "end": en, "text": txt})
            continue
        m_spk = re.match(r"^([A-Za-zÆØÅæøå0-9_ ()\-]{2,40}):\s+(.+)$", clean)
        if m_spk and not clean.startswith("#"):
            spk = m_spk.group(1).strip()
            txt = m_spk.group(2).strip()
            turns.append({"speaker": spk, "start": cur_t, "end": cur_t + 5.0, "text": txt})
            cur_t += 5.0
        else:
            turns.append({"speaker": "SPEAKER_00", "start": cur_t, "end": cur_t + 5.0, "text": clean})
            cur_t += 5.0
    return turns, speaker_map


def extract_context_from_file(path: Path) -> str:
    """Extract readable text from .pptx, .docx, .odp, .odt, .pdf, .md, .txt, .html, .json, or .csv."""
    ext = path.suffix.lower()
    if ext == ".pptx":
        slides_out: list[str] = []
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            slide_files = sorted(
                (n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
                key=lambda s: int(re.search(r"(\d+)", s).group(1)),  # type: ignore[union-attr]
            )
            for idx, s_name in enumerate(slide_files, start=1):
                xml_bytes = zf.read(s_name)
                root = ET.fromstring(xml_bytes)
                paras: list[str] = []
                for p_elem in root.iter():
                    if p_elem.tag.endswith("}p"):
                        runs = [
                            t_elem.text.strip()
                            for t_elem in p_elem.iter()
                            if t_elem.tag.endswith("}t") and t_elem.text and t_elem.text.strip()
                        ]
                        if runs:
                            paras.append(" ".join(runs))
                note_name = f"ppt/notesSlides/notesSlide{idx}.xml"
                if note_name in names:
                    n_root = ET.fromstring(zf.read(note_name))
                    n_runs = [
                        t_elem.text.strip()
                        for t_elem in n_root.iter()
                        if t_elem.tag.endswith("}t") and t_elem.text and t_elem.text.strip()
                    ]
                    if n_runs:
                        paras.append("Foredragsnotat: " + " ".join(n_runs))
                if paras:
                    slides_out.append(f"[Lysbilde {idx}] " + " | ".join(paras))
        return "\n".join(slides_out)

    if ext == ".docx":
        paras_out: list[str] = []
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" in zf.namelist():
                root = ET.fromstring(zf.read("word/document.xml"))
                for p_elem in root.iter():
                    if p_elem.tag.endswith("}p"):
                        runs = [
                            t_elem.text
                            for t_elem in p_elem.iter()
                            if t_elem.tag.endswith("}t") and t_elem.text
                        ]
                        line = "".join(runs).strip()
                        if line:
                            paras_out.append(line)
        return "\n".join(paras_out)

    if ext in (".odp", ".odt"):
        paras_out = []
        with zipfile.ZipFile(path, "r") as zf:
            if "content.xml" in zf.namelist():
                root = ET.fromstring(zf.read("content.xml"))
                for elem in root.iter():
                    if elem.tag.endswith("}p") or elem.tag.endswith("}h"):
                        txt = "".join(elem.itertext()).strip()
                        if txt:
                            paras_out.append(txt)
        return "\n".join(paras_out)

    if ext == ".pdf":
        # 1. Try pdftotext if installed
        pdftotext_bin = shutil.which("pdftotext", path="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""))
        if pdftotext_bin:
            try:
                out = subprocess.check_output([pdftotext_bin, str(path), "-"], text=True, stderr=subprocess.DEVNULL)
                if out.strip():
                    return sanitize_text(out)
            except Exception:
                pass
        # 2. Try macOS Spotlight kMDItemTextContent
        if sys.platform == "darwin":
            try:
                out = subprocess.check_output(
                    ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                if out and out != "(null)" and len(out.strip()) > 20:
                    return sanitize_text(out)
            except Exception:
                pass
        # 3. Pure-Python FlateDecode PDF text extractor fallback
        raw_bytes = path.read_bytes()
        extracted_chunks: list[str] = []
        for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw_bytes, flags=re.DOTALL):
            stream_data = m.group(1)
            try:
                decompressed = zlib.decompress(stream_data)
            except Exception:
                continue
            for tm in re.finditer(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decompressed):
                s = tm.group(1).decode("latin1", errors="ignore")
                if s.strip():
                    extracted_chunks.append(s)
            for tj_arr in re.finditer(rb"\[(.*?)\]\s*TJ", decompressed, flags=re.DOTALL):
                parts = re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", tj_arr.group(1))
                joined = "".join(p.decode("latin1", errors="ignore") for p in parts)
                if joined.strip():
                    extracted_chunks.append(joined)
        if extracted_chunks:
            return sanitize_text(" ".join(extracted_chunks))

    txt = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        txt = re.sub(r"<script[\s\S]*?</script>", "", txt, flags=re.IGNORECASE)
        txt = re.sub(r"<style[\s\S]*?</style>", "", txt, flags=re.IGNORECASE)
        txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    return sanitize_text(txt)


def build_whisper_initial_prompt(
    meeting_title: str, speaker_names_hint: str, agenda_text: str
) -> str:
    """Build a vocabulary-rich initial_prompt (up to ~750 chars) from title, attendees, agenda, and slides."""
    parts: list[str] = []
    if meeting_title.strip():
        parts.append(meeting_title.strip())
    if speaker_names_hint.strip():
        parts.append(f"Deltakere: {speaker_names_hint.strip()}.")
    if agenda_text.strip():
        clean_ag = re.sub(r"\s+", " ", agenda_text.strip())
        parts.append(clean_ag[:550])
    return " ".join(parts).strip()[:750]


def get_whisperx_python(whisperx_bin: str) -> str | None:
    """Find the Python interpreter inside the uv/pipx whisperx environment."""
    wx_path = Path(whisperx_bin).resolve()
    for name in ("python.exe", "python3", "python"):
        cand = wx_path.parent / name
        if cand.is_file():
            return str(cand)
    try:
        first_line = Path(whisperx_bin).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        if first_line.startswith("#!"):
            shebang_bin = first_line[2:].strip().split()[0]
            if Path(shebang_bin).is_file():
                return shebang_bin
    except Exception:
        pass
    return None


WHISPERX_STAGE_RUNNER_CODE = """\
import gc
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")

import torch
import whisperx
from whisperx.audio import load_audio
from whisperx.diarize import DiarizationPipeline, assign_word_speakers

def save_atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def main() -> int:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    audio_path = cfg["audio_path"]
    ckpt_dir = Path(cfg["ckpt_dir"])
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    stages = cfg["stages"]
    device = cfg.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    compute_type = cfg.get("compute_type", "int8")
    language = cfg.get("language") or "no"
    model_name = cfg["model"]
    hf_token = cfg.get("hf_token") or None
    min_spk = cfg.get("min_speakers")
    max_spk = cfg.get("max_speakers")
    initial_prompt = cfg.get("initial_prompt") or None

    asr_json = ckpt_dir / "1_asr.json"
    align_json = ckpt_dir / "2_align.json"
    diarize_json = ckpt_dir / "3_diarize.json"

    audio_arr = None
    def get_audio():
        nonlocal audio_arr
        if audio_arr is None:
            audio_arr = load_audio(audio_path)
        return audio_arr

    result = None

    if "asr" in stages or not asr_json.is_file():
        print("STAGE:ASR:START", flush=True)
        asr_options = {
            "beam_size": 5,
            "patience": 1.0,
            "length_penalty": 1.0,
            "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "condition_on_previous_text": False,
            "initial_prompt": initial_prompt,
            "suppress_tokens": [-1],
            "suppress_numerals": False,
        }
        model = whisperx.load_model(
            model_name,
            device=device,
            compute_type=compute_type,
            language=language,
            asr_options=asr_options,
        )
        result = model.transcribe(
            get_audio(),
            batch_size=8,
            language=language,
            print_progress=True,
        )
        result["language"] = language
        save_atomic_json(asr_json, result)
        print("STAGE:ASR:DONE", flush=True)
        del model
        gc.collect()
    else:
        result = json.loads(asr_json.read_text(encoding="utf-8"))

    if "align" in stages or ("diarize" in stages and not align_json.is_file()):
        print("STAGE:ALIGN:START", flush=True)
        if result is None:
            result = json.loads(asr_json.read_text(encoding="utf-8"))
        if result.get("segments"):
            align_lang = result.get("language") or language
            model_a, metadata = whisperx.load_align_model(language_code=align_lang, device=device)
            result = whisperx.align(
                result["segments"],
                model_a,
                metadata,
                get_audio(),
                device,
                return_char_alignments=False,
                print_progress=True,
            )
            result["language"] = align_lang
            del model_a
            gc.collect()
        save_atomic_json(align_json, result)
        print("STAGE:ALIGN:DONE", flush=True)
    elif align_json.is_file():
        result = json.loads(align_json.read_text(encoding="utf-8"))

    if "diarize" in stages:
        print("STAGE:DIARIZE:START", flush=True)
        if result is None:
            source_file = align_json if align_json.is_file() else asr_json
            result = json.loads(source_file.read_text(encoding="utf-8"))

        if min_spk == 1 and max_spk == 1:
            print("En-talers modus (1 taler): tilordner SPEAKER_00 direkte uten Pyannote-ventetid.", flush=True)
            for seg in result.get("segments", []):
                seg["speaker"] = "SPEAKER_00"
                for w in seg.get("words", []):
                    if isinstance(w, dict):
                        w["speaker"] = "SPEAKER_00"
        else:
            diarize_model = DiarizationPipeline(
                model_name="pyannote/speaker-diarization-community-1",
                token=hf_token,
                device=device,
            )
            def _on_diarize_progress(pct: float) -> None:
                print(f"DiarizeProgress: {pct:.2f}%", flush=True)

            diarize_segments = diarize_model(
                get_audio(),
                min_speakers=min_spk,
                max_speakers=max_spk,
                progress_callback=_on_diarize_progress,
            )
            result = assign_word_speakers(diarize_segments, result)
            del diarize_model
            gc.collect()

        save_atomic_json(diarize_json, result)
        print("STAGE:DIARIZE:DONE", flush=True)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""



class MoteApi:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self._rec_proc: subprocess.Popen | None = None
        self._rec_path: Path | None = None
        self._live_monitor: object | None = None
        self._tts_proc: subprocess.Popen | None = None
        self._active_proc: subprocess.Popen | None = None
        self._cancel_requested: bool = False
        self._log_lines: list[str] = []

    def get_defaults(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        base_dir = Path(__file__).resolve().parent
        default_dir = base_dir / "data" if (base_dir / "data").is_dir() else (Path.home() / "Downloads")
        return {
            "hf_token": get_initial_hf_token(),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(default_dir),
            "default_meeting_title": f"Møte {today}",
        }

    def stop_job(self) -> dict:
        """Stop/pause the active transcription or LLM job while keeping completed checkpoints."""
        self._cancel_requested = True
        proc = self._active_proc
        if proc and proc.poll() is None:
            try:
                if os.name != "nt":
                    proc.terminate()
                else:
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
    ) -> dict:
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
        base_dir = Path(__file__).resolve().parent
        audio_path = resolve_moved_audio_path(Path(audio_path_str), base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        ckpt_dir = get_checkpoint_dir(out_dir, audio_path)
        is_trans = is_transcript_file(audio_path)

        html_exists = bool(html_name_str.strip()) and (out_dir / Path(html_name_str.strip()).name).is_file()
        notes_exists = bool(notes_name_str.strip()) and (out_dir / Path(notes_name_str.strip()).name).is_file()

        return {
            "has_input": True,
            "is_transcript_input": is_trans,
            "s1_asr": is_trans or (ckpt_dir / "1_asr.json").is_file(),
            "s2_align": is_trans or (ckpt_dir / "2_align.json").is_file(),
            "s3_diarize": is_trans or (ckpt_dir / "3_diarize.json").is_file(),
            "s4_names": (ckpt_dir / "4_speakers.json").is_file() or html_exists,
            "s5_notes": notes_exists,
            "ckpt_dir": str(ckpt_dir),
        }

    def save_log_dialog(self, log_text: str, out_dir_str: str, audio_path_str: str) -> str | None:
        if not self.window:
            return None
        base_dir = Path(__file__).resolve().parent
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

    def _on_live_level(self, lvl: dict) -> None:
        if self.window:
            payload = json.dumps(lvl, ensure_ascii=False)
            self.window.evaluate_js(f"updateLiveLevel({payload})")

    def _on_live_chunk(self, line: str, lvl: dict, _text: str) -> None:
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
        whisper_model: str = "NbAiLab/nb-whisper-large",
        language: str = "no",
        meeting_title: str = "",
        speaker_names_hint: str = "",
        agenda_text: str = "",
    ) -> dict:
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
                base_dir = Path(__file__).resolve().parent
                if str(base_dir) not in sys.path:
                    sys.path.insert(0, str(base_dir))
                import mote_core

                init_prompt = build_whisper_initial_prompt(meeting_title, speaker_names_hint, agenda_text)
                mon = mote_core.LiveAudioMonitor(
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

    def stop_recording(self, out_dir_str: str) -> dict | None:
        """Gracefully stop the active ffmpeg microphone recording and return suggested file names."""
        if self._live_monitor is not None:
            try:
                self._live_monitor.stop()  # type: ignore[attr-defined]
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
        whisper_model: str = "NbAiLab/nb-whisper-large",
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
                base_dir = Path(__file__).resolve().parent
                if str(base_dir) not in sys.path:
                    sys.path.insert(0, str(base_dir))
                import mote_core

                init_prompt = build_whisper_initial_prompt(meeting_title, speaker_names_hint, agenda_text)
                res = mote_core.run_live_check_on_file(
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

    def suggest_output_names(self, audio_path_str: str, out_dir_str: str) -> dict:
        base_dir = Path(__file__).resolve().parent
        audio_path = resolve_moved_audio_path(Path(audio_path_str), base_dir)
        out_dir = Path(out_dir_str) if out_dir_str else audio_path.parent
        if not out_dir.exists() and audio_path.parent.is_dir():
            out_dir = audio_path.parent
        stem = clean_base_stem(audio_path)

        # If checkpoints already exist for this stem and partial outputs exist, keep the standard names
        # unless all three outputs already exist from a finished run
        ckpt_dir = get_checkpoint_dir(out_dir, audio_path)
        std_html = f"{stem}_transkripsjon.html"
        std_md = f"{stem}_referat.md"
        std_rhtml = f"{stem}_referat.html"
        has_partial_ckpt = ckpt_dir.is_dir() and any(ckpt_dir.glob("*.json"))

        if has_partial_ckpt and not ((out_dir / std_md).exists() and (out_dir / std_rhtml).exists()):
            html_name = std_html if std_html != audio_path.name else unique_filename(out_dir, f"{stem}_transkripsjon", ".html")
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

    def pick_audio_file(self) -> dict | None:
        if not self.window:
            return None
        result = self.window.create_file_dialog(
            FileDialog.OPEN, allow_multiple=False
        )
        if result and len(result) > 0:
            p = Path(result[0])
            return self.suggest_output_names(str(p), str(p.parent))
        return None

    def pick_agenda_file(self) -> str | None:
        """Let user pick one or more agenda, slide (.pptx/.pdf/.odp), or document (.docx/.md/.txt) files."""
        if not self.window:
            return None
        result = self.window.create_file_dialog(
            FileDialog.OPEN, allow_multiple=True
        )
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
        note_mode: str = "auto",
        task_mode: str = "auto_resume",
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
            base_dir = Path(__file__).resolve().parent
            target_dir = out_dir if out_dir.is_dir() else (base_dir / "data" if (base_dir / "data").is_dir() else base_dir)
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
    ) -> None:
        self._cancel_requested = False
        self._log_lines = [f"Starter jobb (oppgavemodus: {task_mode})..."]
        self._set_running(True)
        self._set_progress(5, "Forbereder miljø og sjekkpunkter...")
        base_dir = Path(__file__).resolve().parent
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

            # Determine which steps (1..5) to execute based on task_mode and existing checkpoints
            if task_mode == "full_fresh":
                run_s1 = not is_trans_input
                run_s2 = not is_trans_input
                run_s3 = not is_trans_input
                run_s4 = True
                run_s5 = True
            elif task_mode == "only_1_asr":
                run_s1, run_s2, run_s3, run_s4, run_s5 = True, False, False, False, False
            elif task_mode == "only_2_align":
                run_s1 = not asr_json.is_file()
                run_s2, run_s3, run_s4, run_s5 = True, False, False, False, False
            elif task_mode == "only_3_diarize":
                run_s1 = not asr_json.is_file() and not align_json.is_file()
                run_s2 = not align_json.is_file()
                run_s3, run_s4, run_s5 = True, False, False
            elif task_mode == "from_3_diarize":
                run_s1 = not asr_json.is_file() and not align_json.is_file()
                run_s2 = not align_json.is_file()
                run_s3, run_s4, run_s5 = True, True, True
            elif task_mode == "from_4_names":
                run_s1 = not is_trans_input and not diarize_json.is_file() and not align_json.is_file() and not asr_json.is_file()
                run_s2 = not is_trans_input and not diarize_json.is_file() and not align_json.is_file()
                run_s3 = not is_trans_input and not diarize_json.is_file()
                run_s4, run_s5 = True, True
            elif task_mode == "only_4_names":
                run_s1 = not is_trans_input and not diarize_json.is_file() and not align_json.is_file() and not asr_json.is_file()
                run_s2 = not is_trans_input and not diarize_json.is_file() and not align_json.is_file()
                run_s3 = not is_trans_input and not diarize_json.is_file()
                run_s4, run_s5 = True, False
            elif task_mode == "only_5_notes":
                has_trans_source = (
                    is_trans_input
                    or speakers_json.is_file()
                    or html_path.is_file()
                    or diarize_json.is_file()
                    or align_json.is_file()
                    or asr_json.is_file()
                )
                run_s1 = not has_trans_source
                run_s2 = not has_trans_source
                run_s3 = not has_trans_source
                run_s4 = not (is_trans_input or speakers_json.is_file() or html_path.is_file())
                run_s5 = True
            else:
                # auto_resume: skip already-completed stages, or if all are done, re-run from step 4/5
                if is_trans_input:
                    run_s1 = run_s2 = run_s3 = False
                    run_s4 = not speakers_json.is_file() or not html_path.is_file()
                    run_s5 = True
                else:
                    run_s1 = not asr_json.is_file()
                    run_s2 = run_s1 or not align_json.is_file()
                    run_s3 = run_s2 or not diarize_json.is_file()
                    run_s4 = run_s3 or not speakers_json.is_file() or not html_path.is_file()
                    run_s5 = True

            if is_trans_input and (run_s1 or run_s2 or run_s3):
                self._log(
                    f"==> Merk: {audio_path.name} er allerede en transkripsjonsfil. "
                    "Lydsteg 1–3 hoppes over."
                )
                run_s1 = run_s2 = run_s3 = False
                if task_mode in ("only_1_asr", "only_2_align", "only_3_diarize"):
                    self._set_progress(100, "Inndata er allerede transkribert")
                    return

            env = build_runtime_env()

            # Execute WhisperX stages (1: ASR, 2: Align, 3: Diarize) if needed
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
                    meeting_title, speaker_names_hint, agenda_text
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

            # If user requested only step 1, 2, or 3, stop here after saving checkpoint
            if not run_s4 and not run_s5:
                self._refresh_checkpoints_ui()
                self._set_progress(100, f"Steg ({', '.join(wx_stages)}) fullført og lagret i sjekkpunkt")
                self._log(f"==> Valgt deloppgave ({task_mode}) er ferdig og lagret i {ckpt_dir}")
                return

            # Load turns and any existing speaker map for Steps 4 and/or 5
            turns: list[dict] = []
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
                turns = saved_s4.get("turns", [])
                speaker_map = saved_s4.get("speaker_map", {})
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
                    )
                    # Merge inferred names with any existing speaker map from transcript
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
                    # Build clean_transcript string in-memory without overwriting html_path unless missing
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

                if note_mode in ("foredrag", "mote"):
                    effective_mode = note_mode
                elif (
                    max_speakers_str.strip() == "1"
                    or (min_speakers_str.strip() == "1" and not max_speakers_str.strip())
                    or len(set(speaker_map.keys())) == 1
                ):
                    effective_mode = "foredrag"
                else:
                    effective_mode = "mote"

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
    margin: 0; padding: 10px 14px;
    background: Canvas; color: CanvasText;
    font-size: 12.5px; user-select: none;
    display: flex; flex-direction: column; gap: 5px; height: calc(100vh - 20px);
  }
  .top-cards {
    display: flex; gap: 8px; align-items: stretch;
  }
  #dropzone {
    flex: 1.25; border: 2px dashed GrayText; border-radius: 10px;
    padding: 7px 12px; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; transition: all 0.15s ease;
  }
  #dropzone.dragover { border-color: #0a84ff; background: rgba(10, 132, 255, 0.1); }
  .dz-text { flex: 1; overflow: hidden; }
  .dz-text strong { font-size: 12px; display: block; margin-bottom: 2px; }
  #selected-file {
    color: GrayText; display: block; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-size: 11px;
  }
  .rec-card {
    flex: 0.95; border: 1px solid rgba(128,128,128,0.35); border-radius: 10px;
    padding: 7px 12px; display: flex; flex-direction: column; justify-content: center; gap: 4px;
    background: rgba(128,128,128,0.06);
  }
  .rec-controls { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  #rec_btn.recording { background: #ff453a; color: white; border-color: #ff453a; font-weight: 600; }
  #rec_timer { font-family: "SF Mono", Menlo, monospace; font-weight: 600; font-size: 12px; color: #ff453a; }
  .live-badge {
    font-size: 10.5px; padding: 2px 7px; border-radius: 999px;
    border: 1px solid rgba(128,128,128,0.35); color: GrayText;
    background: rgba(128,128,128,0.08); white-space: nowrap;
  }
  .live-badge.godt { border-color: rgba(48, 209, 88, 0.7); background: rgba(48, 209, 88, 0.16); color: CanvasText; font-weight: 600; }
  .live-badge.lavt { border-color: rgba(255, 159, 10, 0.75); background: rgba(255, 159, 10, 0.16); color: CanvasText; font-weight: 600; }
  .live-badge.hoyt { border-color: rgba(255, 69, 58, 0.8); background: rgba(255, 69, 58, 0.18); color: #ff453a; font-weight: 600; }
  #live_check_panel {
    display: none; background: rgba(48, 209, 88, 0.07); border: 1px solid rgba(48, 209, 88, 0.45);
    border-radius: 8px; padding: 6px 10px; font-size: 11px; line-height: 1.4;
    max-height: 68px; overflow-y: auto; font-family: "SF Mono", Menlo, Consolas, monospace;
    white-space: pre-wrap; user-select: text;
  }

  #prompt_banner {
    display: none; background: rgba(10, 132, 255, 0.1); border: 1px solid #0a84ff;
    border-radius: 8px; padding: 7px 12px; font-size: 11.5px; line-height: 1.4;
  }
  #prompt_banner strong { color: #0a84ff; }

  .row { display: flex; gap: 6px; align-items: center; }
  .row label { width: 115px; flex-shrink: 0; font-weight: 500; font-size: 12px; }
  input[type="text"], input[type="password"], input[type="number"], select, textarea {
    flex: 1; padding: 4px 8px; border-radius: 6px;
    border: 1px solid rgba(128,128,128,0.4); background: Field; color: FieldText;
    font-size: 12px; font-family: inherit;
  }
  textarea { resize: vertical; min-height: 42px; max-height: 100px; }
  input.conflict { border-color: #ff9f0a; background: rgba(255, 159, 10, 0.08); }
  button {
    padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.4);
    background: ButtonFace; color: ButtonText; cursor: pointer; font-size: 12px;
    white-space: nowrap;
  }
  button.primary {
    background: #0a84ff; color: white; border: none; font-weight: 600; padding: 6px 14px;
  }
  button.danger {
    background: rgba(255, 69, 58, 0.14); color: #ff453a; border-color: rgba(255, 69, 58, 0.45); font-weight: 600;
  }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  #conflict-msg { color: GrayText; font-size: 11px; min-height: 14px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .ckpt-bar {
    display: flex; gap: 5px; align-items: center; flex-wrap: wrap;
  }
  .ckpt-pill {
    font-size: 10.5px; padding: 2px 8px; border-radius: 999px;
    border: 1px solid rgba(128,128,128,0.35); color: GrayText;
    background: rgba(128,128,128,0.07); cursor: pointer;
    transition: all 0.15s ease;
  }
  .ckpt-pill.done {
    border-color: rgba(48, 209, 88, 0.6);
    background: rgba(48, 209, 88, 0.14);
    color: CanvasText;
    font-weight: 600;
  }
  .ckpt-pill:hover { border-color: #0a84ff; }

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
        <strong>Slipp lydfil (.m4a, .wav, .opus, .mp3) eller transkripsjon (.html, .json, .txt, .srt)</strong>
        <span id="selected-file">Ingen lyd- eller transkripsjonsfil valgt ennå</span>
      </div>
      <button onclick="browseAudio()">Velg fil...</button>
    </div>

    <div class="rec-card">
      <div class="rec-controls">
        <button id="rec_btn" onclick="toggleRecording()">Spill inn møte</button>
        <span id="rec_timer">00:00</span>
        <span id="live_level_badge" class="live-badge" title="Sanntids lydnivå (dBFS) fra mikrofon">Lyd: klar</span>
        <button onclick="speakGuide()" title="Les opp møtestart-instruks høyt">Tale-instruks</button>
      </div>
      <div style="display: flex; gap: 10px; flex-wrap: wrap;">
        <label style="font-size: 11px; color: GrayText; display: flex; align-items: center; gap: 4px;">
          <input type="checkbox" id="auto_speak_guide" checked>
          Les opp navnerunde-guide
        </label>
        <label style="font-size: 11px; color: GrayText; display: flex; align-items: center; gap: 4px;" title="Transkriberer grove 5-sekunders utsnitt fortløpende under opptak for å bekrefte at alle rundt bordet høres tydelig">
          <input type="checkbox" id="live_transcribe_chk" checked>
          Grov sanntids-transkripsjon (lydsjekk)
        </label>
      </div>
    </div>
  </div>

  <div id="prompt_banner">
    <strong>Møtestart-guide for god talergjenkjenning og lydsjekk:</strong><br>
    1. <b>Møteleder:</b> Si kort hva møtet gjelder og dagens dato.<br>
    2. <b>Navnerunde rundt bordet:</b> Hver deltaker sier eget navn og tittel/rolle i én hel setning. Følg med i <b>Grov sanntids-lydsjekk</b> under for å se at alle rundt bordet fanges opp med <i>Godt lydnivå</i> (slå av macOS «Stemmeisolasjon» hvis noen faller ut).
  </div>

  <div id="live_check_panel"></div>

  <div class="row">
    <label>Møtetittel & Dato:</label>
    <input type="text" id="meeting_title" placeholder="F.eks. Prosjektmøte – 2026-10-29">
    <button onclick="loadAgendaFile()" title="Last inn agenda, lysbilder (.pptx, .pdf, .odp) eller notater (.docx, .md, .txt)">+ Agenda / Lysbilder...</button>
  </div>

  <div class="row">
    <label>Tilstede (Navn/tittel):</label>
    <input type="text" id="speaker_names_hint" placeholder="F.eks. Ola Nordmann (Møteleder), Kari Hansen (Arkitekt), Per Olsen, Anne Berg" oninput="autoCountSpeakersFromHint()">
  </div>

  <div class="row" style="align-items: flex-start;">
    <label style="padding-top: 4px;">Agenda / Lysbilder:</label>
    <textarea id="agenda_text" placeholder="Valgfritt: Lim inn agenda/stikkord, eller klikk «+ Agenda / Lysbilder...» for å hente tekst fra PowerPoint (.pptx), PDF (.pdf), Word (.docx) eller Markdown (.md)..."></textarea>
  </div>

  <div class="row">
    <label>Talere & Modus:</label>
    <input type="number" id="min_speakers" placeholder="Min" min="1" max="20" style="max-width: 54px;">
    <input type="number" id="max_speakers" placeholder="Maks" min="1" max="20" style="max-width: 54px;">
    <button onclick="setExactSpeakers(1)" title="1 taler – Foredrag / Presentasjon">1</button>
    <button onclick="setExactSpeakers(2)" title="2 talere">2</button>
    <button onclick="setExactSpeakers(3)" title="3 talere">3</button>
    <button onclick="setExactSpeakers(4)" title="4 talere">4</button>
    <button onclick="setExactSpeakers(5)" title="5 talere">5</button>
    <select id="note_mode" onchange="onModeChanged()" title="Velg eller overstyr modus før du genererer transkripsjon og notater">
      <option value="auto" selected>Modus: Auto (Foredrag ved 1 taler, ellers Møte)</option>
      <option value="mote">Modus: Møtereferat (flere deltakere & aksjonspunkter)</option>
      <option value="foredrag">Modus: Foredrag / Presentasjon (faglige hovedpoenger)</option>
    </select>
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
    <button onclick="autoIncrementNames()" title="Foreslå nye ledige filnavn hvis du ikke vil overskrive">Ledig navn</button>
  </div>

  <div class="row">
    <label>Modeller & Språk:</label>
    <select id="whisper_model">
      <option value="NbAiLab/nb-whisper-large" selected>NB-Whisper Large (Anbefalt Norsk)</option>
      <option value="large-v3-turbo">Whisper: large-v3-turbo</option>
      <option value="large-v3">Whisper: large-v3</option>
      <option value="NbAiLab/nb-whisper-medium">NB-Whisper Medium (Raskere)</option>
    </select>
    <input type="text" id="ollama_model" value="gemma4:e4b" placeholder="Ollama-modell" style="max-width: 115px;">
    <select id="language" style="max-width: 58px;">
      <option value="no" selected>NO</option>
      <option value="en">EN</option>
      <option value="sv">SV</option>
      <option value="da">DA</option>
    </select>
    <input type="password" id="hf_token" placeholder="HF Token (hf_...)" style="max-width: 120px;">
  </div>

  <div class="row">
    <label>Sjekkpunkt & Steg:</label>
    <div class="ckpt-bar" id="ckpt_bar">
      <span class="ckpt-pill" id="ckpt_s1" onclick="selectTaskMode('only_1_asr')" title="Klikk for å kjøre kun Steg 1 (Whisper ASR)">1. ASR</span>
      <span class="ckpt-pill" id="ckpt_s2" onclick="selectTaskMode('only_2_align')" title="Klikk for å kjøre kun Steg 2 (Alignment)">2. Align</span>
      <span class="ckpt-pill" id="ckpt_s3" onclick="selectTaskMode('from_3_diarize')" title="Klikk for å kjøre fra Steg 3 (Pyannote -> Referat)">3. Pyannote</span>
      <span class="ckpt-pill" id="ckpt_s4" onclick="selectTaskMode('from_4_names')" title="Klikk for å kjøre fra Steg 4 (Navn & HTML -> Referat)">4. Navn & HTML</span>
      <span class="ckpt-pill" id="ckpt_s5" onclick="selectTaskMode('only_5_notes')" title="Klikk for å kjøre kun Steg 5 (Generer Referat/Notat på nytt)">5. Referat/Notat</span>
    </div>
    <select id="task_mode" onchange="validateNames()" title="Velg om du vil fortsette fra siste sjekkpunkt, kjøre alt på nytt, eller kjøre enkeltoppgaver">
      <option value="auto_resume" selected>Fortsett fra sjekkpunkt (hopp over ferdige steg)</option>
      <option value="full_fresh">Kjør alt på nytt (Steg 1–5)</option>
      <option value="from_3_diarize">Fra Steg 3 -> 5 (Pyannote + Navn + Referat)</option>
      <option value="from_4_names">Fra Steg 4 -> 5 (Navn & HTML + Referat)</option>
      <option value="only_5_notes">Kun Steg 5: Generer Referat / Notater på nytt</option>
      <option value="only_4_names">Kun Steg 4: Taler-navn & HTML-transkripsjon</option>
      <option value="only_3_diarize">Kun Steg 3: Taler-ID (Pyannote)</option>
      <option value="only_2_align">Kun Steg 2: Ord-tidsjustering (Alignment)</option>
      <option value="only_1_asr">Kun Steg 1: Rå-transkripsjon (ASR)</option>
    </select>
  </div>

  <div class="row">
    <span id="conflict-msg"></span>
    <button id="check_audio_btn" onclick="runQuickAudioCheck()" disabled title="Kjør grov sanntidstranskripsjon og lydnivåsjekk (dBFS) på de første 20 sekundene av valgt lydfil">Lydsjekk (20s)</button>
    <button onclick="copyLog()" title="Kopier hele loggen til utklippstavlen">Kopier logg</button>
    <button onclick="saveLog()" title="Lagre loggen til en .txt-fil">Lagre logg...</button>
    <button class="danger" id="stop_btn" onclick="stopJob()" disabled title="Stopp/pause pågående steg (fullførte steg er lagret i sjekkpunkt)">Stopp / Pause</button>
    <button class="primary" id="run_btn" onclick="runPipeline()" disabled>Start / Fortsett</button>
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

  <div id="log">Klar. Start et opptak, eller velg en lydfil (.wav/.m4a/.opus/.mp3) eller en eksisterende transkripsjonsfil (.html/.json/.txt/.srt).</div>

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

  function selectTaskMode(mode) {
    const sel = document.getElementById('task_mode');
    if (sel) {
      sel.value = mode;
      validateNames();
    }
  }

  function setExactSpeakers(n) {
    document.getElementById('min_speakers').value = n;
    document.getElementById('max_speakers').value = n;
  }

  function onModeChanged() {
    const mode = document.getElementById('note_mode').value;
    if (mode === 'foredrag' && !document.getElementById('min_speakers').value && !document.getElementById('max_speakers').value) {
      document.getElementById('min_speakers').value = 1;
      document.getElementById('max_speakers').value = 1;
    }
  }

  function autoCountSpeakersFromHint() {
    const raw = document.getElementById('speaker_names_hint').value.trim();
    if (!raw) return;
    const parts = raw.split(/[,;\\n]+/).map(s => s.trim()).filter(Boolean);
    if (parts.length >= 1 && parts.length <= 20) {
      document.getElementById('min_speakers').value = parts.length;
      document.getElementById('max_speakers').value = parts.length;
    }
  }

  function speakGuide() {
    document.getElementById('prompt_banner').style.display = 'block';
    pywebview.api.speak_intro_prompt();
  }

  function updateLiveLevel(lvl) {
    const badge = document.getElementById('live_level_badge');
    if (!badge || !lvl) return;
    badge.className = 'live-badge ' + (lvl.status || '');
    const stMap = { godt: 'Godt', lavt: 'Lavt!', stille: 'Stille', hoyt: 'Høyt/Klipp!' };
    const stTxt = stMap[lvl.status] || lvl.status;
    badge.textContent = `Lyd: ${lvl.rms_dbfs} dBFS (${stTxt})`;
    badge.title = lvl.label || '';
  }

  function appendLiveChunk(line, lvl) {
    const panel = document.getElementById('live_check_panel');
    if (!panel) return;
    panel.style.display = 'block';
    if (lvl) updateLiveLevel(lvl);
    panel.textContent = (panel.textContent ? panel.textContent + "\n" : "") + line;
    panel.scrollTop = panel.scrollHeight;
  }

  async function runQuickAudioCheck() {
    if (!currentAudioPath) return;
    const panel = document.getElementById('live_check_panel');
    if (panel) {
      panel.style.display = 'block';
      panel.textContent = "Starter grov sanntids-lydsjekk (første 20 sekunder)...";
    }
    await pywebview.api.run_quick_audio_check(
      currentAudioPath,
      document.getElementById('whisper_model').value,
      document.getElementById('language').value,
      document.getElementById('meeting_title').value,
      document.getElementById('speaker_names_hint').value,
      document.getElementById('agenda_text').value
    );
  }

  async function toggleRecording() {
    const btn = document.getElementById('rec_btn');
    const outDir = document.getElementById('out_dir').value;
    if (!isRecording) {
      const autoSpeak = document.getElementById('auto_speak_guide').checked;
      const liveTranscribe = document.getElementById('live_transcribe_chk').checked;
      document.getElementById('prompt_banner').style.display = 'block';
      const panel = document.getElementById('live_check_panel');
      if (panel && liveTranscribe) {
        panel.style.display = 'block';
        panel.textContent = "Lytter på mikrofon (oppdaterer lydnivå hvert sekund og grov transkripsjon hvert 5. sekund)...";
      }
      const res = await pywebview.api.start_recording(
        outDir,
        autoSpeak,
        liveTranscribe,
        document.getElementById('whisper_model').value,
        document.getElementById('language').value,
        document.getElementById('meeting_title').value,
        document.getElementById('speaker_names_hint').value,
        document.getElementById('agenda_text').value
      );
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
      const box = document.getElementById('agenda_text');
      box.value = box.value.trim() ? (box.value.trim() + "\\n\\n" + content) : content;
    }
  }

  async function refreshCheckpointBar() {
    if (!currentAudioPath) return;
    const outDir = document.getElementById('out_dir').value;
    const htmlName = document.getElementById('html_name').value;
    const notesName = document.getElementById('notes_name').value;
    const st = await pywebview.api.get_checkpoint_status(outDir, currentAudioPath, htmlName, notesName);
    const map = [
      ['ckpt_s1', st.s1_asr, '1. ASR'],
      ['ckpt_s2', st.s2_align, '2. Align'],
      ['ckpt_s3', st.s3_diarize, '3. Pyannote'],
      ['ckpt_s4', st.s4_names, '4. Navn & HTML'],
      ['ckpt_s5', st.s5_notes, '5. Referat/Notat'],
    ];
    for (const [id, done, label] of map) {
      const el = document.getElementById(id);
      if (el) {
        el.classList.toggle('done', !!done);
        el.textContent = (done ? "✓ " : "") + label;
      }
    }
  }

  async function applySelection(info) {
    currentAudioPath = info.audio_path;
    document.getElementById('selected-file').textContent = info.audio_path + (info.is_transcript_input ? " (Transkripsjon)" : "");
    if (info.out_dir) document.getElementById('out_dir').value = info.out_dir;
    if (info.html_name) document.getElementById('html_name').value = info.html_name;
    if (info.notes_name) document.getElementById('notes_name').value = info.notes_name;
    if (info.notes_html_name) document.getElementById('notes_html_name').value = info.notes_html_name;
    if (info.is_transcript_input) {
      document.getElementById('task_mode').value = 'auto_resume';
    }
    await refreshCheckpointBar();
    await validateNames();
  }

  async function handleDroppedAudio(fullPath) {
    const low = fullPath.toLowerCase();
    if (low.endsWith('.pptx') || low.endsWith('.pdf') || low.endsWith('.odp') || low.endsWith('.docx') || low.endsWith('.odt')) {
      const ctx = await pywebview.api.load_context_file_path(fullPath);
      if (ctx) {
        const box = document.getElementById('agenda_text');
        box.value = box.value.trim() ? (box.value.trim() + "\\n\\n" + ctx) : ctx;
      }
      return;
    }
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
      await refreshCheckpointBar();
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
    await refreshCheckpointBar();
    await validateNames();
  }

  async function validateNames() {
    const outDir = document.getElementById('out_dir').value;
    const htmlInput = document.getElementById('html_name');
    const notesInput = document.getElementById('notes_name');
    const notesHtmlInput = document.getElementById('notes_html_name');
    const msg = document.getElementById('conflict-msg');
    const btn = document.getElementById('run_btn');
    const checkBtn = document.getElementById('check_audio_btn');
    if (checkBtn) checkBtn.disabled = !currentAudioPath || isJobRunning;

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
      msg.textContent = "Merk: Eksisterende utdatafil oppdateres ved kjøring (eller klikk «Ledig navn»).";
    } else {
      msg.textContent = "";
    }
    btn.disabled = isJobRunning;
    await refreshCheckpointBar();
  }

  function openFinder() {
    pywebview.api.reveal_in_finder(document.getElementById('out_dir').value);
  }

  async function copyLog() {
    const text = document.getElementById('log').textContent;
    try {
      await navigator.clipboard.writeText(text);
      appendLog("==> Logg kopiert til utklippstavlen.");
    } catch (e) {
      appendLog("Merk: Kunne ikke kopiere automatisk – marker teksten i loggen med Cmd+A / Ctrl+A.");
    }
  }

  async function saveLog() {
    const text = document.getElementById('log').textContent;
    const outDir = document.getElementById('out_dir').value;
    await pywebview.api.save_log_dialog(text, outDir, currentAudioPath);
  }

  async function stopJob() {
    if (!isJobRunning) return;
    await pywebview.api.stop_job();
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
    const stopBtn = document.getElementById('stop_btn');
    btn.textContent = running ? "Arbeider..." : "Start / Fortsett";
    stopBtn.disabled = !running;
    validateNames();
  }

  async function runPipeline() {
    await validateNames();
    if (document.getElementById('run_btn').disabled) return;

    const taskMode = document.getElementById('task_mode').value;
    updateProgress(0, "Starter jobb...");
    document.getElementById('log').textContent = "Starter jobb (" + taskMode + ")...";
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
      document.getElementById('agenda_text').value,
      document.getElementById('note_mode').value,
      taskMode
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
        width=820,
        height=790,
    )
    api.window = window
    webview.start(bind_drag_and_drop, window)


if __name__ == "__main__":
    main()
