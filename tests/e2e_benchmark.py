#!/usr/bin/env python3
"""End-to-end synthetic meeting generator and model benchmark suite for Møtereferat."""

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
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from mote import core as mote_core  # noqa: E402

SYNTH_ROOT = BASE_DIR / "synthetic_test"
MEETINGS_DIR = SYNTH_ROOT / "meetings"

# 4 distinct acoustic voice slots built from macOS Norwegian TTS ('Nora')
# Using vocal-tract length warping (asetrate + aresample + atempo) + formant EQ
VOICE_SLOTS: list[dict[str, str]] = [
    {
        "slot_id": "slot_a_soprano",
        "say_voice": "Nora",
        "say_rate": "182",
        "af_filter": (
            "asetrate=22050*1.05,aresample=16000,atempo=0.9524,"
            "highpass=f=110,treble=g=3:f=3200,loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    {
        "slot_id": "slot_b_tenor",
        "say_voice": "Nora",
        "say_rate": "175",
        "af_filter": (
            "asetrate=22050*0.78,aresample=16000,atempo=1.282,"
            "highpass=f=75,bass=g=5:f=160,treble=g=-3:f=4200,loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    {
        "slot_id": "slot_c_alto",
        "say_voice": "Nora",
        "say_rate": "168",
        "af_filter": (
            "asetrate=22050*0.93,aresample=16000,atempo=1.075,"
            "highpass=f=95,equalizer=f=1100:t=q:w=1.2:g=3,aecho=0.8:0.88:12:0.15,"
            "loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
    {
        "slot_id": "slot_d_baritone",
        "say_voice": "Nora",
        "say_rate": "188",
        "af_filter": (
            "asetrate=22050*0.70,aresample=16000,atempo=1.4285,"
            "highpass=f=65,bass=g=6:f=130,equalizer=f=700:t=q:w=1.0:g=3,"
            "loudnorm=I=-16:TP=-1.5:LRA=9"
        ),
    },
]

SYNTHETIC_MEETINGS: list[dict[str, Any]] = [
    {
        "id": "mote_1_sikkerhet",
        "title": "Statusmøte Sikkerhetsplattform 2026",
        "num_speakers": 4,
        "speaker_hint": (
            "Kari Nordmann (Prosjektleder), "
            "Ola Hansen (Systemutvikler), "
            "Ingrid Berg (Sikkerhetsarkitekt), "
            "Per Johansen (Økonomiansvarlig)"
        ),
        "speakers": [
            {"name": "Kari Nordmann", "role": "Prosjektleder", "slot": 0},
            {"name": "Ola Hansen", "role": "Systemutvikler", "slot": 1},
            {"name": "Ingrid Berg", "role": "Sikkerhetsarkitekt", "slot": 2},
            {"name": "Per Johansen", "role": "Økonomiansvarlig", "slot": 3},
        ],
        "agenda_md": """# Agenda: Statusmøte Sikkerhetsplattform 2026
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
""",
        "fasit_referat_md": """# Møtereferat: Statusmøte Sikkerhetsplattform 2026

**Dato:** 29. oktober 2026
**Deltakere:** Kari Nordmann (Prosjektleder), Ola Hansen (Systemutvikler), Ingrid Berg (Sikkerhetsarkitekt), Per Johansen (Økonomiansvarlig)

## 1. Sammendrag
Prosjektgruppen gjennomgikk status for API-migrering til OAuth2, funn fra sikkerhetstesting og budsjettstatus for fjerde kvartal. Overgangen til OAuth2 i testmiljøet er fullført med svartider under 40 ms, og det ble vedtatt at tofaktorautentisering skal være obligatorisk for alle administratorbrukere før produksjonssetting.

## 2. Gjennomgang per sak
- **API-migrering og OAuth2 (Ola Hansen):** Overgangen til ny OAuth2-innlogging er ferdig i testmiljøet. Alle endepunkter svarer under 40 millisekunder, men det gjenstår to dagers arbeid med feilhåndtering.
- **Sikkerhetsrevisjon (Ingrid Berg):** Sikkerhetstesten viste at tofaktorautentisering må kreves for alle administratorbrukere før lansering. Oppdatert sikkerhetsrapport sendes innen onsdag.
- **Budsjett og lisenser Q4 (Per Johansen):** Prosjektet ligger 15 prosent under budsjett etter samling av skyservere i Norge. Sertifikatlisensen må fornyes innen 15. november til en kostnad på 40 000 kroner.

## 3. Vedtak og beslutninger
1. Tofaktorautentisering er vedtatt som obligatorisk krav for alle administratorbrukere før produksjonssetting.
2. Sertifikatlisensen til 40 000 kroner fornyes innen 15. november.

## 4. Aksjonspunkter
| Oppgave | Ansvarlig | Frist |
| :--- | :--- | :--- |
| Ferdigstille feilhåndtering i OAuth2 og aktivere tvungen tofaktorautentisering | Ola Hansen | Fredag |
| Sende oppdatert sikkerhetsrapport og kravspesifikasjon til Kari og Per | Ingrid Berg | Onsdag |
| Bestille og fornye sertifikatlisens (40 000 kr) | Per Johansen | 15. november |
""",
        "evaluation": {
            "expected_speakers": ["Kari Nordmann", "Ola Hansen", "Ingrid Berg", "Per Johansen"],
            "required_facts": [
                ["oauth2", "api"],
                ["40", "førti"],
                ["tofaktor", "2fa"],
                ["15", "femten"],
                ["november"],
                ["fredag"],
                ["onsdag"],
            ],
        },
        "script": [
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
                "pause_after_ms": 250,
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
        ],
    },
    {
        "id": "mote_2_styremote",
        "title": "Styremøte Tromsø Kultur- ogIdrettslag – Høst 2026",
        "num_speakers": 3,
        "speaker_hint": (
            "Astrid Solberg (Styreleder), "
            "Henrik Vik (Kasserer), "
            "Marte Lunde (Arrangementansvarlig)"
        ),
        "speakers": [
            {"name": "Astrid Solberg", "role": "Styreleder", "slot": 0},
            {"name": "Henrik Vik", "role": "Kasserer", "slot": 1},
            {"name": "Marte Lunde", "role": "Arrangementansvarlig", "slot": 2},
        ],
        "agenda_md": """# Agenda: Styremøte Tromsø Kultur- og Idrettslag
Dato: 3. november 2026
Tilstede:
- Astrid Solberg (Styreleder)
- Henrik Vik (Kasserer)
- Marte Lunde (Arrangementansvarlig)

## Saksliste
1. Godkjenning av innkalling og presentasjonsrunde
2. Sak 12/26: Regnskap for tredje kvartal og medlemskontingent (Henrik)
3. Sak 13/26: Høstseminar på Kulturhuset 18. november (Marte)
4. Oppsummering av vedtak og oppgaver
""",
        "fasit_referat_md": """# Møtereferat: Styremøte Tromsø Kultur- og Idrettslag

**Dato:** 3. november 2026
**Deltakere:** Astrid Solberg (Styreleder), Henrik Vik (Kasserer), Marte Lunde (Arrangementansvarlig)

## 1. Sammendrag
Styret behandlet regnskapet for tredje kvartal, fastsatte forslag til medlemskontingent og vedtok budsjettramme for høstseminaret på Kulturhuset 18. november.

## 2. Gjennomgang per sak
- **Sak 12/26 – Regnskap og kontingent (Henrik Vik):** Foreningen har et positivt resultat på 28 500 kroner etter tredje kvartal, og 142 betalende medlemmer. Medlemskontingenten foreslås videreført uendret på 450 kroner.
- **Sak 13/26 – Høstseminar på Kulturhuset (Marte Lunde):** Høstseminaret arrangeres på Kulturhuset 18. november kl. 18:00. Det er behov for en budsjettramme på 15 000 kroner til leie av sal og enkel servering. Påmeldingsfristen settes til 10. november.

## 3. Vedtak og beslutninger
1. Regnskapet for tredje kvartal med overskudd på 28 500 kroner godkjennes, og medlemskontingenten holdes på 450 kroner.
2. Styret bevilger en budsjettramme på 15 000 kroner til høstseminaret på Kulturhuset 18. november.

## 4. Aksjonspunkter
| Oppgave | Ansvarlig | Frist |
| :--- | :--- | :--- |
| Sende ut betalingspåminnelse til medlemmer som ikke har betalt kontingent | Henrik Vik | Mandag |
| Bestille sal og servering på Kulturhuset og sende ut invitasjon med frist 10. november | Marte Lunde | Torsdag |
| Publisere programmet for høstseminaret på foreningens nettside | Astrid Solberg | Fredag |
""",
        "evaluation": {
            "expected_speakers": ["Astrid Solberg", "Henrik Vik", "Marte Lunde"],
            "required_facts": [
                ["28", "tjueåtte"],
                ["450", "fire hundre og femti"],
                ["kulturhuset"],
                ["18", "attende"],
                ["15", "femten"],
                ["10", "tiende"],
                ["mandag"],
                ["torsdag"],
            ],
        },
        "script": [
            {
                "speaker": "Astrid Solberg",
                "pause_after_ms": 450,
                "text": (
                    "Velkommen til styremøte i Tromsø Kultur- og Idrettslag, tredje november to tusen og tjueseks. "
                    "Jeg heter Astrid Solberg og er styreleder. Vi tar en kort navnerunde før vi går løs på sakslista."
                ),
            },
            {
                "speaker": "Henrik Vik",
                "pause_after_ms": 400,
                "text": (
                    "Hei, jeg heter Henrik Vik, og jeg er kasserer og ansvarlig for regnskap og medlemsregister i laget."
                ),
            },
            {
                "speaker": "Marte Lunde",
                "pause_after_ms": 450,
                "text": (
                    "Og mitt navn er Marte Lunde. Jeg er arrangementansvarlig i styret og koordinerer høstseminaret."
                ),
            },
            {
                "speaker": "Astrid Solberg",
                "pause_after_ms": 350,
                "text": (
                    "Takk. Innkallingen er godkjent. Da går vi til sak tolv tjueseks som er regnskap for tredje kvartal. "
                    "Henrik, hva viser tallene?"
                ),
            },
            {
                "speaker": "Henrik Vik",
                "pause_after_ms": 350,
                "text": (
                    "Regnskapet viser et overskudd på tjueåtte tusen fem hundre kroner etter tredje kvartal, "
                    "og vi har nå hundre og førtito betalende medlemmer. Jeg foreslår at medlemskontingenten holdes uendret på fire hundre og femti kroner."
                ),
            },
            {
                "speaker": "Astrid Solberg",
                "pause_after_ms": 300,
                "text": (
                    "Det er veldig ryddig. Da vedtar styret regnskapet og beholder kontingenten på fire hundre og femti kroner. "
                    "Henrik, sender du ut betalingspåminnelse innen mandag?"
                ),
            },
            {
                "speaker": "Henrik Vik",
                "pause_after_ms": 400,
                "text": (
                    "Ja, Astrid, jeg sender ut påminnelse til de siste medlemmene innen mandag."
                ),
            },
            {
                "speaker": "Marte Lunde",
                "pause_after_ms": 350,
                "text": (
                    "Så over til sak tretten tjueseks om høstseminaret. Vi har reservert Kulturhuset den attende november klokka atten. "
                    "Vi trenger en budsjettramme på femten tusen kroner til leie av sal og enkel bevertning, og påmeldingsfrist tiende november."
                ),
            },
            {
                "speaker": "Astrid Solberg",
                "pause_after_ms": 300,
                "text": (
                    "Flott tiltak, Marte. Styret vedtar en budsjettramme på femten tusen kroner til høstseminaret på Kulturhuset. "
                    "Kan du bekrefte bestillingen og sende ut invitasjon innen torsdag?"
                ),
            },
            {
                "speaker": "Marte Lunde",
                "pause_after_ms": 350,
                "text": (
                    "Det ordner jeg innen torsdag, og så sender jeg programteksten til deg så du kan legge den ut på nettsiden før fredag."
                ),
            },
        ],
    },
    {
        "id": "mote_3_kommune_helse",
        "title": "Prosjektmøte Velferdsteknologi i Hjemmetjenesten",
        "num_speakers": 4,
        "speaker_hint": (
            "Anne Lindqvist (Virksomhetsleder), "
            "Thomas Bakke (Fysioterapeut), "
            "Silje Haugen (Ergoterapeut), "
            "Lars Nygård (IT-rådgiver)"
        ),
        "speakers": [
            {"name": "Anne Lindqvist", "role": "Virksomhetsleder", "slot": 0},
            {"name": "Thomas Bakke", "role": "Fysioterapeut", "slot": 1},
            {"name": "Silje Haugen", "role": "Ergoterapeut", "slot": 2},
            {"name": "Lars Nygård", "role": "IT-rådgiver", "slot": 3},
        ],
        "agenda_md": """# Agenda: Prosjektmøte Velferdsteknologi i Hjemmetjenesten
Dato: 4. november 2026
Tilstede:
- Anne Lindqvist (Virksomhetsleder)
- Thomas Bakke (Fysioterapeut)
- Silje Haugen (Ergoterapeut)
- Lars Nygård (IT-rådgiver)

## Saksliste
1. Presentasjonsrunde
2. Pilotprosjekt med digitale trygghetsalarmer (Silje og Thomas)
3. Opplæring av nattevakter i uke 46
4. Personvern og integrasjon mot pasientjournal (Lars)
5. Videre fremdrift og frister
""",
        "fasit_referat_md": """# Møtereferat: Prosjektmøte Velferdsteknologi i Hjemmetjenesten

**Dato:** 4. november 2026
**Deltakere:** Anne Lindqvist (Virksomhetsleder), Thomas Bakke (Fysioterapeut), Silje Haugen (Ergoterapeut), Lars Nygård (IT-rådgiver)

## 1. Sammendrag
Prosjektgruppen gjennomgikk utprøvingen av digitale trygghetsalarmer og fallsensorer hos 25 brukere i hjemmetjenesten. Det ble vedtatt å gjennomføre obligatorisk opplæring for nattevakter i uke 46 og ferdigstille kryptert overføring mot pasientjournalen før 1. desember.

## 2. Gjennomgang per sak
- **Pilot med digitale trygghetsalarmer (Silje Haugen & Thomas Bakke):** 25 brukere har nå fått installert nye digitale trygghetsalarmer, og 8 brukere med høy fallrisiko har fått fallsensor. Responsen fra pårørende er god, men nattevaktene trenger bedre opplæring i varslingsappen.
- **Personvern og pasientjournal (Lars Nygård):** Kryptert kobling mot pasientjournalen er klar i test, og risikovurderingen (ROS) ferdigstilles innen fredag 13. november.

## 3. Vedtak og beslutninger
1. Det gjennomføres to opplæringsøkter for nattevaktene i uke 46.
2. Evalueringsrapporten for pilotprosjektet skal leveres innen 1. desember.

## 4. Aksjonspunkter
| Oppgave | Ansvarlig | Frist |
| :--- | :--- | :--- |
| Holde to praktiske opplæringsøkter i varslingsappen for nattevaktene | Silje Haugen | Uke 46 |
| Følge opp fysisk funksjonskartlegging hos de 8 brukerne med fallsensor | Thomas Bakke | 20. november |
| Ferdigstille risikovurdering og aktivere kryptert kobling mot pasientjournal | Lars Nygård | 13. november |
| Sammenstille endelig evalueringsrapport til kommunalsjefen | Anne Lindqvist | 1. desember |
""",
        "evaluation": {
            "expected_speakers": ["Anne Lindqvist", "Thomas Bakke", "Silje Haugen", "Lars Nygård"],
            "required_facts": [
                ["25", "tjuefem"],
                ["trygghetsalarm", "fallsensor"],
                ["46", "førtiseks"],
                ["pasientjournal", "kryptert"],
                ["13", "trettende"],
                ["20", "tjuende"],
                ["desember"],
            ],
        },
        "script": [
            {
                "speaker": "Anne Lindqvist",
                "pause_after_ms": 450,
                "text": (
                    "Hei og velkommen til prosjektmøte om velferdsteknologi i hjemmetjenesten, fjerde november. "
                    "Jeg heter Anne Lindqvist og er virksomhetsleder. Vi starter med en kort presentasjonsrunde."
                ),
            },
            {
                "speaker": "Thomas Bakke",
                "pause_after_ms": 400,
                "text": (
                    "Hei, jeg heter Thomas Bakke, og jeg jobber som fysioterapeut med ansvar for fallforebygging i prosjektet."
                ),
            },
            {
                "speaker": "Silje Haugen",
                "pause_after_ms": 400,
                "text": (
                    "Jeg heter Silje Haugen, og jeg er ergoterapeut med ansvar for utplassering av digitale trygghetsalarmer."
                ),
            },
            {
                "speaker": "Lars Nygård",
                "pause_after_ms": 450,
                "text": (
                    "Og mitt navn er Lars Nygård. Jeg er IT-rådgiver i kommunen og jobber med personvern og pasientjournal."
                ),
            },
            {
                "speaker": "Anne Lindqvist",
                "pause_after_ms": 350,
                "text": (
                    "Takk. Silje og Thomas, hvordan har oppstarten av pilotprosjektet med trygghetsalarmer gått?"
                ),
            },
            {
                "speaker": "Silje Haugen",
                "pause_after_ms": 350,
                "text": (
                    "Vi har nå montert digitale trygghetsalarmer hos tjuefem brukere i hjemmetjenesten. "
                    "Men vi ser at nattevaktene er usikre på den nye varslingsappen, så jeg holder to opplæringsøkter i uke førtiseks."
                ),
            },
            {
                "speaker": "Thomas Bakke",
                "pause_after_ms": 350,
                "text": (
                    "I tillegg har åtte brukere med høy fallrisiko fått ny fallsensor. "
                    "Jeg gjennomfører ny funksjonskartlegging hos disse åtte brukerne innen tjuende november."
                ),
            },
            {
                "speaker": "Lars Nygård",
                "pause_after_ms": 400,
                "text": (
                    "Fra IT sin side er kryptert overføring til pasientjournalen ferdig testet. "
                    "Jeg ferdigstiller risikovurderingen og skrur på integrasjonen innen fredag trettende november."
                ),
            },
            {
                "speaker": "Anne Lindqvist",
                "pause_after_ms": 350,
                "text": (
                    "Veldig bra. Da vedtar vi opplæring av nattevakter i uke førtiseks, og jeg sammenstiller "
                    "evalueringsrapporten til kommunalsjefen innen første desember. Takk for møtet!"
                ),
            },
        ],
    },
    {
        "id": "mote_4_produkt_sprint",
        "title": "Sprintplanlegging Mobilapp Versjon 2.4",
        "num_speakers": 2,
        "speaker_hint": (
            "Jonas Lie (Produkteier), "
            "Sofie Moe (Sjefsdesigner)"
        ),
        "speakers": [
            {"name": "Jonas Lie", "role": "Produkteier", "slot": 1},
            {"name": "Sofie Moe", "role": "Sjefsdesigner", "slot": 0},
        ],
        "agenda_md": """# Agenda: Sprintplanlegging Mobilapp Versjon 2.4
Dato: 5. november 2026
Tilstede:
- Jonas Lie (Produkteier)
- Sofie Moe (Sjefsdesigner)

## Saksliste
1. Introduksjon og mål for versjon 2.4
2. Komprimert kontrollpanel på mobil og universell utforming (Sofie)
3. Funn fra brukerundersøkelsen og lanseringsplan (Jonas)
""",
        "fasit_referat_md": """# Møtereferat: Sprintplanlegging Mobilapp Versjon 2.4

**Dato:** 5. november 2026
**Deltakere:** Jonas Lie (Produkteier), Sofie Moe (Sjefsdesigner)

## 1. Sammendrag
Jonas Lie og Sofie Moe gjennomgikk tilbakemeldinger fra brukerundersøkelsen (120 svar) og fastsatte designendringer for versjon 2.4. Kontrollpanelet på mobil gjøres sammenleggbart, kontrastene oppgraderes til WCAG 2.1 AA, og versjon 2.4 lanseres 20. november.

## 2. Gjennomgang per sak
- **Brukerundersøkelse (Jonas Lie):** Undersøkelsen blant 120 brukere viste at 68 prosent ønsket mer skjermplass til selve teksten på mobiltelefoner.
- **Design og universell utforming (Sofie Moe):** Kontrollpanelet legges i en sammenleggbar skuff på skjermer under 680 piksler, og fargekontrastene justeres for å oppfylle WCAG 2.1 AA-kravene.

## 3. Vedtak og beslutninger
1. Sammenleggbart kontrollpanel på mobil og WCAG 2.1 AA-kontraster tas inn i versjon 2.4.
2. Lansering av versjon 2.4 fastsettes til fredag 20. november.

## 4. Aksjonspunkter
| Oppgave | Ansvarlig | Frist |
| :--- | :--- | :--- |
| Levere ferdige skisser og CSS-spesifikasjon for mobilpanel og WCAG-kontrast | Sofie Moe | Tirsdag 10. november |
| Oppdatere sprinttavlen og klargjøre testutgave til betapanelet | Jonas Lie | Torsdag 12. november |
""",
        "evaluation": {
            "expected_speakers": ["Jonas Lie", "Sofie Moe"],
            "required_facts": [
                ["2.4", "to punkt fire"],
                ["120", "hundre og tjue"],
                ["68", "sekstiåtte"],
                ["wcag", "kontrast"],
                ["10", "tiende"],
                ["12", "tolvte"],
                ["20", "tjuende"],
            ],
        },
        "script": [
            {
                "speaker": "Jonas Lie",
                "pause_after_ms": 400,
                "text": (
                    "Hei og velkommen til sprintplanlegging for mobilappen versjon to punkt fire, femte november. "
                    "Jeg heter Jonas Lie og er produkteier."
                ),
            },
            {
                "speaker": "Sofie Moe",
                "pause_after_ms": 400,
                "text": (
                    "Hei, jeg heter Sofie Moe, og jeg er sjefsdesigner med ansvar for brukeropplevelse og universell utforming."
                ),
            },
            {
                "speaker": "Jonas Lie",
                "pause_after_ms": 350,
                "text": (
                    "Vi har fått inn hundre og tjue svar i brukerundersøkelsen vår. "
                    "Hele sekstiåtte prosent sier at kontrollpanelet tar for mye plass på mobilskjermer. "
                    "Sofie, hvordan løser vi det i versjon to punkt fire?"
                ),
            },
            {
                "speaker": "Sofie Moe",
                "pause_after_ms": 350,
                "text": (
                    "Vi legger redigeringsfeltene i en sammenleggbar skuff som er lukket som standard på mobil, "
                    "og samtidig oppgraderer vi alle tekstkontraster til WCAG to punkt en dobbel A. "
                    "Jeg leverer ferdige skisser og stilark innen tirsdag tiende november."
                ),
            },
            {
                "speaker": "Jonas Lie",
                "pause_after_ms": 300,
                "text": (
                    "Strålende, Sofie. Da vedtar vi det designet for versjon to punkt fire. "
                    "Jeg oppdaterer sprinttavlen og sender ut testutgaven innen torsdag tolvte november, "
                    "slik at vi er klare til lansering fredag tjuende november."
                ),
            },
        ],
    },
]


def find_ffmpeg_bin() -> str:
    env = mote_core.build_runtime_env()
    ffmpeg_bin = shutil.which("ffmpeg", path=env["PATH"])
    if not ffmpeg_bin:
        raise RuntimeError("Fant ikke ffmpeg.")
    return ffmpeg_bin


def synthesize_meeting(meeting: dict[str, Any], out_dir: Path, ffmpeg_bin: str) -> Path:
    """Generate audio (.opus) and save ground-truth agenda, transcript, notes, and evaluation JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    opus_path = out_dir / f"{meeting['id']}.opus"
    agenda_path = out_dir / "fasit_agenda.md"
    transcript_path = out_dir / "fasit_transkripsjon.json"
    notes_path = out_dir / "fasit_referat.md"
    eval_path = out_dir / "fasit_evaluering.json"

    agenda_path.write_text(meeting["agenda_md"], encoding="utf-8")
    transcript_path.write_text(
        json.dumps(meeting["script"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    notes_path.write_text(meeting["fasit_referat_md"], encoding="utf-8")
    eval_path.write_text(
        json.dumps(
            {
                "id": meeting["id"],
                "title": meeting["title"],
                "num_speakers": meeting["num_speakers"],
                "speaker_hint": meeting["speaker_hint"],
                "speakers": meeting["speakers"],
                **meeting["evaluation"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    slot_by_speaker = {s["name"]: VOICE_SLOTS[s["slot"]] for s in meeting["speakers"]}

    with tempfile.TemporaryDirectory(prefix=f"synth_{meeting['id']}_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        part_files: list[Path] = []
        for idx, turn in enumerate(meeting["script"]):
            speaker = turn["speaker"]
            text = turn["text"]
            pause_ms = int(turn.get("pause_after_ms", 350))
            profile = slot_by_speaker[speaker]

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
                str(opus_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    return opus_path


def get_audio_duration(audio_path: Path, ffmpeg_bin: str) -> float:
    ffprobe_bin = ffmpeg_bin.replace("ffmpeg", "ffprobe")
    out = subprocess.check_output(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        text=True,
    ).strip()
    return float(out)


def normalize_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\sæøåÆØÅ]", " ", text.lower())
    return [w for w in cleaned.split() if w]


def compute_word_accuracy(ref_text: str, hyp_text: str) -> float:
    """Compute (1 - WER) * 100 in [0.0, 100.0] using Levenshtein word edit distance."""
    ref = normalize_words(ref_text)
    hyp = normalize_words(hyp_text)
    if not ref:
        return 100.0
    n, m = len(ref), len(hyp)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            temp = dp[j]
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    wer = dp[m] / float(n)
    return round(max(0.0, (1.0 - wer) * 100.0), 2)


def evaluate_speaker_mapping(
    turns: list[mote_core.TurnDict],
    speaker_map: dict[str, str],
    script: list[dict[str, Any]],
    expected_speakers: list[str],
) -> float:
    """Evaluate what percentage of expected speakers are mapped to the right SPEAKER_XX ID."""
    if not expected_speakers or not turns:
        return 0.0
    # Match each ground-truth turn to the generated turn with highest word overlap
    correct_speaker_matches = 0
    matched_speakers: set[str] = set()
    for spk_name in expected_speakers:
        # Find ground truth turns by this speaker
        gt_texts = [t["text"] for t in script if t["speaker"] == spk_name]
        gt_words = set(normalize_words(" ".join(gt_texts)))
        if not gt_words:
            continue
        # Find which SPEAKER_XX has the highest word overlap with gt_words
        best_spk_id = None
        best_overlap = 0
        by_spk: dict[str, list[str]] = {}
        for t in turns:
            by_spk.setdefault(t["speaker"], []).append(t["text"])
        for spk_id, txts in by_spk.items():
            wset = set(normalize_words(" ".join(txts)))
            overlap = len(gt_words & wset)
            if overlap > best_overlap:
                best_overlap = overlap
                best_spk_id = spk_id
        if best_spk_id:
            mapped_label = speaker_map.get(best_spk_id, best_spk_id).lower()
            first_name = spk_name.split()[0].lower()
            last_name = spk_name.split()[-1].lower()
            if first_name in mapped_label and last_name in mapped_label:
                correct_speaker_matches += 1
                matched_speakers.add(spk_name)
    return round((correct_speaker_matches / len(expected_speakers)) * 100.0, 2)


def evaluate_notes_quality(
    notes_md: str,
    expected_speakers: list[str],
    required_facts: list[list[str]],
) -> dict[str, float]:
    """Score generated Markdown meeting notes on facts, speaker mentions, and clean formatting."""
    lower_md = notes_md.lower()
    # 1. Fact recall
    fact_hits = 0
    for syn_group in required_facts:
        if any(tok.lower() in lower_md for tok in syn_group):
            fact_hits += 1
    fact_score = (fact_hits / len(required_facts) * 100.0) if required_facts else 100.0

    # 2. Speaker presence in minutes
    spk_hits = sum(1 for name in expected_speakers if name.split()[0].lower() in lower_md)
    spk_score = (spk_hits / len(expected_speakers) * 100.0) if expected_speakers else 100.0

    # 3. Structural cleanliness (headings, table/bullets, no leaked <think> or English CoT)
    has_headings = "## " in notes_md or "# " in notes_md
    has_actions = any(w in lower_md for w in ("aksjon", "oppgave", "ansvarlig", "frist", "vedtak", "beslutning"))
    has_leaked_cot = any(
        bad in lower_md
        for bad in ("<think>", "</think>", "thinking process:", "here is the summary")
    )
    struct_score = 0.0
    if has_headings:
        struct_score += 40.0
    if has_actions:
        struct_score += 40.0
    if not has_leaked_cot and len(notes_md.strip()) >= 250:
        struct_score += 20.0

    total_notes_score = round(0.50 * fact_score + 0.25 * spk_score + 0.25 * struct_score, 2)
    return {
        "fact_score": round(fact_score, 2),
        "speaker_mention_score": round(spk_score, 2),
        "structure_score": round(struct_score, 2),
        "notes_total_score": total_notes_score,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generer 4 syntetiske møter og kjør E2E modell-benchmark.")
    parser.add_argument(
        "--action",
        choices=["generate", "benchmark", "llm-only", "all"],
        default="all",
    )
    parser.add_argument(
        "--asr-models",
        nargs="+",
        default=[
            "NbAiLab/nb-whisper-small",
            "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2",
            "large-v3-turbo",
            "NbAiLab/nb-whisper-large",
        ],
    )
    parser.add_argument(
        "--llm-models",
        nargs="+",
        default=[
            "llama3.2:latest",
            "qwen3:4b",
            "gemma4:e4b",
            "devstral:latest",
        ],
    )
    args = parser.parse_args()

    ffmpeg_bin = find_ffmpeg_bin()
    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

    if args.action in ("generate", "all"):
        print("==> Genererer 4 syntetiske norske møter med fasit (agenda, transkripsjon, referat) og lyd...")
        for m in SYNTHETIC_MEETINGS:
            m_dir = MEETINGS_DIR / m["id"]
            t0 = time.time()
            opus_path = synthesize_meeting(m, m_dir, ffmpeg_bin)
            dur = get_audio_duration(opus_path, ffmpeg_bin)
            print(
                f"  [OK] {m['id']:22s} ({m['num_speakers']} talere, {len(m['script']):2d} replikker, {dur:5.1f}s lyd) "
                f"generert på {time.time() - t0:.1f}s -> {opus_path}"
            )

    if args.action == "generate":
        return 0

    # Benchmark execution
    hf_token = mote_core.get_initial_hf_token()
    results_json_path = SYNTH_ROOT / "benchmark_results.json"
    asr_benchmark: dict[str, dict[str, Any]] = {}

    if args.action == "llm-only" and results_json_path.is_file():
        prev_data = json.loads(results_json_path.read_text(encoding="utf-8"))
        asr_benchmark = prev_data.get("asr_benchmark", {})
        ref_asr_model = max(asr_benchmark.keys(), key=lambda k: asr_benchmark[k]["avg_word_acc"])
        print(f"==> Gjenbruker Fase 1 ASR-data fra {results_json_path.name} (referanse-ASR: {ref_asr_model})")
    else:
        # Pre-warm any uncached ASR model on the shortest meeting so one-time download time
        # does not skew the inference benchmark.
        shortest_opus = MEETINGS_DIR / "mote_4_produkt_sprint" / "mote_4_produkt_sprint.opus"
        with tempfile.TemporaryDirectory(prefix="mote_warmup_") as warm_dir:
            for asr_model in args.asr_models:
                folder_name = "models--" + asr_model.replace("/", "--")
                if asr_model == "large-v3-turbo":
                    folder_name = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
                if not (mote_core.HF_CACHE_DIR / "hub" / folder_name).is_dir():
                    print(f"==> Forhåndslaster {asr_model} til lokal HF-cache før tidsmåling...", flush=True)
                    mote_core.run_pipeline(
                        input_path=shortest_opus,
                        out_dir=Path(warm_dir) / folder_name,
                        html_name="w.html",
                        notes_name="w.md",
                        notes_html_name="w_n.html",
                        whisper_model=asr_model,
                        ollama_model="gemma4:e4b",
                        language="no",
                        hf_token=hf_token,
                        min_speakers=1,
                        max_speakers=1,
                        task_mode="only_1_asr",
                        log_fn=lambda msg: None,
                        progress_fn=lambda pct, lbl: None,
                    )

        print("\n" + "=" * 88)
        print("FASE 1: BENCHMARK AV TALEGJENKJENNING (STEG 1–2: ASR + ALIGN) PÅ 4 MØTER")
        print("=" * 88)

        for asr_model in args.asr_models:
            asr_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", asr_model)
            asr_benchmark[asr_model] = {"meetings": {}, "avg_word_acc": 0.0, "total_sec": 0.0, "avg_rtf": 0.0}
            print(f"\n---> Tester ASR-modell: {asr_model}", flush=True)

            acc_list: list[float] = []
            rtf_list: list[float] = []
            total_s = 0.0

            for m in SYNTHETIC_MEETINGS:
                m_dir = MEETINGS_DIR / m["id"]
                opus_path = m_dir / f"{m['id']}.opus"
                dur = get_audio_duration(opus_path, ffmpeg_bin)
                run_dir = m_dir / "runs" / asr_slug
                ckpt_dir = mote_core.get_checkpoint_dir(run_dir, opus_path)
                if ckpt_dir.is_dir():
                    for old_f in ("1_asr.json", "2_align.json"):
                        if (ckpt_dir / old_f).exists():
                            (ckpt_dir / old_f).unlink()
                run_dir.mkdir(parents=True, exist_ok=True)

                t0 = time.time()
                mote_core.run_pipeline(
                    input_path=opus_path,
                    out_dir=run_dir,
                    html_name="tx.html",
                    notes_name="notes.md",
                    notes_html_name="notes.html",
                    whisper_model=asr_model,
                    ollama_model="gemma4:e4b",
                    language="no",
                    hf_token=hf_token,
                    min_speakers=m["num_speakers"],
                    max_speakers=m["num_speakers"],
                    speaker_names_hint=m["speaker_hint"],
                    meeting_title=m["title"],
                    agenda_text=m["agenda_md"],
                    note_mode="mote",
                    task_mode="only_1_asr",
                    log_fn=lambda msg: None,
                    progress_fn=lambda pct, lbl: None,
                )
                s1_sec = time.time() - t0

                t1 = time.time()
                mote_core.run_pipeline(
                    input_path=opus_path,
                    out_dir=run_dir,
                    html_name="tx.html",
                    notes_name="notes.md",
                    notes_html_name="notes.html",
                    whisper_model=asr_model,
                    ollama_model="gemma4:e4b",
                    language="no",
                    hf_token=hf_token,
                    min_speakers=m["num_speakers"],
                    max_speakers=m["num_speakers"],
                    speaker_names_hint=m["speaker_hint"],
                    meeting_title=m["title"],
                    agenda_text=m["agenda_md"],
                    note_mode="mote",
                    task_mode="only_2_align",
                    log_fn=lambda msg: None,
                    progress_fn=lambda pct, lbl: None,
                )
                s2_sec = time.time() - t1
                elapsed = s1_sec + s2_sec

                align_json = ckpt_dir / "2_align.json"
                turns = mote_core.extract_turns_from_whisperx(align_json)

                ref_full_text = " ".join(t["text"] for t in m["script"])
                hyp_full_text = " ".join(t["text"] for t in turns)
                word_acc = compute_word_accuracy(ref_full_text, hyp_full_text)
                rtf = elapsed / dur if dur > 0 else 0.0

                asr_benchmark[asr_model]["meetings"][m["id"]] = {
                    "s1_asr_sec": round(s1_sec, 2),
                    "s2_align_sec": round(s2_sec, 2),
                    "elapsed_sec": round(elapsed, 2),
                    "audio_dur_sec": round(dur, 2),
                    "rtf": round(rtf, 3),
                    "word_accuracy_pct": word_acc,
                    "turn_count": len(turns),
                    "align_json": str(align_json),
                    "ckpt_dir": str(ckpt_dir),
                    "run_dir": str(run_dir),
                }
                acc_list.append(word_acc)
                rtf_list.append(rtf)
                total_s += elapsed
                print(
                    f"  * {m['id']:22s}: Ord-nøyaktighet={word_acc:6.2f}% | "
                    f"Tid={elapsed:5.1f}s (ASR={s1_sec:4.1f}s, Align={s2_sec:4.1f}s, RTF={rtf:.2f}x)",
                    flush=True,
                )

            asr_benchmark[asr_model]["avg_word_acc"] = round(sum(acc_list) / len(acc_list), 2)
            asr_benchmark[asr_model]["avg_rtf"] = round(sum(rtf_list) / len(rtf_list), 3)
            asr_benchmark[asr_model]["total_sec"] = round(total_s, 2)
            print(
                f"  => SNITT {asr_model}: Ord-nøyaktighet={asr_benchmark[asr_model]['avg_word_acc']:.2f}%, "
                f"Total tid={total_s:.1f}s (Snitt RTF={asr_benchmark[asr_model]['avg_rtf']:.2f}x)",
                flush=True,
            )

        # Pick the highest-accuracy ASR model as reference for Pyannote Stage 3 + LLM Stage 4-5
        ref_asr_model = max(asr_benchmark.keys(), key=lambda k: asr_benchmark[k]["avg_word_acc"])
        print(f"\n==> Kjører Steg 3 (Pyannote taler-ID) for de 4 møtene (referanse-ASR: {ref_asr_model})...", flush=True)
        for m in SYNTHETIC_MEETINGS:
            m_dir = MEETINGS_DIR / m["id"]
            opus_path = m_dir / f"{m['id']}.opus"
            run_dir = Path(asr_benchmark[ref_asr_model]["meetings"][m["id"]]["run_dir"])
            ckpt_dir = Path(asr_benchmark[ref_asr_model]["meetings"][m["id"]]["ckpt_dir"])
            diarize_json = ckpt_dir / "3_diarize.json"
            if not diarize_json.is_file():
                t_d = time.time()
                mote_core.run_pipeline(
                    input_path=opus_path,
                    out_dir=run_dir,
                    html_name="tx.html",
                    notes_name="notes.md",
                    notes_html_name="notes.html",
                    whisper_model=ref_asr_model,
                    ollama_model="gemma4:e4b",
                    language="no",
                    hf_token=hf_token,
                    min_speakers=m["num_speakers"],
                    max_speakers=m["num_speakers"],
                    speaker_names_hint=m["speaker_hint"],
                    meeting_title=m["title"],
                    agenda_text=m["agenda_md"],
                    note_mode="mote",
                    task_mode="only_3_diarize",
                    log_fn=lambda msg: None,
                    progress_fn=lambda pct, lbl: None,
                )
                print(f"  * Pyannote {m['id']:22s} fullført på {time.time() - t_d:.1f}s", flush=True)
            asr_benchmark[ref_asr_model]["meetings"][m["id"]]["diarize_json"] = str(diarize_json)

    print("\n" + "=" * 88)
    print("FASE 2: BENCHMARK AV LOKALE LLM-MODELLER (STEG 4–5: NAVN & REFERAT) PÅ 4 MØTER")
    print("=" * 88)

    llm_benchmark: dict[str, dict[str, Any]] = {}

    for llm_model in args.llm_models:
        llm_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", llm_model)
        llm_benchmark[llm_model] = {
            "meetings": {},
            "avg_speaker_acc": 0.0,
            "avg_notes_score": 0.0,
            "total_sec": 0.0,
        }
        print(f"\n---> Tester Ollama LLM-modell: {llm_model}")

        spk_scores: list[float] = []
        note_scores: list[float] = []
        total_llm_s = 0.0

        for m in SYNTHETIC_MEETINGS:
            m_dir = MEETINGS_DIR / m["id"]
            diarize_json = Path(asr_benchmark[ref_asr_model]["meetings"][m["id"]]["diarize_json"])
            turns = mote_core.extract_turns_from_whisperx(diarize_json)
            llm_out_dir = m_dir / "llm_runs" / llm_slug
            llm_out_dir.mkdir(parents=True, exist_ok=True)

            t0 = time.time()
            try:
                spk_map = mote_core.infer_speaker_names_via_api(
                    turns,
                    llm_model,
                    m["speaker_hint"],
                    m["title"],
                    m["agenda_md"],
                    lambda msg: None,
                )
                s4_sec = time.time() - t0

                html_path = llm_out_dir / f"{m['id']}_transkripsjon.html"
                clean_tx = mote_core.build_transcript_html_and_text(
                    turns, spk_map, html_path, m["title"]
                )

                t1 = time.time()
                notes_md = mote_core.generate_notes_via_api(
                    clean_tx,
                    llm_model,
                    spk_map,
                    m["speaker_hint"],
                    m["title"],
                    m["agenda_md"],
                    note_mode="mote",
                )
                s5_sec = time.time() - t1
                elapsed = s4_sec + s5_sec

                notes_md_path = llm_out_dir / f"{m['id']}_referat.md"
                notes_html_path = llm_out_dir / f"{m['id']}_referat.html"
                notes_md_path.write_text(notes_md + "\n", encoding="utf-8")
                mote_core.build_notes_html(notes_md, notes_html_path, m["title"])

                spk_acc = evaluate_speaker_mapping(
                    turns, spk_map, m["script"], m["evaluation"]["expected_speakers"]
                )
                n_eval = evaluate_notes_quality(
                    notes_md,
                    m["evaluation"]["expected_speakers"],
                    m["evaluation"]["required_facts"],
                )
            except Exception as exc:
                print(f"  [FEIL] {llm_model} på {m['id']}: {exc}")
                s4_sec = 0.0
                s5_sec = 0.0
                elapsed = 999.0
                spk_map = {}
                spk_acc = 0.0
                n_eval = {
                    "fact_score": 0.0,
                    "speaker_mention_score": 0.0,
                    "structure_score": 0.0,
                    "notes_total_score": 0.0,
                }

            llm_benchmark[llm_model]["meetings"][m["id"]] = {
                "s4_sec": round(s4_sec, 2),
                "s5_sec": round(s5_sec, 2),
                "elapsed_sec": round(elapsed, 2),
                "speaker_accuracy_pct": spk_acc,
                "speaker_map": spk_map,
                **n_eval,
            }
            spk_scores.append(spk_acc)
            note_scores.append(n_eval["notes_total_score"])
            total_llm_s += elapsed
            print(
                f"  * {m['id']:22s}: Navn-ID={spk_acc:6.1f}% | Referat={n_eval['notes_total_score']:6.2f}% "
                f"(Fakta={n_eval['fact_score']:.0f}%) | Tid={elapsed:5.1f}s (S4={s4_sec:.1f}s, S5={s5_sec:.1f}s)"
            )

        llm_benchmark[llm_model]["avg_speaker_acc"] = round(sum(spk_scores) / len(spk_scores), 2)
        llm_benchmark[llm_model]["avg_notes_score"] = round(sum(note_scores) / len(note_scores), 2)
        llm_benchmark[llm_model]["total_sec"] = round(total_llm_s, 2)
        print(
            f"  => SNITT {llm_model}: Navn-ID={llm_benchmark[llm_model]['avg_speaker_acc']:.1f}%, "
            f"Referat={llm_benchmark[llm_model]['avg_notes_score']:.2f}%, Total tid={total_llm_s:.1f}s"
        )

    # Combine ASR x LLM matrix & rank profiles
    combinations: list[dict[str, Any]] = []
    for asr_model, a_data in asr_benchmark.items():
        for llm_model, l_data in llm_benchmark.items():
            comb_sec = a_data["total_sec"] + l_data["total_sec"]
            # Speed score: 100 pts at <= 60s total across 4 meetings, linear decay to 0 at 420s
            speed_score = max(0.0, min(100.0, (420.0 - comb_sec) / 3.6))
            total_score = (
                0.30 * a_data["avg_word_acc"]
                + 0.25 * l_data["avg_speaker_acc"]
                + 0.30 * l_data["avg_notes_score"]
                + 0.15 * speed_score
            )
            combinations.append(
                {
                    "asr_model": asr_model,
                    "llm_model": llm_model,
                    "avg_word_acc": a_data["avg_word_acc"],
                    "avg_speaker_acc": l_data["avg_speaker_acc"],
                    "avg_notes_score": l_data["avg_notes_score"],
                    "asr_total_sec": a_data["total_sec"],
                    "llm_total_sec": l_data["total_sec"],
                    "combined_sec": round(comb_sec, 2),
                    "speed_score": round(speed_score, 2),
                    "total_score": round(total_score, 2),
                }
            )

    combinations.sort(key=lambda x: x["total_score"], reverse=True)

    summary_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "meetings_count": len(SYNTHETIC_MEETINGS),
        "asr_benchmark": asr_benchmark,
        "llm_benchmark": llm_benchmark,
        "ranked_combinations": combinations,
    }
    results_json_path = SYNTH_ROOT / "benchmark_results.json"
    results_json_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("\n" + "=" * 88)
    print("RANGERING AV MODELLKOMBINASJONER (TOTALSCORE 0–100 OVER 4 SYNTETISKE MØTER)")
    print("=" * 88)
    for rank, c in enumerate(combinations, 1):
        print(
            f"#{rank:2d} | Score={c['total_score']:6.2f} | "
            f"ASR={c['asr_model']:48s} ({c['avg_word_acc']:5.1f}%, {c['asr_total_sec']:5.1f}s) | "
            f"LLM={c['llm_model']:16s} (Navn={c['avg_speaker_acc']:5.1f}%, Ref={c['avg_notes_score']:5.1f}%, {c['llm_total_sec']:5.1f}s) | "
            f"Totaltid={c['combined_sec']:5.1f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
