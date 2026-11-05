#!/usr/bin/env python3
"""Core engine, helpers (with doctests), and CLI pipeline for local meeting transcription and notes."""

from __future__ import annotations

import array
import getpass
import html
import json
import math
import os
import platform
import re
import shutil
import stat
import struct
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

from mote.template_loader import load_template

APP_SUPPORT_DIR = Path.home() / ".local" / "share" / "mote_transcribe"
LOCAL_BIN_DIR = APP_SUPPORT_DIR / "bin"
OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE}/api/tags"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE}/api/chat"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"

TRANSCRIPT_EXTENSIONS: frozenset[str] = frozenset(
    {".html", ".htm", ".json", ".txt", ".md", ".srt", ".vtt"}
)
CONTEXT_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {".pptx", ".pdf", ".odp", ".docx", ".odt", ".md", ".txt", ".html", ".json", ".csv"}
)
VALID_TASK_MODES: tuple[str, ...] = (
    "auto_resume",
    "full_fresh",
    "only_1_asr",
    "only_2_align",
    "only_3_diarize",
    "from_3_diarize",
    "from_4_names",
    "only_4_names",
    "only_5_notes",
)
VALID_NOTE_MODES: tuple[str, ...] = ("auto", "mote", "foredrag")


class TurnDict(TypedDict):
    speaker: str
    start: float
    end: float
    text: str


class PipelineResult(TypedDict):
    ok: bool
    input_path: Path
    out_dir: Path
    ckpt_dir: Path
    html_path: Path
    notes_path: Path
    notes_html_path: Path
    executed_stages: list[str]
    effective_mode: str
    speaker_map: dict[str, str]
    turn_count: int


def format_seconds(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS.

    >>> format_seconds(0)
    '00:00'
    >>> format_seconds(65.9)
    '01:05'
    >>> format_seconds(3661)
    '01:01:01'
    >>> format_seconds(-10)
    '00:00'
    """
    total = max(0, int(seconds))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def parse_time_str(t_str: str) -> float:
    """Parse MM:SS, HH:MM:SS, or SRT/VTT timestamps into float seconds.

    >>> parse_time_str("01:05")
    65.0
    >>> parse_time_str("01:01:01.500")
    3661.5
    >>> parse_time_str("00:02:10,250")
    130.25
    >>> parse_time_str("invalid")
    0.0
    """
    cleaned = t_str.strip().replace(",", ".")
    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def sanitize_text(text: str) -> str:
    """Normalize Unicode (NFC), strip ANSI codes, replacement chars, and control bytes.

    >>> sanitize_text("  Hei\\x1b[31m verden\\x1b[0m!\\ufffd  ")
    'Hei verden!'
    >>> sanitize_text("M\\u00f8te\\x00\\x07")
    'Møte'
    """
    norm = unicodedata.normalize("NFC", text)
    norm = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", norm)
    norm = norm.replace("\ufffd", "")
    norm = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", norm)
    return norm.strip()


def clean_llm_output(raw_output: str) -> str:
    """Strip reasoning/thinking blocks from Gemma 4, DeepSeek, Qwen, etc.

    >>> clean_llm_output("<think>Internal reasoning...</think>\\n# Møtereferat\\nInnhold")
    '# Møtereferat\\nInnhold'
    >>> clean_llm_output("<|thought|>tenker...<|/thought|>Resultat")
    'Resultat'
    """
    cleaned = sanitize_text(raw_output)
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thought>[\s\S]*?</thought>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"<\|thought\|>[\s\S]*?(?:<\|/thought\|>|<\|end_thought\|>)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    if "<think>" in cleaned.lower() and "# " in cleaned:
        idx = cleaned.find("# ")
        cleaned = cleaned[idx:]
    return cleaned.strip()


def is_transcript_file(path: Path | str) -> bool:
    """Return True if path has a known transcript file extension.

    >>> is_transcript_file(Path("mote_transkripsjon.html"))
    True
    >>> is_transcript_file("undertekst.srt")
    True
    >>> is_transcript_file("opptak.wav")
    False
    >>> is_transcript_file("opptak.opus")
    False
    """
    p = Path(path) if isinstance(path, str) else path
    return p.suffix.lower() in TRANSCRIPT_EXTENSIONS


def clean_base_stem(path: Path | str) -> str:
    """Strip output or checkpoint suffixes from a file stem to get the canonical meeting stem.

    >>> clean_base_stem(Path("20261027_møte_transkripsjon.html"))
    '20261027_møte'
    >>> clean_base_stem("prosjekt_referat.md")
    'prosjekt'
    >>> clean_base_stem("opptak.wav")
    'opptak'
    """
    p = Path(path) if isinstance(path, str) else path
    stem = unicodedata.normalize("NFC", p.stem)
    for suf in (
        "_transkripsjon",
        "_referat",
        "_notater",
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
    """Return the per-recording checkpoint directory `<out_dir>/.mote_checkpoints/<clean_stem>`.

    >>> get_checkpoint_dir(Path("/tmp/out"), Path("/tmp/out/mote_transkripsjon.html")).as_posix()
    '/tmp/out/.mote_checkpoints/mote'
    """
    stem = (
        clean_base_stem(input_or_stem)
        if isinstance(input_or_stem, Path)
        else clean_base_stem(Path(input_or_stem))
    )
    return out_dir / ".mote_checkpoints" / stem


def find_wav_data_offset(header_bytes: bytes) -> int:
    """Find the byte offset where raw PCM samples begin in a WAV header.

    >>> hdr = b"RIFF" + b"\\x00" * 4 + b"WAVEfmt " + b"\\x10\\x00\\x00\\x00" + b"\\x00" * 16 + b"data" + b"\\x00" * 4
    >>> find_wav_data_offset(hdr)
    44
    >>> find_wav_data_offset(b"not a wav")
    44
    """
    idx = header_bytes.find(b"data")
    if idx != -1 and idx + 8 <= len(header_bytes):
        return idx + 8
    return 44


def build_wav_bytes_from_pcm16(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw 16-bit mono little-endian PCM bytes in a 44-byte RIFF/WAVE header.

    >>> wav = build_wav_bytes_from_pcm16(b"\\x00\\x00" * 160, 16000)
    >>> (wav[:4], wav[8:12], len(wav))
    (b'RIFF', b'WAVE', 364)
    """
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    data_size = len(pcm_bytes)
    byte_rate = sample_rate * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        byte_rate,
        2,
        16,
        b"data",
        data_size,
    )
    return header + pcm_bytes


def analyze_pcm16_audio_level(pcm_bytes: bytes, sample_rate: int = 16000) -> dict[str, Any]:
    """Analyze 16-bit mono PCM audio level (RMS dBFS, peak dBFS, and microphone health status).

    >>> silent = analyze_pcm16_audio_level(b"\\x00\\x00" * 1600, 16000)
    >>> (silent["status"], silent["rms_dbfs"])
    ('stille', -96.0)
    >>> good = analyze_pcm16_audio_level(struct.pack("<h", 4000) * 1600, 16000)
    >>> (good["status"], round(good["rms_dbfs"], 1))
    ('godt', -18.3)
    >>> low = analyze_pcm16_audio_level(struct.pack("<h", 250) * 1600, 16000)
    >>> low["status"]
    'lavt'
    >>> clip = analyze_pcm16_audio_level(struct.pack("<h", 32500) * 1600, 16000)
    >>> clip["status"]
    'hoyt'
    """
    if len(pcm_bytes) >= 2 and len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if n == 0:
        return {
            "duration_sec": 0.0,
            "rms_dbfs": -96.0,
            "peak_dbfs": -96.0,
            "status": "stille",
            "label": "Stille (ingen lyddata ennå)",
        }

    sum_sq = 0.0
    peak = 0
    for s in samples:
        a = abs(s)
        if a > peak:
            peak = a
        sum_sq += float(s) * float(s)

    rms = math.sqrt(sum_sq / n)
    rms_dbfs = max(-96.0, 20.0 * math.log10(rms / 32768.0)) if rms > 0.5 else -96.0
    peak_dbfs = max(-96.0, 20.0 * math.log10(peak / 32768.0)) if peak > 0 else -96.0
    duration_sec = n / float(sample_rate)

    if rms < 32.0 or rms_dbfs < -60.0:
        status = "stille"
        label = f"Stille ({rms_dbfs:.0f} dBFS) – ingen tale registrert"
    elif peak >= 30600 or peak_dbfs > -0.6:
        status = "hoyt"
        label = f"Høyt nivå / klipping ({rms_dbfs:.0f} dBFS, topp {peak_dbfs:.1f} dBFS) – øk avstand til mikrofon"
    elif rms_dbfs < -35.0:
        status = "lavt"
        label = f"Lavt lydnivå ({rms_dbfs:.0f} dBFS) – snakk nærmere eller sjekk at Stemmeisolasjon er AV"
    else:
        status = "godt"
        label = f"Godt lydnivå ({rms_dbfs:.0f} dBFS) – tydelig tale"

    return {
        "duration_sec": duration_sec,
        "rms_dbfs": round(rms_dbfs, 1),
        "peak_dbfs": round(peak_dbfs, 1),
        "status": status,
        "label": label,
    }


def format_live_check_line(
    start_sec: float, end_sec: float, rms_dbfs: float, status: str, text: str
) -> str:
    """Format a coarse real-time transcription snippet with timestamps and audio level diagnosis.

    >>> format_live_check_line(0.0, 5.0, -18.2, "godt", "Hei, hører alle meg greit?")
    '[00:00–00:05 | -18 dBFS (godt)] Hei, hører alle meg greit?'
    >>> format_live_check_line(5.0, 10.0, -41.0, "lavt", "")
    '[00:05–00:10 | -41 dBFS (lavt)] (Ingen tydelig tale fanget opp – lavt lydnivå)'
    """
    t_range = f"{format_seconds(start_sec)}–{format_seconds(end_sec)}"
    clean = sanitize_text(text)
    if not clean:
        if status == "stille":
            clean = "(Stille – ingen tale registrert)"
        elif status == "lavt":
            clean = "(Ingen tydelig tale fanget opp – lavt lydnivå)"
        else:
            clean = "(Ingen tydelig tale gjenkjent i utsnittet)"
    return f"[{t_range} | {rms_dbfs:.0f} dBFS ({status})] {clean}"


def unique_filename(directory: Path, stem: str, suffix: str) -> str:
    """Return a non-colliding filename inside directory.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     dp = Path(d)
    ...     first = unique_filename(dp, "test", ".md")
    ...     _ = (dp / first).write_text("x", encoding="utf-8")
    ...     second = unique_filename(dp, "test", ".md")
    ...     (first, second)
    ('test.md', 'test_1.md')
    """
    candidate = f"{stem}{suffix}"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def build_whisper_initial_prompt(
    meeting_title: str,
    speaker_names_hint: str,
    agenda_text: str,
    manual_notes_text: str = "",
) -> str:
    """Build a vocabulary-rich initial_prompt (up to 750 chars) from title, attendees, slides/agenda, and manual notes.

    >>> build_whisper_initial_prompt("Statusmøte", "Kari, Ola", "Punkt 1: OAuth2")
    'Statusmøte Deltakere: Kari, Ola. Punkt 1: OAuth2'
    >>> build_whisper_initial_prompt("", "", "")
    ''
    """
    parts: list[str] = []
    if meeting_title.strip():
        parts.append(meeting_title.strip())
    if speaker_names_hint.strip():
        parts.append(f"Deltakere: {speaker_names_hint.strip()}.")
    if agenda_text.strip():
        clean_ag = re.sub(r"\s+", " ", agenda_text.strip())
        parts.append(clean_ag[:450])
    if manual_notes_text.strip():
        clean_mn = re.sub(r"\s+", " ", manual_notes_text.strip())
        parts.append(clean_mn[:450])
    return " ".join(parts).strip()[:750]


def resolve_effective_note_mode(
    note_mode: str,
    min_speakers_str: str,
    max_speakers_str: str,
    speaker_map: dict[str, str],
) -> str:
    """Resolve whether to generate multi-speaker meeting minutes ('mote') or single-speaker lecture notes ('foredrag').

    >>> resolve_effective_note_mode("foredrag", "4", "4", {"A": "A", "B": "B"})
    'foredrag'
    >>> resolve_effective_note_mode("mote", "1", "1", {"SPEAKER_00": "Kari"})
    'mote'
    >>> resolve_effective_note_mode("auto", "1", "1", {"SPEAKER_00": "Kari", "SPEAKER_01": "Ola"})
    'foredrag'
    >>> resolve_effective_note_mode("auto", "", "", {"SPEAKER_00": "Kari"})
    'foredrag'
    >>> resolve_effective_note_mode("auto", "2", "4", {"SPEAKER_00": "Kari", "SPEAKER_01": "Ola"})
    'mote'
    """
    if note_mode in ("foredrag", "mote"):
        return note_mode
    if (
        max_speakers_str.strip() == "1"
        or (min_speakers_str.strip() == "1" and not max_speakers_str.strip())
        or len(set(speaker_map.keys())) == 1
    ):
        return "foredrag"
    return "mote"


