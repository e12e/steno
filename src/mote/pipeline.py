"""5-stage checkpointed WhisperX + Pyannote + Ollama transcription and notes pipeline."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from mote.audio import (
    pick_multilingual_whisper_model,
    resolve_moved_audio_path,
    stage_audio_input,
)
from mote.constants import (
    OLLAMA_CHAT_URL,
    PipelineResult,
    TurnDict,
    build_runtime_env,
    build_whisper_initial_prompt,
    clean_base_stem,
    clean_llm_output,
    detect_text_language,
    determine_stages_to_run,
    format_seconds,
    get_checkpoint_dir,
    get_language_display_name,
    is_norwegian_language,
    is_transcript_file,
    normalize_audio_language,
    resolve_effective_note_mode,
    resolve_speaker_bounds,
    sanitize_text,
)
from mote.context import (
    extract_context_from_file,
    extract_turns_from_whisperx,
    load_turns_from_transcript_file,
)
from mote.hardware import (
    ensure_ffmpeg,
    ensure_whisperx,
    fetch_ollama_models,
    get_initial_hf_token,
    get_whisperx_python,
    is_diarization_cached,
    managed_ollama_server,
    save_hf_token,
)
from mote.render import build_notes_html, build_transcript_html_and_text
from mote.template_loader import load_runner

WHISPERX_STAGE_RUNNER_CODE: str = load_runner("whisperx_stage_runner.py")


def infer_speaker_names_via_api(
    turns: list[TurnDict],
    model: str,
    known_names_hint: str,
    meeting_title: str,
    agenda_text: str,
    log_fn: Callable[[str], None],
    manual_notes_text: str = "",
    language: str = "no",
) -> dict[str, str]:
    """Use Ollama to detect self-introductions, direct address, and roles from manual notes to map SPEAKER_XX."""
    unique_speakers = sorted({t["speaker"] for t in turns})
    if not unique_speakers:
        return {}

    explicit_map: dict[str, str] = {}
    for m_exp in re.finditer(r"(SPEAKER_\d+)\s*[=:]\s*([^,;]+)", known_names_hint):
        spk_id = m_exp.group(1).strip()
        spk_val = m_exp.group(2).strip()
        if spk_id in unique_speakers and spk_val:
            explicit_map[spk_id] = f"{spk_val} ({spk_id})" if spk_id not in spk_val else spk_val
    if len(explicit_map) == len(unique_speakers):
        return explicit_map

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
        "my name",
        "i am ",
        "i'm ",
        "chair",
        "welcome",
        "role",
        "title",
        "representing",
        "treasurer",
        "secretary",
        "heiße",
        "mein name",
        "jag heter",
        "jeg hedder",
        "je m'appelle",
        "me llamo",
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

    norm_lang = normalize_audio_language(language)
    if norm_lang == "auto":
        norm_lang = str(detect_text_language(" ".join(sample_lines)[:2500]).get("detected_language") or "no")
    is_no = is_norwegian_language(norm_lang)

    context_parts: list[str] = []
    if meeting_title.strip():
        lbl_title = "Møtetittel/Dato" if is_no else "Meeting title/Date"
        context_parts.append(f"{lbl_title}: {meeting_title.strip()}")
    if known_names_hint.strip():
        lbl_part = "Forhåndsoppgitte deltakere (tilstede)" if is_no else "Known participants (present)"
        context_parts.append(f"{lbl_part}: {known_names_hint.strip()}")
    if agenda_text.strip():
        lbl_ag = "Agenda / Lysbilder / Kontekst" if is_no else "Agenda / Slides / Context"
        context_parts.append(f"{lbl_ag}:\n{agenda_text.strip()[:3500]}")
    if manual_notes_text.strip():
        lbl_mn = (
            "Manuelle møtenotater / referat-utkast (bruk til å koble hvem som sier hva / har hvilken rolle)"
            if is_no
            else "Manual notes / draft minutes (use to match who speaks / holds each role)"
        )
        context_parts.append(f"{lbl_mn}:\n{manual_notes_text.strip()[:4500]}")
    default_ctx = "Ingen forhåndsoppgitt deltakerliste." if is_no else "No pre-supplied participant list."
    context_str = "\n".join(context_parts) if context_parts else default_ctx

    if is_no:
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
    else:
        prompt = (
            "Analyze the transcript excerpt below and identify who each speaker ID "
            f"({', '.join(unique_speakers)}) is, based on self-introductions, direct address between speakers, "
            "and who presents each topic/role.\n\n"
            f"{context_str}\n\n"
            "CRITICAL RULES (READ CAREFULLY TO AVOID SWAPPING SPEAKERS):\n"
            "1. DIRECT ADDRESS: When speaker SPEAKER_A addresses someone by name (e.g. 'What do you think, Alice?'), "
            "SPEAKER_A is NOT Alice — the speaker responding in the turn immediately after usually is!\n"
            "2. THIRD-PERSON REFERENCE: When a speaker refers to someone in third person ('Bob checked...'), "
            "the current speaker is NOT that person.\n"
            "3. ROLES & TOPICS FROM NOTES: Match who chairs the discussion or presents items mentioned in the manual notes.\n"
            "4. Return ONLY a valid JSON object mapping each speaker ID to the person's full name "
            "(matching the spelling in the participant list/notes, e.g. 'Alice Smith').\n"
            "5. If you are NOT certain about a speaker ID, keep the original ID, e.g. "
            '{"SPEAKER_00": "SPEAKER_00"}. Do not guess.\n\n'
            "Transcript excerpt:\n" + "\n".join(sample_lines)
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
    language: str = "no",
    cancel_check_fn: Callable[[], bool] | None = None,
) -> str:
    """Use Ollama HTTP /api/chat with streaming to generate meeting minutes or lecture notes."""
    detected_list = ", ".join(sorted(set(speaker_map.values())))
    context_lines: list[str] = []
    if meeting_title.strip():
        context_lines.append(f"Tittel og dato / Title & Date: {meeting_title.strip()}")
    if known_names_hint.strip():
        context_lines.append(f"Oppgitte personer / Participants: {known_names_hint.strip()}")
    if detected_list:
        context_lines.append(f"Identifiserte stemmer / Identified voices: {detected_list}")
    if agenda_text.strip():
        context_lines.append(f"Agenda / Slides / Context:\n{agenda_text.strip()}")
    if manual_notes_text.strip():
        context_lines.append(
            "Manuelle møtenotater / Draft notes (AUTHORITATIVE source for names, roles, decisions, and action items):\n"
            f"{manual_notes_text.strip()}"
        )
    context_block = "\n".join(context_lines)

    norm_lang = normalize_audio_language(language)
    if norm_lang == "auto":
        norm_lang = str(detect_text_language(transcript[:2500]).get("detected_language") or "no")

    is_no = is_norwegian_language(norm_lang)
    lang_name_en = get_language_display_name(norm_lang, "en").split("(")[0].strip()

    if not is_no:
        lang_instruction = (
            "Write in clear, professional English."
            if norm_lang == "en"
            else f"Write the entire document and all section headings in {lang_name_en} ({norm_lang})."
        )
        if note_mode == "foredrag":
            system_prompt = (
                "You are an expert academic and technical editor. Output ONLY the structured lecture notes in clean Markdown. "
                "Do NOT include internal reasoning, introductions, or <think> blocks. "
                f"The recording is a lecture, presentation, or single-speaker talk. {lang_instruction}"
            )
            user_prompt = (
                "Create a comprehensive, well-structured summary of the lecture/presentation based on the context and timestamped transcript below.\n\n"
                f"--- CONTEXT ---\n{context_block}\n--- END CONTEXT ---\n\n"
                "Use this structure (translated into the target language if not English):\n"
                "# Lecture Notes and Key Takeaways\n\n"
                "## 1. Overview and Core Message\n"
                "(Title/date, speaker and role if mentioned, and the central thesis summarized in one paragraph)\n\n"
                "## 2. Key Takeaways and Insights\n"
                "(Bullet list of the most important findings, conclusions, and lessons from the talk)\n\n"
                "## 3. Thematic Breakdown\n"
                "(Structured by main topics with subheadings, key arguments, concrete examples, figures/facts, and timestamps)\n\n"
                "## 4. Key Concepts, Models, or Tools\n"
                "(Concise explanations of important terms, methods, technologies, or references covered)\n\n"
                "## 5. Recommendations and Further Reflection\n"
                "(Practical advice, open questions, or suggested follow-up mentioned in the talk)\n\n"
                f"--- TRANSCRIPT START ---\n{transcript}\n--- TRANSCRIPT END ---"
            )
        else:
            system_prompt = (
                "You are a professional meeting secretary. Output ONLY the meeting minutes in clean Markdown. "
                "Do NOT include internal reasoning, introductions, or <think> blocks. "
                f"{lang_instruction} Organize the minutes by the agenda/topics and use the participants' real names and titles throughout."
            )
            user_prompt = (
                "Create thorough, structured meeting minutes based on the context and timestamped transcript below.\n\n"
                f"--- MEETING CONTEXT ---\n{context_block}\n--- END CONTEXT ---\n\n"
                "Use this structure (translated into the target language if not English):\n"
                "# Meeting Minutes\n\n"
                "## 1. Meeting Overview and Executive Summary\n"
                "(Title, date, purpose of the meeting, and main conclusions)\n\n"
                "## 2. Participants and Roles\n"
                "(List of participants with full name, title/role, and area of responsibility)\n\n"
                "## 3. Agenda Items and Discussion\n"
                "(Structured by agenda items/topics with subheadings, viewpoints attributed by name, and timestamps)\n\n"
                "## 4. Decisions\n"
                "(Concrete decisions and agreements reached)\n\n"
                "## 5. Action Items\n"
                "(Markdown table with columns: Owner | Task | Deadline / Status)\n\n"
                f"--- TRANSCRIPT START ---\n{transcript}\n--- TRANSCRIPT END ---"
            )
    elif note_mode == "foredrag":
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
            if cancel_check_fn is not None and cancel_check_fn():
                break
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


def get_checkpoint_status_dict(
    out_dir: Path,
    input_path: Path,
    html_name: str = "",
    notes_name: str = "",
    notes_html_name: str = "",
) -> dict[str, Any]:
    """Return checkpoint completion status for stages 1..5 and output file existence."""
    ckpt_dir = get_checkpoint_dir(out_dir, input_path)
    is_trans = is_transcript_file(input_path)
    stem = clean_base_stem(input_path)
    eff_html = html_name.strip() or f"{stem}_transkripsjon.html"
    eff_notes = notes_name.strip() or f"{stem}_referat.md"
    eff_notes_html = notes_html_name.strip() or f"{stem}_referat.html"
    html_exists = (out_dir / Path(eff_html).name).is_file()
    notes_exists = (out_dir / Path(eff_notes).name).is_file()
    notes_html_exists = (out_dir / Path(eff_notes_html).name).is_file()
    return {
        "has_input": input_path.is_file(),
        "is_transcript_input": is_trans,
        "s1_asr": is_trans or (ckpt_dir / "1_asr.json").is_file(),
        "s2_align": is_trans or (ckpt_dir / "2_align.json").is_file(),
        "s3_diarize": is_trans or (ckpt_dir / "3_diarize.json").is_file(),
        "s4_names": (ckpt_dir / "4_speakers.json").is_file() or html_exists,
        "s5_notes": notes_exists or notes_html_exists,
        "html_exists": html_exists,
        "notes_exists": notes_exists,
        "notes_html_exists": notes_html_exists,
        "ckpt_dir": str(ckpt_dir),
    }


def list_resumable_sessions(
    out_dir: Path | None = None,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Discover existing recordings, transcripts, and checkpointed sessions in out_dir and data/."""
    audio_exts = {".wav", ".m4a", ".opus", ".mp3", ".ogg", ".flac", ".aac", ".mp4", ".webm"}
    candidate_dirs: list[Path] = []
    if out_dir is not None and out_dir.is_dir():
        candidate_dirs.append(out_dir.resolve())
    if base_dir is not None:
        data_root = (base_dir / "data").resolve()
        if data_root.is_dir():
            candidate_dirs.append(data_root)
            try:
                children = sorted(data_root.iterdir())
            except OSError:
                children = []
            for child in children:
                if child.is_dir() and not child.name.startswith("."):
                    candidate_dirs.append(child.resolve())

    seen_stems: set[tuple[str, str]] = set()
    sessions: list[dict[str, Any]] = []

    for folder in candidate_dirs:
        if not folder.is_dir():
            continue
        ckpt_root = folder / ".mote_checkpoints"
        stems_in_folder: dict[str, Path] = {}

        if ckpt_root.is_dir():
            try:
                ckpt_subs = list(ckpt_root.iterdir())
            except OSError:
                ckpt_subs = []
            for ckpt_sub in ckpt_subs:
                if ckpt_sub.is_dir():
                    stems_in_folder[ckpt_sub.name] = ckpt_sub

        try:
            folder_entries = list(folder.iterdir())
        except OSError:
            folder_entries = []
        for f in folder_entries:
            if not f.is_file() or f.name.startswith("."):
                continue
            low_ext = f.suffix.lower()
            if low_ext in audio_exts or f.name.endswith("_transkripsjon.html"):
                st = clean_base_stem(f)
                if st and st not in stems_in_folder:
                    stems_in_folder[st] = folder / ".mote_checkpoints" / st

        for stem, ckpt_dir in stems_in_folder.items():
            key = (str(folder), stem)
            if key in seen_stems:
                continue
            seen_stems.add(key)

            input_file: Path | None = None
            for ext in (".wav", ".opus", ".m4a", ".mp3", ".ogg", ".flac", ".mp4", ".webm"):
                cand = folder / f"{stem}{ext}"
                if cand.is_file():
                    input_file = cand
                    break
            if input_file is None:
                for f in folder_entries:
                    if f.is_file() and f.suffix.lower() in audio_exts and clean_base_stem(f) == stem:
                        input_file = f
                        break
            is_ckpt_fallback = False
            if input_file is None:
                trans_cand = folder / f"{stem}_transkripsjon.html"
                if trans_cand.is_file():
                    input_file = trans_cand
                elif (ckpt_dir / "4_speakers.json").is_file():
                    input_file = ckpt_dir / "4_speakers.json"
                    is_ckpt_fallback = True
                elif (ckpt_dir / "3_diarize.json").is_file():
                    input_file = ckpt_dir / "3_diarize.json"
                    is_ckpt_fallback = True
                elif (ckpt_dir / "1_asr.json").is_file():
                    input_file = ckpt_dir / "1_asr.json"
                    is_ckpt_fallback = True

            if input_file is None or not input_file.is_file():
                continue

            if is_ckpt_fallback:
                status = get_checkpoint_status_dict(
                    folder,
                    folder / f"{stem}.wav",
                    html_name=f"{stem}_transkripsjon.html",
                    notes_name=f"{stem}_referat.md",
                    notes_html_name=f"{stem}_referat.html",
                )
                status["has_input"] = input_file.is_file()
                status["is_transcript_input"] = is_transcript_file(input_file)
            else:
                status = get_checkpoint_status_dict(folder, input_file)
            completed = sum(
                1 for k in ("s1_asr", "s2_align", "s3_diarize", "s4_names", "s5_notes") if status.get(k)
            )
            try:
                mtime = input_file.stat().st_mtime
            except OSError:
                mtime = 0.0
            if ckpt_dir.is_dir():
                for cf in ckpt_dir.glob("*.json"):
                    try:
                        mtime = max(mtime, cf.stat().st_mtime)
                    except OSError:
                        pass

            if not status["s1_asr"]:
                next_no, next_en = "Steg 1: Rå-transkripsjon (ASR)", "Stage 1: Whisper ASR"
            elif not status["s2_align"]:
                next_no, next_en = "Steg 2: Ord-tidsjustering", "Stage 2: Alignment"
            elif not status["s3_diarize"]:
                next_no, next_en = "Steg 3: Taler-ID (Pyannote)", "Stage 3: Speaker Diarization"
            elif not status["s4_names"]:
                next_no, next_en = "Steg 4: Navn & HTML", "Stage 4: Speaker Names & HTML"
            elif not status["s5_notes"]:
                next_no, next_en = "Steg 5: Skriv Referat / Notat", "Stage 5: Generate Minutes/Notes"
            else:
                next_no, next_en = "Fullført (kan regenerere referat)", "Completed (can regenerate notes)"

            sessions.append(
                {
                    "stem": stem,
                    "audio_path": str(input_file),
                    "out_dir": str(folder),
                    "html_name": f"{stem}_transkripsjon.html",
                    "notes_name": f"{stem}_referat.md",
                    "notes_html_name": f"{stem}_referat.html",
                    "is_transcript_input": is_transcript_file(input_file),
                    "checkpoints": status,
                    "completed_count": completed,
                    "next_stage_no": next_no,
                    "next_stage_en": next_en,
                    "updated_ts": mtime,
                    "updated_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "",
                }
            )

    sessions.sort(key=lambda s: float(s.get("updated_ts", 0.0)), reverse=True)
    return sessions


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

    here = Path(__file__).resolve().parent
    base_dir = here.parent.parent if (here.name == "mote" and here.parent.name == "src") else here
    resolved_input = resolve_moved_audio_path(input_path, base_dir)
    if not resolved_input.is_file():
        raise FileNotFoundError(f"Fant ikke inndatafilen: {input_path}")

    target_out_dir = (out_dir or resolved_input.parent).resolve()
    target_out_dir.mkdir(parents=True, exist_ok=True)

    stem = clean_base_stem(resolved_input)
    html_path = target_out_dir / (
        Path(html_name.strip()).name if html_name.strip() else f"{stem}_transkripsjon.html"
    )
    notes_path = target_out_dir / (
        Path(notes_name.strip()).name if notes_name.strip() else f"{stem}_referat.md"
    )
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
    norm_lang = normalize_audio_language(language)
    effective_language = norm_lang
    effective_whisper_model = whisper_model
    if not is_norwegian_language(norm_lang) and "nb-whisper" in whisper_model.lower():
        effective_whisper_model = pick_multilingual_whisper_model(whisper_model)
        log(
            f"==> Merk: '{whisper_model}' er kun trent for norsk. Byttet automatisk til flerspråklig modell "
            f"'{effective_whisper_model}' for språk='{norm_lang}'."
        )
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
                "model": effective_whisper_model,
                "language": norm_lang,
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
                        prog(12, f"[Steg 1/5] Transkriberer med {effective_whisper_model}...")
                        log(
                            f"==> [Steg 1/5] Kjører WhisperX ASR ({effective_whisper_model}, språk={norm_lang}) "
                            f"på {resolved_input.name}..."
                        )
                        continue
                    if clean.startswith("STAGE:DETECTED_LANG:"):
                        det_code = normalize_audio_language(clean.split(":", 2)[2].strip())
                        if det_code and det_code != "auto":
                            effective_language = det_code
                            log(
                                f"==> [Steg 1/5] Oppdaget talespråk: "
                                f"{get_language_display_name(det_code, 'no')} ({det_code})"
                            )
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

    def _cancelled_result(eff_mode: str) -> PipelineResult:
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
            "effective_mode": eff_mode,
            "speaker_map": speaker_map,
            "turn_count": len(turns),
        }

    with managed_ollama_server(log, prog, env=env) as ollama_bin:
        installed = fetch_ollama_models() or []
        if ollama_model not in installed and f"{ollama_model}:latest" not in installed:
            if cancel_check_fn is not None and cancel_check_fn():
                return _cancelled_result(note_mode)
            prog(75, f"Laster ned {ollama_model}...")
            log(f"==> Laster ned '{ollama_model}'...")
            subprocess.run([ollama_bin, "pull", ollama_model], env=env, check=True)

        if cancel_check_fn is not None and cancel_check_fn():
            return _cancelled_result(note_mode)

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
                language=effective_language,
            )
            merged_map = dict(speaker_map)
            for spk_id, resolved_name in inferred_map.items():
                if resolved_name == spk_id and spk_id in merged_map and merged_map[spk_id] != spk_id:
                    continue
                merged_map[spk_id] = resolved_name
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

        if cancel_check_fn is not None and cancel_check_fn():
            return _cancelled_result(effective_mode)

        mode_desc = (
            "foredragsnotater (1-talers modus / faglige hovedpoenger)"
            if effective_mode == "foredrag"
            else "møtereferat (flere deltakere & aksjonspunkter)"
        )
        prog(84, f"[Steg 5/5] Skriver {mode_desc} med {ollama_model}...")
        log(f"==> [Steg 5/5] Genererer {mode_desc} ({ollama_model})...")

        if effective_language == "auto" and asr_json.is_file():
            try:
                s1_data = json.loads(asr_json.read_text(encoding="utf-8"))
                s1_lang = normalize_audio_language(str(s1_data.get("language", "")))
                if s1_lang and s1_lang != "auto":
                    effective_language = s1_lang
            except Exception:
                pass
        if effective_language == "auto":
            txt_det = detect_text_language(clean_transcript)
            t_lang = str(txt_det.get("detected_language", ""))
            t_prob = float(txt_det.get("confidence", 0.0) or 0.0)
            if t_lang and t_prob >= 0.55:
                effective_language = t_lang

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
            language=effective_language,
            cancel_check_fn=cancel_check_fn,
        )
        if cancel_check_fn is not None and cancel_check_fn():
            return _cancelled_result(effective_mode)
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
