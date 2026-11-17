"""Shared constants, preferences, path helpers, language detection, and pipeline state utilities."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile  # noqa: F401 - used in doctests
import unicodedata
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

APP_SUPPORT_DIR = Path.home() / ".local" / "share" / "mote_transcribe"
LOCAL_BIN_DIR = APP_SUPPORT_DIR / "bin"
OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE}/api/tags"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE}/api/chat"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface"
HF_TOKEN_FILE = HF_CACHE_DIR / "token"
USER_PREFS_FILE = APP_SUPPORT_DIR / "user_prefs.json"

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
TASK_MODE_ALIASES: dict[str, str] = {
    "full": "full_fresh",
    "names_and_notes": "from_4_names",
    "notes_only": "only_5_notes",
    "transcribe_s1": "only_1_asr",
    "transcribe_s2": "only_2_align",
    "transcribe_s3": "only_3_diarize",
    "transcribe_only": "only_4_names",
}
VALID_NOTE_MODES: tuple[str, ...] = ("auto", "mote", "foredrag")
VALID_COLOR_SCHEMES: tuple[str, ...] = ("system", "light", "dark")

LANGUAGE_LABELS: dict[str, tuple[str, str]] = {
    "auto": ("Auto (oppdag språk)", "Auto (detect language)"),
    "no": ("Norsk", "Norwegian"),
    "nb": ("Norsk bokmål", "Norwegian Bokmål"),
    "nn": ("Norsk nynorsk", "Norwegian Nynorsk"),
    "en": ("Engelsk", "English"),
    "sv": ("Svensk", "Swedish"),
    "da": ("Dansk", "Danish"),
    "de": ("Tysk", "German"),
    "es": ("Spansk", "Spanish"),
    "fr": ("Fransk", "French"),
    "fi": ("Finsk", "Finnish"),
    "nl": ("Nederlandsk", "Dutch"),
    "pl": ("Polsk", "Polish"),
    "it": ("Italiensk", "Italian"),
    "pt": ("Portugisisk", "Portuguese"),
    "uk": ("Ukrainsk", "Ukrainian"),
    "ar": ("Arabisk", "Arabic"),
    "zh": ("Kinesisk", "Chinese"),
    "ja": ("Japansk", "Japanese"),
    "ko": ("Koreansk", "Korean"),
    "tr": ("Tyrkisk", "Turkish"),
    "hi": ("Hindi", "Hindi"),
}

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


def normalize_task_mode(task_mode: str) -> str:
    """Normalize GUI and CLI task_mode aliases into canonical VALID_TASK_MODES names.

    >>> normalize_task_mode("full")
    'full_fresh'
    >>> normalize_task_mode("notes_only")
    'only_5_notes'
    >>> normalize_task_mode("from_3_diarize")
    'from_3_diarize'
    """
    cleaned = (task_mode or "").strip()
    canonical = TASK_MODE_ALIASES.get(cleaned, cleaned)
    return canonical if canonical in VALID_TASK_MODES else "auto_resume"


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


def is_media_url(url: str | Path | None) -> bool:
    """Return True if url represents a web, streaming, or network media location.

    >>> is_media_url("https://hypertekst.net/steno/test_meeting.opus")
    True
    >>> is_media_url("http://example.com/live/stream.m3u8")
    True
    >>> is_media_url("rtmp://live.stream.org/audio")
    True
    >>> is_media_url("meeting.wav")
    False
    >>> is_media_url(Path("/tmp/audio.mp3"))
    False
    >>> is_media_url(None)
    False
    """
    if url is None:
        return False
    s = str(url).strip()
    parsed = urllib.parse.urlparse(s)
    return parsed.scheme.lower() in ("http", "https", "rtmp", "rtmps", "rtsp", "mms", "hls")


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


def unique_filename(directory: Path, stem: str, suffix: str) -> str:
    """Return a non-colliding filename inside directory.

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
    norm_mode = normalize_task_mode(task_mode)
    if norm_mode == "full_fresh":
        return (not is_trans_input, not is_trans_input, not is_trans_input, True, True)
    if norm_mode == "only_1_asr":
        return (not is_trans_input, False, False, False, False)
    if norm_mode == "only_2_align":
        return (not is_trans_input and not has_s1, not is_trans_input, False, False, False)
    if norm_mode == "only_3_diarize":
        return (
            not is_trans_input and not has_s1 and not has_s2,
            not is_trans_input and not has_s2,
            not is_trans_input,
            False,
            False,
        )
    if norm_mode == "from_3_diarize":
        return (
            not is_trans_input and not has_s1 and not has_s2,
            not is_trans_input and not has_s2,
            not is_trans_input,
            True,
            True,
        )
    if norm_mode == "from_4_names":
        return (
            not is_trans_input and not has_s3 and not has_s2 and not has_s1,
            not is_trans_input and not has_s3 and not has_s2,
            not is_trans_input and not has_s3,
            True,
            True,
        )
    if norm_mode == "only_4_names":
        return (
            not is_trans_input and not has_s3 and not has_s2 and not has_s1,
            not is_trans_input and not has_s3 and not has_s2,
            not is_trans_input and not has_s3,
            True,
            False,
        )
    if norm_mode == "only_5_notes":
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


