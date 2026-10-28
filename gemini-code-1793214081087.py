#!/usr/bin/env python3
"""Transcribe and diarize meetings with WhisperX (JSON/HTML) and summarize via Ollama."""

import argparse
from contextlib import contextmanager
import html
import json
import os
from pathlib import Path
import re
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
        description="Transkriber lydfil med tidsstempler, talere, rik HTML og referat."
    )
    parser.add_argument("audio_file", type=Path, help="Sti til lydfil")
    parser.add_argument("-w", "--whisper-model", default="large-v3-turbo", help="Whisper-modell (standard: %(default)s)")
    parser.add_argument("-m", "--ollama-model", default="gemma4:e4b", help="Ollama-modell (standard: %(default)s)")
    parser.add_argument("-l", "--language", default="no", help="Språkkode (standard: %(default)s)")
    parser.add_argument("--min-speakers", type=int, default=None, help="Minimum antall talere")
    parser.add_argument("--max-speakers", type=int, default=None, help="Maksimalt antall talere")
    return parser.parse_args()


def resolve_hf_token() -> str | None:
    if token := os.environ.get("HF_TOKEN", "").strip():
        return token
    if HF_TOKEN_FILE.is_file():
        if token := HF_TOKEN_FILE.read_text(encoding="utf-8").strip():
            return token
    return None


def format_seconds(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


def generate_rich_html(json_path: Path, html_path: Path, title: str) -> str:
    """Parse WhisperX JSON into a styled HTML document and plain-text context for LLM."""
    with open(json_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    
    # Speaker palette
    colors = ["#007AFF", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5856D6"]
    speaker_color_map = {}
    
    llm_lines = []
    html_items = []

    for seg in segments:
        speaker = seg.get("speaker", "Ukjent taler")
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()

        # Sanitize corrupt unicode replacement strings
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
    .text {{
      font-size: 14.5px;
    }}
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


def run_whisperx(
    audio_path: Path,
    model: str,
    language: str,
    hf_token: str | None,
    min_speakers: int | None,
    max_speakers: int | None
) -> Path:
    out_dir = audio_path.parent
    print(f"1/2 Transkriberer og diariserer -> {audio_path.name} ...")
    env = {**os.environ, "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io"}
    cmd = [
        "whisperx", str(audio_path),
        "--output_dir", str(out_dir),
        "--model", model,
        "--language", language,
        "--diarize",
        "--compute_type", "int8",
        "--output_format", "json",
    ]
    if hf_token:
        cmd.extend(["--hf_token", hf_token])
    if min_speakers:
        cmd.extend(["--min_speakers", str(min_speakers)])
    if max_speakers:
        cmd.extend(["--max_speakers", str(max_speakers)])

    subprocess.run(cmd, env=env, check=True)
    return out_dir / f"{audio_path.stem}.json"


def clean_llm_output(raw_output: str) -> str:
    """Strip <think>...</think> reasoning traces and unicode glitched tokens."""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_output, flags=re.DOTALL)
    # Strip broken non-printable unicode control codes
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", cleaned)
    return cleaned.strip()


def generate_notes(clean_transcript: str, notes_path: Path, model: str) -> None:
    print(f"2/2 Genererer møtereferat via {model} -> {notes_path.name} ...")
    prompt = (
        "Du er en assistent som skriver presise møtereferater på norsk basert på transkripsjonen under. "
        "Ikke inkluder tankeprosess eller interne instruksjoner i svaret. "
        "Fokuser kun på substans og hold taleransvar tydelig.\n\n"
        "Inkluder følgende struktur:\n"
        "# Møtereferat\n"
        "## 1. Oppsummering\n"
        "## 2. Hoveddiskusjoner per tema (med henvisning til hvem som sa hva)\n"
        "## 3. Beslutninger\n"
        "## 4. Aksjonspunkter (hvem gjør hva til når)\n\n"
        f"Transkripsjon:\n{clean_transcript}"
    )

    proc = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=True
    )
    final_notes = clean_llm_output(proc.stdout)
    notes_path.write_text(final_notes, encoding="utf-8")


def main() -> int:
    args = parse_args()
    audio_path = args.audio_file.resolve()
    if not audio_path.is_file():
        print(f"Feil: Fant ikke '{audio_path}'", file=sys.stderr)
        return 1

    hf_token = resolve_hf_token()
    json_path = run_whisperx(
        audio_path,
        args.whisper_model,
        args.language,
        hf_token,
        args.min_speakers,
        args.max_speakers
    )

    html_path = audio_path.with_name(f"{audio_path.stem}_transkripsjon.html")
    notes_path = audio_path.with_name(f"{audio_path.stem}_referat.md")

    # Generate styled HTML transcript and prepare clean chronological input for Ollama
    clean_transcript = generate_rich_html(json_path, html_path, audio_path.stem)

    generate_notes(clean_transcript, notes_path, args.ollama_model)

    print(f"\nFerdig!")
    print(f" - HTML transkripsjon: {html_path}")
    print(f" - Møtereferat       : {notes_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())