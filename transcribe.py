#!/usr/bin/env python3
"""Transcribe and diarize meetings with WhisperX (Rich HTML) and summarize via Ollama REST API."""

import argparse
from contextlib import contextmanager
import getpass
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from typing import Iterator
import urllib.request

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE}/api/tags"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE}/api/chat"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transkriber lydfil med taler-ID (WhisperX -> HTML) og generer møtereferat (Ollama)."
    )
    parser.add_argument(
        "audio_file",
        type=Path,
        help="Sti til lydfil (f.eks. meeting.m4a eller meeting.wav)",
    )
    parser.add_argument(
        "-w", "--whisper-model",
        default="NbAiLab/nb-whisper-large",
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
    parser.add_argument(
        "--min-speakers",
        type=int,
        default=None,
        help="Minimum antall talere",
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        default=None,
        help="Maksimalt antall talere",
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


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
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


def merge_and_build_html(json_path: Path, html_path: Path, title: str) -> str:
    """Merge consecutive speaker segments, build rich HTML, and return clean LLM transcript."""
    raw_bytes = json_path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    raw_segments = data.get("segments", [])

    merged: list[dict] = []
    last_known_speaker = "SPEAKER_00"

    for seg in raw_segments:
        text = sanitize_text(seg.get("text", ""))
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        speaker = seg.get("speaker") or last_known_speaker
        last_known_speaker = speaker

        if merged and merged[-1]["speaker"] == speaker and (start - merged[-1]["end"]) < 4.0:
            merged[-1]["end"] = end
            merged[-1]["text"] += " " + text
        else:
            merged.append({"speaker": speaker, "start": start, "end": end, "text": text})

    colors = ["#007AFF", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5856D6", "#00C7BE"]
    speaker_color_map: dict[str, str] = {}
    llm_lines: list[str] = []
    html_items: list[str] = []

    for item in merged:
        spk = item["speaker"]
        if spk not in speaker_color_map:
            speaker_color_map[spk] = colors[len(speaker_color_map) % len(colors)]
        color = speaker_color_map[spk]
        t_str = f"{format_seconds(item['start'])} – {format_seconds(item['end'])}"

        llm_lines.append(f"[{t_str}] {spk}: {item['text']}")
        html_items.append(
            f'<div class="turn" data-speaker="{html.escape(spk)}">'
            f'  <div class="meta">'
            f'    <span class="badge" style="background:{color}" data-spk-label="{html.escape(spk)}">{html.escape(spk)}</span>'
            f'    <span class="timestamp">{t_str}</span>'
            f'  </div>'
            f'  <div class="text">{html.escape(item["text"])}</div>'
            f'</div>'
        )

    speaker_inputs = "".join(
        f'<div class="spk-edit">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <input type="text" value="{html.escape(spk)}" data-orig="{html.escape(spk)}" '
        f'         oninput="renameSpeaker(this.dataset.orig, this.value)" title="Endre navn i visningen">'
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
      background: var(--bg); color: var(--text); font-size: 12px; width: 115px; font-weight: 600;
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
    <h1>{html.escape(title)}</h1>
    <div class="controls">
      <input type="text" id="filter" placeholder="Søk i tekst, tidspunkt eller taler..." oninput="filterTurns()">
    </div>
    <div class="speakers-bar">
      <span style="color:var(--sub)">Gi talere navn:</span>
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


def run_whisperx_and_build_html(
    audio_path: Path,
    html_path: Path,
    model: str,
    language: str,
    hf_token: str | None,
    min_speakers: int | None,
    max_speakers: int | None,
    env: dict,
) -> str:
    print(f"1/2 Transkriberer og identifiserer talere ({model}) -> {html_path.name} ...")
    with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
        cmd = [
            "whisperx",
            str(audio_path),
            "--output_dir", tmp_dir,
            "--model", model,
            "--language", language,
            "--diarize",
            "--compute_type", "int8",
            "--output_format", "json",
            "--print_progress", "True",
        ]
        if hf_token:
            cmd.extend(["--hf_token", hf_token])
        if min_speakers is not None:
            cmd.extend(["--min_speakers", str(min_speakers)])
        if max_speakers is not None:
            cmd.extend(["--max_speakers", str(max_speakers)])

        subprocess.run(cmd, env=env, check=True)
        raw_json = Path(tmp_dir) / f"{audio_path.stem}.json"
        if not raw_json.is_file():
            raise RuntimeError("Fant ikke JSON-utdata fra WhisperX.")
        return merge_and_build_html(raw_json, html_path, audio_path.stem)


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
def managed_ollama_server(env: dict | None = None) -> Iterator[None]:
    """Start ollama serve if not running; stop it on exit if started by us."""
    if fetch_ollama_models() is not None:
        yield
        return

    print("Starter ollama serve midlertidig...")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
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


def ensure_ollama_model(model: str, env: dict) -> None:
    installed = fetch_ollama_models() or []
    if model not in installed and f"{model}:latest" not in installed:
        print(f"Modell '{model}' finnes ikke lokalt. Laster ned med ollama pull...")
        subprocess.run(["ollama", "pull", model], env=env, check=True)


def generate_notes(transcript: str, notes_path: Path, model: str) -> None:
    print(f"2/2 Genererer møtereferat via {model} -> {notes_path.name} ...")
    system_prompt = (
        "Du er en profesjonell norsk møtesekretær. Du skriver kun selve møtereferatet i ren Markdown. "
        "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
        "Bruk korrekt norsk (bokmål) og organiser innholdet tematisk, ikke bare kronologisk."
    )
    user_prompt = (
        "Lag et grundig og strukturert møtereferat basert på den tidsstemplede transkripsjonen under.\n\n"
        "Bruk denne strukturen:\n"
        "# Møtereferat\n\n"
        "## 1. Kort oppsummering\n"
        "(Hva handlet møtet om, og hva var hovedkonklusjonen?)\n\n"
        "## 2. Deltakere / Talere\n"
        "(Kort beskrivelse av rollene/bidragene til hver taler-ID basert på konteksten)\n\n"
        "## 3. Hovedtemaer diskutert\n"
        "(Sortert etter tema med underoverskrifter, hovedargumenter og tidsstempler der det er relevant)\n\n"
        "## 4. Beslutninger\n"
        "(Konkrete vedtak eller enigheter)\n\n"
        "## 5. Aksjonspunkter\n"
        "(Tabell eller sjekkliste med Hvem / Oppgave / Frist)\n\n"
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
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw_line in resp:
            if not raw_line.strip():
                continue
            body = json.loads(raw_line.decode("utf-8", errors="replace"))
            piece = body.get("message", {}).get("content", "")
            if piece:
                chunks.append(piece)
                sys.stdout.write(".")
                sys.stdout.flush()
            if body.get("done"):
                break
    print()
    notes_md = clean_llm_output("".join(chunks))
    notes_path.write_text(notes_md + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    audio_path = args.audio_file.resolve()

    if not audio_path.is_file():
        print(f"Feil: Fant ikke filen '{audio_path}'", file=sys.stderr)
        return 1

    env = {
        **os.environ,
        "PYTHONWARNINGS": "ignore::UserWarning:pyannote.audio.core.io",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "LC_ALL": "en_US.UTF-8",
    }
    env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"

    hf_token = resolve_hf_token()
    html_path = unique_path(audio_path.parent, f"{audio_path.stem}_transkripsjon", ".html")
    notes_path = unique_path(audio_path.parent, f"{audio_path.stem}_referat", ".md")

    clean_transcript = run_whisperx_and_build_html(
        audio_path,
        html_path,
        args.whisper_model,
        args.language,
        hf_token,
        args.min_speakers,
        args.max_speakers,
        env,
    )

    with managed_ollama_server(env=env):
        ensure_ollama_model(args.ollama_model, env=env)
        generate_notes(clean_transcript, notes_path, args.ollama_model)

    print(f"Ferdig!\n - HTML-transkripsjon: {html_path}\n - Møtereferat       : {notes_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