def is_termux(env: Mapping[str, str] | None = None, prefix_dir: Path | None = None) -> bool:
    """Detect whether the current environment is Android Termux (aarch64-linux-android).

    >>> is_termux({"TERMUX_VERSION": "0.118.1"}, prefix_dir=Path("/nonexistent"))
    True
    >>> is_termux({"PREFIX": "/data/data/com.termux/files/usr"}, prefix_dir=Path("/nonexistent"))
    True
    >>> is_termux({}, prefix_dir=Path("/nonexistent"))
    False
    """
    e = env if env is not None else os.environ
    if e.get("TERMUX_VERSION") or e.get("STENO_TERMUX") == "1":
        return True
    if "com.termux" in e.get("PREFIX", ""):
        return True
    pdir = prefix_dir if prefix_dir is not None else Path("/data/data/com.termux/files/usr")
    if pdir.is_dir():
        return True
    if env is None and "android" in platform.platform().lower():
        return True
    return False


def detect_cpu_arch(machine_str: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Return normalized CPU architecture ('arm64' or 'amd64') across Linux, macOS, Windows, and Termux.

    >>> detect_cpu_arch("aarch64", {})
    'arm64'
    >>> detect_cpu_arch("ARM64", {})
    'arm64'
    >>> detect_cpu_arch("x86_64", {})
    'amd64'
    >>> detect_cpu_arch("AMD64", {})
    'amd64'
    >>> detect_cpu_arch("AMD64", {"PROCESSOR_ARCHITEW6432": "ARM64"})
    'arm64'
    """
    e = env if env is not None else os.environ
    wow_arch = e.get("PROCESSOR_ARCHITEW6432", "").strip().lower()
    proc_arch = e.get("PROCESSOR_ARCHITECTURE", "").strip().lower()
    raw = (machine_str if machine_str is not None else platform.machine()).strip().lower()
    for token in (wow_arch, raw, proc_arch):
        if token in ("arm64", "aarch64", "armv8l", "armv8b") or token.startswith("aarch64"):
            return "arm64"
    return "amd64"


def resolve_posix_shell(env: Mapping[str, str] | None = None) -> str:
    """Return an executable POSIX sh binary path, supporting Android Termux ($PREFIX/bin/sh) and standard UNIX.

    >>> resolve_posix_shell({"PREFIX": "/nonexistent"}).endswith("sh")
    True
    """
    e = env if env is not None else os.environ
    prefix = e.get("PREFIX", "").strip()
    if prefix:
        cand = Path(prefix) / "bin" / "sh"
        if cand.is_file():
            return str(cand)
    termux_sh = Path("/data/data/com.termux/files/usr/bin/sh")
    if termux_sh.is_file():
        return str(termux_sh)
    if Path("/bin/sh").is_file():
        return "/bin/sh"
    found = shutil.which("sh", path=e.get("PATH"))
    if found:
        return found
    if Path("/system/bin/sh").is_file():
        return "/system/bin/sh"
    return "/bin/sh"


def build_runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore::UserWarning:pyannote.audio.core.io"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if os.name != "nt":
        env.setdefault("LC_ALL", "en_US.UTF-8")
    termux_prefix = env.get("PREFIX", "/data/data/com.termux/files/usr")
    extra = [
        str(LOCAL_BIN_DIR),
        str(Path(termux_prefix) / "bin"),
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
            extra.append(str(Path(local_app) / "uv" / "bin"))
            extra.append(str(Path(local_app) / "Programs" / "Ollama"))
            extra.append(str(Path(local_app) / "Microsoft" / "WinGet" / "Links"))
        app_data = os.environ.get("APPDATA", "")
        if app_data:
            extra.append(str(Path(app_data) / "uv" / "tools" / "whisperx" / "Scripts"))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def normalize_audio_language(lang: str | None) -> str:
    """Normalize a speech/audio language code or name into a canonical Whisper ISO code (or 'auto').

    >>> normalize_audio_language("no")
    'no'
    >>> normalize_audio_language("nb-NO")
    'no'
    >>> normalize_audio_language("en_US")
    'en'
    >>> normalize_audio_language("Auto")
    'auto'
    >>> normalize_audio_language("de")
    'de'
    """
    if not lang:
        return "no"
    raw = str(lang).strip().lower()
    if not raw or raw in ("auto", "detect", "auto-detect"):
        return "auto"
    base = re.split(r"[-_]", raw)[0].strip()
    aliases = {
        "nb": "no",
        "nob": "no",
        "nor": "no",
        "norsk": "no",
        "norwegian": "no",
        "bokmål": "no",
        "bokmal": "no",
        "nno": "nn",
        "nynorsk": "nn",
        "eng": "en",
        "english": "en",
        "engelsk": "en",
        "swe": "sv",
        "svensk": "sv",
        "swedish": "sv",
        "dan": "da",
        "dansk": "da",
        "danish": "da",
        "deu": "de",
        "ger": "de",
        "deutsch": "de",
        "german": "de",
        "tysk": "de",
        "spa": "es",
        "spanish": "es",
        "spansk": "es",
        "fra": "fr",
        "fre": "fr",
        "french": "fr",
        "fransk": "fr",
    }
    return aliases.get(base, base)


def is_norwegian_language(lang: str | None) -> bool:
    """Return True if the normalized language code is Norwegian ('no', 'nb', or 'nn').

    >>> is_norwegian_language("no")
    True
    >>> is_norwegian_language("nb-NO")
    True
    >>> is_norwegian_language("en")
    False
    """
    return normalize_audio_language(lang) in ("no", "nb", "nn")


def get_language_display_name(lang: str | None, ui_lang: str = "no") -> str:
    """Return human-readable language name with ISO code in Norwegian ('no') or English ('en').

    >>> get_language_display_name("en", "no")
    'Engelsk (en)'
    >>> get_language_display_name("no", "en")
    'Norwegian (no)'
    >>> get_language_display_name("pl", "en")
    'Polish (pl)'
    """
    code = normalize_audio_language(lang)
    is_en = str(ui_lang).strip().lower().startswith("en")
    if code in LANGUAGE_LABELS:
        lbl_no, lbl_en = LANGUAGE_LABELS[code]
        lbl = lbl_en if is_en else lbl_no
        return lbl if code == "auto" else f"{lbl} ({code})"
    return f"{code.upper()} ({code})"


def load_user_preferences(prefs_path: Path | None = None) -> dict[str, Any]:
    """Load persisted user preferences (UI locale, color scheme, speech language, and per-language models).

    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = Path(d) / "prefs.json"
    ...     _ = save_user_preferences({"ui_lang": "no", "audio_language": "en", "color_scheme": "dark"}, p)
    ...     loaded = load_user_preferences(p)
    ...     (loaded["ui_lang"], loaded["audio_language"], loaded["color_scheme"])
    ('no', 'en', 'dark')
    """
    target = prefs_path if prefs_path is not None else USER_PREFS_FILE
    defaults: dict[str, Any] = {
        "ui_lang": "no",
        "audio_language": "no",
        "color_scheme": "system",
        "language_models": {},
    }
    if not target.is_file():
        return defaults
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out = dict(defaults)
            if raw.get("ui_lang") in ("no", "en"):
                out["ui_lang"] = str(raw["ui_lang"])
            if raw.get("audio_language"):
                out["audio_language"] = normalize_audio_language(str(raw["audio_language"]))
            if raw.get("color_scheme") in VALID_COLOR_SCHEMES:
                out["color_scheme"] = str(raw["color_scheme"])
            if isinstance(raw.get("language_models"), dict):
                out["language_models"] = dict(raw["language_models"])
            return out
    except Exception:
        pass
    return defaults


def save_user_preferences(
    updates: Mapping[str, Any],
    prefs_path: Path | None = None,
) -> dict[str, Any]:
    """Persist user preferences (UI locale, color scheme, audio language, and per-language models) atomically."""
    target = prefs_path if prefs_path is not None else USER_PREFS_FILE
    current = load_user_preferences(target)
    if "ui_lang" in updates and updates["ui_lang"] in ("no", "en"):
        current["ui_lang"] = str(updates["ui_lang"])
    if "audio_language" in updates and updates["audio_language"]:
        current["audio_language"] = normalize_audio_language(str(updates["audio_language"]))
    if "color_scheme" in updates and updates["color_scheme"] in VALID_COLOR_SCHEMES:
        current["color_scheme"] = str(updates["color_scheme"])
    if isinstance(updates.get("language_models"), Mapping):
        lm = dict(current.get("language_models") or {})
        for k, v in updates["language_models"].items():
            if isinstance(v, Mapping):
                norm_k = normalize_audio_language(str(k))
                lm[norm_k] = {
                    "whisper_model": str(v.get("whisper_model", "")),
                    "ollama_model": str(v.get("ollama_model", "")),
                    "profile": str(v.get("profile", "recommended")),
                }
        current["language_models"] = lm
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass
    return current


def detect_text_language(text: str) -> dict[str, Any]:
    """Detect speech/transcript language from text using lexical stopwords and character signatures.

    >>> detect_text_language("Hei alle sammen, og velkommen til statusmøte for prosjektet vårt i dag.")["detected_language"]
    'no'
    >>> detect_text_language("Welcome everyone to today's project status meeting and architecture review.")["detected_language"]
    'en'
    >>> detect_text_language("Välkommen till dagens möte om säkerhet och budget för nästa år.")["detected_language"]
    'sv'
    """
    clean = sanitize_text(text).lower()
    words = re.findall(r"[a-zæøåäöüßéèêàùçñ]+", clean)
    if len(words) < 2:
        return {
            "detected_language": "no",
            "confidence": 0.0,
            "label_no": get_language_display_name("no", "no"),
            "label_en": get_language_display_name("no", "en"),
        }

    stopword_sets: dict[str, set[str]] = {
        "no": {
            "og", "i", "det", "på", "som", "er", "en", "til", "har", "for",
            "med", "ikke", "om", "den", "vi", "av", "skal", "kan", "møte",
            "møtet", "velkommen", "hei", "hva", "eller", "så", "ut", "nå",
            "før", "etter", "også", "alle", "sammen", "dette", "fra", "ved",
        },
        "en": {
            "the", "and", "to", "of", "in", "is", "it", "for", "that", "with",
            "on", "as", "are", "this", "by", "be", "at", "or", "from", "an",
            "we", "you", "have", "welcome", "meeting", "today", "please", "our",
            "will", "can", "about", "what", "which", "their", "has", "been",
        },
        "sv": {
            "och", "att", "det", "i", "en", "jag", "hon", "som", "han", "på",
            "den", "med", "var", "sig", "för", "så", "till", "är", "men", "ett",
            "om", "hade", "de", "av", "icke", "mig", "du", "henne", "då", "möte",
            "välkommen", "inte", "vi", "har", "ska", "från", "också",
        },
        "da": {
            "og", "i", "jeg", "det", "at", "en", "den", "til", "er", "som",
            "på", "de", "med", "han", "af", "for", "ikke", "der", "var", "mig",
            "sig", "men", "et", "har", "om", "vi", "møde", "mødet", "ud", "fra",
        },
        "de": {
            "der", "die", "und", "in", "den", "von", "zu", "das", "mit", "sich",
            "des", "auf", "für", "ist", "im", "dem", "nicht", "ein", "eine",
            "als", "auch", "es", "an", "werden", "aus", "er", "hat", "wir",
        },
        "es": {
            "de", "la", "que", "el", "en", "y", "a", "los", "del", "se",
            "las", "por", "un", "para", "con", "no", "una", "su", "al", "es",
            "lo", "como", "más", "pero", "sus", "le", "ya", "o", "este",
        },
        "fr": {
            "de", "la", "le", "et", "les", "des", "en", "un", "du", "une",
            "que", "est", "pour", "qui", "dans", "a", "par", "plus", "pas",
            "au", "sur", "ne", "se", "ce", "sont", "avec", "nous", "vous",
        },
    }

    scores: dict[str, float] = {k: 0.0 for k in stopword_sets}
    for w in words:
        for lang_code, sw_set in stopword_sets.items():
            if w in sw_set:
                scores[lang_code] += 1.0

    if any(ch in clean for ch in ("æ", "ø")):
        scores["no"] += 2.5
        scores["da"] += 1.5
    if "å" in clean:
        scores["no"] += 1.5
        scores["sv"] += 1.2
        scores["da"] += 1.0
    if any(ch in clean for ch in ("ä", "ö")):
        scores["sv"] += 3.0
        scores["de"] += 1.5
    if "ß" in clean or "ü" in clean:
        scores["de"] += 3.0

    best_lang = max(scores, key=lambda k: scores[k])
    best_score = scores[best_lang]
    total_hits = sum(scores.values())
    if best_score <= 0.0:
        return {
            "detected_language": "no",
            "confidence": 0.0,
            "label_no": get_language_display_name("no", "no"),
            "label_en": get_language_display_name("no", "en"),
        }
    conf = min(0.99, round((best_score / max(1.0, total_hits)) * min(1.0, len(words) / 5.0), 2))
    if best_score >= 3.0 and conf < 0.72:
        conf = 0.78
    return {
        "detected_language": best_lang,
        "confidence": conf,
        "label_no": get_language_display_name(best_lang, "no"),
        "label_en": get_language_display_name(best_lang, "en"),
    }


def resolve_ui_lang(lang_arg: str | None = None, argv: list[str] | None = None) -> str:
    """Resolve UI/help language ('en' or 'no') from explicit argument, CLI flags, MOTE_LANG, or saved prefs.

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
    if env_lang in ("no", "nb", "nn"):
        return "no"
    if argv is None:
        prefs = load_user_preferences()
        if prefs.get("ui_lang") in ("no", "en"):
            return str(prefs["ui_lang"])
    return "no"
