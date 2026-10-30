# Lokalt Møtereferat & Transkripsjon (`mote_gui.py` og `mote_app.py`)

Helautomatisk, **100 % lokal og skyfri** løsning for transkripsjon av norske møter, taler-identifisering (hvem sa hva) og generering av strukturerte møtereferater. All lydbehandling, talegjenkjenning og tekstgenerering kjører utelukkende på din egen maskin. Ingen lydfiler eller tekster sendes til eksterne skytjenester.

---

## 1. Oversikt over applikasjonene (Gammel vs. Ny App)

Prosjektet inneholder to grafiske skrivebordsapplikasjoner (begge pakket som selvstendige enkeltfiler som automatisk installerer nødvendige avhengigheter ved første oppstart) samt et kommandolinjeverktøy:

| Fil | Beskrivelse og bruksområde |
| :--- | :--- |
| **`mote_gui.py`** *(Original app)* | Klassisk skrivebords-GUI for behandling av **lydfiler** (`.wav`, `.m4a`, `.opus`, `.mp3`, `.flac`, `.ogg`) eller **eksisterende transkripsjonsfiler** (`.html`, `.json`, `.txt`, `.md`, `.srt`, `.vtt`). Støtter antall talere (`1`–`20`), valgfri modus-overstyring (**Møtereferat** vs. **Foredrag / Presentasjon**), **Stopp/Pause og gjenopptak etter krasj** via sjekkpunkter på disk, manuell kjøring av enkeltoppgaver (`Steg 1–5`), innlasting av agenda/lysbilder (`.pptx`, `.pdf`, `.docx`, `.md`, `.txt`), lagring/kopiering av kjørelogg, og eksport til Markdown (`.md`), utskriftsvennlig HTML-referat (`_referat.html`) og fargekodet HTML-transkripsjon (`_transkripsjon.html`). |
| **`mote_app.py`** *(Ny utvidet app)* | Bygger videre på `mote_gui.py` med:<br>1. **Direkte mikrofonopptak med grov sanntids-transkripsjon (lydsjekk)**: Tar opp `16 kHz` mono PCM WAV, viser løpende dBFS-lydnivå hvert sekund (`Godt`, `Lavt`, `Stille`, `Høyt/Klipp`) og grov-transkriberer 5-sekunders utsnitt fortløpende under opptak (eller via knappen `Lydsjekk (20s)` på valgt fil) slik at du ser med én gang om lyden og mikrofonoppsettet er godt nok.<br>2. **Møtestart-guide (talestyrt og visuell)**: Veileder møteleder og deltakere gjennom en kort presentasjonsrunde ved møtestart slik at systemet låser stemme-ID til riktig navn og tittel.<br>3. **Dedikert 1-talers foredragsmodus (`Modus: Foredrag / Presentasjon`)**: Bruker nøyaktig samme transkripsjonsmotor, men skreddersyr oppsummeringen som faglige hovedpoenger (*Key Takeaways*), tematisk gjennomgang og begrepsforklaringer i stedet for vedtak og flerparts aksjonstabell. Kan velges automatisk (ved `1` taler) eller overstyres manuelt før kjøring.<br>4. **Stopp/Pause, krasj-gjenopptak og enkeltoppgaver (`Steg 1–5`)**: Lagrer sjekkpunkt etter hvert fullførte steg (`1_asr.json`, `2_align.json`, `3_diarize.json`, `4_speakers.json`) i `.mote_checkpoints/`, slik at du aldri mister arbeid og kan kjøre enkeltsteg på nytt.<br>5. **Start direkte fra transkripsjon**: Slipp inn en ferdig transkripsjon (`.html`, `.json`, `.txt`, `.md`, `.srt`, `.vtt`) for å hoppe over lydprosessering og gå rett til navnekobling eller nytt referat/foredragsnotat.<br>6. **Kontekst fra agenda, lysbilder og dokumenter**: Henter tekst og forelesernotater direkte fra PowerPoint (`.pptx`), PDF (`.pdf`), OpenDocument (`.odp`/`.odt`), Word (`.docx`) og Markdown/tekst (`.md`/`.txt`) for bedre fagbegreper i Whisper og mer treffsikkert referat. |
| **`transcribe.py`** *(CLI-verktøy)* | Kommandolinjegrensesnitt med støtte for alle 5 steg, sjekkpunkter (`--task`, `--status`), kontekstfiler (`-c`), og grov sanntids-lydsjekk (`--live-check`). |
| **`debug_synthetic_test.py`** *(Testverktøy)* | Genererer og kjører ende-til-ende-test på et syntetisk 4-talers møteopptak komprimert med Opus i `synthetic_test/`. |

