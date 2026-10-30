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
# Cross-platform single-file GUI for WhisperX + Ollama meeting transcription.
from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python mote_gui.py' without uv, auto-install uv and re-exec."""
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


def ensure_ffmpeg(env: dict[str, str], log_fn) -> None:
    """Ensure ffmpeg and ffprobe are available on PATH, using static-ffmpeg if missing."""
    if shutil.which("ffmpeg", path=env.get("PATH")):
        return
    log_fn("==> Laster ned innebygd FFmpeg for lydbehandling...")
    static_ffmpeg.add_paths(weak=True)
    env["PATH"] = os.environ.get("PATH", "") + os.pathsep + env.get("PATH", "")


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

    # Query uv tool bin dir in case it differs from ~/.local/bin
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
        # Prefer winget if available, otherwise download zip
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
    log_fn,
    agenda_text: str = "",
) -> dict[str, str]:
    """Use Ollama to detect self-introductions and map SPEAKER_XX -> Real Name."""
    unique_speakers = sorted({t["speaker"] for t in turns})
    if not unique_speakers:
        return {}

    intro_keywords = ("heter", "jeg er", "mitt navn", "fra ", "representerer", "leder", "velkommen")
    sample_lines: list[str] = []

    for idx, t in enumerate(turns):
        low = t["text"].lower()
        if idx < 100 or any(k in low for k in intro_keywords):
            t_str = f"{format_seconds(t['start'])}–{format_seconds(t['end'])}"
            sample_lines.append(f"[{t_str}] {t['speaker']}: {t['text']}")

    hint_block = (
        f"Kjente deltakernavn oppgitt av bruker: {known_names_hint.strip()}\n"
        if known_names_hint.strip()
        else "Ingen forhåndsoppgitte navn (finn navn ut fra transkripsjonen hvis folk presenterer seg).\n"
    )
    if agenda_text.strip():
        hint_block += f"Agenda / lysbilder / kontekst:\n{agenda_text.strip()[:1200]}\n"

    prompt = (
        "Analyser utdraget fra møtetranskripsjonen under og finn ut hvem de ulike taler-IDene "
        f"({', '.join(unique_speakers)}) er, basert på at deltakerne presenterer seg (f.eks. «Jeg heter...», "
        "«Mitt navn er...») eller tiltaler hverandre ved navn.\n\n"
        f"{hint_block}\n"
        "Regler:\n"
        "1. Returner KUN et gyldig JSON-objekt som knytter taler-ID til navn (og gjerne rolle i parentes hvis nevnt).\n"
        "2. Hvis du IKKE finner navnet til en taler-ID med høy sikkerhet, behold original ID eller skriv f.eks. "
        '{"SPEAKER_00": "SPEAKER_00"}. Ikke gjett tilfeldige navn.\n'
        '3. Eksempel på format: {"SPEAKER_00": "Ola Nordmann", "SPEAKER_01": "Kari Hansen"}\n\n'
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
    progress_fn=None,
    note_mode: str = "mote",
    agenda_text: str = "",
) -> str:
    """Use Ollama HTTP /api/chat directly with streaming to avoid ANSI codes, thinking leaks, and UI freezes."""
    detected_list = ", ".join(sorted(set(speaker_map.values())))
    hint_line = (
        f"Bruker har oppgitt følgende personer/roller: {known_names_hint.strip()}.\n"
        if known_names_hint.strip()
        else ""
    )
    agenda_block = (
        f"\nAgenda / lysbilder / ekstra kontekst:\n{agenda_text.strip()}\n"
        if agenda_text.strip()
        else ""
    )

    if note_mode == "foredrag":
        system_prompt = (
            "Du er en dyktig norsk fagredaktør og notatskriver. Du skriver kun selve oppsummeringen i ren Markdown. "
            "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
            "Opptaket er et foredrag, en presentasjon eller en monolog (ikke et møte med vedtak og aksjonstabell). "
            "Bruk korrekt norsk (bokmål) og trekk ut de viktigste innsiktene, hovedpoengene, faglige sammenhengene og konkrete læringspunktene."
        )
        user_prompt = (
            "Lag et grundig og strukturert faglig sammendrag av foredraget/presentasjonen basert på den tidsstemplede transkripsjonen under.\n"
            f"Identifiserte stemmer i opptaket: {detected_list}\n"
            f"{hint_line}{agenda_block}\n"
            "Bruk denne strukturen:\n"
            "# Foredragsnotater og hovedpoenger\n\n"
            "## 1. Oversikt og hovedbudskap\n"
            "(Foredragsholder hvis kjent, tema og kjernebudskapet oppsummert kort)\n\n"
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
        system_prompt = (
            "Du er en profesjonell norsk møtesekretær. Du skriver kun selve møtereferatet i ren Markdown. "
            "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
            "Bruk korrekt norsk (bokmål) og organiser innholdet tematisk, ikke bare kronologisk. "
            "Hvis deltakere presenterer seg med navn i transkripsjonen, bruk alltid deres faktiske navn i referatet."
        )
        user_prompt = (
            "Lag et grundig og strukturert møtereferat basert på den tidsstemplede transkripsjonen under.\n"
            f"Identifiserte talere i transkripsjonen: {detected_list}\n"
            f"{hint_line}{agenda_block}\n"
            "Bruk denne strukturen:\n"
            "# Møtereferat\n\n"
            "## 1. Kort oppsummering\n"
            "(Hva handlet møtet om, og hva var hovedkonklusjonen?)\n\n"
            "## 2. Deltakere og roller\n"
            "(Liste over deltakerne med navn — fang opp navn der folk presenterer seg — samt deres rolle og hovedbidrag)\n\n"
            "## 3. Hovedtemaer diskutert\n"
            "(Sortert etter tema med underoverskrifter, hvem som fremmet hvilke synspunkter, og tidsstempler der det er relevant)\n\n"
            "## 4. Beslutninger\n"
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
                    progress_fn(pct, f"[Steg 5/5] Skriver notater/referat ({token_count} tokens)...")
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


TRANSCRIPT_EXTS = {".html", ".htm", ".json", ".txt", ".md", ".srt", ".vtt"}


def is_transcript_file(path: Path) -> bool:
    return path.suffix.lower() in TRANSCRIPT_EXTS


def clean_base_stem(path: Path) -> str:
    stem = path.stem
    for suffix in ("_transkripsjon", "_referat", "_notater"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)]
    return stem


def get_checkpoint_dir(out_dir: Path, input_path: Path) -> Path:
    return out_dir / ".mote_checkpoints" / clean_base_stem(input_path)


def parse_time_str(ts: str) -> float:
    parts = [p.strip() for p in ts.replace(",", ".").split(":")]
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def load_turns_from_transcript_file(path: Path) -> tuple[list[dict], dict[str, str]]:
    """Parse an existing transcript file (.html, .json, .txt, .md, .srt, .vtt) into (turns, speaker_map)."""
    ext = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace")

    if ext == ".json":
        data = json.loads(raw)
        if isinstance(data, dict) and "turns" in data and isinstance(data["turns"], list):
            smap = data.get("speaker_map", {})
            return data["turns"], (smap if isinstance(smap, dict) else {})
        turns = extract_turns_from_whisperx(path)
        smap = {t["speaker"]: t["speaker"] for t in turns}
        return turns, smap

    if ext in (".html", ".htm"):
        turns: list[dict] = []
        speaker_map: dict[str, str] = {}
        pattern = re.compile(
            r'<div\s+class="turn"\s+data-speaker="([^"]*)"[^>]*>.*?'
            r'<span\s+class="badge"[^>]*>(.*?)</span>\s*'
            r'<span\s+class="timestamp">(.*?)</span>.*?'
            r'<div\s+class="text">(.*?)</div>',
            flags=re.DOTALL,
        )
        for m in pattern.finditer(raw):
            orig_spk = html.unescape(m.group(1)).strip() or "SPEAKER_00"
            disp_spk = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip() or orig_spk
            ts_range = html.unescape(m.group(3)).strip()
            text_val = html.unescape(re.sub(r"<[^>]+>", "", m.group(4))).strip()
            t_parts = re.split(r"[\-–—]+", ts_range)
            start_s = parse_time_str(t_parts[0]) if t_parts else 0.0
            end_s = parse_time_str(t_parts[1]) if len(t_parts) > 1 else start_s
            speaker_map[orig_spk] = disp_spk
            if text_val:
                turns.append({"speaker": orig_spk, "start": start_s, "end": end_s, "text": text_val})
        if turns:
            return turns, speaker_map
        raw = html.unescape(re.sub(r"<[^>]+>", "\n", raw))

    if ext in (".srt", ".vtt"):
        turns = []
        blocks = re.split(r"\n\s*\n", raw.strip())
        for b in blocks:
            b_lines = [ln.strip() for ln in b.splitlines() if ln.strip() and ln.strip() != "WEBVTT"]
            if not b_lines:
                continue
            ts_idx = -1
            for i, ln in enumerate(b_lines):
                if "-->" in ln:
                    ts_idx = i
                    break
            if ts_idx >= 0 and ts_idx + 1 < len(b_lines):
                ts_parts = b_lines[ts_idx].split("-->")
                start_s = parse_time_str(ts_parts[0])
                end_s = parse_time_str(ts_parts[1].split()[0]) if len(ts_parts) > 1 else start_s
                body_text = " ".join(b_lines[ts_idx + 1 :]).strip()
                spk = "SPEAKER_00"
                m_spk = re.match(r"^(?:\[([^\]]+)\]|([^:]{2,35}):)\s*(.+)$", body_text)
                if m_spk:
                    spk = (m_spk.group(1) or m_spk.group(2)).strip()
                    body_text = m_spk.group(3).strip()
                if body_text:
                    turns.append({"speaker": spk, "start": start_s, "end": end_s, "text": body_text})
        if turns:
            return turns, {t["speaker"]: t["speaker"] for t in turns}

    turns = []
    speaker_map = {}
    line_re = re.compile(
        r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[\-–—]+\s*(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):\s*(.+)$"
    )
    simple_re = re.compile(r"^([A-ZÆØÅa-zæøå0-9_\-\s\(\),]{2,45}):\s+(.+)$")
    cur_time = 0.0
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            continue
        m1 = line_re.match(s)
        if m1:
            start_s = parse_time_str(m1.group(1))
            end_s = parse_time_str(m1.group(2))
            spk = m1.group(3).strip()
            txt = m1.group(4).strip()
            turns.append({"speaker": spk, "start": start_s, "end": end_s, "text": txt})
            speaker_map[spk] = spk
            cur_time = end_s
            continue
        m2 = simple_re.match(s)
        if m2 and not s.startswith("#") and not s.startswith("http"):
            spk = m2.group(1).strip()
            txt = m2.group(2).strip()
            turns.append({"speaker": spk, "start": cur_time, "end": cur_time + 5.0, "text": txt})
            speaker_map[spk] = spk
            cur_time += 5.0

    if not turns and raw.strip():
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw.strip()) if p.strip()]
        for idx, p in enumerate(paragraphs):
            turns.append({
                "speaker": "SPEAKER_00",
                "start": float(idx * 10),
                "end": float((idx + 1) * 10),
                "text": re.sub(r"\s+", " ", p),
            })
        speaker_map["SPEAKER_00"] = "SPEAKER_00"

    return turns, speaker_map


