"""Audio I/O, PCM16 analysis/waveform downsampling, mic discovery, live monitor, and mic test."""

from __future__ import annotations

import array
import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from mote.constants import (
    APP_SUPPORT_DIR,
    HF_CACHE_DIR,
    build_runtime_env,
    clean_base_stem,
    detect_text_language,
    format_seconds,
    get_language_display_name,
    is_norwegian_language,
    is_termux,
    is_transcript_file,
    load_user_preferences,
    normalize_audio_language,
    sanitize_text,
    unique_filename,
)
from mote.context import extract_context_from_file, load_turns_from_transcript_file
from mote.hardware import (
    detect_hardware_resources,
    ensure_ffmpeg,
    ensure_whisperx,
    get_whisperx_python,
    recommend_models_for_language,
)
from mote.template_loader import load_runner, load_template

MACOS_COREAUDIO_RECORD_RUNNER_CODE: str = load_runner("macos_coreaudio_record.py")
LIVE_CHUNK_WORKER_CODE: str = load_runner("live_chunk_worker.py")


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


def _decode_pcm16_samples(pcm_bytes: bytes) -> array.array[int]:
    if len(pcm_bytes) >= 2 and len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    elif len(pcm_bytes) < 2:
        return array.array("h")
    samples = array.array("h")
    samples.frombytes(pcm_bytes)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def downsample_pcm16_waveform(pcm_bytes: bytes, bins: int = 72) -> list[float]:
    """Downsample 16-bit signed mono PCM bytes into signed normalized peak bins in [-1.0, 1.0].

    >>> downsample_pcm16_waveform(b"", bins=4)
    [0.0, 0.0, 0.0, 0.0]
    >>> downsample_pcm16_waveform(struct.pack("<4h", 16384, -16384, 8192, -8192), bins=4)
    [0.5, -0.5, 0.25, -0.25]
    """
    target_bins = max(1, int(bins))
    samples = _decode_pcm16_samples(pcm_bytes)
    n = len(samples)
    if n == 0:
        return [0.0] * target_bins

    out: list[float] = []
    for idx in range(target_bins):
        lo = (idx * n) // target_bins
        hi = max(lo + 1, ((idx + 1) * n) // target_bins)
        hi = min(n, hi)
        if lo >= n:
            out.append(0.0)
            continue
        best = 0
        best_abs = -1
        for s in samples[lo:hi]:
            a = abs(s)
            if a > best_abs:
                best_abs = a
                best = s
        norm = max(-1.0, min(1.0, best / 32768.0))
        out.append(round(norm, 4))
    return out


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
    samples = _decode_pcm16_samples(pcm_bytes)
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


def build_media_url_ffmpeg_cmd(
    url: str,
    out_wav: Path,
    ffmpeg_bin: str = "ffmpeg",
    max_seconds: float | None = None,
) -> list[str]:
    """Construct ffmpeg command arguments to stream or download audio from a URL into 16kHz mono WAV.

    >>> cmd = build_media_url_ffmpeg_cmd("https://example.com/audio.mp3", Path("/tmp/out.wav"), max_seconds=20)
    >>> "-vn" in cmd and "-ac" in cmd and "16000" in cmd and "-t" in cmd
    True
    """
    cmd = [
        ffmpeg_bin,
        "-y",
        "-rw_timeout",
        "15000000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-user_agent",
        "Mozilla/5.0 (compatible; Steno/1.0)",
        "-i",
        str(url).strip(),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
    ]
    if max_seconds is not None and max_seconds > 0:
        cmd.extend(["-t", str(max_seconds)])
    cmd.append(str(out_wav))
    return cmd


def download_media_url_audio(
    url: str,
    out_dir: Path | None = None,
    log_fn: Callable[[str], None] = print,
    max_seconds: float | None = None,
    ffmpeg_bin: str | None = None,
    env: dict[str, str] | None = None,
    timeout_sec: int | None = None,
) -> Path:
    """Download or stream media from a URL and extract 16kHz mono 16-bit PCM WAV audio via ffmpeg."""
    clean_url = str(url).strip()
    target_dir = Path(out_dir).resolve() if out_dir is not None else Path.cwd().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    parsed = urllib.parse.urlparse(clean_url)
    path_name = Path(parsed.path).name
    stem = clean_base_stem(Path(path_name)) if path_name else ""
    if not stem or stem.startswith("?") or len(stem) < 2:
        host_slug = re.sub(r"[^a-zA-Z0-9]+", "_", parsed.netloc).strip("_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"stream_{host_slug}_{ts}" if host_slug else f"stream_{ts}"

    out_name = unique_filename(target_dir, stem, ".wav")
    out_wav = target_dir / out_name

    run_env = env.copy() if env is not None else os.environ.copy()
    f_bin = ffmpeg_bin or ensure_ffmpeg(run_env, log_fn)

    cmd = build_media_url_ffmpeg_cmd(
        url=clean_url,
        out_wav=out_wav,
        ffmpeg_bin=f_bin,
        max_seconds=max_seconds,
    )
    eff_timeout = (
        timeout_sec
        if timeout_sec is not None
        else (int(max_seconds) + 60 if max_seconds and max_seconds > 0 else 3600)
    )

    log_fn(
        f"==> Laster ned og trekker ut lyd fra nettadresse / Extracting audio from URL:\n"
        f"    {clean_url} -> {out_wav.name}"
    )

    try:
        proc = subprocess.run(
            cmd,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=eff_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Tidsavbrudd ved nedlasting av lydstrøm ({eff_timeout}s) fra {clean_url}"
        ) from exc
    if proc.returncode != 0 and not out_wav.is_file():
        cmd_simple = [
            f_bin,
            "-y",
            "-i",
            clean_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
        ]
        if max_seconds is not None and max_seconds > 0:
            cmd_simple.extend(["-t", str(max_seconds)])
        cmd_simple.append(str(out_wav))
        try:
            proc = subprocess.run(
                cmd_simple,
                env=run_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=eff_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Tidsavbrudd ved nedlasting av lydstrøm ({eff_timeout}s) fra {clean_url}"
            ) from exc
        if proc.returncode != 0 and not out_wav.is_file():
            err_msg = proc.stderr.strip() or f"ffmpeg exited with code {proc.returncode}"
            raise RuntimeError(
                f"Kunne ikke hente lyd fra nettadresse / Failed to extract audio from {clean_url}: {err_msg}"
            )

    log_fn(f"==> Lyd hentet og lagret til / Saved extracted audio to: {out_wav}")
    return out_wav


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


def get_voiceover_script(ui_lang: str = "no", note_mode: str = "mote") -> str:
    """Return the guided voiceover script in Norwegian ('no') or English ('en') for meeting or lecture mode.

    >>> "Møteleder" in get_voiceover_script("no", "mote")
    True
    >>> "chair" in get_voiceover_script("en", "mote").lower()
    True
    """
    is_en = str(ui_lang).strip().lower().startswith("en")
    is_lecture = str(note_mode).strip().lower() == "foredrag"
    if is_en:
        if is_lecture:
            return (
                "Recording is ready for the lecture. "
                "Please state your name, the lecture topic, and today's date, and check the audio oscilloscope."
            )
        return (
            "Welcome. Before the meeting begins, please do a quick round-table check. "
            "First, the chair states the meeting topic and today's date. "
            "Then each participant states their full name and role in a complete sentence while checking the audio oscilloscope."
        )
    if is_lecture:
        return (
            "Opptaket er klart for foredrag. "
            "Si gjerne ditt navn, tema for foredraget og dagens dato, og sjekk at oscilloskopet reagerer tydelig."
        )
    return (
        "Velkommen. Før møtet starter, ta en kort runde rundt bordet. "
        "Møteleder sier først møtets tema og dagens dato. "
        "Deretter sier hver deltaker sitt fulle navn og sin rolle i én hel setning, mens dere ser at oscilloskopet fanger opp stemmene."
    )


def ensure_pregenerated_voiceover(
    ui_lang: str = "no",
    note_mode: str = "mote",
    cache_dir: Path | None = None,
    ffmpeg_bin: str | None = None,
) -> Path | None:
    """Ensure a high-quality pre-generated neural voiceover audio file is written to disk and return its Path."""
    lang = "en" if str(ui_lang).strip().lower().startswith("en") else "no"
    target_dir = (cache_dir or (APP_SUPPORT_DIR / "voiceover")).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    out_mp3 = target_dir / f"voiceover_{lang}.mp3"

    if out_mp3.is_file() and out_mp3.stat().st_size > 4000:
        return out_mp3

    b64_data = load_template(f"voiceover_{lang}.b64", "").strip()
    if b64_data:
        try:
            raw_bytes = base64.b64decode(b64_data)
            if len(raw_bytes) > 4000:
                tmp_mp3 = out_mp3.with_suffix(".mp3.tmp")
                tmp_mp3.write_bytes(raw_bytes)
                tmp_mp3.replace(out_mp3)
                return out_mp3
        except Exception:
            pass

    script_text = get_voiceover_script(lang, note_mode)
    if sys.platform == "darwin" and shutil.which("say"):
        voice = "Samantha" if lang == "en" else "Nora"
        with tempfile.TemporaryDirectory(prefix="steno_vo_") as tmp:
            aiff_path = Path(tmp) / "raw.aiff"
            try:
                subprocess.run(
                    ["say", "-v", voice, "-r", "180", "-o", str(aiff_path), script_text],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                f_bin = ffmpeg_bin or shutil.which("ffmpeg")
                if f_bin and aiff_path.is_file():
                    subprocess.run(
                        [
                            f_bin,
                            "-y",
                            "-i",
                            str(aiff_path),
                            "-af",
                            "highpass=f=85,equalizer=f=2800:t=q:w=1.2:g=2.5,loudnorm=I=-16:TP=-1.5:LRA=9",
                            "-ac",
                            "1",
                            "-ar",
                            "22050",
                            "-b:a",
                            "24k",
                            str(out_mp3),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if out_mp3.is_file() and out_mp3.stat().st_size > 4000:
                        return out_mp3
            except Exception:
                pass

    out_wav = target_dir / f"voiceover_{lang}.wav"
    pcm_samples = struct.pack("<h", 1200) * 8000
    out_wav.write_bytes(build_wav_bytes_from_pcm16(pcm_samples, 16000))
    return out_wav


def parse_avfoundation_audio_devices(ffmpeg_output: str) -> list[dict[str, Any]]:
    """Parse AVFoundation audio input devices from ffmpeg -list_devices output.

    >>> sample = '''
    ... [AVFoundation indev @ 0x1] AVFoundation video devices:
    ... [AVFoundation indev @ 0x1] [0] FaceTime HD Camera
    ... [AVFoundation indev @ 0x1] AVFoundation audio devices:
    ... [AVFoundation indev @ 0x1] [0] MacBook Air Microphone
    ... [AVFoundation indev @ 0x1] [1] USB audio CODEC
    ... '''
    >>> devs = parse_avfoundation_audio_devices(sample)
    >>> [(d["id"], d["name"]) for d in devs]
    [(':0', 'MacBook Air Microphone'), (':1', 'USB audio CODEC')]
    """
    in_audio_section = False
    devices: list[dict[str, Any]] = []
    for raw_line in (ffmpeg_output or "").splitlines():
        if "AVFoundation audio devices:" in raw_line:
            in_audio_section = True
            continue
        if "AVFoundation video devices:" in raw_line:
            in_audio_section = False
            continue
        if in_audio_section:
            m = re.search(r"\[(\d+)\]\s+(.+)$", raw_line.strip())
            if m:
                idx_str, dev_name = m.group(1), m.group(2).strip()
                devices.append(
                    {
                        "id": f":{idx_str}",
                        "index": int(idx_str),
                        "name": dev_name,
                    }
                )
    return devices


def list_audio_input_devices(
    ffmpeg_bin: str | None = None,
    platform_str: str | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Discover available audio input (microphone) devices across macOS, Windows, and Linux."""
    plat = platform_str if platform_str is not None else sys.platform
    run_env = env if env is not None else build_runtime_env()
    f_bin = ffmpeg_bin or shutil.which("ffmpeg", path=run_env.get("PATH")) or "ffmpeg"

    if plat == "darwin":
        default_name = ""
        transport_map: dict[str, str] = {}
        if sys.platform == "darwin" and shutil.which("system_profiler"):
            try:
                sp_out = subprocess.check_output(
                    ["system_profiler", "SPAudioDataType", "-json"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                sp_data = json.loads(sp_out)
                for group in sp_data.get("SPAudioDataType", []):
                    for item in group.get("_items", []):
                        if "coreaudio_input_source" in item or "coreaudio_device_input" in item:
                            nm = str(item.get("_name", "")).strip()
                            raw_tr = str(item.get("coreaudio_device_transport", "")).lower()
                            tr = (
                                "builtin"
                                if "builtin" in raw_tr
                                else (
                                    "usb"
                                    if "usb" in raw_tr
                                    else (
                                        "bluetooth"
                                        if "bluetooth" in raw_tr
                                        else ("virtual" if "virtual" in raw_tr else "audio")
                                    )
                                )
                            )
                            if nm:
                                transport_map[nm] = tr
                            if item.get("coreaudio_default_audio_input_device") == "spaudio_yes" and nm:
                                default_name = nm
            except Exception:
                pass

        avf_devs: list[dict[str, Any]] = []
        if sys.platform == "darwin":
            try:
                proc = subprocess.run(
                    [f_bin, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3,
                    check=False,
                )
                avf_devs = parse_avfoundation_audio_devices(proc.stdout or "")
            except Exception:
                pass

        if not default_name and avf_devs:
            default_name = str(avf_devs[0]["name"])

        tr_labels_no = {
            "builtin": "Innebygd",
            "usb": "USB",
            "bluetooth": "Bluetooth",
            "virtual": "Virtuell",
            "audio": "Lydenhet",
        }
        tr_labels_en = {
            "builtin": "Built-in",
            "usb": "USB",
            "bluetooth": "Bluetooth",
            "virtual": "Virtual",
            "audio": "Audio",
        }

        def_disp = default_name or "Systemstandard mikrofon"
        def_tr = transport_map.get(default_name, "builtin")
        result: list[dict[str, Any]] = [
            {
                "id": ":default",
                "name": def_disp,
                "is_default": True,
                "transport": def_tr,
                "label_no": f"Standard: {def_disp} ({tr_labels_no.get(def_tr, 'Lyd')})",
                "label_en": f"Default: {def_disp} ({tr_labels_en.get(def_tr, 'Audio')})",
            }
        ]
        for d in avf_devs:
            nm = str(d["name"])
            tr = transport_map.get(nm, "audio")
            is_def = bool(default_name and nm == default_name)
            tag_no = f"{tr_labels_no.get(tr, 'Lyd')}{' • Standard' if is_def else ''}"
            tag_en = f"{tr_labels_en.get(tr, 'Audio')}{' • Default' if is_def else ''}"
            result.append(
                {
                    "id": str(d["id"]),
                    "name": nm,
                    "is_default": is_def,
                    "transport": tr,
                    "label_no": f"[{d['id']}] {nm} ({tag_no})",
                    "label_en": f"[{d['id']}] {nm} ({tag_en})",
                }
            )
        return result

    if plat.startswith("win") or os.name == "nt":
        win_devs: list[dict[str, Any]] = [
            {
                "id": "default",
                "name": "System Default Microphone",
                "is_default": True,
                "transport": "audio",
                "label_no": "Standard mikrofon (Windows DirectShow)",
                "label_en": "Default Microphone (Windows DirectShow)",
            }
        ]
        if os.name == "nt" and Path(f_bin).is_file():
            try:
                proc = subprocess.run(
                    [f_bin, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                    env=run_env,
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
                        dname = m_dev.group(1)
                        win_devs.append(
                            {
                                "id": f"audio={dname}",
                                "name": dname,
                                "is_default": len(win_devs) == 1,
                                "transport": "audio",
                                "label_no": f"{dname}",
                                "label_en": f"{dname}",
                            }
                        )
            except Exception:
                pass
        return win_devs

    lin_devs: list[dict[str, Any]] = [
        {
            "id": "default",
            "name": "Default Audio Input",
            "is_default": True,
            "transport": "audio",
            "label_no": "Standard mikrofon (PulseAudio / ALSA)",
            "label_en": "Default Microphone (PulseAudio / ALSA)",
        }
    ]
    if sys.platform.startswith("linux") and shutil.which("pactl"):
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sources"], text=True, stderr=subprocess.DEVNULL, timeout=2
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and not parts[1].endswith(".monitor"):
                    src_name = parts[1]
                    lin_devs.append(
                        {
                            "id": src_name,
                            "name": src_name,
                            "is_default": False,
                            "transport": "audio",
                            "label_no": src_name,
                            "label_en": src_name,
                        }
                    )
        except Exception:
            pass
    return lin_devs


def get_system_input_volume(platform_str: str | None = None) -> dict[str, Any]:
    """Read OS microphone input volume (0-100%) on macOS or Linux PulseAudio."""
    plat = platform_str if platform_str is not None else sys.platform
    if plat == "darwin" and sys.platform == "darwin" and shutil.which("osascript"):
        try:
            out = subprocess.check_output(
                ["osascript", "-e", "input volume of (get volume settings)"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            if out.isdigit():
                vol = max(0, min(100, int(out)))
                return {"supported": True, "volume": vol, "muted": vol == 0}
        except Exception:
            pass
    if plat.startswith("linux") and sys.platform.startswith("linux") and shutil.which("pactl"):
        try:
            out = subprocess.check_output(
                ["pactl", "get-source-volume", "@DEFAULT_SOURCE@"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            m = re.search(r"(\d+)%", out)
            if m:
                vol = max(0, min(150, int(m.group(1))))
                return {"supported": True, "volume": vol, "muted": vol == 0}
        except Exception:
            pass
    return {"supported": False, "volume": 100, "muted": False}


def set_system_input_volume(volume_pct: int, platform_str: str | None = None) -> dict[str, Any]:
    """Set OS microphone input volume (0-100%) on macOS or Linux PulseAudio."""
    plat = platform_str if platform_str is not None else sys.platform
    clamped = max(0, min(100, int(volume_pct)))
    if plat == "darwin" and sys.platform == "darwin" and shutil.which("osascript"):
        try:
            subprocess.run(
                ["osascript", "-e", f"set volume input volume {clamped}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return {"ok": True, "supported": True, "volume": clamped, "muted": clamped == 0}
        except Exception as exc:
            return {"ok": False, "supported": True, "volume": clamped, "error": str(exc)}
    if plat.startswith("linux") and sys.platform.startswith("linux") and shutil.which("pactl"):
        try:
            subprocess.run(
                ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{clamped}%"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return {"ok": True, "supported": True, "volume": clamped, "muted": clamped == 0}
        except Exception as exc:
            return {"ok": False, "supported": True, "volume": clamped, "error": str(exc)}
    return {"ok": True, "supported": False, "volume": clamped, "muted": clamped == 0}


def open_system_sound_settings(platform_str: str | None = None) -> dict[str, Any]:
    """Open OS Sound Input settings panel so user can inspect microphone permissions, Voice Isolation, or levels."""
    plat = platform_str if platform_str is not None else sys.platform
    try:
        if plat == "darwin":
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.Sound-Settings.extension"])
            return {"ok": True}
        if plat.startswith("win") or os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "ms-settings:sound"])
            return {"ok": True}
        for tool in ("pavucontrol", "gnome-control-center"):
            if shutil.which(tool):
                args = [tool, "sound"] if tool == "gnome-control-center" else [tool]
                subprocess.Popen(args)
                return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "Unsupported platform sound settings launcher"}


def build_mic_audio_filter(gain_db: float = 0.0, auto_gain: bool = False) -> str:
    """Build an ffmpeg audio filter chain (-af) for microphone gain boost, denoising, and dynamic normalization.

    >>> build_mic_audio_filter(0.0, False)
    ''
    >>> build_mic_audio_filter(6.0, False)
    'highpass=f=80,lowpass=f=7600,afftdn=nf=-25,volume=6.0dB,alimiter=limit=0.95:level=0'
    >>> "dynaudnorm" in build_mic_audio_filter(6.0, True)
    True
    """
    if abs(gain_db) < 0.05 and not auto_gain:
        return ""
    filters: list[str] = ["highpass=f=80", "lowpass=f=7600", "afftdn=nf=-25"]
    if abs(gain_db) >= 0.05:
        filters.append(f"volume={float(gain_db):.1f}dB")
    if auto_gain:
        filters.append("dynaudnorm=f=100:g=7:p=0.85:m=3.0:s=12")
    filters.append("alimiter=limit=0.95:level=0")
    return ",".join(filters)


def start_mic_record_process(
    ffmpeg_bin: str,
    out_wav: Path,
    env: dict[str, str] | None = None,
    mic_device: str = "default",
    gain_db: float = 0.0,
    auto_gain: bool = False,
    platform_str: str | None = None,
) -> subprocess.Popen[str]:
    """Start a microphone capture subprocess (multi-buffered CoreAudio AudioQueue on macOS, ffmpeg elsewhere)."""
    plat = platform_str if platform_str is not None else sys.platform
    if plat == "darwin" and sys.platform == "darwin":
        af_str = build_mic_audio_filter(gain_db=gain_db, auto_gain=auto_gain)
        if not af_str:
            af_str = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25"
        cfg = {
            "ffmpeg_bin": ffmpeg_bin,
            "out_wav": str(out_wav),
            "af_str": af_str,
            "mic_device": mic_device,
        }
        return subprocess.Popen(
            [sys.executable, "-u", "-c", MACOS_COREAUDIO_RECORD_RUNNER_CODE, json.dumps(cfg)],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    cmd = build_ffmpeg_mic_record_cmd(
        ffmpeg_bin,
        out_wav,
        platform_str=plat,
        env=env,
        mic_device=mic_device,
        gain_db=gain_db,
        auto_gain=auto_gain,
    )
    return subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def build_ffmpeg_mic_record_cmd(
    ffmpeg_bin: str,
    out_wav: Path,
    platform_str: str | None = None,
    os_name: str | None = None,
    env: dict[str, str] | None = None,
    mic_device: str = "default",
    gain_db: float = 0.0,
    auto_gain: bool = False,
) -> list[str]:
    """Build cross-platform 16kHz mono PCM16 ffmpeg microphone capture command for macOS, Windows, Linux, and Termux.

    >>> cmd_mac = build_ffmpeg_mic_record_cmd("ffmpeg", Path("/tmp/a.wav"), platform_str="darwin", os_name="posix")
    >>> cmd_mac[:6]
    ['ffmpeg', '-y', '-f', 'avfoundation', '-i', ':default']
    >>> cmd_win = build_ffmpeg_mic_record_cmd("ffmpeg.exe", Path("C:/a.wav"), platform_str="win32", os_name="nt")
    >>> cmd_win[:6]
    ['ffmpeg.exe', '-y', '-f', 'dshow', '-i', 'audio=default']
    >>> cmd_lin = build_ffmpeg_mic_record_cmd("ffmpeg", Path("/tmp/a.wav"), platform_str="linux", os_name="posix")
    >>> cmd_lin[2] == '-f' and cmd_lin[3] in ('pulse', 'alsa')
    True
    >>> cmd_tmx = build_ffmpeg_mic_record_cmd("ffmpeg", Path("/tmp/a.wav"), platform_str="linux-android", os_name="posix", env={"TERMUX_VERSION": "0.118.1"})
    >>> cmd_tmx[:6]
    ['ffmpeg', '-y', '-f', 'pulse', '-i', 'default']
    """
    plat = platform_str if platform_str is not None else sys.platform
    os_n = os_name if os_name is not None else os.name
    clean_dev = (mic_device or "").strip()
    af_str = build_mic_audio_filter(gain_db=gain_db, auto_gain=auto_gain)
    af_args = ["-af", af_str] if af_str else []

    if plat == "darwin":
        if not clean_dev or clean_dev in ("default", ":default"):
            mac_input = ":default"
        elif clean_dev.startswith(":"):
            mac_input = clean_dev
        else:
            mac_input = f":{clean_dev}"
        return [
            ffmpeg_bin,
            "-y",
            "-f",
            "avfoundation",
            "-i",
            mac_input,
            *af_args,
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
        if clean_dev and clean_dev not in ("default", ":default"):
            dshow_input = clean_dev if clean_dev.startswith("audio=") else f"audio={clean_dev}"
        elif os.name == "nt" and Path(ffmpeg_bin).is_file():
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
            *af_args,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]

    linux_fmt = "pulse"
    if plat == "linux-android" or (env is not None and is_termux(env, prefix_dir=Path("/nonexistent"))):
        linux_fmt = "pulse"
    elif sys.platform.startswith("linux") and not shutil.which("pactl") and not os.environ.get("PULSE_SERVER"):
        linux_fmt = "alsa"
    lin_input = clean_dev if (clean_dev and clean_dev not in (":default",)) else "default"
    return [
        ffmpeg_bin,
        "-y",
        "-f",
        linux_fmt,
        "-i",
        lin_input,
        *af_args,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]


def pick_multilingual_whisper_model(preferred_model: str = "") -> str:
    """Return a cached multilingual Whisper model (non-nb-whisper) for language detection or international speech."""
    hub_dir = HF_CACHE_DIR / "hub"
    repo_map = {
        "large-v3-turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
        "small": "models--Systran--faster-whisper-small",
        "base": "models--Systran--faster-whisper-base",
        "tiny": "models--Systran--faster-whisper-tiny",
        "medium": "models--Systran--faster-whisper-medium",
        "large-v3": "models--Systran--faster-whisper-large-v3",
    }
    if preferred_model and "nb-whisper" not in preferred_model.lower():
        folder = repo_map.get(preferred_model, "models--" + preferred_model.replace("/", "--"))
        if hub_dir.is_dir() and (hub_dir / folder).is_dir() and any((hub_dir / folder).glob("snapshots/*/*")):
            return preferred_model

    if hub_dir.is_dir():
        for short_name, folder_name in repo_map.items():
            model_dir = hub_dir / folder_name
            if model_dir.is_dir() and any(model_dir.glob("snapshots/*/*")):
                return short_name
    if preferred_model and "nb-whisper" not in preferred_model.lower():
        return preferred_model
    return "small"


def pick_live_whisper_model(
    preferred_model: str = "NbAiLab/nb-whisper-small",
    language: str = "no",
) -> str:
    """Return a cached Whisper model for fast coarse real-time transcription, respecting audio language."""
    if not is_norwegian_language(language):
        return pick_multilingual_whisper_model(preferred_model)
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


def detect_audio_language(
    input_path: Path,
    max_seconds: float = 20.0,
    sample_seconds: float | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Detect spoken language from an audio/video file or existing transcript file."""
    log = log_fn or (lambda _msg: None)
    eff_seconds = float(sample_seconds) if sample_seconds is not None else float(max_seconds)
    base_dir = Path(__file__).resolve().parent
    resolved = resolve_moved_audio_path(input_path, base_dir)
    if not resolved.is_file():
        return {"ok": False, "error": f"Fant ikke filen: {input_path}"}

    hw = detect_hardware_resources(resolved.parent)
    ram_gb = float(hw.get("ram_gb", 16.0))
    prefs = load_user_preferences()

    if is_transcript_file(resolved):
        turns, _ = load_turns_from_transcript_file(resolved)
        sample_txt = (
            " ".join(t["text"] for t in turns[:40])
            if turns
            else extract_context_from_file(resolved)[:2500]
        )
        res_txt = detect_text_language(sample_txt)
        det_code = str(res_txt.get("detected_language", "no"))
        conf = float(res_txt.get("confidence", 0.8))
        rec = recommend_models_for_language(det_code, ram_gb=ram_gb, prefs=prefs)
        return {
            "ok": True,
            "detected_language": det_code,
            "confidence": conf,
            "language_probability": conf,
            "label_no": get_language_display_name(det_code, "no"),
            "label_en": get_language_display_name(det_code, "en"),
            "recommended_whisper": rec["whisper_model"],
            "recommended_ollama": rec["ollama_model"],
        }

    env = build_runtime_env()
    ffmpeg_bin = ensure_ffmpeg(env, log)
    multi_model = pick_multilingual_whisper_model()
    wx_bin = shutil.which("whisperx", path=env["PATH"])
    if not wx_bin:
        return {"ok": False, "error": "WhisperX er ikke installert ennå."}
    wx_py = get_whisperx_python(wx_bin)

    with tempfile.TemporaryDirectory(prefix="steno_lang_det_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        conv_wav = tmp_path / "sample_16k.wav"
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(resolved),
                "-t",
                str(max(4.0, min(30.0, float(eff_seconds)))),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(conv_wav),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if not conv_wav.is_file():
            return {"ok": False, "error": "Kunne ikke lese lydspor fra filen."}

        det_code_script = (
            "import json, sys, warnings\n"
            "warnings.filterwarnings('ignore')\n"
            "from faster_whisper import WhisperModel\n"
            "from whisperx.audio import load_audio\n"
            "m = WhisperModel(sys.argv[1], device='cpu', compute_type='int8')\n"
            "audio = load_audio(sys.argv[2])\n"
            "lang, prob, _ = m.detect_language(audio)\n"
            "print('LANG_DET:' + json.dumps({'lang': str(lang), 'prob': round(float(prob), 3)}))\n"
        )
        proc = subprocess.run(
            [wx_py, "-c", det_code_script, multi_model, str(conv_wav)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        det_lang = "no"
        det_prob = 0.0
        for ln in (proc.stdout or "").splitlines():
            if ln.strip().startswith("LANG_DET:"):
                data = json.loads(ln.strip()[len("LANG_DET:") :])
                det_lang = normalize_audio_language(str(data.get("lang", "no")))
                det_prob = float(data.get("prob", 0.0))
                break

    rec = recommend_models_for_language(det_lang, ram_gb=ram_gb, prefs=prefs)
    log(
        f"==> Språkgjenkjenning ({resolved.name}): {get_language_display_name(det_lang, 'no')} "
        f"({det_prob * 100:.0f}% sikkerhet) -> anbefalt modell: {rec['whisper_model']}"
    )
    return {
        "ok": True,
        "detected_language": det_lang,
        "confidence": det_prob,
        "language_probability": det_prob,
        "label_no": get_language_display_name(det_lang, "no"),
        "label_en": get_language_display_name(det_lang, "en"),
        "recommended_whisper": rec["whisper_model"],
        "recommended_ollama": rec["ollama_model"],
    }


class _LiveWhisperWorker:
    """Manage a persistent subprocess running LIVE_CHUNK_WORKER_CODE over JSON stdin/stdout."""

    def __init__(
        self,
        whisper_model: str,
        language: str = "no",
        initial_prompt: str = "",
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.whisper_model = pick_live_whisper_model(whisper_model, language=normalize_audio_language(language))
        self.language = normalize_audio_language(language)
        self.initial_prompt = initial_prompt
        self.log_fn = log_fn
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._ready = False

    def start(self, env: dict[str, str] | None = None, require_whisperx: bool = False) -> bool:
        if self._proc is not None:
            return True
        run_env = env if env is not None else build_runtime_env()
        wx_bin = shutil.which("whisperx", path=run_env["PATH"])
        if not wx_bin and require_whisperx:
            wx_bin = ensure_whisperx(run_env, self.log_fn or (lambda _m: None))
        if not wx_bin:
            return False

        self._tmp_dir = tempfile.TemporaryDirectory(prefix="mote_live_")
        tmp_path = Path(self._tmp_dir.name)
        worker_py = tmp_path / "live_worker.py"
        worker_py.write_text(LIVE_CHUNK_WORKER_CODE, encoding="utf-8")
        cfg_path = tmp_path / "live_cfg.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "model": self.whisper_model,
                    "detect_lang_model": pick_multilingual_whisper_model(),
                    "language": self.language,
                    "compute_type": "int8",
                    "initial_prompt": self.initial_prompt,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        wx_py = get_whisperx_python(wx_bin)
        self._proc = subprocess.Popen(
            [wx_py, "-u", str(worker_py), str(cfg_path)],
            env=run_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._ready = False
        return True

    def wait_ready(self, stop_event: threading.Event | None = None) -> bool:
        proc = self._proc
        if not proc or not proc.stdout:
            return False
        if self._ready:
            return True
        for _ in range(60):
            if stop_event is not None and stop_event.is_set():
                return False
            ln = proc.stdout.readline()
            if not ln:
                return False
            if "LIVE_WORKER:READY" in ln:
                self._ready = True
                return True
        return False

    def transcribe(
        self,
        wav_path: Path,
        start: float = 0.0,
        end: float = 0.0,
        detect_language: bool = False,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        proc = self._proc
        if not proc or proc.poll() is not None or not proc.stdin or not proc.stdout:
            return {}
        if not self._ready and not self.wait_ready(stop_event=stop_event):
            return {}
        req = json.dumps(
            {
                "wav_path": str(wav_path),
                "start": start,
                "end": end,
                "detect_language": bool(detect_language),
            },
            ensure_ascii=False,
        )
        try:
            proc.stdin.write(req + "\n")
            proc.stdin.flush()
            while stop_event is None or not stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                clean = line.strip()
                if clean.startswith("LIVE_RESULT:"):
                    payload = json.loads(clean[len("LIVE_RESULT:") :])
                    return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            pass
        return {}

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self._ready = False
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
                proc.kill()
        if self._tmp_dir is not None:
            try:
                self._tmp_dir.cleanup()
            except Exception:
                pass
            self._tmp_dir = None


class LiveAudioMonitor:
    """Background monitor tailing an active 16kHz mono WAV recording for oscilloscope waveforms, dBFS, and coarse ASR."""

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
        on_waveform: Callable[[list[float], dict[str, Any]], None] | None = None,
        poll_interval: float = 0.08,
        level_interval: float = 0.5,
    ) -> None:
        self.wav_path = wav_path
        self.language = normalize_audio_language(language)
        self.whisper_model = pick_live_whisper_model(whisper_model, language=self.language)
        self.initial_prompt = initial_prompt
        self.chunk_seconds = max(2.0, chunk_seconds)
        self.enable_asr = enable_asr
        self.on_level = on_level
        self.on_chunk = on_chunk
        self.log_fn = log_fn
        self.on_waveform = on_waveform
        self.poll_interval = max(0.04, float(poll_interval))
        self.level_interval = max(0.2, float(level_interval))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker: _LiveWhisperWorker | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        sample_rate = 16000
        bytes_per_sec = sample_rate * 2
        chunk_bytes = int(self.chunk_seconds * bytes_per_sec)
        if chunk_bytes % 2 != 0:
            chunk_bytes += 1
        wave_window_bytes = 2048

        worker: _LiveWhisperWorker | None = None
        if self.enable_asr:
            try:
                worker = _LiveWhisperWorker(
                    whisper_model=self.whisper_model,
                    language=self.language,
                    initial_prompt=self.initial_prompt,
                    log_fn=self.log_fn,
                )
                if worker.start():
                    worker.wait_ready(stop_event=self._stop_event)
                    self._worker = worker
            except Exception as exc:
                if self.log_fn:
                    self.log_fn(
                        f"Merk: Grov sanntids-ASR kunne ikke startes ({exc}). Viser kun lydnivå."
                    )

        with tempfile.TemporaryDirectory(prefix="mote_live_chunks_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            data_offset: int | None = None
            next_chunk_offset = 0
            chunk_idx = 0
            last_level_at = 0.0
            last_wave_size = -1

            try:
                while True:
                    now = time.monotonic()
                    if self.wav_path.is_file():
                        try:
                            file_size = self.wav_path.stat().st_size
                            if file_size >= 128:
                                with self.wav_path.open("rb") as f:
                                    if data_offset is None:
                                        hdr = f.read(min(4096, file_size))
                                        data_offset = find_wav_data_offset(hdr)
                                        next_chunk_offset = data_offset

                                    total_pcm_bytes = max(0, file_size - data_offset)

                                    if (
                                        self.on_waveform is not None
                                        and total_pcm_bytes >= 64
                                        and file_size != last_wave_size
                                    ):
                                        last_wave_size = file_size
                                        w_bytes = min(wave_window_bytes, total_pcm_bytes)
                                        w_bytes -= w_bytes % 2
                                        if w_bytes >= 2:
                                            w_start = data_offset + total_pcm_bytes - w_bytes
                                            if (w_start - data_offset) % 2 != 0:
                                                w_start -= 1
                                            f.seek(w_start)
                                            wave_pcm = f.read(w_bytes)
                                            bins_vals = downsample_pcm16_waveform(wave_pcm, bins=72)
                                            lvl_wave = analyze_pcm16_audio_level(wave_pcm, sample_rate)
                                            self.on_waveform(bins_vals, lvl_wave)

                                    if (
                                        self.on_level is not None
                                        and total_pcm_bytes >= bytes_per_sec
                                        and (last_level_at == 0.0 or (now - last_level_at) >= self.level_interval)
                                    ):
                                        last_level_at = now
                                        sec_start = data_offset + total_pcm_bytes - bytes_per_sec
                                        if (sec_start - data_offset) % 2 != 0:
                                            sec_start -= 1
                                        f.seek(sec_start)
                                        last_sec_pcm = f.read(bytes_per_sec)
                                        lvl = analyze_pcm16_audio_level(last_sec_pcm, sample_rate)
                                        self.on_level(lvl)

                                    if self.on_chunk is not None or self.enable_asr:
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
                                            if (
                                                self.enable_asr
                                                and worker is not None
                                                and lvl_chunk["status"] != "stille"
                                            ):
                                                chunk_wav = tmp_path / f"chunk_{chunk_idx:04d}.wav"
                                                chunk_wav.write_bytes(
                                                    build_wav_bytes_from_pcm16(pcm_slice, sample_rate)
                                                )
                                                payload = worker.transcribe(
                                                    chunk_wav,
                                                    start=start_s,
                                                    end=end_s,
                                                    stop_event=self._stop_event,
                                                )
                                                text = sanitize_text(str(payload.get("text", "")))
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
                            pass

                    wait_step = self.poll_interval if self.on_waveform is not None else min(0.25, self.level_interval)
                    if self._stop_event.wait(wait_step):
                        break
            finally:
                if worker is not None:
                    worker.close()
                    if self._worker is worker:
                        self._worker = None


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
    norm_lang = normalize_audio_language(language)
    eff_model = pick_live_whisper_model(whisper_model, language=norm_lang)
    log(
        f"==> GROV SANNTIDS-LYDSJEKK ({eff_model}, språk={norm_lang}, "
        f"utsnitt på {chunk_seconds:.0f}s, maks {max_seconds:.0f}s): {resolved.name}"
    )

    sample_rate = 16000
    bytes_per_sec = sample_rate * 2
    chunk_bytes = int(chunk_seconds * bytes_per_sec)
    if chunk_bytes % 2 != 0:
        chunk_bytes += 1

    detected_lang = ""
    detected_prob = 0.0
    all_texts: list[str] = []
    lines_out: list[str] = []
    statuses: list[str] = []
    dbfs_vals: list[float] = []

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

        worker = _LiveWhisperWorker(
            whisper_model=eff_model,
            language=norm_lang,
            initial_prompt=initial_prompt,
            log_fn=log,
        )
        worker.start(env=env, require_whisperx=True)
        worker.wait_ready()
        try:
            offset = 0
            idx = 0
            while offset < len(pcm_all):
                pcm_slice = pcm_all[offset : offset + chunk_bytes]
                if len(pcm_slice) < sample_rate:
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
                    payload = worker.transcribe(
                        c_wav,
                        start=start_s,
                        end=end_s,
                        detect_language=not bool(detected_lang),
                    )
                    text = sanitize_text(str(payload.get("text", "")))
                    if text:
                        all_texts.append(text)
                    dl = normalize_audio_language(str(payload.get("detected_language", "")))
                    dp = float(payload.get("language_probability", 0.0) or 0.0)
                    if dl and dl != "auto" and dp >= detected_prob:
                        detected_lang = dl
                        detected_prob = dp
                    c_wav.unlink(missing_ok=True)

                formatted = format_live_check_line(
                    start_s, end_s, float(lvl["rms_dbfs"]), str(lvl["status"]), text
                )
                lines_out.append(formatted)
                log(f"    {formatted}")
        finally:
            worker.close()

    if not detected_lang and all_texts:
        txt_det = detect_text_language(" ".join(all_texts))
        txt_lang = str(txt_det.get("detected_language", ""))
        txt_prob = float(txt_det.get("confidence", 0.0) or 0.0)
        if txt_lang and txt_prob >= 0.55:
            detected_lang = txt_lang
            detected_prob = txt_prob

    avg_dbfs = round(sum(dbfs_vals) / len(dbfs_vals), 1) if dbfs_vals else -96.0
    good_cnt = statuses.count("godt")
    low_cnt = statuses.count("lavt")
    silent_cnt = statuses.count("stille")
    clip_cnt = statuses.count("hoyt")
    lang_msg = (
        f" | Oppdaget språk: {get_language_display_name(detected_lang, 'no')} ({detected_prob * 100:.0f}%)"
        if detected_lang
        else ""
    )
    log(
        f"==> LYDSJEKK OPPSUMMERING: Snitt {avg_dbfs:.1f} dBFS | "
        f"Godt: {good_cnt} | Lavt: {low_cnt} | Stille: {silent_cnt} | Høyt/klipp: {clip_cnt}{lang_msg}"
    )
    recs = recommend_models_for_language(detected_lang or norm_lang)
    return {
        "ok": True,
        "chunks": len(statuses),
        "good_chunks": good_cnt,
        "low_chunks": low_cnt,
        "silent_chunks": silent_cnt,
        "clip_chunks": clip_cnt,
        "avg_rms_dbfs": avg_dbfs,
        "lines": lines_out,
        "detected_language": detected_lang,
        "language_probability": round(detected_prob, 3),
        "detected_label_no": get_language_display_name(detected_lang, "no") if detected_lang else "",
        "detected_label_en": get_language_display_name(detected_lang, "en") if detected_lang else "",
        "recommended_whisper": recs["default_whisper"],
    }


def run_mic_test(
    mic_device: str = "default",
    gain_db: float = 6.0,
    auto_gain: bool = True,
    duration_sec: float = 4.0,
    whisper_model: str = "NbAiLab/nb-whisper-small",
    language: str = "no",
    on_state: Callable[[dict[str, Any]], None] | None = None,
    on_level: Callable[[dict[str, Any]], None] | None = None,
    on_waveform: Callable[[list[float], dict[str, Any]], None] | None = None,
    on_audio_ready: Callable[[Path], None] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Capture a short microphone clip with live oscilloscope/dBFS monitoring and run STT + language detection."""
    log = log_fn or (lambda _msg: None)
    emit_state = on_state or (lambda _state: None)
    env = build_runtime_env()
    ffmpeg_bin = ensure_ffmpeg(env, log)
    tmp_wav = Path(tempfile.gettempdir()) / "steno_mictest_latest.wav"
    tmp_wav.unlink(missing_ok=True)
    dur = max(2.0, min(8.0, float(duration_sec)))
    norm_lang = normalize_audio_language(language)
    eff_model = pick_live_whisper_model(whisper_model, language=norm_lang)

    log(
        f"==> MIKROFON- & STT-TEST ({dur:.0f}s): enhet={mic_device}, boost=+{float(gain_db):.0f} dB"
        + (" + auto-gain" if auto_gain else "")
        + f", STT={eff_model} (språk={norm_lang}) — Snakk en kort setning nå..."
    )
    emit_state(
        {
            "phase": "recording",
            "duration_sec": dur,
            "remaining_sec": dur,
            "model": eff_model,
        }
    )

    proc: subprocess.Popen[str] | None = None
    monitor: LiveAudioMonitor | None = None
    worker = _LiveWhisperWorker(whisper_model=eff_model, language=norm_lang, log_fn=log)

    try:
        worker.start(env=env)
        proc = start_mic_record_process(
            ffmpeg_bin,
            tmp_wav,
            env=env,
            mic_device=mic_device,
            gain_db=float(gain_db),
            auto_gain=bool(auto_gain),
        )
        latest_lvl: dict[str, Any] = {}

        def _capture_level(lvl: dict[str, Any]) -> None:
            latest_lvl.update(lvl)
            if on_level is not None:
                on_level(lvl)

        monitor = LiveAudioMonitor(
            wav_path=tmp_wav,
            whisper_model=eff_model,
            language=norm_lang,
            chunk_seconds=max(2.0, dur + 1.0),
            enable_asr=False,
            on_level=_capture_level,
            on_waveform=on_waveform,
            poll_interval=0.07,
            level_interval=0.35,
        )
        monitor.start()

        steps = max(2, int(dur / 0.5))
        for idx in range(steps):
            time.sleep(0.5)
            rem = max(0.0, dur - ((idx + 1) * 0.5))
            emit_state(
                {
                    "phase": "recording",
                    "duration_sec": dur,
                    "remaining_sec": rem,
                    "level": dict(latest_lvl) if latest_lvl else None,
                    "model": eff_model,
                }
            )

        monitor.stop()
        monitor = None

        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                proc.wait(timeout=2)
            except Exception:
                proc.terminate()

        if tmp_wav.is_file() and tmp_wav.stat().st_size > 128:
            if on_audio_ready is not None:
                on_audio_ready(tmp_wav)
            raw_all = tmp_wav.read_bytes()
            data_off = find_wav_data_offset(raw_all[:4096])
            pcm_all = raw_all[data_off:]
            final_lvl = analyze_pcm16_audio_level(pcm_all)
            if on_level is not None:
                on_level(final_lvl)
            rms_db = float(final_lvl.get("rms_dbfs", -96.0))
            st = str(final_lvl.get("status", "lavt"))

            emit_state(
                {
                    "phase": "transcribing",
                    "level": final_lvl,
                    "model": eff_model,
                    "audio_path": str(tmp_wav),
                }
            )

            stt_text = ""
            detected_lang = ""
            detected_prob = 0.0
            if rms_db > -55.0:
                payload = worker.transcribe(tmp_wav, start=0.0, end=dur, detect_language=True)
                stt_text = sanitize_text(str(payload.get("text", "")))
                dl = normalize_audio_language(str(payload.get("detected_language", "")))
                dp = float(payload.get("language_probability", 0.0) or 0.0)
                if dl and dl != "auto":
                    detected_lang = dl
                    detected_prob = dp

            if not detected_lang and stt_text:
                txt_det = detect_text_language(stt_text)
                t_lang = str(txt_det.get("detected_language", ""))
                t_prob = float(txt_det.get("confidence", 0.0) or 0.0)
                if t_lang and t_prob >= 0.55:
                    detected_lang = t_lang
                    detected_prob = t_prob

            recs = recommend_models_for_language(detected_lang or norm_lang)
            done_state = {
                "phase": "done",
                "ok": True,
                "level": final_lvl,
                "stt_text": stt_text,
                "model": eff_model,
                "audio_path": str(tmp_wav),
                "detected_language": detected_lang,
                "language_probability": round(detected_prob, 3),
                "detected_label_no": get_language_display_name(detected_lang, "no") if detected_lang else "",
                "detected_label_en": get_language_display_name(detected_lang, "en") if detected_lang else "",
                "recommended_whisper": recs["default_whisper"],
            }
            emit_state(done_state)

            stt_summary = (
                f' | STT: «{stt_text}»' if stt_text else " | STT: (ingen tydelig tale oppfattet)"
            )
            if st == "stille" or rms_db < -48.0:
                log(
                    f"==> MIKROFON-TEST RESULTAT: Svært lavt/stille ({rms_db} dBFS){stt_summary}. "
                    "Sjekk valgt mikrofon, øk systemvolum til 85–100%, eller velg +12 dB boost."
                )
            elif st == "lavt" or rms_db < -34.0:
                log(
                    f"==> MIKROFON-TEST RESULTAT: Lavt lydnivå ({rms_db} dBFS){stt_summary}. "
                    "Anbefaling: Sett mikrofon-boost til +12 dB."
                )
            else:
                log(
                    f"==> MIKROFON-TEST RESULTAT: Godt lydnivå ({rms_db} dBFS){stt_summary}. Klar til opptak!"
                )
            return done_state

        err_state = {
            "phase": "done",
            "ok": False,
            "error": "Ingen lyd mottatt fra mikrofonen. Sjekk mikrofontilgang i Systeminnstillinger.",
        }
        emit_state(err_state)
        log(
            "==> MIKROFON-TEST FEILET: Ingen lyd mottatt fra mikrofonen. Sjekk mikrofontilgang i Systeminnstillinger."
        )
        return err_state
    except Exception as exc:
        err_state = {"phase": "done", "ok": False, "error": str(exc)}
        emit_state(err_state)
        log(f"==> MIKROFON-TEST FEIL: {exc}")
        return err_state
    finally:
        if monitor is not None:
            monitor.stop()
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        worker.close()
