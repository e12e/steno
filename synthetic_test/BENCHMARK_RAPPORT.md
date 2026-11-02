# E2E Modell-Benchmark og Evalueringsrapport (4 Syntetiske Møter)

**Testmaskin:** Apple M2 (8 kjerner), 24 GB Unified Memory, macOS  
**Testskript:** [`tests/e2e_benchmark.py`](../tests/e2e_benchmark.py)  
**Rådata (JSON):** [`synthetic_test/benchmark_results.json`](benchmark_results.json)

---

## 1. Oversikt over de 4 syntetiske møtene (`synthetic_test/meetings/`)

Hvert møte har ferdig generert lyd (`.opus`), saksliste (`fasit_agenda.md`), fasit-transkripsjon (`fasit_transkripsjon.json`), fasit-referat (`fasit_referat.md`) og strukturerte evalueringskriterier (`fasit_evaluering.json`):

| Møte-ID | Tittel / Tema | Talere | Replikker | Lydlengde | Mappe |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`mote_1_sikkerhet`** | Statusmøte Sikkerhetsplattform 2026 (OAuth2, 2FA, Q4-budsjett) | 4 | 12 | `117,6 s` | [`meetings/mote_1_sikkerhet/`](meetings/mote_1_sikkerhet/) |
| **`mote_2_styremote`** | Styremøte Tromsø Kultur- og Idrettslag (Q3-regnskap, kontingent, høstseminar) | 3 | 10 | `103,3 s` | [`meetings/mote_2_styremote/`](meetings/mote_2_styremote/) |
| **`mote_3_kommune_helse`** | Prosjektmøte Velferdsteknologi i Hjemmetjenesten (trygghetsalarmer, ROS, uke 46) | 4 | 9 | `85,3 s` | [`meetings/mote_3_kommune_helse/`](meetings/mote_3_kommune_helse/) |
| **`mote_4_produkt_sprint`** | Sprintplanlegging Mobilapp Versjon 2.4 (mobilpanel, WCAG 2.1 AA, lansering) | 2 | 5 | `60,1 s` | [`meetings/mote_4_produkt_sprint/`](meetings/mote_4_produkt_sprint/) |

**Total lydlengde over 4 møter:** `366,3 sekunder` (`6 min 6 sek`).

---

## 2. Fase 1: Talegjenkjenning og Alignment (Steg 1–2)

| Rang | ASR-modell (+ Alignment-modell) | Disk (ASR + Align) | Møte 1 | Møte 2 | Møte 3 | Møte 4 | **Snitt Ord-nøyaktighet** | **Total tid (4 møter)** | **Snitt RTF** | Status / Konklusjon |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **#1** | **`NbAiLab/nb-whisper-small`** *(244M)*<br>+ `nb-wav2vec2-300m-bokmaal` *(300M)* | **2,1 GB** | 78,99 % | **82,70 %** | **92,31 %** | **79,86 %** | **83,47 %** | **179,6 s** | **0,50x** | **TESTVINNER (Standard)** – 2,7x raskere og 17 prosentpoeng høyere nøyaktighet enn `nb-whisper-large`. |
| **#2** | **`UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2`** *(756M)*<br>+ `nb-wav2vec2-1b-bokmaal-v2` *(1B)* | 5,0 GB | 82,10 % | 58,23 % | 59,89 % | 70,50 % | **67,68 %** | **319,7 s** | **0,90x** | **Beholdt som alternativ #2** – Raskere og mer presis enn full `nb-whisper-large`. |
| ~~#3~~ | ~~`NbAiLab/nb-whisper-large`~~ *(1,55B)*<br>+ `nb-wav2vec2-1b-bokmaal-v2` *(1B)* | 9,4 GB | 82,49 % | 76,79 % | 28,02 % | 78,42 % | **66,43 %** | **488,1 s** | **1,34x** | **FJERNET** – 2,7x tregere, 4,5x større på disk, og utsatt for repetisjonsløkker i lange segmenter. |
| ~~#4~~ | ~~`large-v3-turbo`~~ *(809M)*<br>+ `nb-wav2vec2-1b-bokmaal-v2` *(1B)* | 5,1 GB | 67,70 % | 32,07 % | 79,67 % | 79,86 % | **64,83 %** | **309,4 s** | **0,85x** | **FJERNET** – Lavest nøyaktighet på norsk (ikke finjustert på norske dialekter/tall). |

