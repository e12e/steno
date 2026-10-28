#!/usr/bin/env python3
"""Transcribe and diarize meetings with WhisperX and summarize via Ollama."""

import argparse
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transkriber lydfil med taler-ID (WhisperX) og generer møtereferat (Ollama)."
    )
    parser.add_argument(
        "audio_file",
        type=Path,
        help="Sti til lydfil (f.eks. meeting.m4a eller meeting.wav)",
    )
    parser.add_argument(
        "-w", "--whisper-model",
        default="large-v3-turbo",
        help="Whisper-modell (standard: %(default)s)",
    )
    parser.add_argument(
        "-m", "--ollama-model",
        default="gemma4:e4b",
        help="Ollama-modell for referat (standard: %(default)s)",
    )
    parser.add_argument(
        "-l", "--language",
        default="no",
        help="Språkkode for transkripsjon (standard: %(default)s)",
    )
    return parser.parse_args()


def is_diarization_cached() -> bool:
    hub_dir = HF_CACHE_DIR / "hub"
    if not hub_dir.is_dir():
        return False
    return any(
        any(p.glob("snapshots/*/*"))
        for p in hub_dir.glob("models--pyannote--speaker-diarization*")
    )


def prompt_and_save_token() -> str:
    print("HF_TOKEN ble ikke funnet, og pyannote-modellen er ikke lastet ned ennå.")
    token = getpass.getpass("Skriv inn Hugging Face Read-token (hf_...): ").strip()
    if not token:
        sys.exit("Feil: Hugging Face-token kreves for første gangs nedlasting.")

    save = input(f"Lagre token i {HF_TOKEN_FILE} for fremtidig bruk? [J/n]: ").strip().lower()
    if save in ("", "j", "ja", "y", "yes"):
        HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        HF_TOKEN_FILE.write_text(token, encoding="utf-8")
        HF_TOKEN_FILE.chmod(0o600)
        print(f"Token lagret i {HF_TOKEN_FILE}.")
    return token


def resolve_hf_token() -> str | None:
    if token := os.environ.get("HF_TOKEN", "").strip():
        return token
    if HF_TOKEN_FILE.is_file():
        if token := HF_TOKEN_FILE.read_text(encoding="utf-8").strip():
            return token
    if is_diarization_cached():
        return None
    return prompt_and_save_token()


def run_whisperx(audio_path: Path, model: str, language: str, hf_token: str | None) -> Path:
    txt_path = audio_path.with_suffix(".txt")
    print(f"1/2 Transkriberer og identifiserer talere -> {txt_path.name} ...")

    env = {**os.environ, "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io"}
    cmd = [
        "whisperx",
        str(audio_path),
        "--output_dir", str(audio_path.parent),
        "--model", model,
        "--language", language,
        "--diarize",
        "--compute_type", "int8",
        "--output_format", "txt",
    ]
    if hf_token:
        cmd.extend(["--hf_token", hf_token])

    subprocess.run(cmd, env=env, check=True)
    return txt_path


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
def managed_ollama_server() -> Iterator[None]:
    """Start ollama serve if not running; stop it on exit if started by us."""
    if fetch_ollama_models() is not None:
        yield
        return

    print("Starter ollama serve midlertidig...")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_ollama():
            raise RuntimeError("Klarte ikke å starte ollama serve innen tidsfristen.")
        yield
    finally:
        if proc.poll() is None:
            print(f"Stopper midlertidig ollama serve (PID {proc.pid})...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def ensure_ollama_model(model: str) -> None:
    installed = fetch_ollama_models() or []
    if model not in installed and f"{model}:latest" not in installed:
        print(f"Modell '{model}' finnes ikke lokalt. Laster ned med ollama pull...")
        subprocess.run(["ollama", "pull", model], check=True)


def generate_notes(txt_path: Path, notes_path: Path, model: str) -> None:
    print(f"2/2 Genererer møtereferat -> {notes_path.name} ...")
    transcript = txt_path.read_text(encoding="utf-8")
    prompt = (
        "Lag et strukturert møtereferat på norsk basert på denne transkripsjonen med taler-IDer. "
        "Inkluder:\n"
        "1. Kort oppsummering\n"
        "2. Hovedpunkter per tema\n"
        "3. Beslutninger\n"
        "4. Aksjonspunkter\n\n"
        f"Transkripsjon:\n{transcript}"
    )
    with notes_path.open("w", encoding="utf-8") as out_file:
        subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            text=True,
            stdout=out_file,
            check=True,
        )


def main() -> int:
    args = parse_args()
    audio_path = args.audio_file.resolve()

    if not audio_path.is_file():
        print(f"Feil: Fant ikke filen '{audio_path}'", file=sys.stderr)
        return 1

    hf_token = resolve_hf_token()
    txt_path = run_whisperx(audio_path, args.whisper_model, args.language, hf_token)
    notes_path = audio_path.with_name(f"{audio_path.stem}_referat.md")

    with managed_ollama_server():
        ensure_ollama_model(args.ollama_model)
        generate_notes(txt_path, notes_path, args.ollama_model)

    print(f"Ferdig! Filer lagret: {txt_path.name} og {notes_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())