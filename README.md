# Steno (`steno.py` og `transcribe.py`)

**Steno - a modern annalist for your meetings - an automaton stenographer.**

Helautomatisk, **100 % lokal og skyfri** løsning for transkripsjon av norske møter, taler-identifisering (hvem sa hva) og generering av strukturerte møtereferater. All lydbehandling, talegjenkjenning og tekstgenerering kjører utelukkende på din egen maskin. Ingen lydfiler eller tekster sendes til eksterne skytjenester.

---

## 1. Oversikt over applikasjonene

Prosjektet leveres både som en modulær Python-pakke (`src/mote/`), en selvstendig alt-i-ett-fil (`dist/steno.py` og `www/rec/steno.py` som automatisk installerer nødvendige avhengigheter ved første oppstart), og et kommandolinjeverktøy (`transcribe.py`):

| Fil | Beskrivelse og bruksområde |
| :--- | :--- |
| **`steno.py`** / **`dist/steno.py`** *(GUI + CLI)* | Skrivebordsapp med:<br>1. **Direkte mikrofonopptak med grov sanntids-transkripsjon (lydsjekk)**: Tar opp `16 kHz` mono PCM WAV, viser løpende dBFS-lydnivå hvert sekund (`Godt`, `Lavt`, `Stille`, `Høyt/Klipp`) og grov-transkriberer 5-sekunders utsnitt fortløpende under opptak (eller via knappen `Lydsjekk (20s)` på valgt fil) slik at du ser med én gang om lyden og mikrofonoppsettet er godt nok.<br>2. **Møtestart-guide (talestyrt og visuell)**: Veileder møteleder og deltakere gjennom en kort presentasjonsrunde ved møtestart slik at systemet låser stemme-ID til riktig navn og tittel.<br>3. **Dedikert 1-talers foredragsmodus (`Modus: Foredrag / Presentasjon`)**: Bruker nøyaktig samme transkripsjonsmotor, men skreddersyr oppsummeringen som faglige hovedpoenger (*Key Takeaways*), tematisk gjennomgang og begrepsforklaringer i stedet for vedtak og flerparts aksjonstabell. Kan velges automatisk (ved `1` taler) eller overstyres manuelt før kjøring.<br>4. **Stopp/Pause, krasj-gjenopptak og enkeltoppgaver (`Steg 1–5`)**: Lagrer sjekkpunkt etter hvert fullførte steg (`1_asr.json`, `2_align.json`, `3_diarize.json`, `4_speakers.json`) i `.mote_checkpoints/`, slik at du aldri mister arbeid og kan kjøre enkeltsteg på nytt.<br>5. **Start direkte fra transkripsjon**: Slipp inn en ferdig transkripsjon (`.html`, `.json`, `.txt`, `.md`, `.srt`, `.vtt`) for å hoppe over lydprosessering og gå rett til navnekobling eller nytt referat/foredragsnotat.<br>6. **Kontekst fra agenda, lysbilder og manuelle møtenotater**: Henter tekst og forelesernotater direkte fra PowerPoint (`.pptx`), PDF (`.pdf`), OpenDocument (`.odp`/`.odt`), Word (`.docx`) og Markdown/tekst (`.md`/`.txt`) for bedre fagbegreper i Whisper og mer treffsikkert referat. |
| **`transcribe.py`** *(CLI-verktøy)* | Kommandolinjegrensesnitt med støtte for alle 5 steg, maskinvaredeteksjon (`--profile auto`, `--check-hw`), sjekkpunkter (`--task`, `--status`), kontekstfiler (`-c`), manuelle møtenotater (`-M`) og grov sanntids-lydsjekk (`--live-check`). |
| **`tests/e2e_benchmark.py`** *(Benchmark & E2E-test)* | Genererer og evaluerer 4 syntetiske norske møteopptak komprimert med Opus i `synthetic_test/meetings/` på tvers av ASR- og LLM-modeller. |

### Oppstart (Windows, macOS og Linux)
Appen har en innebygd kryssplattform-oppstarter (`uv` med Python 3.12) som automatisk henter og setter opp Python-miljø, `ffmpeg`, `whisperx` og `ollama` lokalt ved behov:
- **macOS / Linux**: Kjør `./steno.py` (eller `./dist/steno.py`) i terminalen.
- **Windows**: Kjør `python steno.py` (eller gi filen en `.cmd`-snarvei og dobbeltklikk).

---

## 2. Maskinvarekrav, Automatisk Profilvalg og Ressursbruk

Appen har **innebygd maskinvaredeteksjon (`detect_hardware_resources`)** som ved oppstart sjekker tilgjengelig RAM, CPU-arkitektur/kjerner, eventuell NVIDIA GPU VRAM og ledig diskplass:
- **Automatisk profilvalg (`★ Anbefalt (Auto ut fra PC/Mac)` / `--profile auto`)**: Velger automatisk beste kombinasjon av ASR-, Alignment- og Ollama-modell for maskinen din.
- **Sanntids ressursadvarsel**: Hvis maskinen har for lite RAM (< 8 GB), lite ledig diskplass, eller du manuelt velger en modellkombinasjon som overstiger tilgjengelig minne, viser appen en tydelig **oransje/rød ressursadvarsel** med ett-klikks knapp (**«Bruk anbefalt»**) for å bytte til en trygg profil.