### Oppstart (Windows, macOS og Linux)
Begge GUI-filene har en innebygd kryssplattform-oppstarter (`uv` med Python 3.12) som automatisk henter og setter opp Python-miljø, `ffmpeg`, `whisperx` og `ollama` lokalt ved behov:
- **macOS / Linux**: Kjør `./mote_app.py` (eller `./mote_gui.py`) i terminalen.
- **Windows**: Kjør `python mote_app.py` (eller gi filen en `.cmd`-snarvei og dobbeltklikk).

---

## 2. Valg av språkmodeller, opphav og lisenser

Løsningen kombinerer fire spesialiserte lokale AI-modeller i en tretrinns kjede (**Lyd -> Ordtidsjustert transkripsjon -> Taler-separasjon -> Møtereferat**).

### 2.1 Talegjenkjenning (ASR) og Ordtidsjustering (Alignment)

| Modell | Rolle | Utvikler / Opphav | Lisens | Kilde |
| :--- | :--- | :--- | :--- | :--- |
| **`NbAiLab/nb-whisper-large`** *(Standard)*<br>`nb-whisper-medium`<br>`nb-whisper-small` | Norsk tale-til-tekst (bokmål, nynorsk og norske dialekter) | **Nasjonalbiblioteket (NB AI-Lab)** i Norge. Finjustert fra OpenAI Whisper på Nasjonalbibliotekets lydarkiv og Stortingets forhandlinger. | **Apache License 2.0** *(Tillater fri privat, offentlig og kommersiell bruk)* | [Hugging Face: NbAiLab/nb-whisper-large](https://huggingface.co/NbAiLab/nb-whisper-large) |
| **`NbAiLab/nb-wav2vec2-1b-bokmaal`** | Ordtidsjustering (*Forced Alignment*) ned på millisekundnivå for hvert enkelt ord | **Nasjonalbiblioteket (NB AI-Lab)** | **Apache License 2.0** | [Hugging Face: NbAiLab/nb-wav2vec2-1b-bokmaal](https://huggingface.co/NbAiLab/nb-wav2vec2-1b-bokmaal) |

### 2.2 Taler-identifisering (Speaker Diarization)

| Modell / Bibliotek | Rolle | Utvikler / Opphav | Lisens | Kilde |
| :--- | :--- | :--- | :--- | :--- |
| **`pyannote/speaker-diarization-3.1`** & **`pyannote/segmentation-3.0`** | Skiller ulike stemmer fra hverandre og grupperer hvem som snakker når (bruker nevrale stemme-fingeravtrykk fra *WeSpeaker ResNet34*) | **Hervé Bredin (CNRS – Centre national de la recherche scientifique, Frankrike)** | **MIT License** *(Åpen kildekode; krever engangs godkjenning av vilkår på Hugging Face ved første nedlasting)* | [Hugging Face: pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) |
| **`WhisperX`** *(med `faster-whisper` / `CTranslate2`)* | Kobler sammen NB-Whisper, Wav2Vec2 og Pyannote slik at hvert enkelt ord får tildelt taler-ID (`SPEAKER_00`, `SPEAKER_01`, osv.) | **Max Bain m.fl. (University of Oxford / VGG)** og **OpenNMT** | **BSD-4-Clause** (`WhisperX`) / **MIT License** (`faster-whisper`) | [GitHub: m-bain/whisperX](https://github.com/m-bain/whisperX) |

### 2.3 Lokale språkmodeller (LLM via Ollama) for navnegjenkjenning og møtereferat

Språkmodellen kjører lokalt gjennom **Ollama** (MIT-lisens) og utfører to oppgaver:
1. **Automatisk navneoppslag**: Leser møtestart-introduksjonene og agendaen for å oversette anonyme koder (`SPEAKER_00`..`SPEAKER_03`) til faktiske navn og titler (f.eks. *«Kari Nordmann (Prosjektleder)»*).
2. **Møtereferat**: Skriver et strukturert norsk møtereferat med sammendrag, gjennomgang per agendapunkt, beslutninger og aksjonspunkter.

| Ollama-modell | Anbefalt bruk | Utvikler / Opphav | Lisens |
| :--- | :--- | :--- | :--- |
| **`gemma4:e4b`** *(Standard)* / **`gemma4:26b`** | **Anbefalt standard.** Svært god norsk språkforståelse, følger instrukser presist og skiller tenketrinn fra selve referatet. | **Google DeepMind** | **Apache License 2.0** *(Frigitt som full åpen kildekode i 2025/2026)* |
| **`gemma3:12b`** / **`gemma3:4b`** | Godt alternativ på maskiner med 8–16 GB RAM. | **Google DeepMind** | **Gemma Terms of Use** *(Fri lokal bruk med standard bruksbegrensninger)* |
| **`qwen2.5:7b`** / **`qwen2.5:14b`** | Sterk på strukturert JSON-uttrekk og tabeller. | **Alibaba Cloud (Qwen Team)** | **Apache License 2.0** |
| **`llama3.1:8b`** / **`llama3.2:3b`** | Rask på eldre maskinvare, men noe svakere på norsk rettskriving enn Gemma 4. | **Meta AI** | **Llama 3 Community License** |

---

## 3. Arbeidsflyt: Slik gjennomfører du et møteopptak

Du kan enten ta opp lyden **direkte i `mote_app.py`** på PC/Mac, eller **ta opp på mobiltelefon** og overføre lydfilen lokalt etter møtet.

### 3.1 Gullregelen ved møtestart (for presis taler-gjenkjenning)
Uansett om du tar opp i appen eller på mobil, bør de første 30 sekundene av opptaket følge denne faste rutinen:
1. **Møteleder starter opptaket** og sier tydelig hva møtet gjelder og dagens dato (f.eks.: *«Velkommen til statusmøte for Sikkerhetsplattformen, 29. oktober. Jeg heter Kari Nordmann og er prosjektleder.»*).
2. **Runde rundt bordet**: Hver deltaker sier minst én hel setning med eget navn og tittel/rolle (f.eks.: *«Hei, jeg heter Ola Hansen og er systemutvikler på prosjektet.»*).
   - *Hvorfor?* Pyannote trenger ca. 3–5 sekunder sammenhengende tale i rolige omgivelser fra hver person for å etablere et tydelig akustisk stemme-fingeravtrykk, og den lokale språkmodellen bruker denne runden til å koble stemme-ID til riktig navn i hele resten av møtet.
3. **Lås antall talere i appen**: Hvis dere er 4 personer i rommet, sett både **Min talere = 4** og **Maks talere = 4** i appen før du trykker *Start*.

---

### 3.2 Alternativ A: Opptak direkte i `mote_app.py`
1. Fyll gjerne inn **Møtetittel & Dato**, **Tilstede (Navn og tittel)** og **Agenda / Lysbilder** før møtet starter. Når du fyller inn deltakere (kommaseparert), oppdaterer appen automatisk *Min/Maks talere*.
2. Klikk **Spill inn møte** øverst i appen.
3. Hvis **«Vis og les opp navnerunde-guide ved opptaksstart»** er krysset av, leser maskinen opp en kort instruks før dere tar runden rundt bordet.
4. Når møtet er ferdig, klikk **Stopp opptak**. Lydfilen lagres automatisk som `16 kHz` mono WAV og legges klar i appen. Klikk **Start / Fortsett**.

---

### 3.4 Sjekkpunkter, Stopp/Pause, Krasj-gjenopptak og Enkeltoppgaver (Steg 1–5)

Kjøringen er delt inn i 5 atomiske steg som lagres fortløpende i `<utdatamappe>/.mote_checkpoints/<filstamme>/`:

| Steg | Sjekkpunktfil | Hva som gjøres |
| :--- | :--- | :--- |
| **1. ASR** | `1_asr.json` | Grov-transkripsjon av tale med `NbAiLab/nb-whisper-large` (inkl. faguttrykk fra agenda/lysbilder). |
| **2. Align** | `2_align.json` | Millisekund-presis ordtidsjustering (*Forced Alignment*) med `nb-wav2vec2-1b-bokmaal`. |
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
  - **Under samme møte**: `mote_app.py` tar opp lyden med svært lav CPU-bruk (`ffmpeg` PCM WAV) og kjører WhisperX + Pyannote etter at opptaket stoppes, fordi både ordtidsjustering (Steg 2) og global taler-klynging i Pyannote (Steg 3) trenger hele lydfilen for å sammenligne stemmer på tvers av hele møtet. I tillegg unngår man at høy CPU/GPU-last og viftestøy fra Whisper under selve møtet forstyrrer mikrofonopptaket.
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
For å slippe å oppdage etter et timeslangt møte at mikrofonen sto feil eller at *Stemmeisolasjon* filtrerte bort halve bordet, har både GUI-appen (`mote_app.py`) og kommandolinjen (`transcribe.py --live-check`) innebygd **grov sanntidstranskripsjon med dBFS-lydmåling**:

- **Under opptak i `mote_app.py`**:
  1. Huk av for **«Grov sanntids-transkripsjon (lydsjekk)»** (på som standard) og trykk **Spill inn møte**.
  2. **Hvert sekund** oppdateres lydnivåindikatoren (`Lyd: -18 dBFS (Godt)`), og **hvert 5. sekund** kjøres en rask grov-transkripsjon av det siste 5-sekunders utsnittet direkte fra den voksende `.wav`-filen:
     `[00:00–00:05 | -18 dBFS (godt)] Hei, jeg heter Kari Nordmann og er prosjektleder.`
  3. **Slik bruker du dette under navnerunden i starten av møtet**:
     - **Godt nivå (`-35 dBFS` til `-1 dBFS`, grønt)**: Talen er tydelig og ordene dukker opp fortløpende i lydsjekk-panelet.
     - **Lavt nivå (`< -35 dBFS`, oransje) eller manglende tekst når noen lenger unna snakker**: Enten står macOS/iPhone **Stemmeisolasjon** (*Voice Isolation*) / Windows **Lydforbedringer** på og stenger ute rommet, eller mikrofonen står for langt unna.
     - **Stille (`< -60 dBFS`, grått)**: Feil mikrofonkilde er valgt i operativsystemet, eller mikrofonen er dempet (mute).
     - **Høyt nivå / klipping (`> -0.6 dBFS`, rødt)**: Mikrofonen overstyrer; flytt den litt lenger unna nærmeste taler.
- **Hurtigsjekk av ferdige lydfiler (`Lydsjekk (20s)` / `--live-check`)**:
  - Har du overført et opptak fra mobilen, kan du klikke **Lydsjekk (20s)** i `mote_app.py` (eller kjøre `python3 transcribe.py opptak.wav --live-check`) for å sjekke lydnivå og tekstgjenkjenning på de første 20 sekundene før du starter full 5-stegs kjøring.
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

## 7. Syntetisk ende-til-ende-test (`synthetic_test/`)

Mappen `synthetic_test/` inneholder et syntetisk 4-talers møteopptak komprimert med Opus (`syntetisk_mote_4_talere.opus`, ~324 KB, 1 min 57 sek) samt tilhørende fasit-agenda (`fasit_agenda.md`), manus (`fasit_transkripsjon.json`) og ferdiggenererte referat- og transkripsjonsfiler.

Du kan kjøre ende-til-ende-testen med:

```bash
python3 debug_synthetic_test.py --step run
```

For å generere ny syntetisk lyd fra macOS TTS (`say`) komprimert med `libopus` før kjøring:

```bash
python3 debug_synthetic_test.py --step all
```

---

## 8. Kommandolinjebruk (CLI) – `transcribe.py` og `mote_app.py --cli`

Den delte kjernemodulen (`mote_core.py`) eksponerer hele 5-stegs-pipelinen med sjekkpunkter direkte for kommandolinjebruk via `transcribe.py` (eller `python3 mote_app.py --cli ...`):

```bash
# Transkriber møteopptak med 4 talere og oppgitte navn (fortsetter automatisk fra sjekkpunkt ved avbrudd):
python3 transcribe.py data/20261027_møte.wav \
  -n 4 \
  --speaker-names "Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen"

# 1-talers foredragsmodus med lysbilder (.pptx) og agenda (.md) som ekstra kontekst:
python3 transcribe.py foredrag.opus -n 1 --mode foredrag -c slides.pptx -c agenda.md

# Kjør kun Steg 5 (generer nytt referat/notater) fra eksisterende transkripsjon eller sjekkpunkt:
python3 transcribe.py mote_transkripsjon.html --task only_5_notes --mode mote

# Sjekk sjekkpunktstatus (Steg 1–5) for en fil uten å starte kjøring:
python3 transcribe.py data/20261027_møte.wav --status

# Grov sanntids-lydsjekk (dBFS + 5s grov-transkripsjon) direkte fra mikrofon i 15 sekunder:
python3 transcribe.py --live-check --live-seconds 15

# Grov lydsjekk på de første 20 sekundene av en eksisterende lydfil før full kjøring:
python3 transcribe.py synthetic_test/syntetisk_mote_4_talere.opus --live-check --live-seconds 20
```

---

## 9. Testing (Doctests + CI), Linting og Statiske Typer

- **Doctests (`mote_core.py` og `transcribe.py`)**: Alle rene hjelpefunksjoner, tidsformatering, transkripsjonsparsing (`.html`, `.srt`, `.vtt`, `.txt`), modusvalg, filnavnsrensing og stegplanlegging har innebygde doctests.
- **CI- og integrasjonstester (`tests/test_cli_and_pipeline.py`)**: Kjører alle doctests automatisk via `unittest` (`load_tests`) samt tester uthenting av kontekst fra `.pptx` (inkludert foredragsnotater) og `.docx`, og sjekkpunkt-rundtur.
- **Linting og statiske typer (`pyproject.toml`, `Makefile`)**: Konfigurert for `ruff` og `mypy`.
- **Hvorfor ikke Cython?**: Over 99,9 % av kjøretiden tilbringes i native C/C++/Rust/Metal-kode (`ffmpeg`, `CTranslate2`/`faster-whisper`, `PyTorch`/`pyannote` og `Ollama`/`llama.cpp`). Python-koden koordinerer kun prosesser, JSON-sjekkpunkter og HTML/Markdown-formatering (<50 ms totalt). Cython ville derfor gitt <0,01 % ytelsesgevinst og samtidig ødelagt enkeltfil-portabiliteten på tvers av macOS, Windows og Linux.

Kjør alle sjekker (doctests, enhetstester, `ruff` og `mypy`) med:

```bash
make check
```