def resolve_speaker_bounds(
    min_speakers: int | str | None,
    max_speakers: int | str | None,
    speaker_names_hint: str = "",
) -> tuple[int | None, int | None]:
    """Normalize min/max speaker bounds, inferring exact speaker count from speaker_names_hint when both are empty.

    >>> resolve_speaker_bounds(None, None, "Kari Nordmann, Ola Hansen, Ingrid Berg")
    (3, 3)
    >>> resolve_speaker_bounds("2", "4", "Kari Nordmann, Ola Hansen, Ingrid Berg")
    (2, 4)
    >>> resolve_speaker_bounds("", "", "")
    (None, None)
    """
    def _to_int(val: int | str | None) -> int | None:
        if isinstance(val, int):
            return val if val > 0 else None
        if isinstance(val, str) and val.strip().isdigit():
            parsed = int(val.strip())
            return parsed if parsed > 0 else None
        return None

    min_s = _to_int(min_speakers)
    max_s = _to_int(max_speakers)
    if min_s is None and max_s is None and speaker_names_hint.strip():
        parts = [p.strip() for p in re.split(r"[,;\n]+", speaker_names_hint) if p.strip()]
        if 1 <= len(parts) <= 20:
            return len(parts), len(parts)
    return min_s, max_s


def determine_stages_to_run(
    task_mode: str,
    is_trans_input: bool,
    has_s1: bool,
    has_s2: bool,
    has_s3: bool,
    has_s4: bool,
    has_html: bool,
) -> tuple[bool, bool, bool, bool, bool]:
    """Return booleans (run_s1, run_s2, run_s3, run_s4, run_s5) for the requested task_mode and checkpoint state.

    >>> determine_stages_to_run("auto_resume", False, False, False, False, False, False)
    (True, True, True, True, True)
    >>> determine_stages_to_run("auto_resume", False, True, True, False, False, False)
    (False, False, True, True, True)
    >>> determine_stages_to_run("auto_resume", False, True, True, True, True, True)
    (False, False, False, False, True)
    >>> determine_stages_to_run("only_3_diarize", False, True, True, False, False, False)
    (False, False, True, False, False)
    >>> determine_stages_to_run("only_5_notes", False, True, True, True, True, True)
    (False, False, False, False, True)
    >>> determine_stages_to_run("auto_resume", True, False, False, False, False, False)
    (False, False, False, True, True)
    """
    if task_mode == "full_fresh":
        return (not is_trans_input, not is_trans_input, not is_trans_input, True, True)
    if task_mode == "only_1_asr":
        return (not is_trans_input, False, False, False, False)
    if task_mode == "only_2_align":
        return (not is_trans_input and not has_s1, not is_trans_input, False, False, False)
    if task_mode == "only_3_diarize":
        return (
            not is_trans_input and not has_s1 and not has_s2,
            not is_trans_input and not has_s2,
            not is_trans_input,
            False,
            False,
        )
    if task_mode == "from_3_diarize":
        return (
            not is_trans_input and not has_s1 and not has_s2,
            not is_trans_input and not has_s2,
            not is_trans_input,
            True,
            True,
        )
    if task_mode == "from_4_names":
        return (
            not is_trans_input and not has_s3 and not has_s2 and not has_s1,
            not is_trans_input and not has_s3 and not has_s2,
            not is_trans_input and not has_s3,
            True,
            True,
        )
    if task_mode == "only_4_names":
        return (
            not is_trans_input and not has_s3 and not has_s2 and not has_s1,
            not is_trans_input and not has_s3 and not has_s2,
            not is_trans_input and not has_s3,
            True,
            False,
        )
    if task_mode == "only_5_notes":
        has_trans_source = is_trans_input or has_s4 or has_html or has_s3 or has_s2 or has_s1
        return (
            not has_trans_source,
            not has_trans_source,
            not has_trans_source,
            not (is_trans_input or has_s4 or has_html),
            True,
        )
    # auto_resume
    if is_trans_input:
        return (False, False, False, not has_s4 or not has_html, True)
    run_s1 = not has_s1
    run_s2 = run_s1 or not has_s2
    run_s3 = run_s2 or not has_s3
    run_s4 = run_s3 or not has_s4 or not has_html
    return (run_s1, run_s2, run_s3, run_s4, True)


def extract_turns_from_whisperx_data(data: dict[str, Any]) -> list[TurnDict]:
    """Extract word-level speaker turns from parsed WhisperX JSON dict and merge adjacent same-speaker words.

    >>> sample = {
    ...     "segments": [
    ...         {
    ...             "start": 0.0,
    ...             "end": 3.0,
    ...             "speaker": "SPEAKER_00",
    ...             "words": [
    ...                 {"word": "Hei", "start": 0.0, "end": 0.5, "speaker": "SPEAKER_00"},
    ...                 {"word": "der.", "start": 0.6, "end": 1.0, "speaker": "SPEAKER_00"},
    ...                 {"word": "Hallo!", "start": 1.2, "end": 2.0, "speaker": "SPEAKER_01"},
    ...             ],
    ...         }
    ...     ]
    ... }
    >>> turns = extract_turns_from_whisperx_data(sample)
    >>> [(t["speaker"], t["text"]) for t in turns]
    [('SPEAKER_00', 'Hei der.'), ('SPEAKER_01', 'Hallo!')]
    """
    raw_segments = data.get("segments", [])
    atomic_items: list[TurnDict] = []
    last_speaker = "SPEAKER_00"

    for seg in raw_segments:
        seg_speaker = str(seg.get("speaker") or last_speaker)
        words = seg.get("words") or []

        if words and any(isinstance(w, dict) and w.get("speaker") for w in words):
            for w in words:
                if not isinstance(w, dict):
                    continue
                w_text = sanitize_text(str(w.get("word", "")))
                if not w_text:
                    continue
                w_spk = str(w.get("speaker") or seg_speaker or last_speaker)
                last_speaker = w_spk
                w_start = float(w.get("start", seg.get("start", 0.0)))
                w_end = float(w.get("end", w_start))
                atomic_items.append(
                    {"speaker": w_spk, "start": w_start, "end": w_end, "text": w_text}
                )
        else:
            text = sanitize_text(str(seg.get("text", "")))
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            last_speaker = seg_speaker
            atomic_items.append(
                {"speaker": seg_speaker, "start": start, "end": end, "text": text}
            )

    merged: list[TurnDict] = []
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


def extract_turns_from_whisperx(json_path: Path) -> list[TurnDict]:
    """Load WhisperX JSON file and extract speaker turns."""
    raw_bytes = json_path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    return extract_turns_from_whisperx_data(data)


def parse_transcript_text(raw_text: str, ext: str) -> tuple[list[TurnDict], dict[str, str]]:
    """Parse turns and speaker_map from raw transcript content (.html, .json, .srt, .vtt, .txt, .md).

    >>> turns, smap = parse_transcript_text("[00:01 – 00:05] Kari: Velkommen til møtet.\\n[00:06 – 00:10] Ola: Takk!", ".txt")
    >>> [(t["speaker"], t["start"], t["text"]) for t in turns]
    [('Kari', 1.0, 'Velkommen til møtet.'), ('Ola', 6.0, 'Takk!')]
    >>> srt = "1\\n00:00:01,000 --> 00:00:04,500\\nIngrid: Sikkerhetsrapporten er klar.\\n"
    >>> turns_srt, _ = parse_transcript_text(srt, ".srt")
    >>> (turns_srt[0]["speaker"], turns_srt[0]["end"], turns_srt[0]["text"])
    ('Ingrid', 4.5, 'Sikkerhetsrapporten er klar.')
    """
    ext = ext.lower()
    turns: list[TurnDict] = []
    speaker_map: dict[str, str] = {}

    if ext in (".html", ".htm"):
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
        raw_text = re.sub(r"<[^>]+>", "\n", raw_text)

    if ext == ".json":
        data = json.loads(raw_text)
        if isinstance(data, dict) and "segments" in data:
            return extract_turns_from_whisperx_data(data), {}
        if isinstance(data, dict) and "turns" in data and isinstance(data["turns"], list):
            raw_map = data.get("speaker_map")
            s_map: dict[str, str] = (
                {str(k): str(v) for k, v in raw_map.items()} if isinstance(raw_map, dict) else {}
            )
            return list(data["turns"]), s_map
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

    cur_t = 0.0
    for line in raw_text.splitlines():
        clean = sanitize_text(line)
        if not clean:
            continue
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


def load_turns_from_transcript_file(path: Path) -> tuple[list[TurnDict], dict[str, str]]:
    """Read a transcript file from disk and parse turns + speaker_map."""
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    return parse_transcript_text(raw_text, path.suffix.lower())


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
        pdftotext_bin = shutil.which(
            "pdftotext", path="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
        )
        if pdftotext_bin:
            try:
                out = subprocess.check_output(
                    [pdftotext_bin, str(path), "-"], text=True, stderr=subprocess.DEVNULL
                )
                if out.strip():
                    return sanitize_text(out)
            except Exception:
                pass
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