---

## 3. Fase 2: Lokale språkmodeller i Ollama (Steg 4 Navnekobling + Steg 5 Referat)

| Rang | Ollama-modell | Parametere / Disk | **Taler-ID (Navnekobling)** | **Referatkvalitet (Fakta & Struktur)** | **Total LLM-tid (4 møter)** | Status / Konklusjon |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **#1** | **`gemma4:e4b`** | 4,5B eff. (~5,3 GB GGUF / 9,4 GB MLX) | **100,0 %** *(feilfri på alle 4 møter)* | **96,65 %** *(94 % fakta-dekning)* | **236,2 s** *(~59 s per møte)* | **TESTVINNER (Standard)** – Eneste modell med 100 % treff på taler-ID og høyest referatkvalitet. |
| **#2** | **`llama3.2:latest`** | 3,2B (~2,0 GB) | **25,0 %** *(100 % ved 2 talere)* | **93,31 %** *(87 % fakta-dekning)* | **126,6 s** *(~31 s per møte)* | **Beholdt for «Lettvekt / 8 GB RAM»** – Svært rask og lavt minnebruk (~3,5 GB RAM). |
| ~~#3~~ | ~~`qwen3:4b`~~ | 4,0B (~2,5 GB) | 0,0 % | 93,44 % | 1 749,4 s *(~7,3 min/møte)* | **FJERNET** – 7,4x tregere pga. lang intern tenkjede og feilet på JSON-navnekobling. |
| ~~#4~~ | ~~`devstral:latest`~~ | 23,6B (~14,3 GB) | 0,0 % *(tidsavbrudd i Steg 4)* | 94,86 % | 8 108,2 s *(~33 min/møte)* | **FJERNET** – 34x tregere enn `gemma4:e4b` og krever >16 GB RAM uten å gi bedre referat. |

---

## 4. Samlet Rangering av Modellkombinasjoner (Totalscore 0–100)

Totalscoren vekter **Ord-nøyaktighet (30 %)**, **Taler-navnegjenkjenning (25 %)**, **Referatkvalitet (30 %)** og **Hastighet (15 %)**:

| Plass | Totalscore | ASR-modell (Steg 1–2) | LLM-modell (Steg 4–5) | Ord-nøyaktighet | Taler-ID | Referat | Samlet tid (4 møter) | Profil i appen |
| :---: | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **#1** | **79,21** | **`NbAiLab/nb-whisper-small`** | **`gemma4:e4b`** | **83,5 %** | **100,0 %** | **96,7 %** | **415,9 s** | **`recommended` / `auto` (Standard)** |
| **#2** | **74,30** | **`UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2`** | **`gemma4:e4b`** | 67,7 % | **100,0 %** | **96,7 %** | 555,9 s | **`fast` (Alternativ #2)** |
| ~~#3~~ | ~~73,92~~ | ~~`NbAiLab/nb-whisper-large`~~ | `gemma4:e4b` | 66,4 % | 100,0 % | 96,7 % | 724,3 s | *Fjernet (slått av #1 og #2)* |
| ~~#4~~ | ~~73,44~~ | ~~`large-v3-turbo`~~ | `gemma4:e4b` | 64,8 % | 100,0 % | 96,7 % | 545,6 s | *Fjernet (slått av #1 og #2)* |
| **#5** | **64,02** | **`NbAiLab/nb-whisper-small`** | **`llama3.2:latest`** | **83,5 %** | 25,0 % | 93,3 % | **306,3 s** | **`light` (Lettvekt / 8 GB RAM)** |
| #6–16 | 47,48–54,55 | Øvrige kombinasjoner (`qwen3:4b`, `devstral:latest`, `large-v3-turbo`) | – | 64,8–83,5 % | 0–25 % | 93,3–94,9 % | 436–8 596 s | *Forkastet* |
