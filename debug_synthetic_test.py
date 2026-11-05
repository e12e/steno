#!/usr/bin/env python3
"""Generate safe synthetic 4-speaker Norwegian meeting audio and run a full debug session."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEST_DIR = BASE_DIR / "synthetic_test"

MEETING_TITLE = "Statusmøte Sikkerhetsplattform 2026"
SPEAKER_HINT = (
    "Kari Nordmann (Prosjektleder), "
    "Ola Hansen (Systemutvikler), "
    "Ingrid Berg (Sikkerhetsarkitekt), "
    "Per Johansen (Økonomiansvarlig)"
)

AGENDA_MD = """# Agenda: Statusmøte Sikkerhetsplattform 2026
Dato: 29. oktober 2026
Tilstede:
- Kari Nordmann (Prosjektleder)
- Ola Hansen (Systemutvikler)
- Ingrid Berg (Sikkerhetsarkitekt)
- Per Johansen (Økonomiansvarlig)

## Saksliste
1. Velkomst og presentasjon av deltakerne
2. Status på API-migrering og OAuth2-innlogging (Ola)
3. Sikkerhetsrevisjon og krav om tofaktorautentisering (Ingrid)
4. Budsjett og lisenskostnader for fjerde kvartal (Per)
5. Beslutninger og aksjonspunkter
"""

# 4 distinct acoustic voice profiles built from macOS Norwegian TTS ('Nora')
# Using vocal-tract length warping (asetrate + aresample + atempo) + formant EQ
# so Pyannote's ResNet34 speaker embedding model sees 4 distinct vocal tracts.
VOICE_PROFILES = {
    "Kari Nordmann": {
        "role": "Prosjektleder",
        "say_voice": "Nora",
        "say_rate": "182",
        # Natural soprano/mezzo female vocal tract (slightly brighter)
        "af_filter": (
            "asetrate=22050*1.05,aresample=16000,atempo=0.9524,"
            "highpass=f=110,treble=g=3:f=3200,loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    "Ola Hansen": {
        "role": "Systemutvikler",
        "say_voice": "Nora",
        "say_rate": "175",
        # Tenor/baritone male vocal tract (22% longer vocal tract + chest resonance)
        "af_filter": (
            "asetrate=22050*0.78,aresample=16000,atempo=1.282,"
            "highpass=f=75,bass=g=5:f=160,treble=g=-3:f=4200,loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    "Ingrid Berg": {
        "role": "Sikkerhetsarkitekt",
        "say_voice": "Nora",
        "say_rate": "168",
        # Warm alto female vocal tract + subtle room reflection
        "af_filter": (
            "asetrate=22050*0.93,aresample=16000,atempo=1.075,"
            "highpass=f=95,equalizer=f=1100:t=q:w=1.2:g=3,aecho=0.8:0.88:12:0.15,"
            "loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    "Per Johansen": {
        "role": "Økonomiansvarlig",
        "say_voice": "Nora",
        "say_rate": "188",
        # Deep baritone male vocal tract (30% longer vocal tract + low-mid warmth)
        "af_filter": (
            "asetrate=22050*0.70,aresample=16000,atempo=1.4285,"
            "highpass=f=65,bass=g=6:f=130,equalizer=f=700:t=q:w=1.0:g=3,"
            "loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
}

DEFAULT_SCRIPT = [
    {
        "speaker": "Kari Nordmann",
        "pause_after_ms": 500,
        "text": (
            "Hei alle sammen, og velkommen til statusmøte for Sikkerhetsplattform to tusen og tjueseks. "
            "Mitt navn er Kari Nordmann, og jeg er prosjektleder. "
            "La oss ta en kort runde rundt bordet så alle får sagt hvem de er."
        ),
    },
    {
        "speaker": "Ola Hansen",
        "pause_after_ms": 400,
        "text": (
            "Hei, jeg heter Ola Hansen, og jeg jobber som systemutvikler med ansvar for backend og API-migrering."
        ),
    },
    {
        "speaker": "Ingrid Berg",
        "pause_after_ms": 400,
        "text": (
            "Og jeg heter Ingrid Berg. Jeg er sikkerhetsarkitekt på prosjektet og følger opp sikkerhetsrevisjonen."
        ),
    },
    {
        "speaker": "Per Johansen",
        "pause_after_ms": 550,
        "text": (
            "Hei, mitt navn er Per Johansen, og jeg er økonomiansvarlig for plattformbudsjettet i fjerde kvartal."
        ),
    },
    {
        "speaker": "Kari Nordmann",
        "pause_after_ms": 300,
        "text": (
            "Takk skal dere ha. Første punkt på agendaen er status på API-migrering. Ola, hvordan ligger vi an der?"
        ),
    },
    {
        "speaker": "Ola Hansen",
        "pause_after_ms": 200,
        "text": (
            "Vi har fullført overgangen til ny OAuth2-innlogging i testmiljøet denne uken. "
            "Alle endepunkter svarer under førti millisekunder, men vi trenger to dager til på feilhåndtering."
        ),
    },
    {
        "speaker": "Ingrid Berg",
        "pause_after_ms": 250,
        "text": (
            "Det høres bra ut, Ola. Under sikkerhetstesten i går fant vi at tofaktorautentisering må være påkrevd "
            "for alle administratorbrukere før vi går i produksjon."
        ),
    },
    {
        "speaker": "Ola Hansen",
        "pause_after_ms": 450,
        "text": (
            "Den er grei, Ingrid. Jeg legger inn tvungen tofaktorautentisering i konfigurasjonen innen fredag."
        ),
    },
    {
        "speaker": "Kari Nordmann",
        "pause_after_ms": 350,
        "text": (
            "Flott, da vedtar vi at tofaktorautentisering er obligatorisk før lansering. "
            "Per, hvordan ser budsjettet og lisenskostnadene ut for fjerde kvartal?"
        ),
    },
    {
        "speaker": "Per Johansen",
        "pause_after_ms": 450,
        "text": (
            "Vi ligger femten prosent under budsjett så langt i kvartalet fordi vi samlet skyserverne i Norge. "
            "Men vi må fornye sertifikatlisensen innen femtende november, som koster førti tusen kroner."
        ),
    },
    {
        "speaker": "Ingrid Berg",
        "pause_after_ms": 350,
        "text": (
            "Jeg sender over oppdatert sikkerhetsrapport og kravspesifikasjon til både Kari og Per innen onsdag."
        ),
    },
    {
        "speaker": "Kari Nordmann",
        "pause_after_ms": 300,
        "text": (
            "Utmerket. Da oppsummerer jeg aksjonspunktene: Ola ferdigstiller OAuth2 og tofaktor innen fredag, "
            "Ingrid leverer sikkerhetsrapporten innen onsdag, og Per bestiller sertifikatlisensen før femtende november. "
            "Takk for et effektivt møte!"
        ),
    },
]


def maybe_generate_script_via_ollama(ollama_model: str) -> list[dict]:
    """Optionally enrich or generate script via local Ollama, falling back to DEFAULT_SCRIPT."""
    url = "http://127.0.0.1:11434/api/chat"
    prompt = (
        "Lag ett kort ekstra diskusjonsinnlegg (1-2 setninger på norsk bokmål) fra hver av de 4 deltakerne "
        "Kari Nordmann, Ola Hansen, Ingrid Berg og Per Johansen om backup-rutiner før produksjonssetting. "
        "Returner KUN gyldig JSON som en liste med objekter: "
        '[{"speaker": "Kari Nordmann", "text": "..."}, ...].'
    )
    payload = json.dumps(
        {
            "model": ollama_model,
            "stream": False,
            "format": "json",
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.2},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body.get("message", {}).get("content", "").strip()
            parsed = json.loads(content)
            extra_turns = []
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        parsed = val
                        break
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("speaker") in VOICE_PROFILES and item.get("text"):
                        extra_turns.append(
                            {
                                "speaker": item["speaker"],
                                "pause_after_ms": 350,
                                "text": str(item["text"]).strip(),
                            }
                        )
            if extra_turns:
                print(f"[SYNTH] Utvidet manus med {len(extra_turns)} replikker fra lokal LLM ({ollama_model}).")
                return DEFAULT_SCRIPT[:-1] + extra_turns + [DEFAULT_SCRIPT[-1]]
    except Exception as exc:
        print(f"[SYNTH] Bruker innebygd 4-talers testmanus (Ollama ikke spurt: {exc}).")
    return list(DEFAULT_SCRIPT)


def synthesize_meeting_wav(script: list[dict], out_wav: Path, ffmpeg_bin: str) -> None:
    """Synthesize multi-speaker 16kHz mono WAV using macOS TTS + FFmpeg vocal-tract profiles."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synth_tts_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        part_files: list[Path] = []

        for idx, turn in enumerate(script):
            speaker = turn["speaker"]
            text = turn["text"]
            pause_ms = int(turn.get("pause_after_ms", 350))
            profile = VOICE_PROFILES[speaker]

            raw_aiff = tmp_path / f"turn_{idx:02d}_raw.aiff"
            proc_wav = tmp_path / f"turn_{idx:02d}_proc.wav"
            pause_wav = tmp_path / f"turn_{idx:02d}_pause.wav"

            subprocess.run(
                [
                    "/usr/bin/say",
                    "-v",
                    profile["say_voice"],
                    "-r",
                    profile["say_rate"],
                    "-o",
                    str(raw_aiff),
                    text,
                ],
                check=True,
            )

            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(raw_aiff),
                    "-af",
                    profile["af_filter"],
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(proc_wav),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            part_files.append(proc_wav)

            if pause_ms > 0:
                dur_sec = f"{pause_ms / 1000.0:.3f}"
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=16000:cl=mono",
                        "-t",
                        dur_sec,
                        "-c:a",
                        "pcm_s16le",
                        str(pause_wav),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                part_files.append(pause_wav)

        concat_list = tmp_path / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in part_files) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                "-vbr",
                "on",
                "-application",
                "voip",
                str(out_wav),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )


def run_debug_pipeline(
    wav_path: Path,
    out_dir: Path,
    whisper_model: str,
    ollama_model: str,
    task_mode: str = "auto_resume",
) -> dict:
    """Run mote.core.run_pipeline with debug instrumentation."""
    sys.path.insert(0, str(BASE_DIR / "src"))
    sys.path.insert(0, str(BASE_DIR))
    from mote import core as mote_core

    html_name = "syntetisk_mote_4_talere_transkripsjon.html"
    notes_name = "syntetisk_mote_4_talere_referat.md"
    notes_html_name = "syntetisk_mote_4_talere_referat.html"

    if task_mode == "full_fresh":
        for fname in (html_name, notes_name, notes_html_name):
            fpath = out_dir / fname
            if fpath.exists():
                fpath.unlink()

    progress_history: list[tuple[float, int, str]] = []
    log_history: list[tuple[float, str]] = []
    t0 = time.time()

    def _log(msg: str) -> None:
        elapsed = time.time() - t0
        log_history.append((elapsed, msg))
        print(f"[{elapsed:6.1f}s] [LOG] {msg}", flush=True)

    def _set_progress(percent: int, label: str) -> None:
        elapsed = time.time() - t0
        if progress_history:
            _, prev_pct, prev_lbl = progress_history[-1]
            if prev_pct == percent and prev_lbl == label:
                return
        progress_history.append((elapsed, percent, label))
        print(f"[{elapsed:6.1f}s] [PROGRESS {percent:3d}%] {label}", flush=True)

    hf_token = mote_core.get_initial_hf_token()

    print("\n" + "=" * 76)
    print(f"STARTER CLI/PIPELINE E2E-KJØRING PÅ SYNTETISK OPPTAK (task={task_mode})")
    print("=" * 76)
    mote_core.run_pipeline(
        input_path=wav_path,
        out_dir=out_dir,
        html_name=html_name,
        notes_name=notes_name,
        notes_html_name=notes_html_name,
        whisper_model=whisper_model,
        ollama_model=ollama_model,
        language="no",
        hf_token=hf_token,
        min_speakers=4,
        max_speakers=4,
        speaker_names_hint=SPEAKER_HINT,
        meeting_title=MEETING_TITLE,
        agenda_text=AGENDA_MD,
        note_mode="mote",
        task_mode=task_mode,
        log_fn=_log,
        progress_fn=_set_progress,
    )

    return {
        "progress_history": progress_history,
        "log_history": log_history,
        "html_path": out_dir / html_name,
        "notes_path": out_dir / notes_name,
        "notes_html_path": out_dir / notes_html_name,
    }