def extract_context_from_file(path: Path) -> str:
    """Extract text from agenda, slides (.pptx, .odp, .pdf), or documents (.docx, .odt, .md, .txt, .html)."""
    import xml.etree.ElementTree as ET
    import zlib

    ext = path.suffix.lower()
    try:
        if ext == ".pptx":
            slides_out: list[str] = []
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                slide_files = sorted(
                    [n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                    key=lambda x: int(re.findall(r"\d+", x)[0]),
                )
                for idx, s_file in enumerate(slide_files, start=1):
                    xml_bytes = zf.read(s_file)
                    root = ET.fromstring(xml_bytes)
                    texts = [el.text.strip() for el in root.iter() if el.tag.endswith("}t") and el.text and el.text.strip()]
                    note_file = f"ppt/notesSlides/notesSlide{idx}.xml"
                    note_texts: list[str] = []
                    if note_file in names:
                        n_root = ET.fromstring(zf.read(note_file))
                        note_texts = [
                            el.text.strip()
                            for el in n_root.iter()
                            if el.tag.endswith("}t") and el.text and el.text.strip()
                        ]
                    if texts or note_texts:
                        block = f"[Lysbilde {idx}] " + " | ".join(texts)
                        if note_texts:
                            block += "\n  Forelesernotat: " + " ".join(note_texts)
                        slides_out.append(block)
            return f"=== Lysbilder fra {path.name} ===\n" + "\n".join(slides_out)

        if ext in (".docx", ".odt", ".odp"):
            with zipfile.ZipFile(path, "r") as zf:
                target_xml = "word/document.xml" if ext == ".docx" else "content.xml"
                if target_xml in zf.namelist():
                    root = ET.fromstring(zf.read(target_xml))
                    texts = [el.text.strip() for el in root.iter() if el.text and el.text.strip()]
                    return f"=== Dokument ({path.name}) ===\n" + "\n".join(texts)

        if ext == ".pdf":
            for cmd in (
                ["pdftotext", str(path), "-"],
                ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
            ):
                if shutil.which(cmd[0]):
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    out = res.stdout.strip()
                    if out and out != "(null)" and len(out) > 20:
                        return f"=== PDF ({path.name}) ===\n{out}"
            raw_bytes = path.read_bytes()
            extracted_chunks: list[str] = []
            for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw_bytes, flags=re.DOTALL):
                stream_data = m.group(1)
                try:
                    decomp = zlib.decompress(stream_data)
                except Exception:
                    decomp = stream_data
                for tm in re.finditer(rb"\(([^()]{2,200})\)", decomp):
                    s = tm.group(1).decode("latin-1", errors="ignore")
                    s = re.sub(r"\\[nrtbf()\\]", " ", s).strip()
                    if len(s) >= 2 and any(c.isalpha() for c in s):
                        extracted_chunks.append(s)
            if extracted_chunks:
                return f"=== PDF ({path.name}) ===\n" + " ".join(extracted_chunks)

        if ext in (".html", ".htm"):
            raw_html = path.read_text(encoding="utf-8", errors="replace")
            cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
            cleaned = html.unescape(re.sub(r"<[^>]+>", " ", cleaned))
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return f"=== {path.name} ===\n{cleaned}"

        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        return f"[Kunne ikke lese {path.name}: {exc}]"