def build_runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore::UserWarning:pyannote.audio.core.io"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if os.name != "nt":
        env.setdefault("LC_ALL", "en_US.UTF-8")
    extra = [
        str(LOCAL_BIN_DIR),
        str(Path.home() / ".cache" / "mote" / "bin"),
        str(Path.home() / ".local" / "share" / "uv" / "tools" / "whisperx" / "bin"),
        str(Path.home() / ".local" / "share" / "uv" / "tools" / "whisperx" / "Scripts"),
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        str(Path.home() / ".local" / "share" / "mise" / "shims"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    if sys.platform == "darwin":
        extra.append("/Applications/Ollama.app/Contents/Resources")
    elif os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            extra.append(str(Path(local_app) / "Programs" / "Ollama"))
            extra.append(str(Path(local_app) / "Microsoft" / "WinGet" / "Links"))
        app_data = os.environ.get("APPDATA", "")
        if app_data:
            extra.append(str(Path(app_data) / "uv" / "tools" / "whisperx" / "Scripts"))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def resolve_ui_lang(lang_arg: str | None = None, argv: list[str] | None = None) -> str:
    """Resolve UI/help language ('en' or 'no') from explicit argument, CLI flags, or MOTE_LANG.

    >>> resolve_ui_lang("en")
    'en'
    >>> resolve_ui_lang("no")
    'no'
    >>> resolve_ui_lang(None, ["--ui-lang", "en", "--help"])
    'en'
    >>> resolve_ui_lang(None, ["--ui-lang=en"])
    'en'
    >>> resolve_ui_lang(None, ["--check-hw"])
    'no'
    """
    if lang_arg:
        low = lang_arg.strip().lower()
        if low.startswith("en"):
            return "en"
        if low in ("no", "nb", "nn"):
            return "no"

    args_list = list(argv) if argv is not None else []
    for idx, token in enumerate(args_list):
        if token == "--ui-lang" and idx + 1 < len(args_list):
            val = args_list[idx + 1].strip().lower()
            if val.startswith("en"):
                return "en"
            return "no"
        if token.startswith("--ui-lang="):
            val = token.split("=", 1)[1].strip().lower()
            if val.startswith("en"):
                return "en"
            return "no"

    env_lang = os.environ.get("MOTE_LANG", "").strip().lower()
    if env_lang.startswith("en"):
        return "en"
    return "no"


def build_ffmpeg_mic_record_cmd(
    ffmpeg_bin: str,
    out_wav: Path,
    platform_str: str | None = None,
    os_name: str | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Build cross-platform 16kHz mono PCM16 ffmpeg microphone capture command for macOS, Windows, and Linux.

    >>> cmd_mac = build_ffmpeg_mic_record_cmd("ffmpeg", Path("/tmp/a.wav"), platform_str="darwin", os_name="posix")
    >>> cmd_mac[:6]
    ['ffmpeg', '-y', '-f', 'avfoundation', '-i', ':default']
    >>> cmd_win = build_ffmpeg_mic_record_cmd("ffmpeg.exe", Path("C:/a.wav"), platform_str="win32", os_name="nt")
    >>> cmd_win[:6]
    ['ffmpeg.exe', '-y', '-f', 'dshow', '-i', 'audio=default']
    >>> cmd_lin = build_ffmpeg_mic_record_cmd("ffmpeg", Path("/tmp/a.wav"), platform_str="linux", os_name="posix")
    >>> cmd_lin[2] == '-f' and cmd_lin[3] in ('pulse', 'alsa')
    True
    """
    plat = platform_str if platform_str is not None else sys.platform
    os_n = os_name if os_name is not None else os.name

    if plat == "darwin":
        return [
            ffmpeg_bin,
            "-y",
            "-f",
            "avfoundation",
            "-i",
            ":default",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]

    if os_n == "nt" or plat.startswith("win"):
        dshow_input = "audio=default"
        if os.name == "nt" and Path(ffmpeg_bin).is_file():
            try:
                proc = subprocess.run(
                    [ffmpeg_bin, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3,
                    check=False,
                )
                for line in (proc.stdout or "").splitlines():
                    m_dev = re.search(r'"([^"]+)"\s*\(audio\)', line)
                    if m_dev:
                        dshow_input = f"audio={m_dev.group(1)}"
                        break
            except Exception:
                pass
        return [
            ffmpeg_bin,
            "-y",
            "-f",
            "dshow",
            "-i",
            dshow_input,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]

    linux_fmt = "pulse"
    if sys.platform.startswith("linux") and not shutil.which("pactl") and not os.environ.get("PULSE_SERVER"):
        linux_fmt = "alsa"
    return [
        ffmpeg_bin,
        "-y",
        "-f",
        linux_fmt,
        "-i",
        "default",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]


MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "recommended": {
        "id": "recommended",
        "label": "Anbefalt Testvinner #1 (12–16 GB+ RAM – NB-Whisper Small + Gemma 4)",
        "whisper_model": "NbAiLab/nb-whisper-small",
        "ollama_model": "gemma4:e4b",
        "min_ram_gb": 12.0,
        "min_disk_gb": 10.0,
        "est_peak_ram_gb": 6.5,
        "description": (
            "Testvinner #1 (Score 79.21): NB-Whisper Small (244M, 83.5% ord-nøyaktighet, 2.7x raskere enn Large) "
            "+ Wav2Vec2-300M + Gemma 4 E4B (100% taler-ID, 96.7% referat)."
        ),
    },
    "fast": {
        "id": "fast",
        "label": "Alternativ #2 (16 GB+ RAM – NB-Whisper Distil-Turbo + Gemma 4)",
        "whisper_model": "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2",
        "ollama_model": "gemma4:e4b",
        "min_ram_gb": 14.0,
        "min_disk_gb": 14.0,
        "est_peak_ram_gb": 6.5,
        "description": "Testvinner #2 (Score 74.30): NB-Whisper Large Distil-Turbo (756M) + Gemma 4 E4B.",
    },
    "light": {
        "id": "light",
        "label": "Lettvekt / 8 GB RAM (NB-Whisper Small + Llama 3.2 3B)",
        "whisper_model": "NbAiLab/nb-whisper-small",
        "ollama_model": "llama3.2:latest",
        "min_ram_gb": 8.0,
        "min_disk_gb": 6.0,
        "est_peak_ram_gb": 3.8,
        "description": "For 8 GB RAM-maskiner (Score 64.02): NB-Whisper Small + Wav2Vec2-300M + Llama 3.2 3B.",
    },
}


def detect_hardware_resources(target_dir: Path | None = None) -> dict[str, Any]:
    """Detect local RAM, CPU brand/cores, GPU VRAM, free disk space, and recommended model profile.

    >>> hw = detect_hardware_resources()
    >>> hw["ram_gb"] > 0 and hw["cpu_cores"] >= 1 and hw["recommended_profile"] in MODEL_PROFILES
    True
    """
    ram_bytes = 0
    cpu_brand = platform.processor() or platform.machine() or "Ukjent CPU"
    cpu_cores = os.cpu_count() or 1
    arch = platform.machine().lower()
    is_apple_silicon = sys.platform == "darwin" and ("arm" in arch or "aarch64" in arch)

    if sys.platform == "darwin":
        try:
            out_mem = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            ram_bytes = int(out_mem)
        except Exception:
            pass
        try:
            out_cpu = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out_cpu:
                cpu_brand = out_cpu
        except Exception:
            pass
    elif os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat_ex = MEMORYSTATUSEX()
            stat_ex.dwLength = ctypes.sizeof(stat_ex)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat_ex))  # type: ignore[attr-defined]
            ram_bytes = int(stat_ex.ullTotalPhys)
        except Exception:
            pass
    else:
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            m_mem = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
            if m_mem:
                ram_bytes = int(m_mem.group(1)) * 1024
        except Exception:
            pass
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            m_cpu = re.search(r"model name\s*:\s*(.+)", cpuinfo)
            if m_cpu:
                cpu_brand = m_cpu.group(1).strip()
        except Exception:
            pass

    if ram_bytes <= 0:
        ram_bytes = 8 * (1024**3)
    ram_gb = round(ram_bytes / float(1024**3), 1)

    gpu_vram_gb = 0.0
    gpu_name = ""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            nv_out = subprocess.check_output(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
            if nv_out:
                first_line = nv_out.splitlines()[0]
                parts = [p.strip() for p in first_line.split(",")]
                if len(parts) >= 2:
                    gpu_name = parts[0]
                    gpu_vram_gb = round(float(parts[1]) / 1024.0, 1)
        except Exception:
            pass

    check_path = target_dir if (target_dir and target_dir.exists()) else Path.home()
    try:
        disk_free_gb = round(shutil.disk_usage(check_path).free / float(1024**3), 1)
    except Exception:
        disk_free_gb = 20.0

    warnings: list[str] = []
    if ram_gb < 7.5:
        warnings.append(
            f"KRITISK: Maskinen har kun {ram_gb:.1f} GB RAM (minimum er 8 GB RAM). "
            "Lokale AI-modeller vil sannsynligvis gå tom for minne eller fryse maskinen."
        )
    elif ram_gb < 12.0:
        warnings.append(
            f"Begrenset minne ({ram_gb:.0f} GB RAM): Bruk profilen «Lettvekt» (nb-whisper-small + llama3.2:latest) "
            "og lukk nettlesere/tunge programmer under kjøring."
        )

    if disk_free_gb < 6.0:
        warnings.append(
            f"KRITISK: Kun {disk_free_gb:.1f} GB ledig diskplass. Du bør ha minst 10–15 GB ledig for AI-modeller og lydfiler."
        )
    elif disk_free_gb < 14.0:
        warnings.append(
            f"Lite ledig diskplass ({disk_free_gb:.1f} GB): Velg lettvektsmodeller hvis modellene ikke allerede er lastet ned."
        )

    if ram_gb < 11.5:
        rec_profile_key = "light"
        tier = "insufficient" if ram_gb < 7.5 else "light"
    else:
        rec_profile_key = "recommended"
        tier = "high" if (ram_gb >= 22.0 or gpu_vram_gb >= 10.0) else "standard"

    rec_prof = MODEL_PROFILES[rec_profile_key]
    hw_summary = f"{cpu_brand} ({cpu_cores} kjerner), {ram_gb:.0f} GB RAM"
    if gpu_name:
        hw_summary += f", GPU: {gpu_name} ({gpu_vram_gb:.0f} GB VRAM)"
    hw_summary += f", {disk_free_gb:.0f} GB ledig disk"

    return {
        "ram_gb": ram_gb,
        "cpu_brand": cpu_brand,
        "cpu_cores": cpu_cores,
        "arch": arch,
        "is_apple_silicon": is_apple_silicon,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram_gb,
        "disk_free_gb": disk_free_gb,
        "tier": tier,
        "hardware_summary": hw_summary,
        "recommended_profile": rec_profile_key,
        "recommended_whisper": rec_prof["whisper_model"],
        "recommended_ollama": rec_prof["ollama_model"],
        "recommended_label": rec_prof["label"],
        "profiles": MODEL_PROFILES,
        "warnings": warnings,
    }


def evaluate_model_resource_fit(
    whisper_model: str,
    ollama_model: str,
    hw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate RAM and disk needs for (whisper_model, ollama_model) and warn if hardware is insufficient.

    >>> hw_8gb = {"ram_gb": 8.0, "disk_free_gb": 50.0, "hardware_summary": "Test 8GB", "recommended_whisper": "NbAiLab/nb-whisper-small", "recommended_ollama": "qwen3:4b"}
    >>> fit_ok = evaluate_model_resource_fit("NbAiLab/nb-whisper-small", "qwen3:4b", hw_8gb)
    >>> fit_ok["level"]
    'ok'
    >>> fit_bad = evaluate_model_resource_fit("NbAiLab/nb-whisper-large", "gemma4:26b", hw_8gb)
    >>> fit_bad["level"]
    'critical'
    """
    if hw is None:
        hw = detect_hardware_resources()

    w_low = whisper_model.lower()
    if any(k in w_low for k in ("tiny", "base", "small")):
        est_asr_ram = 1.2
        est_align_ram = 1.5  # uses nb-wav2vec2-300m-bokmaal
        est_asr_disk = 2.0
    elif any(k in w_low for k in ("distil", "turbo", "medium")):
        est_asr_ram = 2.2
        est_align_ram = 4.6
        est_asr_disk = 5.2
    else:
        est_asr_ram = 3.4
        est_align_ram = 4.8
        est_asr_disk = 7.0

    m_low = ollama_model.lower()
    if any(k in m_low for k in ("26b", "27b", "30b", "32b", "devstral", "24b")):
        est_llm_ram = 18.0
        est_llm_disk = 15.0
    elif any(k in m_low for k in ("11b", "12b", "14b", "normistral")):
        est_llm_ram = 9.5
        est_llm_disk = 7.5
    elif any(k in m_low for k in ("e4b", "7b", "8b", "gemma4:latest")):
        est_llm_ram = 6.5
        est_llm_disk = 5.5
    else:
        # e2b, 3b, 4b (qwen3:4b, llama3.2, gemma4:e2b, gemma3:4b)
        est_llm_ram = 4.0
        est_llm_disk = 3.0

    # Stages run sequentially, so peak RAM is the maximum across stages
    est_peak_ram = round(max(est_asr_ram, est_align_ram, est_llm_ram), 1)
    est_total_disk = round(est_asr_disk + est_llm_disk, 1)
    ram_gb = float(hw.get("ram_gb", 16.0))
    disk_free_gb = float(hw.get("disk_free_gb", 20.0))

    issues: list[str] = []
    level = "ok"

    if ram_gb < 7.5:
        level = "critical"
        issues.append(
            f"Maskinen har kun {ram_gb:.1f} GB RAM (minimum er 8 GB RAM). Kjøring vil sannsynligvis feile."
        )
    elif est_peak_ram > ram_gb * 0.88:
        level = "critical"
        issues.append(
            f"Valgt modellkombinasjon krever ~{est_peak_ram:.1f} GB RAM på topp, men maskinen har kun {ram_gb:.0f} GB RAM. "
            f"Klikk «Bruk anbefalt» ({hw.get('recommended_whisper')} + {hw.get('recommended_ollama')}) for å unngå minnekrasj."
        )
    elif est_peak_ram > ram_gb * 0.72:
        level = "warning"
        issues.append(
            f"Valgte modeller bruker ~{est_peak_ram:.1f} GB av {ram_gb:.0f} GB RAM. Lukk nettleser og tunge apper før start, "
            "eller velg en lettere profil."
        )

    if disk_free_gb < 5.0:
        level = "critical"
        issues.append(f"Kun {disk_free_gb:.1f} GB ledig diskplass (kritisk lavt).")

    if issues:
        msg = " | ".join(issues)
    else:
        msg = (
            f"Maskinvare OK: {hw.get('hardware_summary')} — "
            f"Valgte modeller bruker maks ~{est_peak_ram:.1f} GB RAM sekvensielt."
        )

    return {
        "ok": level != "critical",
        "level": level,
        "est_peak_ram_gb": est_peak_ram,
        "est_disk_gb": est_total_disk,
        "ram_gb": ram_gb,
        "disk_free_gb": disk_free_gb,
        "message": msg,
        "recommended_whisper": hw.get("recommended_whisper", "NbAiLab/nb-whisper-small"),
        "recommended_ollama": hw.get("recommended_ollama", "gemma4:e4b"),
    }


def ensure_ffmpeg(env: dict[str, str], log_fn: Callable[[str], None]) -> str:
    found = shutil.which("ffmpeg", path=env["PATH"])
    if found:
        return found
    log_fn("==> Laster ned innebygd ffmpeg...")
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths(weak=True)
    except Exception:
        pass
    env["PATH"] = os.environ.get("PATH", "") + os.pathsep + env["PATH"]
    found = shutil.which("ffmpeg", path=env["PATH"])
    if not found:
        raise RuntimeError("Fant ikke ffmpeg.")
    return found


def ensure_whisperx(
    env: dict[str, str],
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
) -> str:
    found = shutil.which("whisperx", path=env["PATH"])
    if found:
        return found
    uv_bin = shutil.which("uv", path=env["PATH"])
    if not uv_bin:
        raise RuntimeError("Fant ikke 'uv' for automatisk installasjon av whisperx.")
    if progress_fn:
        progress_fn(8, "Installerer WhisperX automatisk (kun første gang)...")
    log_fn("==> WhisperX mangler. Installerer automatisk via 'uv tool install whisperx'...")
    subprocess.run(
        [uv_bin, "tool", "install", "--python", "3.12", "whisperx"],
        env=env,
        check=True,
    )
    found = shutil.which("whisperx", path=env["PATH"])
    if not found:
        cand = Path.home() / ".local" / "bin" / ("whisperx.exe" if os.name == "nt" else "whisperx")
        if cand.is_file():
            return str(cand)
        raise RuntimeError("WhisperX ble installert, men ble ikke funnet i PATH.")
    return found


def get_whisperx_python(whisperx_bin: str) -> str:
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
    return sys.executable


def is_diarization_cached() -> bool:
    hub_dir = HF_CACHE_DIR / "hub"
    if not hub_dir.is_dir():
        return False
    return any(
        any(p.glob("snapshots/*/*"))
        for p in hub_dir.glob("models--pyannote--speaker-diarization*")
    )


def get_initial_hf_token(interactive_prompt: bool = False) -> str:
    if token := os.environ.get("HF_TOKEN", "").strip():
        return token
    if HF_TOKEN_FILE.is_file():
        try:
            if token := HF_TOKEN_FILE.read_text(encoding="utf-8").strip():
                return token
        except OSError:
            pass
    if interactive_prompt and not is_diarization_cached() and sys.stdin.isatty():
        token = getpass.getpass("Skriv inn Hugging Face Read-token (hf_...): ").strip()
        if token:
            save_hf_token(token)
            return token
    return ""


def save_hf_token(token: str) -> None:
    token = token.strip()
    if not token:
        return
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HF_TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        HF_TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def fetch_ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return [str(m.get("name", "")) for m in data.get("models", [])]
    except Exception:
        pass
    return None


def ensure_ollama_binary(
    env: dict[str, str],
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
) -> str:
    found = shutil.which("ollama", path=env["PATH"])
    if found:
        return found
    if sys.platform == "darwin" and Path("/Applications/Ollama.app/Contents/Resources/ollama").is_file():
        return "/Applications/Ollama.app/Contents/Resources/ollama"

    LOCAL_BIN_DIR.mkdir(parents=True, exist_ok=True)
    if progress_fn:
        progress_fn(74, "Laster ned frittstående Ollama-motor...")
    log_fn("==> Ollama ble ikke funnet. Laster ned frittstående binærfil...")

    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"

    with tempfile.TemporaryDirectory(prefix="ollama_dl_") as tmp:
        tmp_path = Path(tmp)
        if sys.platform == "darwin":
            url = "https://ollama.com/download/Ollama-darwin.zip"
            archive = tmp_path / "Ollama-darwin.zip"
            urllib.request.urlretrieve(url, archive)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(tmp_path)
            src = tmp_path / "Ollama.app" / "Contents" / "Resources" / "ollama"
            target = LOCAL_BIN_DIR / "ollama"
            shutil.copy2(src, target)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)
            return str(target)
        if sys.platform.startswith("linux"):
            url = f"https://ollama.com/download/ollama-linux-{arch}.tgz"
            archive = tmp_path / "ollama.tgz"
            urllib.request.urlretrieve(url, archive)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(tmp_path)
            src = tmp_path / "bin" / "ollama"
            target = LOCAL_BIN_DIR / "ollama"
            shutil.copy2(src, target)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)
            return str(target)
        if os.name == "nt":
            url = f"https://ollama.com/download/ollama-windows-{arch}.zip"
            archive = tmp_path / "ollama.zip"
            urllib.request.urlretrieve(url, archive)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(LOCAL_BIN_DIR)
            for candidate in LOCAL_BIN_DIR.rglob("ollama.exe"):
                return str(candidate)
    raise RuntimeError("Klarte ikke å installere Ollama automatisk.")


@contextmanager
def managed_ollama_server(
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    run_env = env or build_runtime_env()
    ollama_bin = ensure_ollama_binary(run_env, log_fn, progress_fn)

    if fetch_ollama_models() is not None:
        log_fn("Ollama-server kjører allerede. Gjenbruker eksisterende instans.")
        yield ollama_bin
        return

    log_fn("Starter midlertidig Ollama-server i bakgrunnen...")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        [ollama_bin, "serve"],
        env=run_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    try:
        for _ in range(30):
            if fetch_ollama_models() is not None:
                log_fn("Midlertidig Ollama-server er klar.")
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Midlertidig Ollama-server svarte ikke innen 15 sekunder.")
        yield ollama_bin
    finally:
        log_fn("Stopper midlertidig Ollama-server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def infer_speaker_names_via_api(
    turns: list[TurnDict],
    model: str,
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    log_fn: Callable[[str], None],
    manual_notes_text: str = "",
) -> dict[str, str]:
    """Use Ollama to detect self-introductions, direct address, and roles from manual notes to map SPEAKER_XX."""
    unique_speakers = sorted({t["speaker"] for t in turns})
    if not unique_speakers:
        return {}

    # Support explicit SPEAKER_XX=Name or SPEAKER_XX: Name overrides in known_names_hint
    explicit_map: dict[str, str] = {}
    for m_exp in re.finditer(r"(SPEAKER_\d+)\s*[=:]\s*([^,;]+)", known_names_hint):
        spk_id = m_exp.group(1).strip()
        spk_val = m_exp.group(2).strip()
        if spk_id in unique_speakers and spk_val:
            explicit_map[spk_id] = f"{spk_val} ({spk_id})" if spk_id not in spk_val else spk_val
    if len(explicit_map) == len(unique_speakers):
        return explicit_map

    # Extract candidate first names from known_names_hint so we include turns mentioning them + adjacent turns
    name_tokens: list[str] = []
    for raw_part in re.split(r"[,;\n]+", known_names_hint):
        clean_part = re.sub(r"SPEAKER_\d+\s*[=:]\s*", "", raw_part)
        clean_part = re.sub(r"\(.*?\)", "", clean_part).strip()
        if clean_part:
            first_word = clean_part.split()[0].lower()
            if len(first_word) >= 3:
                name_tokens.append(first_word)
                if "-" in first_word:
                    name_tokens.extend(p for p in first_word.split("-") if len(p) >= 3)

    intro_keywords = (
        "heter",
        "jeg er",
        "mitt navn",
        "fra ",
        "representerer",
        "leder",
        "tittel",
        "rolle",
        "ansvarlig",
        "velkommen",
        "møteleder",
        "kasserer",
        "sekretær",
        "referent",
        "regnskap",
        *name_tokens,
    )

    selected_indices: set[int] = set()
    total_turns = len(turns)
    step = max(1, total_turns // 50)
    for idx, t in enumerate(turns):
        low = t["text"].lower()
        if idx < 80 or idx % step == 0:
            selected_indices.add(idx)
        if any(k in low for k in intro_keywords):
            if idx > 0:
                selected_indices.add(idx - 1)
            selected_indices.add(idx)
            if idx + 1 < total_turns:
                selected_indices.add(idx + 1)

    sample_lines: list[str] = []
    for idx in sorted(selected_indices)[:220]:
        t = turns[idx]
        t_str = f"{format_seconds(t['start'])}–{format_seconds(t['end'])}"
        sample_lines.append(f"[{t_str}] {t['speaker']}: {t['text']}")

    context_parts: list[str] = []
    if meeting_title.strip():
        context_parts.append(f"Møtetittel/Dato: {meeting_title.strip()}")
    if known_names_hint.strip():
        context_parts.append(f"Forhåndsoppgitte deltakere (tilstede): {known_names_hint.strip()}")
    if agenda_text.strip():
        context_parts.append(f"Agenda / Lysbilder / Kontekst:\n{agenda_text.strip()[:3500]}")
    if manual_notes_text.strip():
        context_parts.append(
            f"Manuelle møtenotater / referat-utkast (bruk til å koble hvem som sier hva / har hvilken rolle):\n"
            f"{manual_notes_text.strip()[:4500]}"
        )
    context_str = "\n".join(context_parts) if context_parts else "Ingen forhåndsoppgitt deltakerliste."

    prompt = (
        "Analyser utdraget fra møtetranskripsjonen under og finn ut hvem de ulike taler-IDene "
        f"({', '.join(unique_speakers)}) er, basert på selvpresentasjon, tiltale mellom deltakerne, "
        "og hvem som presenterer hvilke saker/roller i møtet.\n\n"
        f"{context_str}\n\n"
        "KRITISKE REGLER (LES NØYE FOR Å UNNGÅ Å BYTTE OM TO TALERE):\n"
        "1. DIREKTE TILTALE: Når en taler (f.eks. SPEAKER_A) tiltaler en annen ved navn (f.eks. «Hva tenker du, Ola?» "
        "eller «Kari, kan du...»), betyr det at SPEAKER_A IKKE er den personen – det er taleren som svarer i replikken "
        "rett etterpå som har det navnet!\n"
        "2. OMTALE I TREDJEPERSON: Når en taler omtaler noen ved navn i tredjeperson (f.eks. «Ola har sjekket...» "
        "eller «Kari skal sende...»), kan den som snakker IKKE være den omtalte personen.\n"
        "3. SAKER OG ROLLER FRA MANUELLE NOTATER: Sjekk i utdraget hvem som leder ordet, hvem som legger frem hvilke saker, "
        "og hvem som har bestemte verv/ansvarsoppgaver nevnt i de manuelle møtenotatene.\n"
        "4. Returner KUN et gyldig JSON-objekt som knytter hver taler-ID til personens korrekte navn (bruk eksakt stavemåte "
        "fra deltakerlisten/notatene, f.eks. 'Kari Nordmann').\n"
        "5. Hvis du IKKE finner navnet til en taler-ID med sikkerhet, behold original ID, f.eks. "
        '{"SPEAKER_00": "SPEAKER_00"}. Ikke gjett tilfeldige navn.\n\n'
        "Utdrag fra transkripsjon:\n" + "\n".join(sample_lines)
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
                    if spk in explicit_map:
                        mapping[spk] = explicit_map[spk]
                        continue
                    val = str(parsed.get(spk, spk)).strip()
                    if val and val != spk:
                        mapping[spk] = f"{val} ({spk})" if spk not in val else val
                    else:
                        mapping[spk] = spk
                return mapping
    except Exception as exc:
        log_fn(f"Merk: Automatisk navnegjenkjenning hoppet over ({exc}).")
    base_fallback = {spk: spk for spk in unique_speakers}
    base_fallback.update(explicit_map)
    return base_fallback


def _format_inline_md(text: str) -> str:
    """Format inline markdown elements: bold, italics, code, links, safely escaped."""
    escaped = html.escape(text)
    # Inline code
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    # Bold **text**
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    # Italic *text*
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    # Links [label](url)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', escaped)
    return escaped


def _render_markdown_to_html(md_text: str) -> str:
    """Render markdown into clean semantic HTML using markdown library if available, with pure-Python fallback."""
    try:
        import markdown as md_lib

        return md_lib.markdown(
            md_text,
            extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        )
    except Exception:
        pass

    # Pure-Python fallback renderer for headings, tables, blockquotes, lists, and formatting
    lines = md_text.splitlines()
    out: list[str] = []
    in_table = False
    in_code = False
    code_block: list[str] = []
    in_ul = False
    in_ol = False

    def close_blocks() -> None:
        nonlocal in_table, in_ul, in_ol
        if in_table:
            out.append("</tbody></table>")
            in_table = False
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        stripped = line.strip()
        # Code fence
        if stripped.startswith("```"):
            close_blocks()
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_block))}</code></pre>")
                code_block.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_block.append(line)
            continue

        if not stripped:
            close_blocks()
            continue

        # Headings
        if stripped.startswith("#### "):
            close_blocks()
            out.append(f"<h4>{_format_inline_md(stripped[5:])}</h4>")
            continue
        if stripped.startswith("### "):
            close_blocks()
            out.append(f"<h3>{_format_inline_md(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            close_blocks()
            out.append(f"<h2>{_format_inline_md(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            close_blocks()
            out.append(f"<h1>{_format_inline_md(stripped[2:])}</h1>")
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            close_blocks()
            out.append("<hr>")
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            raw_cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^:?-+:?$", c) for c in raw_cells if c):
                continue  # Divider line
            if not in_table:
                close_blocks()
                out.append("<table><thead><tr>" + "".join(f"<th>{_format_inline_md(c)}</th>" for c in raw_cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_format_inline_md(c)}</td>" for c in raw_cells) + "</tr>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            close_blocks()
            out.append(f"<blockquote><p>{_format_inline_md(stripped[2:])}</p></blockquote>")
            continue

        # Lists
        if stripped.startswith(("- ", "* ", "+ ")):
            if in_table or in_ol:
                close_blocks()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_format_inline_md(stripped[2:])}</li>")
            continue

        m_num = re.match(r"^\d+\.\s+(.+)$", stripped)
        if m_num:
            if in_table or in_ul:
                close_blocks()
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_format_inline_md(m_num.group(1))}</li>")
            continue

        # Paragraph
        close_blocks()
        out.append(f"<p>{_format_inline_md(stripped)}</p>")

    close_blocks()
    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_block))}</code></pre>")
    return "\n".join(out)


