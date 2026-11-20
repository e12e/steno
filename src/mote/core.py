#!/usr/bin/env python3
"""Core facade and CLI entrypoint delegating to focused mote submodules."""

from __future__ import annotations

import argparse
import inspect
import json
import struct  # noqa: F401 - available in doctest globals
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import mote.audio as _audio_mod
import mote.constants as _constants_mod
import mote.context as _context_mod
import mote.hardware as _hardware_mod
import mote.pipeline as _pipeline_mod
import mote.render as _render_mod
from mote.audio import (  # noqa: F401
    LIVE_CHUNK_WORKER_CODE,
    MACOS_COREAUDIO_RECORD_RUNNER_CODE,
    LiveAudioMonitor,
    analyze_pcm16_audio_level,
    build_ffmpeg_mic_record_cmd,
    build_media_url_ffmpeg_cmd,
    build_mic_audio_filter,
    build_wav_bytes_from_pcm16,
    detect_audio_language,
    download_media_url_audio,
    downsample_pcm16_waveform,
    ensure_pregenerated_voiceover,
    find_wav_data_offset,
    format_live_check_line,
    get_system_input_volume,
    get_voiceover_script,
    list_audio_input_devices,
    open_system_sound_settings,
    parse_avfoundation_audio_devices,
    pick_live_whisper_model,
    pick_multilingual_whisper_model,
    resolve_moved_audio_path,
    run_live_check_on_file,
    run_mic_test,
    set_system_input_volume,
    stage_audio_input,
    start_mic_record_process,
)
from mote.constants import (  # noqa: F401
    APP_SUPPORT_DIR,
    CONTEXT_DOC_EXTENSIONS,
    HF_CACHE_DIR,
    HF_TOKEN_FILE,
    LANGUAGE_LABELS,
    LOCAL_BIN_DIR,
    MODEL_PROFILES,
    OLLAMA_BASE,
    OLLAMA_CHAT_URL,
    OLLAMA_TAGS_URL,
    TASK_MODE_ALIASES,
    TRANSCRIPT_EXTENSIONS,
    USER_PREFS_FILE,
    VALID_COLOR_SCHEMES,
    VALID_NOTE_MODES,
    VALID_TASK_MODES,
    PipelineResult,
    TurnDict,
    build_runtime_env,
    build_whisper_initial_prompt,
    clean_base_stem,
    clean_llm_output,
    detect_cpu_arch,
    detect_text_language,
    determine_stages_to_run,
    format_seconds,
    get_checkpoint_dir,
    get_language_display_name,
    is_media_url,
    is_norwegian_language,
    is_termux,
    is_transcript_file,
    load_user_preferences,
    normalize_audio_language,
    normalize_task_mode,
    parse_time_str,
    resolve_effective_note_mode,
    resolve_posix_shell,
    resolve_speaker_bounds,
    resolve_ui_lang,
    sanitize_text,
    save_user_preferences,
    unique_filename,
)
from mote.context import (  # noqa: F401
    _is_disallowed_ip,
    _SafeRedirectHandler,
    _validate_public_http_url,
    extract_context_from_file,
    extract_turns_from_whisperx,
    extract_turns_from_whisperx_data,
    fetch_context_from_url,
    load_turns_from_transcript_file,
    normalize_context_url,
    parse_transcript_text,
)
from mote.hardware import (  # noqa: F401
    _resolve_steno_install_paths,
    detect_hardware_resources,
    ensure_ffmpeg,
    ensure_ollama_binary,
    ensure_whisperx,
    evaluate_model_resource_fit,
    fetch_ollama_models,
    get_initial_hf_token,
    get_whisperx_python,
    install_steno_app,
    is_diarization_cached,
    managed_ollama_server,
    recommend_models_for_language,
    save_hf_token,
    uninstall_steno_app,
)
from mote.pipeline import (  # noqa: F401
    WHISPERX_STAGE_RUNNER_CODE,
    generate_notes_via_api,
    get_checkpoint_status_dict,
    infer_speaker_names_via_api,
    list_resumable_sessions,
    run_pipeline,
)
from mote.render import (  # noqa: F401
    _FALLBACK_NOTES_TPL,
    _FALLBACK_TRANSCRIPT_TPL,
    _format_inline_md,
    _is_safe_href,
    _MarkdownHtmlSanitizer,
    _render_markdown_to_html,
    _sanitize_rendered_html,
    build_notes_html,
    build_transcript_html_and_text,
)