Fordi **alle 5 steg kjører sekvensielt** og frigjør minne mellom hvert steg (`del model; gc.collect()`, og WhisperX-underprosessen avsluttes helt før Ollama starter i Steg 4–5), ligger **aldri** talegjenkjenning og språkmodell i minnet samtidig.

### 2.1 Maskinvaretabell – Hva kreves for å kjøre lokalt?

| Nivå / Profil | Systemminne (RAM / Unified Memory) | Prosessor / GPU | Ledig diskplass | Anbefalt modelloppsett (Testvinnere) | Topp-RAM (Sekvensielt) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Absolutt minimum**<br>*(Profil: `light`)* | **8 GB RAM** *(lukk nettleser/tunge apper under kjøring)* | Apple Silicon (`M1`+) eller moderne `x86_64` CPU (min. 4 kjerner / AVX2) | **8–10 GB** | ASR: `NbAiLab/nb-whisper-small`<br>Align: `nb-wav2vec2-300m-bokmaal`<br>LLM: `llama3.2:latest` *(3,2B)* | **~3,8 GB RAM** |
| **Anbefalt standard**<br>*(Profil: `recommended` / `auto`)* | **12–16 GB+ RAM** *(eller 8 GB+ NVIDIA VRAM)* | Apple Silicon (`M1/M2/M3/M4`) eller PC med NVIDIA RTX / rask 8-kjerners CPU | **12–15 GB** | ASR: `NbAiLab/nb-whisper-small` *(Testvinner #1)*<br>Align: `nb-wav2vec2-300m-bokmaal`<br>LLM: `gemma4:e4b` *(Testvinner #1)* | **~6,5 GB RAM** |
| **Alternativ #2**<br>*(Profil: `fast`)* | **16 GB+ RAM** | Apple Silicon (`M1–M4`) eller NVIDIA RTX | **15–18 GB** | ASR: `UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2`<br>Align: `nb-wav2vec2-1b-bokmaal-v2`<br>LLM: `gemma4:e4b` | **~6,5 GB RAM** |

> [!WARNING]
> **Enheter som IKKE støttes eller ikke anbefales:**
> - **Maskiner med 4 GB RAM** (f.eks. eldre kontor-PCer eller Raspberry Pi 4): Vil gå tom for minne (OOM / swap-lås). Appen viser kritisk advarsel hvis `< 7,5 GB RAM` oppdages.
> - **Eldre CPU-er uten AVX2-instruksjonssett** (Intel før ~2014 / Haswell) eller **32-bit operativsystemer**: Støttes ikke av `PyTorch`, `CTranslate2` eller `Ollama`.
> - **Chromebooks / nettbrett (iPad / Android)**: Appen krever fullt skrivebords-OS (**macOS 12+**, **Windows 10/11 64-bit** eller **Linux x86_64/ARM64**). På mobil/nettbrett bør man kun *ta opp* lyden og overføre filen til PC/Mac.

### 2.2 Minne- og diskforbruk per steg (Anbefalt Testvinner-oppsett)

| Steg | Aktiv komponent | Diskplass (nedlastet modell) | Topp-RAM under kjøring | Merknad |
| :--- | :--- | :--- | :--- | :--- |
| **Grunnmiljø** | Python 3.12 (`uv`), PyTorch, WhisperX, `ffmpeg`, Ollama | ~3,5 GB | ~0,4 GB (hvile) | Installeres automatisk i `~/.local/share/mote_transcribe` |
| **Steg 1: ASR** | `NbAiLab/nb-whisper-small` (`CTranslate2 int8`) | ~0,9 GB | **~1,2 GB** | 2,7x raskere og mer presis enn `nb-whisper-large` i E2E-test |
| **Steg 2: Align** | `NbAiLab/nb-wav2vec2-300m-bokmaal` (PyTorch FP32) | ~1,2 GB | **~1,5 GB** | Velges automatisk ved `small`-ASR (kutter RAM-toppen med ~70 %) |
| **Steg 3: Pyannote** | `pyannote/speaker-diarization-community-1` | ~0,3 GB | **~1,8–2,5 GB** | Hele Python-prosessen avsluttes etter Steg 3 |
| **Steg 4–5: LLM** | `gemma4:e4b` (`num_ctx=16384` i Ollama) | ~5,3 GB | **~5,5–6,5 GB** | 100 % treff på taler-ID og 96,7 % referatkvalitet i E2E-benchmark |

---

## 3. Valg av språkmodeller, Benchmark-vinnere, Opphav og Lisenser

Alle modeller i appen er evaluert og rangert empirisk på **4 syntetiske norske møter** (se [BENCHMARK_RAPPORT.md](synthetic_test/BENCHMARK_RAPPORT.md)). Tyngre modeller som presterte dårligere eller vesentlig tregere uten kvalitetsgevinst (`NbAiLab/nb-whisper-large`, `large-v3-turbo`, `devstral:24b`, `qwen3:4b`) er fjernet fra standardvalgene til fordel for mindre, raskere og mer presise modeller.

### 3.1 Talegjenkjenning (ASR) og Ordtidsjustering (Alignment)

| Modell | Parametere & Disk | Rolle & Benchmark-resultat | Utvikler / Opphav | Lisens | Kilde |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`NbAiLab/nb-whisper-small`** *(Anbefalt Testvinner #1)* | `244M` (~0,9 GB) | **Testvinner #1 (`83,47 %` ordnøyaktighet, `0,50x` RTF).** 2,7x raskere enn Large, bruker 80 % mindre diskplass og unngår repetisjonshallusinasjoner. | **Nasjonalbiblioteket (NB AI-Lab)** | **Apache 2.0** | [HF: NbAiLab/nb-whisper-small](https://huggingface.co/NbAiLab/nb-whisper-small) |
| **`UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2`** *(Alternativ #2)* | `756M` (~1,4 GB) | **Testvinner #2 (`67,68 %` ordnøyaktighet, `0,90x` RTF).** Destillert 4-lags dekoder fra `NbAiLab/nb-whisper-large-distil-turbo-beta`. | **NB AI-Lab** *(CT2 av Ulrik Koren)* | **Apache 2.0** | [HF: nb-whisper-large-distil-turbo-beta](https://huggingface.co/NbAiLab/nb-whisper-large-distil-turbo-beta) |
| **`NbAiLab/nb-whisper-medium`** | `769M` (~1,5 GB) | Mellommodell for norsk bokmål, nynorsk og dialekter. | **Nasjonalbiblioteket (NB AI-Lab)** | **Apache 2.0** | [HF: NbAiLab/nb-whisper-medium](https://huggingface.co/NbAiLab/nb-whisper-medium) |
| **`NbAiLab/nb-wav2vec2-300m-bokmaal`** *(Standard ved Small)*<br>`nb-wav2vec2-1b-bokmaal-v2` | `300M` (~1,2 GB)<br>`1B` (~3,6 GB) | Millisekund-presis ordtidsjustering (*Forced Alignment*). `300m` brukes automatisk sammen med `nb-whisper-small`. | **Nasjonalbiblioteket (NB AI-Lab)** | **Apache 2.0** | [HF: NbAiLab/nb-wav2vec2-300m-bokmaal](https://huggingface.co/NbAiLab/nb-wav2vec2-300m-bokmaal) |

### 3.2 Taler-identifisering (Speaker Diarization)

| Modell / Bibliotek | Rolle | Utvikler / Opphav | Lisens | Kilde |
| :--- | :--- | :--- | :--- | :--- |
| **`pyannote/speaker-diarization-community-1`** / **`3.1`** | Skiller ulike stemmer fra hverandre og grupperer hvem som snakker når (bruker nevrale stemme-fingeravtrykk fra *WeSpeaker ResNet34*) | **Hervé Bredin (CNRS, Frankrike)** | **MIT / CC-BY-4.0** | [HF: pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) |
| **`WhisperX`** *(med `faster-whisper` / `CTranslate2`)* | Kobler sammen NB-Whisper, Wav2Vec2 og Pyannote slik at hvert enkelt ord får tildelt taler-ID (`SPEAKER_00`, `SPEAKER_01`, osv.) | **Max Bain m.fl. (University of Oxford / VGG)** og **OpenNMT** | **BSD-4-Clause** / **MIT** | [GitHub: m-bain/whisperX](https://github.com/m-bain/whisperX) |

### 3.3 Lokale språkmodeller (LLM via Ollama) for navnegjenkjenning og møtereferat

| Ollama-modell | Størrelse (Disk / RAM) | Benchmark-resultat og bruksområde | Utvikler / Opphav | Lisens |
| :--- | :--- | :--- | :--- | :--- |
| **`gemma4:e4b`** *(Anbefalt Testvinner #1)* | ~5,3 GB disk<br>~6,5 GB RAM | **Testvinner #1 (`100 %` taler-ID på alle 4 møter, `96,65 %` referatkvalitet, ~59s/møte).** Effektiv 4,5B-arkitektur med suveren norsk språkforståelse. | **Google DeepMind** | **Apache 2.0** |
| **`llama3.2:latest`** *(Lettvekt / 8 GB RAM)* | ~2,0 GB disk<br>~3,5 GB RAM | **Lettvektsvinner (`93,31 %` referatkvalitet, kun ~31s/møte).** Brukes automatisk i `light`-profilen på maskiner med 8 GB RAM. | **Meta AI** | **Llama 3.2 License** |
| **`ltg/normistral-11b-thinking`** *(Valgfri Norsk 11B)* | ~6,7 GB disk<br>~9 GB RAM | Spesialtrent norsk modell fra **Universitetet i Oslo (LTG)** for maskiner med 16–32 GB+ RAM. | **UiO (LTG)** | **Apache 2.0** |

---

## 3. Arbeidsflyt: Slik gjennomfører du et møteopptak

Du kan enten ta opp lyden **direkte i `steno.py`** på PC/Mac, eller **ta opp på mobiltelefon** og overføre lydfilen lokalt etter møtet.

### 3.1 Gullregelen ved møtestart (for presis taler-gjenkjenning)
Uansett om du tar opp i appen eller på mobil, bør de første 30 sekundene av opptaket følge denne faste rutinen:
1. **Møteleder starter opptaket** og sier tydelig hva møtet gjelder og dagens dato (f.eks.: *«Velkommen til statusmøte for Sikkerhetsplattformen, 29. oktober. Jeg heter Kari Nordmann og er prosjektleder.»*).
2. **Runde rundt bordet**: Hver deltaker sier minst én hel setning med eget navn og tittel/rolle (f.eks.: *«Hei, jeg heter Ola Hansen og er systemutvikler på prosjektet.»*).
   - *Hvorfor?* Pyannote trenger ca. 3–5 sekunder sammenhengende tale i rolige omgivelser fra hver person for å etablere et tydelig akustisk stemme-fingeravtrykk, og den lokale språkmodellen bruker denne runden til å koble stemme-ID til riktig navn i hele resten av møtet.
3. **Lås antall talere i appen**: Hvis dere er 4 personer i rommet, sett både **Min talere = 4** og **Maks talere = 4** i appen før du trykker *Start*.

---

### 3.2 Alternativ A: Opptak direkte i `steno.py`
1. Fyll gjerne inn **Møtetittel & Dato**, **Tilstede (Navn og tittel)** og **Agenda / Lysbilder** før møtet starter. Når du fyller inn deltakere (kommaseparert), oppdaterer appen automatisk *Min/Maks talere*.
2. Klikk **Spill inn møte** øverst i appen.
3. Hvis **«Vis og les opp navnerunde-guide ved opptaksstart»** er krysset av, leser maskinen opp en kort instruks før dere tar runden rundt bordet.
4. Når møtet er ferdig, klikk **Stopp opptak**. Lydfilen lagres automatisk som `16 kHz` mono WAV og legges klar i appen. Klikk **Start / Fortsett**.

---

### 3.4 Sjekkpunkter, Stopp/Pause, Krasj-gjenopptak og Enkeltoppgaver (Steg 1–5)

Kjøringen er delt inn i 5 atomiske steg som lagres fortløpende i `<utdatamappe>/.mote_checkpoints/<filstamme>/`:

| Steg | Sjekkpunktfil | Hva som gjøres |
| :--- | :--- | :--- |
| **1. ASR** | `1_asr.json` | Grov-transkripsjon av tale med `NbAiLab/nb-whisper-small` (inkl. faguttrykk fra agenda/lysbilder). |
| **2. Align** | `2_align.json` | Millisekund-presis ordtidsjustering (*Forced Alignment*) med `nb-wav2vec2-300m-bokmaal` (eller `1b` for større modeller). |
| **3. Pyannote** | `3_diarize.json` | Taler-identifisering (*Speaker Diarization*) med sanntids fremdriftsprosent (`0–100 %`). Ved `1` taler hoppes Pyannote over automatisk. |
| **4. Navn & HTML** | `4_speakers.json` + `_transkripsjon.html` | Kobler `SPEAKER_00`..`XX` til faktiske navn/roller via Ollama og bygger søkbar HTML-transkripsjon. |
| **5. Referat/Notat** | `_referat.md` + `_referat.html` | Genererer møtereferat eller foredragsnotater med Ollama (`gemma4:e4b`). |

- **Stopp / Pause og Fortsett**: Du kan når som helst trykke **Stopp / Pause**. Alle fullførte steg er allerede lagret på disk. Når du trykker **Start / Fortsett** (med standardvalget *Fortsett fra sjekkpunkt*), fortsetter appen automatisk på neste uferdige steg.
- **Gjenopptak etter krasj eller flyttet mappe**: Hvis maskinen går tom for strøm, prosessen avbrytes eller prosjektmappen flyttes underveis, gjenkjenner appen eksisterende sjekkpunkter i `.mote_checkpoints/` og fullfører kun de gjenværende stegene.
- **Manuell kjøring av enkeltoppgaver**: Klikk direkte på en av de 5 steg-knappene (`1. ASR`, `2. Align`, `3. Pyannote`, `4. Navn & HTML`, `5. Referat/Notat`) eller velg i nedtrekksmenyen for å kjøre kun ett bestemt steg eller fra et valgt steg og ut (f.eks. *Kun Steg 5: Generer Referat / Notater på nytt* hvis du vil bytte mellom Møtereferat og Foredragsmodus eller legge til lysbilder uten å transkribere lyden på nytt).

### 3.5 Starte direkte fra en eksisterende transkripsjon
Du trenger ikke alltid starte fra en lydfil:
- Dra og slipp (eller velg med **Velg fil...**) en eksisterende transkripsjonsfil i formatene **`.html`**, **`.json`**, **`.txt`**, **`.md`**, **`.srt`** eller **`.vtt`**.
- Appen oppdager automatisk at inndatafilen er en transkripsjon, hopper over lydstegene (Steg 1–3), og lar deg kjøre **Steg 4 (Navnekobling & HTML)** og/eller **Steg 5 (Referat / Foredragsnotater)** direkte.

### 3.6 Ekstra kontekst fra Agenda, Lysbilder (`.pptx` / `.pdf` / `.odp`) og Dokumenter (`.docx` / `.md`)
Klikk **«+ Agenda / Lysbilder...»** (eller dra en `.pptx`-, `.pdf`-, `.odp`-, `.docx`- eller `.odt`-fil rett inn i vinduet):
- **PowerPoint (`.pptx`)**: Henter automatisk ut både lysbildetekst (`[Lysbilde 1..N]`) og foreleserens egne talenotater (*Speaker Notes*) direkte fra pakken, helt uten eksterne skytjenester.
- **PDF (`.pdf`), Word (`.docx`) og OpenDocument (`.odp`/`.odt`)**: Henter ut tekstinnholdet lokalt.
- Konteksten brukes tre steder:
  1. **Whisper `--initial_prompt` (Steg 1)**: Gir talegjenkjenningen faguttrykk, egennavn, forkortelser og produktnavn slik at de staves riktig i selve transkripsjonen.
  2. **Navne- og rollegjenkjenning (Steg 4)**: Hjelper språkmodellen å koble riktig person og tittel til riktig stemme.
  3. **Referat / Foredragsnotater (Steg 5)**: Gir riktig struktur, agendapunkter og faglig kontekst i sluttnotatet.

### 3.7 Foredragsmodus vs. Møtereferat – og samtidig opptak/transkribering
- **Modusvalg (`Auto`, `Møtereferat`, `Foredrag / Presentasjon`)**:
  - Et møte med bare 1 deltaker er i praksis et foredrag eller en presentasjon. Velger du `1` taler (eller setter **Modus: Foredrag / Presentasjon**), bruker appen nøyaktig samme transkripsjonsmotor (og hopper over unødvendig Pyannote-separasjon ved 1 taler), men skreddersyr sluttnotatet med *Oversikt og hovedbudskap*, *Sentrale innsikter (Key Takeaways)*, *Tematisk gjennomgang*, *Viktige begreper/modeller* og *Anbefalinger* i stedet for flerparts aksjonstabell.
  - Du kan når som helst overstyre modusen før eller etter transkripsjon og kjøre *Kun Steg 5* på nytt.
- **Kan opptak og transkribering kjøre samtidig?**
  - **Under samme møte**: `steno.py` tar opp lyden med svært lav CPU-bruk (`ffmpeg` PCM WAV) og kjører WhisperX + Pyannote etter at opptaket stoppes, fordi både ordtidsjustering (Steg 2) og global taler-klynging i Pyannote (Steg 3) trenger hele lydfilen for å sammenligne stemmer på tvers av hele møtet. I tillegg unngår man at høy CPU/GPU-last og viftestøy fra Whisper under selve møtet forstyrrer mikrofonopptaket.
  - **Opptak av nytt møte mens en tidligere fil transkriberes**: Opptakskortet (`Spill inn møte`) og transkripsjonsjobben bruker uavhengige prosesser, så du kan starte et nytt mikrofonopptak selv om en bakgrunnsjobb på en tidligere fil pågår (forutsatt at maskinen ikke har hørbar viftestøy).

---

### 3.3 Alternativ B: Opptak på mobiltelefon (Android og iPhone)

En mobiltelefon plassert midt på møtebordet i **Flymodus** gir ofte bedre og jevnere lyd fra alle rundt bordet enn en bærbar PC som står i den ene enden av bordet.

#### Anbefalt app for Android (100 % FOSS – Fri og åpen kildekode)
Vi anbefaler **Fossify Voice Recorder**. Den er fullstendig åpen kildekode (**GPL-3.0**), inneholder ingen reklame eller sporing, og **krever ikke internettilgang** (`INTERNET`-tillatelsen finnes ikke i appen, så den *kan* ikke sende opptak til skyen).

- **F-Droid (anbefalt FOSS-katalog)**: [Fossify Voice Recorder på F-Droid](https://f-droid.org/packages/org.fossify.voicerecorder/)
- **Google Play Store**: [Fossify Voice Recorder på Google Play](https://play.google.com/store/apps/details?id=org.fossify.voicerecorder)
- **Kildekode**: [GitHub – FossifyOrg/Voice-Recorder](https://github.com/FossifyOrg/Voice-Recorder)
- **Anbefalt innstilling i appen**: Åpne *Innstillinger* i Fossify Voice Recorder -> sett **Filformat** til **`wav`** (ukomprimert PCM) eller **`m4a`** med høy bitrate (`128 kbps+`).

#### Anbefalt oppsett for iPhone / iOS
På iOS finnes to gode alternativer for lokal lagring uten sky:

1. **Innebygd Apple «Taleopptak» (Voice Memos) – med avslått iCloud**:
   - **App Store-lenke**: [Apple Taleopptak (Voice Memos) på App Store](https://apps.apple.com/no/app/voice-memos/id1069512134)
   - **KRITISK PERSONVERNSTEG (Slå av iCloud-synkronisering)**:
     Standardinnstillingen i iOS laster opp taleopptak til iCloud. Slik slår du det helt av:
     1. Åpne **Innstillinger** på iPhone -> trykk på **[Ditt navn]** øverst -> **iCloud**.
     2. Trykk **Vis alle** under *Apper som bruker iCloud*.
     3. Finn **Taleopptak** og sett bryteren til **Av**.
   - **Sett tapsfri lydkvalitet**:
     Gå til **Innstillinger -> Apper -> Taleopptak -> Lydkvalitet** og velg **Tabsfri (Lossless)**.
2. **Åpen kildekode (FOSS) på iOS**:
   - Prosjekter som [Whisperboard (GitHub)](https://github.com/Saik0s/Whisperboard) og [WalkWrite OpenSource (GitHub)](https://github.com/lbacaj/WalkWrite-opensource) lagrer alle lydopptak kun i appens lokale sandkasse på telefonen uten sky-kontoer.

---

## 4. Skyfri overføring fra mobil til PC/Mac (LocalSend)

> [!CAUTION]
> **Send aldri møteopptak via e-post, OneDrive, Google Drive, Dropbox, iCloud Drive, Teams eller Slack.** Selv om du sletter filen etterpå, blir opptaket liggende på eksterne skyservere.

### Anbefalt kryssplattform FOSS-verktøy: LocalSend
**LocalSend** (åpen kildekode under **MIT-lisens**) er det beste alternativet til AirDrop som fungerer **på tvers av alle plattformer** (Android <-> macOS, iPhone <-> Windows/Linux, Android <-> Windows/Linux).
- All overføring skjer direkte mellom enhetene over det lokale Wi-Fi-nettverket (punkt-til-punkt med ende-til-ende TLS-kryptering).
- Krever ingen brukerkonto, ingen skyservere og ingen internettilgang.

**Nedlasting av LocalSend:**
- **Offisiell nettside**: [https://localsend.org](https://localsend.org)
- **Android (F-Droid – FOSS)**: [LocalSend på F-Droid](https://f-droid.org/packages/org.localsend.localsend_app/)
- **Android (Google Play)**: [LocalSend på Google Play](https://play.google.com/store/apps/details?id=org.localsend.localsend_app)
- **iPhone / iPad (App Store)**: [LocalSend på Apple App Store](https://apps.apple.com/no/app/localsend/id1661733229)
- **macOS (Homebrew)**: `brew install --cask localsend`
- **Windows (Winget)**: `winget install LocalSend.LocalSend`
- **Kildekode**: [GitHub – localsend/localsend](https://github.com/localsend/localsend)

### Andre sikre overføringsmåter:
- **AirDrop (kun mellom iPhone og Mac)**: Bruker direkte kryptert punkt-til-punkt Wi-Fi/Bluetooth mellom enhetene uten å lagre filen i iCloud.
- **USB-kabel (100 % nettverksfri)**: Koble telefonen direkte til PC/Mac med kabel og kopier `.wav`/`.m4a`-filen over.

---

## 5. Viktige justeringer av mikrofonoppsett (for flere talere i samme rom)

Moderne operativsystemer har innebygde «støyfilter» som er laget for videosamtaler med én person, men som **ødelegger** opptak av fysiske møter rundt et bord ved å dempe stemmene til de som sitter litt lenger unna mikrofonen.

### 5.1 macOS og iPhone: Slå AV «Stemmeisolasjon» (Voice Isolation)
- Når mikrofonen er aktiv på macOS eller iOS, vises et **oransje mikrofonsymbol** øverst i menylinjen (macOS) eller i Kontrollsenteret (iPhone).
- Klikk på det oransje mikrofonsymbolet og kontroller at **Mikrofonmodus** står på **Standard** – **IKKE** *Stemmeisolasjon (Voice Isolation)*.
- *Hvorfor?* *Stemmeisolasjon* låser seg på den nærmeste/kraftigste stemmen og filtrerer aktivt bort øvrige møtedeltakere rundt bordet som «bakgrunnsstøy».

### 5.2 Windows 11: Slå AV «Lydforbedringer» (Audio Enhancements)
1. Åpne **Innstillinger -> System -> Lyd**, og klikk på mikrofonen du bruker.
2. Bla ned til **Lydforbedringer (Audio Enhancements)** og sett den til **Av** under fysiske møteopptak.
3. Da unngår du at Windows sin innebygde støyport (*noise gate*) kutter starten av setninger når noen i andre enden av rommet begynner å snakke.

### 5.3 Plassering og valg av mikrofon
1. **Midt på bordet på mykt underlag**: Legg mobilen eller bordmikrofonen midt mellom deltakerne, gjerne oppå en notatblokk eller et etui. Det demper vibrasjoner i bordplaten fra tastaturskriving, kaffekopper og fingre.
2. **Unngå skriving på samme bærbare PC som tar opp**: Hvis du bruker den innebygde mikrofonen i en bærbar PC samtidig som du tar notater på tastaturet, vil tasteklikkene overdøve talerne. Bruk da heller ekstern mikrofon eller mobilopptak.
3. **Ekstern mikrofon (valgfritt)**: En rundstrålende (*omnidireksjonell / 360°*) bordmikrofon (f.eks. *Jabra Speak*, *Blue Yeti* satt i rundstrålende modus `◯`, eller en *boundary*-mikrofon) fanger opp alle rundt bordet med lik klangfarge, noe som gjør at Pyannote skiller talerne enda sikrere fra hverandre.

### 5.4 Grov sanntidstranskripsjon som lydsjekk (under opptak og på eksisterende lydfiler)
For å slippe å oppdage etter et timeslangt møte at mikrofonen sto feil eller at *Stemmeisolasjon* filtrerte bort halve bordet, har både GUI-appen (`steno.py`) og kommandolinjen (`transcribe.py --live-check`) innebygd **grov sanntidstranskripsjon med dBFS-lydmåling**:

- **Under opptak i `steno.py`**:
  1. Huk av for **«Grov sanntids-transkripsjon (lydsjekk)»** (på som standard) og trykk **Spill inn møte**.
  2. **Hvert sekund** oppdateres lydnivåindikatoren (`Lyd: -18 dBFS (Godt)`), og **hvert 5. sekund** kjøres en rask grov-transkripsjon av det siste 5-sekunders utsnittet direkte fra den voksende `.wav`-filen:
     `[00:00–00:05 | -18 dBFS (godt)] Hei, jeg heter Kari Nordmann og er prosjektleder.`
  3. **Slik bruker du dette under navnerunden i starten av møtet**:
     - **Godt nivå (`-35 dBFS` til `-1 dBFS`, grønt)**: Talen er tydelig og ordene dukker opp fortløpende i lydsjekk-panelet.
     - **Lavt nivå (`< -35 dBFS`, oransje) eller manglende tekst når noen lenger unna snakker**: Enten står macOS/iPhone **Stemmeisolasjon** (*Voice Isolation*) / Windows **Lydforbedringer** på og stenger ute rommet, eller mikrofonen står for langt unna.
     - **Stille (`< -60 dBFS`, grått)**: Feil mikrofonkilde er valgt i operativsystemet, eller mikrofonen er dempet (mute).
     - **Høyt nivå / klipping (`> -0.6 dBFS`, rødt)**: Mikrofonen overstyrer; flytt den litt lenger unna nærmeste taler.
- **Hurtigsjekk av ferdige lydfiler (`Lydsjekk (20s)` / `--live-check`)**:
  - Har du overført et opptak fra mobilen, kan du klikke **Lydsjekk (20s)** i `steno.py` (eller kjøre `python3 transcribe.py opptak.wav --live-check`) for å sjekke lydnivå og tekstgjenkjenning på de første 20 sekundene før du starter full 5-stegs kjøring.
- *Merk*: Sanntidstranskripsjonen under opptak bruker rask grådig dekoding (`beam_size=1`) på 5-sekunders biter uten taler-separasjon (Pyannote) og er kun ment som **lyd- og mikrofonsjekk**. Når du trykker **Stopp opptak** og **Start / Fortsett**, kjøres full 5-stegs høykvalitets-pipeline (full beam-search, ord-tidsjustering, Pyannote taler-ID og Ollama-referat) på hele opptaket.

---

## 6. Slik slår du sammen del-opptak (`.wav`)
Hvis et langt møte er delt opp i flere `.wav`-filer (f.eks. `part_00.wav`, `part_01.wav`, `part_02.wav`), må de **ikke** slås sammen med `cat` (da WAV-headeren i første fil angir kun lengden på første del). Bruk `ffmpeg`:

```bash
ffmpeg -i part_00.wav -i part_01.wav -i part_02.wav \
  -filter_complex "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]" \
  -map "[out]" samlet_mote.wav
```

---

## 7. Syntetiske E2E-tester og Modell-Benchmark (`synthetic_test/` og `tests/e2e_benchmark.py`)

Prosjektet har en komplett ende-til-ende benchmark-suite med **4 ulike syntetiske norske møter** i [`synthetic_test/meetings/`](synthetic_test/meetings/), hvert med generert fler-talers lyd (`.opus`), saksliste (`fasit_agenda.md`), fasit-transkripsjon (`fasit_transkripsjon.json`), fasit-referat (`fasit_referat.md`) og evalueringsskjema (`fasit_evaluering.json`):

1. **`mote_1_sikkerhet`** (4 talere, `117,6 s`): Statusmøte Sikkerhetsplattform 2026 (OAuth2, 2FA, Q4-budsjett).
2. **`mote_2_styremote`** (3 talere, `103,3 s`): Styremøte Tromsø Kultur- og Idrettslag (Q3-regnskap, kontingent, høstseminar).
3. **`mote_3_kommune_helse`** (4 talere, `85,3 s`): Prosjektmøte Velferdsteknologi i Hjemmetjenesten (trygghetsalarmer, ROS, uke 46).
4. **`mote_4_produkt_sprint`** (2 talere, `60,1 s`): Sprintplanlegging Mobilapp Versjon 2.4 (mobilpanel, WCAG 2.1 AA, lansering).

Se fullstendig resultat- og rangeringsrapport i **[`synthetic_test/BENCHMARK_RAPPORT.md`](synthetic_test/BENCHMARK_RAPPORT.md)** og rådata i **[`synthetic_test/benchmark_results.json`](synthetic_test/benchmark_results.json)**.

```bash
# Generer alle 4 syntetiske møter og kjør full ASR x LLM benchmark:
python3 tests/e2e_benchmark.py --action all

# Kjør kun LLM-benchmark (Steg 4–5) mot eksisterende ASR/Pyannote-sjekkpunkter:
python3 tests/e2e_benchmark.py --action llm-only
```

---

## 8. Kommandolinjebruk (CLI) – `transcribe.py` og `steno.py --cli`

Den delte kjernemodulen (`src/mote/core.py`) eksponerer hele 5-stegs-pipelinen med sjekkpunkter og maskinvaredeteksjon direkte for kommandolinjebruk via `transcribe.py` (eller `python3 steno.py --cli ...`):

```bash
# Sjekk maskinvare, ledig RAM/disk og anbefalt modellprofil på maskinen:
python3 transcribe.py --check-hw

# Kjør med automatisk anbefalt modellprofil ut fra maskinvaren (--profile auto):
python3 transcribe.py opptak/møte.wav --profile auto -n 4

# Transkriber møteopptak med 4 talere, oppgitte navn og manuelle møtenotater (-M):
python3 transcribe.py opptak/møte.wav \
  -n 4 \
  --speaker-names "Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen" \
  -M opptak/manuelle_notater.odt

# 1-talers foredragsmodus med lysbilder (.pptx) og agenda (.md) som ekstra kontekst:
python3 transcribe.py foredrag.opus -n 1 --mode foredrag -c slides.pptx -c agenda.md

# Kjør kun Steg 5 (generer nytt referat/notater) fra eksisterende transkripsjon eller sjekkpunkt:
python3 transcribe.py mote_transkripsjon.html --task only_5_notes --mode mote

# Sjekk sjekkpunktstatus (Steg 1–5) for en fil uten å starte kjøring:
python3 transcribe.py opptak/møte.wav --status

# Grov sanntids-lydsjekk (dBFS + 5s grov-transkripsjon) direkte fra mikrofon i 15 sekunder:
python3 transcribe.py --live-check --live-seconds 15

# Grov lydsjekk på de første 20 sekundene av en eksisterende lydfil før full kjøring:
python3 transcribe.py synthetic_test/meetings/mote_1_sikkerhet/mote_1_sikkerhet.opus --live-check --live-seconds 20
```

---

## 9. Testing (Doctests + CI), Linting og Statiske Typer

- **Doctests (`src/mote/core.py`, `src/mote/cli.py` og `transcribe.py`)**: Alle rene hjelpefunksjoner, tidsformatering, transkripsjonsparsing (`.html`, `.srt`, `.vtt`, `.txt`), modusvalg, filnavnsrensing og stegplanlegging har innebygde doctests.
- **CI- og integrasjonstester (`tests/test_cli_and_pipeline.py`)**: Kjører alle doctests automatisk via `unittest` (`load_tests`) samt tester uthenting av kontekst fra `.pptx` (inkludert foredragsnotater) og `.docx`, og sjekkpunkt-rundtur.
- **Linting og statiske typer (`pyproject.toml`, `Makefile`)**: Konfigurert for `ruff` og `mypy`.
- **Hvorfor ikke Cython?**: Over 99,9 % av kjøretiden tilbringes i native C/C++/Rust/Metal-kode (`ffmpeg`, `CTranslate2`/`faster-whisper`, `PyTorch`/`pyannote` og `Ollama`/`llama.cpp`). Python-koden koordinerer kun prosesser, JSON-sjekkpunkter og HTML/Markdown-formatering (<50 ms totalt). Cython ville derfor gitt <0,01 % ytelsesgevinst og samtidig ødelagt enkeltfil-portabiliteten på tvers av macOS, Windows og Linux.

Kjør alle sjekker (doctests, enhetstester, `ruff` og `mypy`) med:

```bash
make check
```

---

## 10. Lisens / License

Dette prosjektet er lisensiert under **Apache License, Version 2.0** — se [`LICENSE`](LICENSE) for fullstendige vilkår.