def build_transcript_html_and_text(
    turns: list[TurnDict],
    speaker_map: dict[str, str],
    html_path: Path,
    title: str,
) -> str:
    """Build rich searchable HTML transcript and return plain-text transcript for LLM."""
    colors = [
        "#2563EB", "#16A34A", "#D97706", "#9333EA", "#E11D48",
        "#0D9488", "#4F46E5", "#CA8A04", "#0284C7", "#BE185D",
    ]
    speaker_color_map: dict[str, str] = {}
    llm_lines: list[str] = []
    html_items: list[str] = []

    total_turns = len(turns)
    total_seconds = (turns[-1]["end"] - turns[0]["start"]) if turns else 0.0
    total_duration_str = format_seconds(total_seconds) if turns else "00:00"

    for idx, item in enumerate(turns, start=1):
        orig_spk = item["speaker"]
        display_spk = speaker_map.get(orig_spk, orig_spk)
        if orig_spk not in speaker_color_map:
            speaker_color_map[orig_spk] = colors[len(speaker_color_map) % len(colors)]
        color = speaker_color_map[orig_spk]
        t_str = f"{format_seconds(item['start'])} – {format_seconds(item['end'])}"

        llm_lines.append(f"[{t_str}] {display_spk}: {item['text']}")
        html_items.append(
            f'<div class="turn" data-speaker="{html.escape(orig_spk)}" id="turn-{idx}">'
            f'  <div class="meta">'
            f'    <span class="badge" style="background:{color}" data-spk-label="{html.escape(orig_spk)}">{html.escape(display_spk)}</span>'
            f'    <span class="timestamp">{t_str}</span>'
            f'    <button class="copy-turn-btn no-print" onclick="copyTurnText(\'turn-{idx}\')" title="Kopier innlegg">Kopier</button>'
            f'  </div>'
            f'  <div class="text">{html.escape(item["text"])}</div>'
            f'</div>'
        )

    speaker_filters = "".join(
        f'<button class="spk-filter-btn" data-spk="{html.escape(spk)}" onclick="filterBySpeaker(\'{html.escape(spk)}\')">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <span data-spk-btn-label="{html.escape(spk)}">{html.escape(speaker_map.get(spk, spk))}</span>'
        f'</button>'
        for spk, col in speaker_color_map.items()
    )

    speaker_inputs = "".join(
        f'<div class="spk-edit">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <input type="text" value="{html.escape(speaker_map.get(spk, spk))}" data-orig="{html.escape(spk)}" '
        f'         oninput="renameSpeaker(this.dataset.orig, this.value)" title="Endre navn i visningen">'
        f'</div>'
        for spk, col in speaker_color_map.items()
    )

    speaker_count = len(speaker_color_map)

    tpl = load_template("transcript.html")
    html_doc = tpl.format(
        escaped_title=html.escape(title),
        total_turns=total_turns,
        speaker_count=speaker_count,
        total_duration_str=total_duration_str,
        speaker_filters=speaker_filters,
        speaker_inputs=speaker_inputs,
        html_items="".join(html_items),
    )
    html_path.write_text(html_doc, encoding="utf-8")
    return "\n".join(llm_lines)