def build_whisper_initial_prompt(
    speaker_names_hint: str,
    agenda_text: str,
) -> str:
    """Build a concise domain/vocabulary prompt (up to ~750 chars) for Whisper ASR."""
    parts: list[str] = []
    if speaker_names_hint.strip():
        parts.append(f"Deltakere: {speaker_names_hint.strip()}.")
    if agenda_text.strip():
        compact_ctx = re.sub(r"\s+", " ", agenda_text.strip())
        parts.append(compact_ctx[:600])
    return " ".join(parts).strip()[:750]


def get_whisperx_python(whisperx_bin: str) -> str:
    """Locate the Python interpreter inside the WhisperX tool virtualenv."""
    bin_path = Path(whisperx_bin)
    if bin_path.is_file():
        for cand in (bin_path.parent / "python3", bin_path.parent / "python", bin_path.parent / "python.exe"):
            if cand.is_file():
                return str(cand)
        try:
            first_line = bin_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            if first_line.startswith("#!"):
                interp = first_line[2:].strip().split()[0]
                if Path(interp).is_file():
                    return interp
        except Exception:
            pass
    return sys.executable


WHISPERX_STAGE_RUNNER_CODE = """\
import gc
import json
import os
from pathlib import Path
import sys
import torch
import whisperx
from whisperx.audio import load_audio, SAMPLE_RATE
from whisperx.diarize import DiarizationPipeline, assign_word_speakers

def save_atomic_json(path: Path, payload: dict) -> None:
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
        self._active_proc: subprocess.Popen | None = None
        self._cancel_requested: bool = False
        self._log_lines: list[str] = []

    def get_defaults(self) -> dict:
        base_dir = Path(__file__).resolve().parent
        default_dir = base_dir / "data" if (base_dir / "data").is_dir() else (Path.home() / "Downloads")
        return {
            "hf_token": get_initial_hf_token(),
            "diarization_cached": is_diarization_cached(),
            "default_out_dir": str(default_dir),
        }

    def stop_job(self) -> dict:
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

    def suggest_output_names(self, audio_path_str: str, out_dir_str: str) -> dict:
        base_dir = Path(__file__).resolve().parent
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
        note_mode: str = "auto",
        agenda_text: str = "",
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
                note_mode,
                agenda_text,
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
        note_mode: str = "auto",
        agenda_text: str = "",
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
                self._log(f"FEIL: Fant ikke filen {audio_path}")
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
                run_s2, run_s3, run_s4, run_s5 = True, False, False, False
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
                self._log(f"==> Merk: {audio_path.name} er allerede en transkripsjonsfil. Lydsteg 1–3 hoppes over.")
                run_s1 = run_s2 = run_s3 = False
                if task_mode in ("only_1_asr", "only_2_align", "only_3_diarize"):
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
                initial_prompt = build_whisper_initial_prompt(speaker_names_hint, agenda_text)

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

            doc_title = clean_base_stem(audio_path)

            with managed_ollama_server(self._log, self._set_progress, env=env) as ollama_bin:
                installed = fetch_ollama_models() or []
                if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
                    self._set_progress(75, f"Laster ned {ollama_model}...")
                    self._log(f"==> Laster ned '{ollama_model}'...")
                    subprocess.run([ollama_bin, "pull", ollama_model], env=env, check=True)

                if self._cancel_requested:
                    return

                if run_s4:
                    self._set_progress(78, "[Steg 4/5] Gjenkjenner deltakernavn fra introduksjoner...")
                    self._log("==> [Steg 4/5] Analyserer introduksjoner og kontekst for å knytte taler-ID til navn...")
                    inferred_map = infer_speaker_names_via_api(
                        turns, ollama_model, speaker_names_hint, self._log, agenda_text=agenda_text
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
                self._log(f"==> [Steg 5/5] Genererer {mode_desc} via Ollama REST API ({ollama_model})...")

                notes_md = generate_notes_via_api(
                    clean_transcript,
                    ollama_model,
                    speaker_map,
                    speaker_names_hint,
                    progress_fn=self._set_progress,
                    note_mode=effective_mode,
                    agenda_text=agenda_text,
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
    margin: 0; padding: 12px 14px;
    background: Canvas; color: CanvasText;
    font-size: 12.5px; user-select: none;
    display: flex; flex-direction: column; gap: 6px; height: calc(100vh - 24px);
  }
  #dropzone {
    border: 2px dashed GrayText; border-radius: 10px;
    padding: 8px 14px; display: flex; align-items: center; justify-content: space-between;
    gap: 12px; transition: all 0.15s ease;
  }
  #dropzone.dragover { border-color: #0a84ff; background: rgba(10, 132, 255, 0.1); }
  .dz-text { flex: 1; overflow: hidden; }
  .dz-text strong { font-size: 12.5px; display: block; margin-bottom: 2px; }
  #selected-file {
    color: GrayText; display: block; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; font-size: 11.5px;
  }
  .row { display: flex; gap: 7px; align-items: center; }
  .row label { width: 115px; flex-shrink: 0; font-weight: 500; font-size: 12px; }
  input[type="text"], input[type="password"], input[type="number"], select, textarea {
    flex: 1; padding: 4px 8px; border-radius: 6px;
    border: 1px solid rgba(128,128,128,0.4); background: Field; color: FieldText;
    font-size: 12px; font-family: inherit;
  }
  textarea { resize: vertical; min-height: 40px; max-height: 95px; }
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

  .progress-section { display: flex; flex-direction: column; gap: 3px; }
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
    border: 1px solid rgba(128,128,128,0.25);
  }
</style>
</head>
<body>

  <div id="dropzone">
    <div class="dz-text">
      <strong>Slipp lydfil (.m4a, .wav, .opus, .mp3) eller transkripsjon (.html, .json, .txt, .srt)</strong>
      <span id="selected-file">Ingen lyd- eller transkripsjonsfil valgt ennå</span>
    </div>
    <button onclick="browseAudio()">Velg fil...</button>
  </div>

  <div class="row">
    <label>Mappe:</label>
    <input type="text" id="out_dir" placeholder="Velg mappe..." oninput="validateNames()">
    <button onclick="browseFolder()">Velg...</button>
    <button onclick="openFinder()" title="Vis i filutforsker">Åpne</button>
  </div>

  <div class="row">
    <label>Utdatafiler:</label>
    <input type="text" id="html_name" placeholder="mote_transkripsjon.html" oninput="validateNames()" title="HTML Transkripsjon">
    <input type="text" id="notes_name" placeholder="mote_referat.md" oninput="validateNames()" title="Markdown Referat">
    <input type="text" id="notes_html_name" placeholder="mote_referat.html" oninput="validateNames()" title="HTML Referat">
    <button onclick="autoIncrementNames()">Ledig navn</button>
  </div>

  <div class="row">
    <label>Modeller &HF:</label>
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
    <label>Talere & Modus:</label>
    <input type="number" id="min_speakers" placeholder="Min" min="1" max="20" style="max-width: 54px;">
    <input type="number" id="max_speakers" placeholder="Maks" min="1" max="20" style="max-width: 54px;">
    <button onclick="setExactSpeakers(1)" title="1 taler – Foredrag / Presentasjon">1</button>
    <button onclick="setExactSpeakers(2)" title="Lås til nøyaktig 2 talere">2</button>
    <button onclick="setExactSpeakers(3)" title="Lås til nøyaktig 3 talere">3</button>
    <button onclick="setExactSpeakers(4)" title="Lås til nøyaktig 4 talere">4</button>
    <button onclick="setExactSpeakers(5)" title="Lås til nøyaktig 5 talere">5</button>
    <select id="note_mode" onchange="onModeChanged()" title="Velg eller overstyr modus før du genererer transkripsjon og notater">
      <option value="auto" selected>Modus: Auto (Foredrag ved 1 taler, ellers Møte)</option>
      <option value="mote">Modus: Møtereferat (flere deltakere & aksjonspunkter)</option>
      <option value="foredrag">Modus: Foredrag / Presentasjon (faglige hovedpoenger)</option>
    </select>
  </div>

  <div class="row">
    <label>Deltakernavn:</label>
    <input type="text" id="speaker_names_hint" placeholder="Valgfritt: f.eks. Ola, Kari, Per, Anne (hjelper AI å koble stemme til navn)">
    <button onclick="loadAgendaFile()" title="Last inn agenda, lysbilder (.pptx, .pdf, .odp) eller notater (.docx, .md, .txt)">+ Agenda / Lysbilder...</button>
  </div>

  <div class="row" style="align-items: flex-start;">
    <label style="padding-top: 4px;">Agenda / Lysbilder:</label>
    <textarea id="agenda_text" placeholder="Valgfritt: Lim inn saksliste/kontekst, eller klikk «+ Agenda / Lysbilder...» (.pptx, .pdf, .docx, .md)..."></textarea>
  </div>

  <div class="row">
    <label>Sjekkpunkt & Steg:</label>
    <div class="ckpt-bar" id="ckpt_bar">
      <span class="ckpt-pill" id="ckpt_s1" onclick="selectTaskMode('only_1_asr')">1. ASR</span>
      <span class="ckpt-pill" id="ckpt_s2" onclick="selectTaskMode('only_2_align')">2. Align</span>
      <span class="ckpt-pill" id="ckpt_s3" onclick="selectTaskMode('from_3_diarize')">3. Pyannote</span>
      <span class="ckpt-pill" id="ckpt_s4" onclick="selectTaskMode('from_4_names')">4. Navn & HTML</span>
      <span class="ckpt-pill" id="ckpt_s5" onclick="selectTaskMode('only_5_notes')">5. Referat/Notat</span>
    </div>
    <select id="task_mode" onchange="validateNames()">
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
    <button onclick="copyLog()" title="Kopier hele loggen til utklippstavlen">Kopier logg</button>
    <button onclick="saveLog()" title="Lagre loggen til en .txt-fil">Lagre logg...</button>
    <button class="danger" id="stop_btn" onclick="stopJob()" disabled>Stopp / Pause</button>
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

  <div id="log">Klar. Dra inn en lydfil eller en eksisterende transkripsjonsfil for å begynne.</div>

<script>
  let currentAudioPath = "";
  let isJobRunning = false;

  window.addEventListener('pywebviewready', async () => {
    const defs = await pywebview.api.get_defaults();
    if (defs.hf_token) document.getElementById('hf_token').value = defs.hf_token;
    if (defs.diarization_cached && !defs.hf_token) {
      document.getElementById('hf_token').placeholder = "HF bufret lokalt";
    }
    document.getElementById('out_dir').value = defs.default_out_dir;
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
      document.getElementById('note_mode').value,
      document.getElementById('agenda_text').value,
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
        "Møtereferat (WhisperX + Ollama)",
        html=HTML,
        js_api=api,
        width=780,
        height=740,
    )
    api.window = window
    webview.start(bind_drag_and_drop, window)


if __name__ == "__main__":
    main()