__test__: dict[str, Any] = {}
for _submod in (
    _constants_mod,
    _audio_mod,
    _context_mod,
    _hardware_mod,
    _render_mod,
    _pipeline_mod,
):
    _submod_name = _submod.__name__
    for _attr_name, _attr_val in inspect.getmembers(_submod):
        if (
            getattr(_attr_val, "__module__", None) == _submod_name
            and (inspect.isfunction(_attr_val) or inspect.isclass(_attr_val))
            and ">>>" in (getattr(_attr_val, "__doc__", None) or "")
        ):
            __test__[f"{_submod_name}.{_attr_name}"] = _attr_val


def build_cli_parser(ui_lang: str | None = None, argv: list[str] | None = None) -> argparse.ArgumentParser:
    """Create the command-line ArgumentParser for steno / steno.py and transcribe.py (English & Norwegian)."""
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
        type=str,
        default=None,
        help=(
            "Path to audio file (.wav, .m4a, .opus, .mp3), existing transcript (.html, .json, .txt, .srt, .vtt), or media stream URL (http://, https://, rtmp://, etc.)"
            if is_en
            else "Sti til lydfil (.wav, .m4a, .opus, .mp3), eksisterende transkripsjon (.html, .json, .txt, .srt, .vtt), eller nettadresse/strøm (http://, https://, osv.)"
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
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Install Steno into ~/.local/share/steno with CLI launcher and desktop application shortcut"
            if is_en
            else "Installer Steno i ~/.local/share/steno med CLI-kommando og skrivebordssnarvei"
        ),
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help=(
            "Uninstall Steno and remove cached runtime environments, models, and shortcuts"
            if is_en
            else "Avinstaller Steno og fjern mellomlagrede kjøremiljøer, modeller og snarveier"
        ),
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "Automatic yes to prompts (e.g. confirm --uninstall without interactive prompt)"
            if is_en
            else "Bekreft automatisk uten spørsmål (f.eks. ved --uninstall)"
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
        print(
            f"==> GROV SANNTIDS-LYDSJEKK FRA MIKROFON ({duration_sec:.0f}s – trykk Ctrl+C for å avslutte)...",
            flush=True,
        )
        proc = start_mic_record_process(ffmpeg_bin, rec_file, env=env, gain_db=6.0, auto_gain=True)
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
    is_en = resolve_ui_lang(getattr(args, "ui_lang", None), argv) == "en"

    if getattr(args, "install", False):
        res = install_steno_app()
        print(res["message_en"] if is_en else res["message_no"], flush=True)
        return 0

    if getattr(args, "uninstall", False):
        if not getattr(args, "yes", False):
            prompt = (
                "Are you sure you want to uninstall Steno and remove all cached files? [y/N]: "
                if is_en
                else "Er du sikker på at du vil avinstallere Steno og slette alle mellomlagrede filer? [j/N]: "
            )
            try:
                ans = input(prompt).strip().lower()
            except EOFError:
                ans = "n"
            if ans not in ("y", "yes", "j", "ja"):
                print("Uninstall cancelled." if is_en else "Avinstallering avbrutt.", flush=True)
                return 0
        res = uninstall_steno_app()
        print(res["message_en"] if is_en else res["message_no"], flush=True)
        return 0

    hw_info = detect_hardware_resources(args.out_dir, language=args.language)
    if args.check_hw:
        fit = evaluate_model_resource_fit(
            args.whisper_model, args.ollama_model, hw_info, language=args.language
        )
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
        if is_norwegian_language(args.language):
            prof = MODEL_PROFILES[args.profile]
            args.whisper_model = prof["whisper_model"]
            args.ollama_model = prof["ollama_model"]
            prof_label = str(prof["label"])
        else:
            lang_rec = recommend_models_for_language(
                args.language,
                profile=args.profile,
                ram_gb=float(hw_info.get("ram_gb", 16.0)),
            )
            args.whisper_model = lang_rec["default_whisper"]
            args.ollama_model = lang_rec["default_ollama"]
            prof_label = f"{args.language} ({args.profile})"
        print(
            f"==> [Profil: {args.profile}] {prof_label} -> ASR={args.whisper_model}, LLM={args.ollama_model}",
            flush=True,
        )

    fit_check = evaluate_model_resource_fit(
        args.whisper_model, args.ollama_model, hw_info, language=args.language
    )
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

    if is_media_url(args.input_file):
        out_base = Path(args.out_dir).resolve() if args.out_dir else Path.cwd().resolve()
        dl_wav = download_media_url_audio(
            url=str(args.input_file),
            out_dir=out_base,
            log_fn=print,
            max_seconds=args.live_seconds if args.live_check else None,
        )
        resolved_input = dl_wav
        target_out_dir = out_base
    else:
        here = Path(__file__).resolve().parent
        base_dir = here.parent.parent if (here.name == "mote" and here.parent.name == "src") else here
        resolved_input = resolve_moved_audio_path(Path(args.input_file), base_dir)
        target_out_dir = (Path(args.out_dir) if args.out_dir else resolved_input.parent).resolve()

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