def build_notes_html(notes_md: str, notes_html_path: Path, title: str) -> None:
    """Render clean Markdown meeting notes into a printable, beautifully styled HTML document."""
    rendered_body = _render_markdown_to_html(notes_md)

    tpl = load_template("notes.html")
    html_doc = tpl.format(
        escaped_title=html.escape(title),
        rendered_body=rendered_body,
        escaped_notes_md=html.escape(notes_md),
    )
    notes_html_path.write_text(html_doc, encoding="utf-8")


def generate_notes_via_api(
    transcript: str,
    model: str,
    speaker_map: dict[str, str],
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    progress_fn: Callable[[int, str], None] | None = None,
    note_mode: str = "mote",
    manual_notes_text: str = "",
) -> str:
    """Use Ollama HTTP /api/chat with streaming to generate meeting minutes or lecture notes."""
    detected_list = ", ".join(sorted(set(speaker_map.values())))
    context_lines: list[str] = []
    if meeting_title.strip():
        context_lines.append(f"Tittel og dato: {meeting_title.strip()}")
    if known_names_hint.strip():
        context_lines.append(f"Oppgitte personer (navn og tittel/rolle): {known_names_hint.strip()}")
    if detected_list:
        context_lines.append(f"Identifiserte stemmer i transkripsjonen: {detected_list}")
    if agenda_text.strip():
        context_lines.append(f"Agenda / Lysbilder / Kontekst:\n{agenda_text.strip()}")
    if manual_notes_text.strip():
        context_lines.append(
            "Manuelle møtenotater / referat-utkast (AUTORITATIV FASIT for korrekte navn, stavemåter, saksnummer, "
            "hvem som hadde ordet/ansvar i enkeltsaker, vedtak og aksjonspunkter):\n"
            f"{manual_notes_text.strip()}"
        )
    context_block = "\n".join(context_lines)

    if note_mode == "foredrag":
        system_prompt = (
            "Du er en dyktig norsk fagredaktør og notatskriver. Du skriver kun selve oppsummeringen i ren Markdown. "
            "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
            "Opptaket er et foredrag, en presentasjon eller en monolog med én hovedtaler (ikke et møte med vedtak og aksjonstabell). "
            "Bruk korrekt norsk (bokmål) og fokuser på faglig innhold, hovedbudskap, innsikter, sentrale begreper og praktiske lærdommer."
        )
        user_prompt = (
            "Lag et grundig og strukturert faglig sammendrag av foredraget/presentasjonen basert på konteksten og den tidsstemplede transkripsjonen under.\n\n"
            f"--- KONTEKST ---\n{context_block}\n--- KONTEKST SLUTT ---\n\n"
            "Bruk denne strukturen:\n"
            "# Foredragsnotater og hovedpoenger\n\n"
            "## 1. Oversikt og hovedbudskap\n"
            "(Tittel/dato, foredragsholder med rolle/bakgrunn hvis nevnt, og kjernebudskapet oppsummert i ett avsnitt)\n\n"
            "## 2. Sentrale innsikter og hovedpoenger (Key Takeaways)\n"
            "(Kulepunktliste med de viktigste faglige poengene, konklusjonene og lærdommene fra presentasjonen)\n\n"
            "## 3. Tematisk gjennomgang\n"
            "(Strukturert etter hovedtemaene i foredraget med egne underoverskrifter, sentrale argumenter, konkrete eksempler, tall/fakta og tidsstempler)\n\n"
            "## 4. Viktige begreper, modeller eller verktøy\n"
            "(Korte forklaringer av sentrale fagbegreper, metoder, teknologier eller referanser som ble gjennomgått)\n\n"
            "## 5. Anbefalinger og videre refleksjon\n"
            "(Konkrete råd til tilhørerne, åpne spørsmål eller forslag til videre oppfølging nevnt i foredraget — uten kunstig møte-aksjonstabell)\n\n"
            f"--- TRANSKRIPSJON START ---\n{transcript}\n--- TRANSKRIPSJON SLUTT ---"
        )
    else:
        system_prompt = (
            "Du er en profesjonell norsk møtesekretær. Du skriver kun selve møtereferatet i ren Markdown. "
            "IKKE inkluder egen tankeprosess, introduksjoner eller <think>-blokker. "
            "Bruk korrekt norsk (bokmål), organiser referatet etter agendaen/sakene, og bruk deltakernes "
            "faktiske navn og titler gjennom hele referatet. "
            "Hvis det er oppgitt manuelle møtenotater / referat-utkast i konteksten, skal du bruke disse som "
            "autoritativ fasit for saksstruktur, korrekt stavemåte på navn, hvem som sto bak hvilke innspill/roller, "
            "beslutninger og aksjonspunkter, samtidig som du beriker referatet med detaljer og tidsstempler fra transkripsjonen."
        )
        user_prompt = (
            "Lag et grundig og strukturert møtereferat basert på møtekonteksten og den tidsstemplede transkripsjonen under.\n\n"
            f"--- MØTEKONTEKST ---\n{context_block}\n--- MØTEKONTEKST SLUTT ---\n\n"
            "Bruk denne strukturen:\n"
            "# Møtereferat\n\n"
            "## 1. Møteinformasjon og kort oppsummering\n"
            "(Møtetittel, dato, og hva som var møtets formål og hovedkonklusjon)\n\n"
            "## 2. Deltakere og roller\n"
            "(Liste over deltakerne med fullt navn, tittel/rolle og kort om deres ansvarsområde i møtet)\n\n"
            "## 3. Gjennomgang av agenda og hovedtemaer\n"
            "(Strukturert etter agendapunktene/hovedtemaene med underoverskrifter, synspunkter knyttet til navn, og tidsstempler)\n\n"
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
                    progress_fn(pct, f"[Steg 5/5] Skriver referat/notater ({token_count} tokens)...")
            if body.get("done"):
                break

    return clean_llm_output("".join(chunks))


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
            "repetition_penalty": 1.15,
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
            align_model_name = None
            if align_lang in ("no", "nb", "nn") and any(
                tag in model_name.lower() for tag in ("small", "base", "tiny")
            ):
                align_model_name = "NbAiLab/nb-wav2vec2-300m-bokmaal"
            model_a, metadata = whisperx.load_align_model(
                language_code=align_lang, device=device, model_name=align_model_name
            )
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

LIVE_CHUNK_WORKER_CODE = """\
import json
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")

import torch
import whisperx
from whisperx.audio import load_audio

def main() -> int:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    device = cfg.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    compute_type = cfg.get("compute_type", "int8")
    language = cfg.get("language") or "no"
    model_name = cfg["model"]
    initial_prompt = cfg.get("initial_prompt") or None

    asr_options = {
        "beam_size": 1,
        "patience": 1.0,
        "length_penalty": 1.0,
        "temperatures": [0.0],
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
    print("LIVE_WORKER:READY", flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        if line == "QUIT":
            break
        try:
            req = json.loads(line)
            wav_path = req["wav_path"]
            audio = load_audio(wav_path)
            res = model.transcribe(
                audio,
                batch_size=4,
                language=language,
                print_progress=False,
            )
            segs = res.get("segments", [])
            text = " ".join(
                str(s.get("text", "")).strip()
                for s in segs
                if isinstance(s, dict) and str(s.get("text", "")).strip()
            ).strip()
            out = {
                "start": req.get("start", 0.0),
                "end": req.get("end", 0.0),
                "text": text,
            }
            print("LIVE_RESULT:" + json.dumps(out, ensure_ascii=False), flush=True)
        except Exception as exc:
            err = {
                "start": 0.0,
                "end": 0.0,
                "text": "",
                "error": str(exc),
            }
            print("LIVE_RESULT:" + json.dumps(err, ensure_ascii=False), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


def pick_live_whisper_model(preferred_model: str = "NbAiLab/nb-whisper-small") -> str:
    """Return a cached Whisper model for fast coarse real-time transcription, preferring smaller cached models."""
    hub_dir = HF_CACHE_DIR / "hub"
    candidates = [
        "NbAiLab/nb-whisper-tiny",
        "NbAiLab/nb-whisper-base",
        "NbAiLab/nb-whisper-small",
        "NbAiLab/nb-whisper-medium",
        "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2",
        preferred_model,
        "NbAiLab/nb-whisper-large",
    ]
    if hub_dir.is_dir():
        for cand in candidates:
            folder_name = "models--" + cand.replace("/", "--")
            model_dir = hub_dir / folder_name
            if model_dir.is_dir() and any(model_dir.glob("snapshots/*/*")):
                return cand
    return preferred_model


class LiveAudioMonitor:
    """Background monitor that tails an active 16kHz mono WAV recording for live dBFS level + coarse ASR."""

    def __init__(
        self,
        wav_path: Path,
        whisper_model: str = "NbAiLab/nb-whisper-small",
        language: str = "no",
        initial_prompt: str = "",
        chunk_seconds: float = 5.0,
        enable_asr: bool = True,
        on_level: Callable[[dict[str, Any]], None] | None = None,
        on_chunk: Callable[[str, dict[str, Any], str], None] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.wav_path = wav_path
        self.whisper_model = pick_live_whisper_model(whisper_model)
        self.language = language
        self.initial_prompt = initial_prompt
        self.chunk_seconds = max(2.0, chunk_seconds)
        self.enable_asr = enable_asr
        self.on_level = on_level
        self.on_chunk = on_chunk
        self.log_fn = log_fn
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        proc = self._worker_proc
        self._worker_proc = None
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("QUIT\n")
                    proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _transcribe_chunk_via_worker(
        self, chunk_wav: Path, start_s: float, end_s: float
    ) -> str:
        proc = self._worker_proc
        if not proc or proc.poll() is not None or not proc.stdin or not proc.stdout:
            return ""
        req = json.dumps(
            {"wav_path": str(chunk_wav), "start": start_s, "end": end_s},
            ensure_ascii=False,
        )
        try:
            proc.stdin.write(req + "\n")
            proc.stdin.flush()
            while not self._stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                clean = line.strip()
                if clean.startswith("LIVE_RESULT:"):
                    payload = json.loads(clean[len("LIVE_RESULT:") :])
                    return sanitize_text(str(payload.get("text", "")))
        except Exception:
            pass
        return ""

    def _run_loop(self) -> None:
        sample_rate = 16000
        bytes_per_sec = sample_rate * 2
        chunk_bytes = int(self.chunk_seconds * bytes_per_sec)
        if chunk_bytes % 2 != 0:
            chunk_bytes += 1

        with tempfile.TemporaryDirectory(prefix="mote_live_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            if self.enable_asr:
                try:
                    env = build_runtime_env()
                    wx_bin = shutil.which("whisperx", path=env["PATH"])
                    if wx_bin:
                        wx_py = get_whisperx_python(wx_bin)
                        worker_py = tmp_path / "live_worker.py"
                        worker_py.write_text(LIVE_CHUNK_WORKER_CODE, encoding="utf-8")
                        cfg_path = tmp_path / "live_cfg.json"
                        cfg_path.write_text(
                            json.dumps(
                                {
                                    "model": self.whisper_model,
                                    "language": self.language,
                                    "compute_type": "int8",
                                    "initial_prompt": self.initial_prompt,
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        self._worker_proc = subprocess.Popen(
                            [wx_py, "-u", str(worker_py), str(cfg_path)],
                            env=env,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                        )
                        if self._worker_proc.stdout:
                            for _ in range(60):
                                if self._stop_event.is_set():
                                    break
                                ln = self._worker_proc.stdout.readline()
                                if not ln or "LIVE_WORKER:READY" in ln:
                                    break
                except Exception as exc:
                    if self.log_fn:
                        self.log_fn(f"Merk: Grov sanntids-ASR kunne ikke startes ({exc}). Viser kun lydnivå.")

            data_offset: int | None = None
            next_chunk_offset = 0
            chunk_idx = 0

            while not self._stop_event.wait(1.0):
                if not self.wav_path.is_file():
                    continue
                try:
                    file_size = self.wav_path.stat().st_size
                    if file_size < 128:
                        continue
                    with self.wav_path.open("rb") as f:
                        if data_offset is None:
                            hdr = f.read(min(4096, file_size))
                            data_offset = find_wav_data_offset(hdr)
                            next_chunk_offset = data_offset

                        total_pcm_bytes = max(0, file_size - data_offset)
                        # 1-second level check
                        if total_pcm_bytes >= bytes_per_sec and self.on_level:
                            sec_start = data_offset + total_pcm_bytes - bytes_per_sec
                            if (sec_start - data_offset) % 2 != 0:
                                sec_start -= 1
                            f.seek(sec_start)
                            last_sec_pcm = f.read(bytes_per_sec)
                            lvl = analyze_pcm16_audio_level(last_sec_pcm, sample_rate)
                            self.on_level(lvl)

                        # Coarse 5-second transcription check
                        while total_pcm_bytes - (next_chunk_offset - data_offset) >= chunk_bytes:
                            if self._stop_event.is_set():
                                break
                            f.seek(next_chunk_offset)
                            pcm_slice = f.read(chunk_bytes)
                            start_s = (next_chunk_offset - data_offset) / float(bytes_per_sec)
                            end_s = start_s + (len(pcm_slice) / float(bytes_per_sec))
                            next_chunk_offset += len(pcm_slice)
                            chunk_idx += 1

                            lvl_chunk = analyze_pcm16_audio_level(pcm_slice, sample_rate)
                            text = ""
                            if self.enable_asr and lvl_chunk["status"] != "stille":
                                chunk_wav = tmp_path / f"chunk_{chunk_idx:04d}.wav"
                                chunk_wav.write_bytes(build_wav_bytes_from_pcm16(pcm_slice, sample_rate))
                                text = self._transcribe_chunk_via_worker(chunk_wav, start_s, end_s)
                                chunk_wav.unlink(missing_ok=True)

                            line_str = format_live_check_line(
                                start_s,
                                end_s,
                                float(lvl_chunk["rms_dbfs"]),
                                str(lvl_chunk["status"]),
                                text,
                            )
                            if self.on_chunk:
                                self.on_chunk(line_str, lvl_chunk, text)
                except Exception:
                    continue


def run_live_check_on_file(
    input_path: Path,
    whisper_model: str = "NbAiLab/nb-whisper-small",
    language: str = "no",
    chunk_seconds: float = 5.0,
    max_seconds: float = 25.0,
    initial_prompt: str = "",
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run coarse chunk-by-chunk real-time transcription and audio quality check on an audio file."""
    log = log_fn or (lambda msg: print(msg, flush=True))
    base_dir = Path(__file__).resolve().parent
    resolved = resolve_moved_audio_path(input_path, base_dir)
    if not resolved.is_file():
        raise FileNotFoundError(f"Fant ikke lydfilen: {input_path}")

    env = build_runtime_env()
    ffmpeg_bin = ensure_ffmpeg(env, log)
    eff_model = pick_live_whisper_model(whisper_model)
    log(f"==> GROV SANNTIDS-LYDSJEKK ({eff_model}, utsnitt på {chunk_seconds:.0f}s, maks {max_seconds:.0f}s): {resolved.name}")

    sample_rate = 16000
    bytes_per_sec = sample_rate * 2
    chunk_bytes = int(chunk_seconds * bytes_per_sec)
    if chunk_bytes % 2 != 0:
        chunk_bytes += 1

    with tempfile.TemporaryDirectory(prefix="mote_live_file_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        conv_wav = tmp_path / "converted_16k.wav"
        ffmpeg_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(resolved),
            "-t",
            str(max_seconds),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(conv_wav),
        ]
        subprocess.run(ffmpeg_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        raw_wav = conv_wav.read_bytes()
        data_offset = find_wav_data_offset(raw_wav[:4096])
        pcm_all = raw_wav[data_offset:]

        wx_bin = ensure_whisperx(env, log)
        wx_py = get_whisperx_python(wx_bin)
        worker_py = tmp_path / "live_worker.py"
        worker_py.write_text(LIVE_CHUNK_WORKER_CODE, encoding="utf-8")
        cfg_path = tmp_path / "live_cfg.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "model": eff_model,
                    "language": language,
                    "compute_type": "int8",
                    "initial_prompt": initial_prompt,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [wx_py, "-u", str(worker_py), str(cfg_path)],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            for _ in range(60):
                ln = proc.stdout.readline()
                if not ln or "LIVE_WORKER:READY" in ln:
                    break

            lines_out: list[str] = []
            statuses: list[str] = []
            dbfs_vals: list[float] = []
            offset = 0
            idx = 0

            while offset < len(pcm_all):
                pcm_slice = pcm_all[offset : offset + chunk_bytes]
                if len(pcm_slice) < sample_rate:  # skip trailing <0.5s
                    break
                start_s = offset / float(bytes_per_sec)
                end_s = (offset + len(pcm_slice)) / float(bytes_per_sec)
                offset += len(pcm_slice)
                idx += 1

                lvl = analyze_pcm16_audio_level(pcm_slice, sample_rate)
                statuses.append(str(lvl["status"]))
                dbfs_vals.append(float(lvl["rms_dbfs"]))

                text = ""
                if lvl["status"] != "stille":
                    c_wav = tmp_path / f"c_{idx:04d}.wav"
                    c_wav.write_bytes(build_wav_bytes_from_pcm16(pcm_slice, sample_rate))
                    proc.stdin.write(
                        json.dumps({"wav_path": str(c_wav), "start": start_s, "end": end_s}) + "\n"
                    )
                    proc.stdin.flush()
                    while True:
                        r_line = proc.stdout.readline()
                        if not r_line:
                            break
                        r_clean = r_line.strip()
                        if r_clean.startswith("LIVE_RESULT:"):
                            payload = json.loads(r_clean[len("LIVE_RESULT:") :])
                            text = sanitize_text(str(payload.get("text", "")))
                            break
                    c_wav.unlink(missing_ok=True)

                formatted = format_live_check_line(
                    start_s, end_s, float(lvl["rms_dbfs"]), str(lvl["status"]), text
                )
                lines_out.append(formatted)
                log(f"    {formatted}")

            proc.stdin.write("QUIT\n")
            proc.stdin.flush()
        finally:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    avg_dbfs = round(sum(dbfs_vals) / len(dbfs_vals), 1) if dbfs_vals else -96.0
    good_cnt = statuses.count("godt")
    low_cnt = statuses.count("lavt")
    silent_cnt = statuses.count("stille")
    clip_cnt = statuses.count("hoyt")
    log(
        f"==> LYDSJEKK OPPSUMMERING: Snitt {avg_dbfs:.1f} dBFS | "
        f"Godt: {good_cnt} | Lavt: {low_cnt} | Stille: {silent_cnt} | Høyt/klipp: {clip_cnt}"
    )
    return {
        "ok": True,
        "chunks": len(statuses),
        "good_chunks": good_cnt,
        "low_chunks": low_cnt,
        "silent_chunks": silent_cnt,
        "clip_chunks": clip_cnt,
        "avg_rms_dbfs": avg_dbfs,
        "lines": lines_out,
    }


def get_checkpoint_status_dict(
    out_dir: Path,
    input_path: Path,
    html_name: str = "",
    notes_name: str = "",
) -> dict[str, Any]:
    """Return checkpoint completion status for stages 1..5."""
    ckpt_dir = get_checkpoint_dir(out_dir, input_path)
    is_trans = is_transcript_file(input_path)
    stem = clean_base_stem(input_path)
    eff_html = html_name.strip() or f"{stem}_transkripsjon.html"
    eff_notes = notes_name.strip() or f"{stem}_referat.md"
    html_exists = (out_dir / Path(eff_html).name).is_file()
    notes_exists = (out_dir / Path(eff_notes).name).is_file()
    return {
        "has_input": input_path.is_file(),
        "is_transcript_input": is_trans,
        "s1_asr": is_trans or (ckpt_dir / "1_asr.json").is_file(),
        "s2_align": is_trans or (ckpt_dir / "2_align.json").is_file(),
        "s3_diarize": is_trans or (ckpt_dir / "3_diarize.json").is_file(),
        "s4_names": (ckpt_dir / "4_speakers.json").is_file() or html_exists,
        "s5_notes": notes_exists,
        "ckpt_dir": str(ckpt_dir),
    }


def run_pipeline(
    input_path: Path,
    out_dir: Path | None = None,
    html_name: str = "",
    notes_name: str = "",
    notes_html_name: str = "",
    whisper_model: str = "NbAiLab/nb-whisper-small",
    ollama_model: str = "gemma4:e4b",
    language: str = "no",
    hf_token: str = "",
    min_speakers: int | str | None = None,
    max_speakers: int | str | None = None,
    speaker_names_hint: str = "",
    meeting_title: str = "",
    agenda_text: str = "",
    context_files: list[Path] | None = None,
    manual_notes_text: str = "",
    manual_notes_files: list[Path] | None = None,
    note_mode: str = "auto",
    task_mode: str = "auto_resume",
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[int, str], None] | None = None,
    cancel_check_fn: Callable[[], bool] | None = None,
    on_proc_fn: Callable[[subprocess.Popen[str] | None], None] | None = None,
    on_checkpoint_fn: Callable[[], None] | None = None,
) -> PipelineResult:
    """Run the checkpointed 5-stage transcription, speaker diarization, name mapping, and note generation pipeline."""
    t0 = time.time()
    last_printed_pct = -10
    last_printed_stage = ""

    def _default_log(msg: str) -> None:
        elapsed = time.time() - t0
        print(f"[{elapsed:6.1f}s] {msg}", flush=True)

    def _default_progress(pct: int, label: str) -> None:
        nonlocal last_printed_pct, last_printed_stage
        stage_prefix = label.split("]")[0] if "]" in label else label[:16]
        if pct == 100 or stage_prefix != last_printed_stage or abs(pct - last_printed_pct) >= 2:
            last_printed_pct = pct
            last_printed_stage = stage_prefix
            elapsed = time.time() - t0
            print(f"[{elapsed:6.1f}s] [{pct:3d}%] {label}", flush=True)

    log = log_fn or _default_log
    prog = progress_fn or _default_progress

    base_dir = Path(__file__).resolve().parent
    resolved_input = resolve_moved_audio_path(input_path, base_dir)
    if not resolved_input.is_file():
        raise FileNotFoundError(f"Fant ikke inndatafilen: {input_path}")

    target_out_dir = (out_dir or resolved_input.parent).resolve()
    target_out_dir.mkdir(parents=True, exist_ok=True)

    stem = clean_base_stem(resolved_input)
    html_path = target_out_dir / (Path(html_name.strip()).name if html_name.strip() else f"{stem}_transkripsjon.html")
    notes_path = target_out_dir / (Path(notes_name.strip()).name if notes_name.strip() else f"{stem}_referat.md")
    notes_html_path = target_out_dir / (
        Path(notes_html_name.strip()).name if notes_html_name.strip() else f"{stem}_referat.html"
    )

    combined_agenda = agenda_text.strip()
    if context_files:
        for cf in context_files:
            cf_resolved = resolve_moved_audio_path(cf, base_dir)
            if cf_resolved.is_file():
                extracted = extract_context_from_file(cf_resolved)
                if extracted:
                    log(f"==> Lastet kontekst fra {cf_resolved.name} ({len(extracted)} tegn)")
                    combined_agenda = f"{combined_agenda}\n\n{extracted}".strip() if combined_agenda else extracted

    combined_manual_notes = manual_notes_text.strip()
    if manual_notes_files:
        for mf in manual_notes_files:
            mf_resolved = resolve_moved_audio_path(mf, base_dir)
            if mf_resolved.is_file():
                extracted_mn = extract_context_from_file(mf_resolved)
                if extracted_mn:
                    log(f"==> Lastet manuelle møtenotater fra {mf_resolved.name} ({len(extracted_mn)} tegn)")
                    combined_manual_notes = (
                        f"{combined_manual_notes}\n\n{extracted_mn}".strip()
                        if combined_manual_notes
                        else extracted_mn
                    )

    ckpt_dir = get_checkpoint_dir(target_out_dir, resolved_input)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    asr_json = ckpt_dir / "1_asr.json"
    align_json = ckpt_dir / "2_align.json"
    diarize_json = ckpt_dir / "3_diarize.json"
    speakers_json = ckpt_dir / "4_speakers.json"

    is_trans_input = is_transcript_file(resolved_input)
    run_s1, run_s2, run_s3, run_s4, run_s5 = determine_stages_to_run(
        task_mode=task_mode,
        is_trans_input=is_trans_input,
        has_s1=asr_json.is_file(),
        has_s2=align_json.is_file(),
        has_s3=diarize_json.is_file(),
        has_s4=speakers_json.is_file(),
        has_html=html_path.is_file(),
    )

    executed_stages: list[str] = []
    wx_stages: list[str] = []
    if run_s1:
        wx_stages.append("asr")
    if run_s2:
        wx_stages.append("align")
    if run_s3:
        wx_stages.append("diarize")

    env = build_runtime_env()
    resolved_min_spk, resolved_max_spk = resolve_speaker_bounds(
        min_speakers, max_speakers, speaker_names_hint
    )
    min_spk_str = str(resolved_min_spk) if resolved_min_spk is not None else ""
    max_spk_str = str(resolved_max_spk) if resolved_max_spk is not None else ""

    if wx_stages:
        eff_token = hf_token.strip() or get_initial_hf_token()
        if "diarize" in wx_stages:
            if eff_token:
                save_hf_token(eff_token)
            elif not is_diarization_cached() and not (resolved_min_spk == 1 and resolved_max_spk == 1):
                raise RuntimeError("Pyannote er ikke bufret ennå. Oppgi HF_TOKEN eller --hf-token.")

        ensure_ffmpeg(env, log)
        whisperx_bin = ensure_whisperx(env, log, prog)
        wx_python = get_whisperx_python(whisperx_bin)
        initial_prompt = build_whisper_initial_prompt(
            meeting_title, speaker_names_hint, combined_agenda, combined_manual_notes
        )
        if initial_prompt:
            log(f"==> Kontekst-ordliste til Whisper ({len(initial_prompt)} tegn) aktivert.")

        skipped = [s for s in ("asr", "align", "diarize") if s not in wx_stages]
        if skipped:
            log(f"==> Sjekkpunkt-gjenopptak: Gjenbruker ({', '.join(skipped)}) fra {ckpt_dir.name}/")

        with tempfile.TemporaryDirectory(prefix="mote_wx_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            staged_audio = stage_audio_input(resolved_input, tmp_dir_path)
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
                "hf_token": eff_token,
                "min_speakers": resolved_min_spk,
                "max_speakers": resolved_max_spk,
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
            if on_proc_fn is not None:
                on_proc_fn(proc)
            assert proc.stdout is not None
            phase = 0 if "asr" in wx_stages else (1 if "align" in wx_stages else 2)
            diarization_start: float | None = None
            last_diarize_pct = 0.0
            stop_heartbeat = threading.Event()

            def _diarization_heartbeat() -> None:
                while not stop_heartbeat.wait(2.0):
                    if phase == 2 and diarization_start is not None:
                        elapsed = int(time.time() - diarization_start)
                        mm, ss = divmod(elapsed, 60)
                        if last_diarize_pct > 0:
                            mapped = int(60 + (last_diarize_pct * 0.14))
                            prog(
                                mapped,
                                f"[Steg 3/5] Pyannote taler-ID: {last_diarize_pct:.0f}% ({mm:02d}:{ss:02d})...",
                            )
                        else:
                            creep = min(13, elapsed // 15)
                            prog(
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
                        prog(12, f"[Steg 1/5] Transkriberer med {whisper_model}...")
                        log(f"==> [Steg 1/5] Kjører WhisperX ASR ({whisper_model}) på {resolved_input.name}...")
                        continue
                    if clean == "STAGE:ASR:DONE":
                        executed_stages.append("1_asr")
                        log("==> [Steg 1/5] Rå-transkripsjon lagret i sjekkpunkt (1_asr.json).")
                        if on_checkpoint_fn is not None:
                            on_checkpoint_fn()
                        continue
                    if clean == "STAGE:ALIGN:START":
                        phase = 1
                        prog(48, "[Steg 2/5] Tidsjusterer ord (Alignment): 0%")
                        log("==> [Steg 2/5] Starter ord-tidsjustering (Alignment)...")
                        continue
                    if clean == "STAGE:ALIGN:DONE":
                        executed_stages.append("2_align")
                        log("==> [Steg 2/5] Tidsjustering lagret i sjekkpunkt (2_align.json).")
                        if on_checkpoint_fn is not None:
                            on_checkpoint_fn()
                        continue
                    if clean == "STAGE:DIARIZE:START":
                        phase = 2
                        last_diarize_pct = 0.0
                        diarization_start = time.time()
                        prog(60, "[Steg 3/5] Identifiserer talere med Pyannote (00:00)...")
                        log("==> [Steg 3/5] Starter Pyannote taler-identifisering...")
                        continue
                    if clean == "STAGE:DIARIZE:DONE":
                        executed_stages.append("3_diarize")
                        log("==> [Steg 3/5] Taler-identifisering lagret i sjekkpunkt (3_diarize.json).")
                        if on_checkpoint_fn is not None:
                            on_checkpoint_fn()
                        continue

                    d_match = re.search(r"DiarizeProgress:\s*([0-9]+(?:\.[0-9]+)?)%", clean)
                    if d_match:
                        last_diarize_pct = float(d_match.group(1))
                        mapped = int(60 + (last_diarize_pct * 0.14))
                        elapsed = int(time.time() - (diarization_start or time.time()))
                        mm, ss = divmod(elapsed, 60)
                        prog(
                            mapped,
                            f"[Steg 3/5] Pyannote taler-ID: {last_diarize_pct:.0f}% ({mm:02d}:{ss:02d})...",
                        )
                        continue

                    match = re.search(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%", clean)
                    if match:
                        if log_fn is not None:
                            log(clean)
                        p_pct = float(match.group(1))
                        if phase == 0:
                            mapped = int(12 + (p_pct * 0.35))
                            prog(mapped, f"[Steg 1/5] Transkriberer tale: {p_pct:.0f}%")
                        elif phase == 1:
                            mapped = int(48 + (p_pct * 0.11))
                            prog(mapped, f"[Steg 2/5] Tidsjusterer ord (Alignment): {p_pct:.0f}%")
                    else:
                        log(clean)
            finally:
                stop_heartbeat.set()
                if on_proc_fn is not None:
                    on_proc_fn(None)

            rc = proc.wait()
            if cancel_check_fn is not None and cancel_check_fn():
                if on_checkpoint_fn is not None:
                    on_checkpoint_fn()
                return {
                    "ok": False,
                    "input_path": resolved_input,
                    "out_dir": target_out_dir,
                    "ckpt_dir": ckpt_dir,
                    "html_path": html_path,
                    "notes_path": notes_path,
                    "notes_html_path": notes_html_path,
                    "executed_stages": executed_stages,
                    "effective_mode": note_mode,
                    "speaker_map": {},
                    "turn_count": 0,
                }
            if rc != 0:
                raise RuntimeError(f"WhisperX feilet med returkode {rc}.")

    if not run_s4 and not run_s5:
        prog(100, f"Steg ({', '.join(wx_stages)}) fullført og lagret i sjekkpunkt")
        return {
            "ok": True,
            "input_path": resolved_input,
            "out_dir": target_out_dir,
            "ckpt_dir": ckpt_dir,
            "html_path": html_path,
            "notes_path": notes_path,
            "notes_html_path": notes_html_path,
            "executed_stages": executed_stages,
            "effective_mode": note_mode,
            "speaker_map": {},
            "turn_count": 0,
        }

    turns: list[TurnDict] = []
    speaker_map: dict[str, str] = {}

    if is_trans_input:
        log(f"==> Leser inn eksisterende transkripsjon fra {resolved_input.name}...")
        turns, speaker_map = load_turns_from_transcript_file(resolved_input)
        if not turns:
            raise RuntimeError(f"Klarte ikke hente ut replikker fra {resolved_input.name}.")
    elif not run_s4 and speakers_json.is_file():
        saved_s4 = json.loads(speakers_json.read_text(encoding="utf-8"))
        turns = saved_s4.get("turns", [])
        speaker_map = saved_s4.get("speaker_map", {})
        log(f"==> Gjenbruker taler-navnekobling fra sjekkpunkt (4_speakers.json, {len(turns)} replikker).")
    elif not run_s4 and html_path.is_file():
        turns, speaker_map = load_turns_from_transcript_file(html_path)
        log(f"==> Gjenbruker ferdig HTML-transkripsjon ({html_path.name}, {len(turns)} replikker).")
    else:
        source_json = (
            diarize_json
            if diarize_json.is_file()
            else (align_json if align_json.is_file() else asr_json)
        )
        if not source_json.is_file():
            raise RuntimeError("Fant ingen transkripsjonsdata å bygge videre på.")
        turns = extract_turns_from_whisperx(source_json)

    doc_title = meeting_title.strip() or stem
    effective_mode = resolve_effective_note_mode(note_mode, min_spk_str, max_spk_str, speaker_map)

    with managed_ollama_server(log, prog, env=env) as ollama_bin:
        installed = fetch_ollama_models() or []
        if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
            prog(75, f"Laster ned {ollama_model}...")
            log(f"==> Laster ned '{ollama_model}'...")
            subprocess.run([ollama_bin, "pull", ollama_model], env=env, check=True)

        if run_s4:
            prog(78, "[Steg 4/5] Gjenkjenner deltakernavn og titler...")
            log("==> [Steg 4/5] Analyserer introduksjoner og kontekst for å knytte taler-ID til navn...")
            inferred_map = infer_speaker_names_via_api(
                turns,
                ollama_model,
                speaker_names_hint,
                meeting_title,
                combined_agenda,
                log,
                manual_notes_text=combined_manual_notes,
            )
            merged_map = dict(speaker_map)
            merged_map.update(inferred_map)
            speaker_map = merged_map
            for spk_id, resolved_name in speaker_map.items():
                if resolved_name != spk_id:
                    log(f"    Gjenkjent {spk_id} -> {resolved_name}")

            clean_transcript = build_transcript_html_and_text(turns, speaker_map, html_path, doc_title)
            tmp_s4 = speakers_json.with_suffix(".json.tmp")
            tmp_s4.write_text(
                json.dumps({"speaker_map": speaker_map, "turns": turns}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_s4.replace(speakers_json)
            executed_stages.append("4_speakers")
            if on_checkpoint_fn is not None:
                on_checkpoint_fn()
            log(f"==> [Steg 4/5] Rik HTML-transkripsjon lagret: {html_path}")
        else:
            if not html_path.is_file():
                clean_transcript = build_transcript_html_and_text(turns, speaker_map, html_path, doc_title)
            else:
                lines_out: list[str] = []
                for t in turns:
                    spk = speaker_map.get(t["speaker"], t["speaker"])
                    ts = f"{format_seconds(t['start'])} – {format_seconds(t['end'])}"
                    lines_out.append(f"[{ts}] {spk}: {t['text']}")
                clean_transcript = "\n\n".join(lines_out)

        effective_mode = resolve_effective_note_mode(note_mode, min_spk_str, max_spk_str, speaker_map)

        if not run_s5:
            prog(100, "Steg 4 (Taler-ID & HTML-transkripsjon) fullført!")
            return {
                "ok": True,
                "input_path": resolved_input,
                "out_dir": target_out_dir,
                "ckpt_dir": ckpt_dir,
                "html_path": html_path,
                "notes_path": notes_path,
                "notes_html_path": notes_html_path,
                "executed_stages": executed_stages,
                "effective_mode": effective_mode,
                "speaker_map": speaker_map,
                "turn_count": len(turns),
            }

        mode_desc = (
            "foredragsnotater (1-talers modus / faglige hovedpoenger)"
            if effective_mode == "foredrag"
            else "møtereferat (flere deltakere & aksjonspunkter)"
        )
        prog(84, f"[Steg 5/5] Skriver {mode_desc} med {ollama_model}...")
        log(f"==> [Steg 5/5] Genererer {mode_desc} ({ollama_model})...")

        notes_md = generate_notes_via_api(
            clean_transcript,
            ollama_model,
            speaker_map,
            speaker_names_hint,
            meeting_title,
            combined_agenda,
            progress_fn=prog,
            note_mode=effective_mode,
            manual_notes_text=combined_manual_notes,
        )
        notes_path.write_text(notes_md + "\n", encoding="utf-8")
        prog(99, "Bygger HTML-utgave av referat/notater...")
        build_notes_html(notes_md, notes_html_path, doc_title)
        executed_stages.append("5_notes")
        if on_checkpoint_fn is not None:
            on_checkpoint_fn()

    prog(100, "Fullført!")
    log(
        f"==> FERDIG!\n"
        f"    HTML-transkripsjon: {html_path}\n"
        f"    Referat/notat (MD): {notes_path}\n"
        f"    Referat/notat(HTML): {notes_html_path}"
    )
    return {
        "ok": True,
        "input_path": resolved_input,
        "out_dir": target_out_dir,
        "ckpt_dir": ckpt_dir,
        "html_path": html_path,
        "notes_path": notes_path,
        "notes_html_path": notes_html_path,
        "executed_stages": executed_stages,
        "effective_mode": effective_mode,
        "speaker_map": speaker_map,
        "turn_count": len(turns),
    }


def build_cli_parser(ui_lang: str | None = None, argv: list[str] | None = None) -> Any:
    """Create the command-line ArgumentParser for steno / steno.py and transcribe.py (English & Norwegian)."""
    import argparse

    lang = resolve_ui_lang(ui_lang, argv if argv is not None else sys.argv[1:])
    is_en = lang == "en"

    parser = argparse.ArgumentParser(
        prog="steno",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            (
                "Steno - a modern annalist for your meetings - an automaton stenographer.\n"
                "100% local meeting transcription, speaker diarization (WhisperX + Pyannote),\n"
                "and meeting minutes / lecture notes (Ollama) with stage checkpoints and crash-resume."
            )
            if is_en
            else (
                "Steno - a modern annalist for your meetings - an automaton stenographer.\n"
                "100% lokal møtetranskripsjon, taler-separasjon (WhisperX + Pyannote)\n"
                "og møtereferat / foredragsnotater (Ollama) med sjekkpunkter og krasj-gjenopptak."
            )
        ),
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        default=None,
        help=(
            "Path to audio file (.wav, .m4a, .opus, .mp3) or existing transcript (.html, .json, .txt, .srt, .vtt)"
            if is_en
            else "Sti til lydfil (.wav, .m4a, .opus, .mp3) eller eksisterende transkripsjon (.html, .json, .txt, .srt, .vtt)"
        ),
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Directory for output files and .mote_checkpoints (default: same folder as input_file)"
            if is_en
            else "Mappe for utdatafiler og .mote_checkpoints (standard: samme mappe som input_file)"
        ),
    )
    parser.add_argument(
        "--ui-lang",
        choices=["no", "en"],
        default=lang,
        help=(
            "Interface and help language: 'en' (English) or 'no' (Norwegian, default)"
            if is_en
            else "Språk for hjelpetekst og brukergrensesnitt: 'no' (norsk, standard) eller 'en' (engelsk)"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=["auto", "light", "fast", "recommended", "custom"],
        default="custom",
        help=(
            (
                "Model profile: 'auto' selects recommended models from hardware (RAM/CPU/GPU), "
                "'light' (8 GB RAM), 'fast' (Distil-Turbo + Gemma 4), 'recommended' (NB-Whisper Small + Gemma 4)"
            )
            if is_en
            else (
                "Modellprofil: 'auto' velger anbefalt kombinasjon ut fra maskinvare (RAM/CPU/GPU), "
                "'light' (8 GB RAM), 'fast' (Distil-Turbo + Gemma 4), 'recommended' (NB-Whisper Small + Gemma 4)"
            )
        ),
    )
    parser.add_argument(
        "--check-hw",
        action="store_true",
        help=(
            "Print detected hardware, resource fit status, and recommended model profile as JSON, then exit"
            if is_en
            else "Vis oppdaget maskinvare, ressursstatus og anbefalt modellkombinasjon, og avslutt"
        ),
    )
    parser.add_argument(
        "-w",
        "--whisper-model",
        default="NbAiLab/nb-whisper-small",
        help=(
            "Whisper ASR model (default: %(default)s)"
            if is_en
            else "Whisper-modell (standard: %(default)s)"
        ),
    )
    parser.add_argument(
        "-m",
        "--ollama-model",
        default="gemma4:e4b",
        help=(
            "Ollama LLM model for speaker name mapping and meeting notes (default: %(default)s)"
            if is_en
            else "Ollama-modell for navnegjenkjenning og referat (standard: %(default)s)"
        ),
    )
    parser.add_argument(
        "-l",
        "--language",
        default="no",
        help=(
            "Spoken audio language code for transcription, e.g. 'no' or 'en' (default: %(default)s)"
            if is_en
            else "Språkkode for transkripsjon (standard: %(default)s)"
        ),
    )
    parser.add_argument(
        "--min-speakers",
        type=int,
        default=None,
        help=(
            "Minimum number of speakers (e.g. 1 for lecture or 4 for a 4-person meeting)"
            if is_en
            else "Minimum antall talere (f.eks. 1 for foredrag eller 4 for 4-talers møte)"
        ),
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        default=None,
        help="Maximum number of speakers" if is_en else "Maksimalt antall talere",
    )
    parser.add_argument(
        "-n",
        "--speakers",
        type=int,
        default=None,
        help=(
            "Lock to exactly N speakers (sets both --min-speakers=N and --max-speakers=N)"
            if is_en
            else "Lås til nøyaktig N talere (setter både --min-speakers=N og --max-speakers=N)"
        ),
    )
    parser.add_argument(
        "--speaker-names",
        default="",
        help=(
            "Comma-separated list of known participants/roles (e.g. 'Kari Nordmann (Chair), Ola Hansen')"
            if is_en
            else "Kommaseparert liste med kjente deltakere/roller (f.eks. 'Kari Nordmann (Leder), Ola Hansen')"
        ),
    )
    parser.add_argument(
        "--title",
        default="",
        help=(
            "Meeting title and date (e.g. 'Status Meeting – 2026-10-29')"
            if is_en
            else "Møtetittel og dato (f.eks. 'Statusmøte – 2026-10-29')"
        ),
    )
    parser.add_argument(
        "--agenda",
        default="",
        help="Agenda/context as plain text" if is_en else "Agenda/kontekst som ren tekst",
    )
    parser.add_argument(
        "-c",
        "--context-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Path to agenda, slides, or document (.pptx, .pdf, .odp, .docx, .md, .txt). Can be passed multiple times."
            if is_en
            else "Sti til agenda, lysbilder eller dokument (.pptx, .pdf, .odp, .docx, .md, .txt). Kan oppgis flere ganger."
        ),
    )
    parser.add_argument(
        "--manual-notes",
        default="",
        help=(
            "Manual meeting notes / draft minutes as plain text to verify names, topics, and decisions."
            if is_en
            else "Manuelle møtenotater / referat-utkast som ren tekst for å kvalitetssikre navn, saker og vedtak."
        ),
    )
    parser.add_argument(
        "-M",
        "--manual-notes-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Path to manual meeting notes / draft minutes (.odt, .docx, .pdf, .md, .txt). Can be passed multiple times."
            if is_en
            else "Sti til manuelle møtenotater / referat-utkast (.odt, .docx, .pdf, .md, .txt). Kan oppgis flere ganger."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=list(VALID_NOTE_MODES),
        default="auto",
        help=(
            "Note mode: auto (lecture if 1 speaker, else meeting), mote (meeting minutes), foredrag (lecture notes)"
            if is_en
            else "Notatmodus: auto (foredrag ved 1 taler, ellers møte), mote (møtereferat), foredrag (foredragsnotater)"
        ),
    )
    parser.add_argument(
        "--task",
        choices=list(VALID_TASK_MODES),
        default="auto_resume",
        help=(
            "Task / stage execution mode (default: auto_resume to continue from last saved checkpoint)"
            if is_en
            else "Oppgavemodus / stegvalg (standard: auto_resume som fortsetter fra siste sjekkpunkt)"
        ),
    )
    parser.add_argument(
        "--hf-token",
        default="",
        help=(
            "Optional Hugging Face Read token (otherwise uses HF_TOKEN or ~/.cache/huggingface/token)"
            if is_en
            else "Valgfri Hugging Face Read-token (ellers brukes HF_TOKEN eller ~/.cache/huggingface/token)"
        ),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help=(
            "Print checkpoint completion status (Stages 1–5) as JSON without running"
            if is_en
            else "Vis kun sjekkpunkt-status (Steg 1–5) for valgt fil uten å kjøre"
        ),
    )
    parser.add_argument(
        "--live-check",
        action="store_true",
        help=(
            "Run coarse real-time transcription and audio level check (dBFS) on input file or default microphone"
            if is_en
            else "Kjør grov sanntids-transkripsjon og lydnivåsjekk (dBFS) på valgt lydfil eller direkte fra mikrofon"
        ),
    )
    parser.add_argument(
        "--live-seconds",
        type=float,
        default=20.0,
        help=(
            "Duration in seconds for --live-check (default: %(default)s)"
            if is_en
            else "Varighet i sekunder for --live-check (standard: %(default)s)"
        ),
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open the desktop GUI window explicitly" if is_en else "Åpne skrivebordsvinduet (GUI) eksplisitt",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line interface (CLI) mode" if is_en else "Kjør i kommandolinjemodus (CLI)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Run built-in self-test of embedded templates, API, and pipeline without opening a GUI window"
            if is_en
            else "Kjør innebygd selvtest av maler, API og rørledning uten å åpne GUI-vindu"
        ),
    )
    return parser


def run_live_mic_check(
    duration_sec: float = 20.0,
    whisper_model: str = "NbAiLab/nb-whisper-small",
    language: str = "no",
    initial_prompt: str = "",
) -> int:
    """Record from default microphone and stream live dBFS levels + 5s coarse transcription to terminal."""
    env = build_runtime_env()
    ffmpeg_bin = ensure_ffmpeg(env, lambda msg: print(msg, flush=True))
    with tempfile.TemporaryDirectory(prefix="mote_mic_check_") as tmp_dir:
        rec_file = Path(tmp_dir) / "mic_live_check.wav"
        cmd = build_ffmpeg_mic_record_cmd(ffmpeg_bin, rec_file, env=env)

        print(
            f"==> GROV SANNTIDS-LYDSJEKK FRA MIKROFON ({duration_sec:.0f}s – trykk Ctrl+C for å avslutte)...",
            flush=True,
        )
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        monitor = LiveAudioMonitor(
            wav_path=rec_file,
            whisper_model=whisper_model,
            language=language,
            initial_prompt=initial_prompt,
            chunk_seconds=5.0,
            enable_asr=True,
            on_level=lambda lvl: print(f"    [Lydnivå nå] {lvl['label']}", flush=True),
            on_chunk=lambda line, _lvl, _txt: print(f"==> {line}", flush=True),
        )
        monitor.start()
        try:
            deadline = time.time() + duration_sec
            while time.time() < deadline and proc.poll() is None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n==> Avslutter mikrofonsjekk...", flush=True)
        finally:
            monitor.stop()
            if proc.poll() is None:
                try:
                    if proc.stdin:
                        proc.stdin.write("q\n")
                        proc.stdin.flush()
                    proc.wait(timeout=2)
                except Exception:
                    proc.terminate()
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for steno.py and transcribe.py."""
    parser = build_cli_parser(argv=argv)
    args = parser.parse_args(argv)

    hw_info = detect_hardware_resources(args.out_dir)
    if args.check_hw:
        fit = evaluate_model_resource_fit(args.whisper_model, args.ollama_model, hw_info)
        print(json.dumps({"hardware": hw_info, "resource_fit": fit}, indent=2, ensure_ascii=False))
        return 0

    if args.profile == "auto":
        args.whisper_model = hw_info["recommended_whisper"]
        args.ollama_model = hw_info["recommended_ollama"]
        print(
            f"==> [Profil: auto] Maskinvare: {hw_info['hardware_summary']}\n"
            f"    Valgte anbefalte modeller: ASR={args.whisper_model}, LLM={args.ollama_model}",
            flush=True,
        )
    elif args.profile in MODEL_PROFILES:
        prof = MODEL_PROFILES[args.profile]
        args.whisper_model = prof["whisper_model"]
        args.ollama_model = prof["ollama_model"]
        print(
            f"==> [Profil: {args.profile}] {prof['label']} -> ASR={args.whisper_model}, LLM={args.ollama_model}",
            flush=True,
        )

    fit_check = evaluate_model_resource_fit(args.whisper_model, args.ollama_model, hw_info)
    if fit_check["level"] in ("critical", "warning"):
        print(f"==> RESSURS-ADVARSEL: {fit_check['message']}", file=sys.stderr, flush=True)

    initial_prompt = build_whisper_initial_prompt(args.title, args.speaker_names, args.agenda)

    if args.live_check and args.input_file is None:
        return run_live_mic_check(
            duration_sec=args.live_seconds,
            whisper_model=args.whisper_model,
            language=args.language,
            initial_prompt=initial_prompt,
        )

    if args.input_file is None:
        parser.error("Du må oppgi 'input_file' (eller bruke '--live-check' for direkte mikrofonsjekk).")

    base_dir = Path(__file__).resolve().parent
    resolved_input = resolve_moved_audio_path(args.input_file, base_dir)
    target_out_dir = (args.out_dir or resolved_input.parent).resolve()

    if args.status:
        st = get_checkpoint_status_dict(target_out_dir, resolved_input)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0

    if args.live_check:
        run_live_check_on_file(
            input_path=resolved_input,
            whisper_model=args.whisper_model,
            language=args.language,
            chunk_seconds=5.0,
            max_seconds=args.live_seconds,
            initial_prompt=initial_prompt,
        )
        return 0

    min_spk, max_spk = resolve_speaker_bounds(
        args.speakers if args.speakers is not None else args.min_speakers,
        args.speakers if args.speakers is not None else args.max_speakers,
        args.speaker_names,
    )

    try:
        run_pipeline(
            input_path=resolved_input,
            out_dir=target_out_dir,
            whisper_model=args.whisper_model,
            ollama_model=args.ollama_model,
            language=args.language,
            hf_token=args.hf_token,
            min_speakers=min_spk,
            max_speakers=max_spk,
            speaker_names_hint=args.speaker_names,
            meeting_title=args.title,
            agenda_text=args.agenda,
            context_files=args.context_file,
            manual_notes_text=args.manual_notes,
            manual_notes_files=args.manual_notes_file,
            note_mode=args.mode,
            task_mode=args.task,
        )
        return 0
    except KeyboardInterrupt:
        print(
            "\n==> AVBRUTT AV BRUKER (Ctrl+C): Fullførte steg er lagret i .mote_checkpoints/. "
            "Kjør samme kommando på nytt for å fortsette der den slapp.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(f"FEIL: {exc}", file=sys.stderr)
        return 1


_FALLBACK_TRANSCRIPT_TPL = """<!DOCTYPE html>
<html lang="no">
<head><meta charset="utf-8"><title>{escaped_title} – Transkripsjon</title></head>
<body>
  <h1>{escaped_title} ({total_turns} innlegg, {speaker_count} deltakere, {total_duration_str})</h1>
  <div>{speaker_filters}</div>
  <div>{speaker_inputs}</div>
  <main id="container">{html_items}</main>
  <script>function saveUpdatedHtml() {{}}</script>
</body>
</html>"""

_FALLBACK_NOTES_TPL = """<!DOCTYPE html>
<html lang="no">
<head><meta charset="utf-8"><title>{escaped_title} – Møtereferat</title></head>
<body>
  <h1>{escaped_title}</h1>
  <article>{rendered_body}</article>
  <textarea id="raw-markdown" style="display:none">{escaped_notes_md}</textarea>
</body>
</html>"""