def evaluate_results(result: dict, script: list[dict]) -> int:
    """Verify generated files, progress stages, and speaker name attribution."""
    print("\n" + "=" * 76)
    print("VERIFIKASJONSRAPPORT FOR SYNTETISK MØTETEST")
    print("=" * 76)

    html_path: Path = result["html_path"]
    notes_path: Path = result["notes_path"]
    notes_html_path: Path = result["notes_html_path"]
    progress_history = result["progress_history"]
    log_history = result["log_history"]

    all_ok = True
    for label, path in [
        ("HTML-transkripsjon", html_path),
        ("Markdown-møtereferat", notes_path),
        ("HTML-møtereferat", notes_html_path),
    ]:
        exists = path.is_file() and path.stat().st_size > 100
        status = "OK" if exists else "MANGLER/TOM"
        size = path.stat().st_size if path.is_file() else 0
        print(f"- {label:22s}: {status} ({size} bytes) -> {path}")
        if not exists:
            all_ok = False

    ckpt_dir = html_path.parent / ".mote_checkpoints" / "syntetisk_mote_4_talere"
    stages_seen = {
        "stage_1_asr": any("[Steg 1/5]" in lbl or "[1/4]" in lbl for _, _, lbl in progress_history)
        or (ckpt_dir / "1_asr.json").is_file(),
        "stage_2_align": any("[Steg 2/5]" in lbl or "[2/4]" in lbl for _, _, lbl in progress_history)
        or (ckpt_dir / "2_align.json").is_file(),
        "stage_3_diarize": any("[Steg 3/5]" in lbl or "[3/4]" in lbl for _, _, lbl in progress_history)
        or (ckpt_dir / "3_diarize.json").is_file(),
        "stage_4_names": any("[Steg 4/5]" in lbl for _, _, lbl in progress_history)
        or (ckpt_dir / "4_speakers.json").is_file()
        or any("4_speakers.json" in msg for _, msg in log_history),
        "stage_5_notes": any("[Steg 5/5]" in lbl or "[4/4]" in lbl for _, _, lbl in progress_history),
        "completed_100": any(pct == 100 for _, pct, _ in progress_history),
    }
    print("\nFremdriftssteg / sjekkpunkter verifisert:")
    for k, seen in stages_seen.items():
        print(f"  * {k:18s}: {'JA' if seen else 'NEI'}")

    expected_speakers = list(VOICE_PROFILES.keys())
    if html_path.is_file():
        tx_html = html_path.read_text(encoding="utf-8")
        print("\nGjenkjente deltakernavn i HTML-transkripsjon:")
        for name in expected_speakers:
            count = len(re.findall(re.escape(name), tx_html, flags=re.IGNORECASE))
            print(f"  * {name:18s}: {count} treff")

    if notes_path.is_file():
        notes_md = notes_path.read_text(encoding="utf-8")
        print("\nGjenkjente deltakernavn i Møtereferat (MD):")
        for name in expected_speakers:
            count = len(re.findall(re.escape(name), notes_md, flags=re.IGNORECASE))
            print(f"  * {name:18s}: {count} treff")

    return 0 if all_ok and stages_seen["completed_100"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Syntetisk 4-talers møtetest og debug-kjøring.")
    parser.add_argument(
        "--step",
        choices=["generate", "run", "map-names", "check", "all"],
        default="all",
        help="Hvilket steg som skal kjøres (generate=kun lyd/manus, run=kjør pipeline, map-names=oppdater talernavn i HTML, check=syntakssjekk, all=begge)",
    )
    parser.add_argument(
        "--whisper-model",
        default="NbAiLab/nb-whisper-small",
        help="Whisper-modell for debug-testen (standard: %(default)s)",
    )
    parser.add_argument(
        "--ollama-model",
        default="gemma4:e4b",
        help="Ollama-modell for debug-testen (standard: %(default)s)",
    )
    parser.add_argument(
        "--llm-script",
        action="store_true",
        help="Bruk Ollama til å utvide det syntetiske møtemanuset før TTS",
    )
    args = parser.parse_args()

    TEST_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = TEST_DIR / "syntetisk_mote_4_talere.opus"
    script_json_path = TEST_DIR / "fasit_transkripsjon.json"
    agenda_path = TEST_DIR / "fasit_agenda.md"

    ffmpeg_bin = shutil.which("ffmpeg", path="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""))
    if not ffmpeg_bin:
        raise RuntimeError("Fant ikke ffmpeg.")

    if args.step in ("generate", "all"):
        script = (
            maybe_generate_script_via_ollama(args.ollama_model)
            if args.llm_script
            else list(DEFAULT_SCRIPT)
        )
        script_json_path.write_text(
            json.dumps(script, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        agenda_path.write_text(AGENDA_MD, encoding="utf-8")
        print(f"[SYNTH] Genererer 4-talers møtelyd ({len(script)} replikker) til {wav_path} ...")
        t0 = time.time()
        synthesize_meeting_wav(script, wav_path, ffmpeg_bin)
        dur_out = subprocess.check_output(
            [
                ffmpeg_bin.replace("ffmpeg", "ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(wav_path),
            ],
            text=True,
        ).strip()
        print(
            f"[SYNTH] Ferdig på {time.time() - t0:.1f}s! Lydlengde: {float(dur_out):.1f}s -> {wav_path}"
        )
    else:
        script = json.loads(script_json_path.read_text(encoding="utf-8"))

    if args.step == "check":
        import py_compile
        for fname in ("transcribe.py", "steno.py", "debug_synthetic_test.py"):
            py_compile.compile(str(BASE_DIR / fname), doraise=True)
            print(f"[CHECK] Syntaks OK: {fname}")
        return 0

    if args.step == "map-names":
        import html

        sys.path.insert(0, str(BASE_DIR / "src"))
        from mote import core as mote_core

        html_path = TEST_DIR / "syntetisk_mote_4_talere_transkripsjon.html"
        raw = html_path.read_text(encoding="utf-8")
        turns = []
        for m in re.finditer(
            r'data-speaker="(SPEAKER_\d+)".*?timestamp">(\d+):(\d+)\s*–\s*(\d+):(\d+)</span>.*?<div class="text">(.*?)</div>',
            raw,
            re.DOTALL,
        ):
            spk, m1, s1, m2, s2, txt = m.groups()
            turns.append(
                {
                    "speaker": spk,
                    "start": int(m1) * 60 + int(s1),
                    "end": int(m2) * 60 + int(s2),
                    "text": html.unescape(txt),
                }
            )
        spk_map = mote_core.infer_speaker_names_via_api(
            turns,
            args.ollama_model,
            SPEAKER_HINT,
            MEETING_TITLE,
            AGENDA_MD,
            print,
        )
        print("Mapped speakers:", spk_map)
        if any(k != v for k, v in spk_map.items()):
            mote_core.build_transcript_html_and_text(
                turns, spk_map, html_path, MEETING_TITLE
            )
            print("Updated HTML transcript with speaker names:", html_path)
        return 0

    if args.step in ("run", "all"):
        res = run_debug_pipeline(
            wav_path=wav_path,
            out_dir=TEST_DIR,
            whisper_model=args.whisper_model,
            ollama_model=args.ollama_model,
        )
        return evaluate_results(res, script)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
