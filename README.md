# Steno — Local AI Meeting & Lecture Transcriber

**Steno - a modern annalist for your meetings - an automaton stenographer.**

100% local, zero-cloud desktop app and CLI for **recording**, **transcribing**, **speaker diarization** (who said what), and **generating structured meeting minutes or lecture notes**. All audio processing, speech-to-text, diarization, and LLM summarization run locally on your own hardware via WhisperX, Pyannote, and Ollama — no audio or text ever leaves your machine.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## English Quick Start

### 1. Run Immediately (Zero Pre-Installed Python Required)

- **Download Latest Release**: [`steno.com`](https://github.com/e12e/steno/releases/latest/download/steno.com) • [`steno.py`](https://github.com/e12e/steno/releases/latest/download/steno.py) • [`SHA256SUMS.txt`](https://github.com/e12e/steno/releases/latest/download/SHA256SUMS.txt) • [Release Notes](https://github.com/e12e/steno/releases/latest)
- **One-line installer**:
  - **macOS / Linux / Termux**: `curl -LsSf https://hypertekst.net/steno/install.sh | sh`
  - **Windows (PowerShell)**: `irm https://hypertekst.net/steno/install.ps1 | iex`
- **Universal single-file executable (`steno.com` / `steno.py`, build locally with `make build`)**:
  - **macOS & Linux (`amd64` / `arm64`)**: `chmod +x steno.com && ./steno.com` (GUI) or `./steno.com meeting.m4a --profile auto` (CLI).
  - **Windows 10/11 (`amd64` / `arm64`)**: Double-click `steno.com`, or run `.\steno.com --ui-lang en --help`.
  - **ChromeOS (Crostini) & Headless Linux**: Automatically starts a local authenticated browser GUI on `127.0.0.1` when GTK/Qt WebKit is absent.
  - **Android Termux (`aarch64-linux-android`)**: Native `$PREFIX/bin/sh` + `pkg` (`ffmpeg`, `ollama`) CLI support.
- **From source checkout**:
  - `python3 steno.py` (GUI) or `python3 transcribe.py --help` (CLI).
  - Build standalone `dist/steno.com`, `dist/steno.py`, and `dist/SHA256SUMS.txt` with `make build`.
  - Install desktop/CLI shortcuts anytime via `./dist/steno.com --install` (or *File → Install Desktop Shortcut*); cleanly remove shortcuts and cached runtimes/models via `--uninstall` (or *File → Uninstall Steno…*).

### 2. Core Features

- **Decoupled UI Locale & Speech Language**: Run the UI in English or Norwegian while transcribing speech in Norwegian (`no`), English (`en`), auto-detected language (`auto` — with one-click switch confirmation and per-language saved model defaults), or any Whisper ISO 639-1 code (`sv`, `da`, `de`, `es`, `fr`, ...).
- **4-Scene Guided Storyboard & Native OS Menu Bar**: Step smoothly through **01 Setup & Source** (*Record*, *Existing File / Resume Session*, *Stream / URL*), **02 Audio Studio** (16 kHz mic recording, 4s mic + live STT check, in-app playback), **03 Context & Agenda** (local extraction from `.pptx` incl. speaker notes, `.pdf`, `.docx`, `.odp`, `.odt`, or URLs), and **04 Pipeline & Run** with resumable atomic checkpoints in `.mote_checkpoints/<stem>/`.
- **Sequential Memory Discipline**: WhisperX/Pyannote stages execute in an isolated subprocess that releases all RAM/VRAM back to the OS before Ollama starts Stages 4–5, allowing 8–16 GB laptops to run without OOM swapping.

---

## Norsk dokumentasjon (Oversikt og bruk)

Helautomatisk, **100 % lokal og skyfri** løsning for transkripsjon av møter og foredrag, taler-identifisering (*diarization*) og generering av strukturerte referater eller forelesningsnotater.

### 1. Oversikt over app og verktøy

| Fil | Beskrivelse og bruksområde |
| :--- | :--- |
| **`dist/steno.com`** / **`dist/steno.py`** / **`steno.py`** *(GUI + CLI)* | Skrivebordsapp med:<br>1. **Direkte mikrofonopptak, 4s mikrofontest med STT & sanntids-dBFS**: Tar opp `16 kHz` mono WAV, viser løpende dBFS-nivå (`Godt`, `Lavt`, `Stille`, `Høyt/Klipp`), forhåndsvarmer Whisper under 4s mikrofontest for umiddelbar STT-tilbakemelding, og lar deg lytte til valgt lydfil direkte i appen.<br>2. **Frakoblet grensesnittspråk og talespråk**: Kjør menyer på norsk eller engelsk uavhengig av talespråket i opptaket, med automatisk språkgjenkjenning + bekreftelsesbanner og lagrede modellvalg per språk.<br>3. **Møte- og foredragsmodus (`Møte` / `Forelesning`)**: Tilpasser både opptaksguide (valgfri, ingen autoplay) og sluttnotater mellom flerparts møtereferat (med beslutninger og aksjonstabell) og 1-talers foredragsnotater.<br>4. **Krasj-sikre sjekkpunkter (`Steg 1–5`) og gjenoppta økt**: Lagrer atomiske sjekkpunkter i `.mote_checkpoints/<stem>/` slik at økter kan pauses og gjenopptas under *Eksisterende fil / Gjenoppta økt*.<br>5. **Start fra nettstrøm/URL eller eksisterende transkripsjon**: Hent lyd direkte fra medie-URL (`.opus`, `.mp3`, `.m3u8`) eller slipp inn en ferdig transkripsjon (`.html`, `.json`, `.srt`, `.vtt`, `.txt`, `.md`).<br>6. **Kontekst fra lysbilder og notater**: Trekker lokalt ut tekst og foredragsnotater fra `.pptx`, `.pdf`, `.odp`, `.docx`, `.odt`, `.md` eller lenker (`Google Docs/Slides`, nettsider) til Whispers ordliste og Ollama. |
| **`transcribe.py`** *(CLI-verktøy)* | Kommandolinjegrensesnitt for alle 5 steg, maskinvaredeteksjon (`--profile auto`, `--check-hw`), språkvalg (`-l no`/`en`/`auto`), sjekkpunkter (`--task`, `--status`), kontekstfiler (`-c`), manuelle notater (`-M`) og sanntids lydsjekk (`--live-check`). |
| **`tests/e2e_benchmark.py`** *(Benchmark & E2E-test)* | Evaluerer syntetiske fler-talers møter i `synthetic_test/meetings/` på tvers av ASR- og LLM-modeller. |

---

## 2. Maskinvarekrav, Profilvalg og Ressursbruk

Appen har **innebygd maskinvaredeteksjon (`detect_hardware_resources`)** som sjekker tilgjengelig RAM, CPU-arkitektur, GPU VRAM og ledig diskplass:
- **Automatisk profilvalg (`--profile auto`)**: Velger automatisk beste kombinasjon av ASR-, Alignment- og Ollama-modell for maskinen og valgt talespråk.
- **Sanntids ressursadvarsel**: Viser tydelig advarsel med én-klikks *«Bruk anbefalt»* hvis valgt modelloppsett overstiger tilgjengelig RAM/disk.
- **Sekvensiell kjøring**: WhisperX-prosessen avsluttes og frigjør alt minne før Ollama starter Steg 4–5.

| Nivå / Profil | Systemminne (RAM) | Prosessor / GPU | Ledig diskplass | Anbefalt norsk modelloppsett | Topp-RAM (Sekvensielt) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Minimum** (`light`) | **8 GB RAM** | Apple Silicon / AVX2 `x86_64` / ARM64 | **8–10 GB** | `NbAiLab/nb-whisper-small` + `nb-wav2vec2-300m-bokmaal` + `llama3.2:latest` | **~3,8 GB RAM** |
| **Anbefalt** (`recommended` / `auto`) | **12–16 GB+ RAM** | Apple Silicon (`M1–M4`) / NVIDIA RTX / 8-kjerners CPU | **12–15 GB** | `NbAiLab/nb-whisper-small` + `nb-wav2vec2-300m-bokmaal` + `gemma4:e4b` | **~6,5 GB RAM** |
| **Høyere presisjon / Flerspråklig** | **16 GB+ RAM** | Apple Silicon / NVIDIA RTX | **15–18 GB** | Engelsk/flerspråklig: `large-v3-turbo` (`small` i `light`) + `gemma4:e4b` | **~6,5 GB RAM** |

---

## 3. Empirisk modellvalg, Opphav og Lisenser

Modellene er evaluert på 4 syntetiske møter i [`synthetic_test/BENCHMARK_RAPPORT.md`](synthetic_test/BENCHMARK_RAPPORT.md):

- **ASR / Alignment**: [`NbAiLab/nb-whisper-small`](https://huggingface.co/NbAiLab/nb-whisper-small), [`NbAiLab/nb-whisper-medium`](https://huggingface.co/NbAiLab/nb-whisper-medium), [`NbAiLab/nb-wav2vec2-300m-bokmaal`](https://huggingface.co/NbAiLab/nb-wav2vec2-300m-bokmaal) (Nasjonalbiblioteket, Apache 2.0), [`WhisperX`](https://github.com/m-bain/whisperX) / `faster-whisper` / `large-v3-turbo` (BSD-4-Clause / MIT).
- **Taler-identifisering**: [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1) (MIT / CC-BY-4.0).
- **Lokal LLM via Ollama**: `gemma4:e4b` (Google, Apache 2.0), `llama3.2:latest` (Meta), `ltg/normistral-11b-thinking` (UiO LTG, Apache 2.0).

---

## 4. Lydkvalitet og mikrofonoppsett (Flere talere i samme rom)

Moderne OS har «støyfilter» laget for 1-person videosamtaler som demper deltakere lenger unna mikrofonen:
- **macOS / iOS**: Klikk på det oransje mikrofonsymbolet øverst og sett **Mikrofonmodus = Standard** (ikke *Stemmeisolasjon*).
- **Windows 11**: *Innstillinger → System → Lyd → Mikrofon* → slå av **Lydforbedringer (Audio Enhancements)**.
- **Tips ved opptak på mobil**: Bruk gjerne **Fossify Voice Recorder** (F-Droid, uten internettilgang) eller Apple Taleopptak (slå av iCloud-synk) og overfør lokalt med **[LocalSend](https://localsend.org)** eller USB-kabel.

---

## 5. Sjekkpunkter (`Steg 1–5`) og CLI-eksempler

Sjekkpunkter lagres i `<utdatamappe>/.mote_checkpoints/<filstamme>/` (`1_asr.json`, `2_align.json`, `3_diarize.json`, `4_speakers.json`), og ferdige dokumenter skrives til `<stamme>_transkripsjon.html`, `<stamme>_referat.md` og `<stamme>_referat.html`.

```bash
# Sjekk maskinvare, ledig RAM/disk og anbefalt profil:
python3 transcribe.py --check-hw

# Norsk møteopptak med automatisk maskinvareprofil og 4 deltakere:
python3 transcribe.py opptak/møte.wav --profile auto -n 4 \
  --speaker-names "Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen" \
  -M opptak/manuelle_notater.odt

# Engelsk opptak med automatisk profil og lysbilder:
python3 transcribe.py recording.m4a -l en --profile auto -c slides.pptx

# 1-talers foredragsmodus:
python3 transcribe.py foredrag.opus -n 1 --mode foredrag -c slides.pptx

# Kjør kun Steg 5 (nytt referat) fra eksisterende transkripsjon eller sjekkpunkt:
python3 transcribe.py mote_transkripsjon.html --task only_5_notes --mode mote

# Grov 15s mikrofontest i terminalen:
python3 transcribe.py --live-check --live-seconds 15
```

---

## 6. Utvikling, Testing og Bygging

```bash
# Aktiver lokal pre-push hook (.githooks/pre-push: lint, typecheck, doctest, test):
make hooks

# Kjør alle tester (doctests, unit/BDD-tester, Ruff, Mypy og 15s --live-check E2E):
make check

# Bygg enkeltfil-distribusjoner (dist/steno.py, dist/steno.com, dist/SHA256SUMS.txt) og kjør isolert test:
make build

# Publiser ny GitHub Release (bygger, tester, oppdaterer Latest og sletter eldre utgivelser/tagger):
git tag v0.3.1 && git push github v0.3.1
```

---

## 7. Lisens / License

Dette prosjektet er lisensiert under **Apache License, Version 2.0** — se [`LICENSE`](LICENSE).
