# Møtereferat

## 1. Møteinformasjon og kort oppsummering
**Møtetittel:** Statusmøte Sikkerhetsplattform 2026
**Dato:** 29. oktober 2026
**Formål:** Å gjennomgå statusen på API-migrering, sikkerhetskrav, og budsjett for Sikkerhetsplattform 2026.
**Hovedkonklusjon:** Det ble vedtatt at tofaktorautentisering er obligatorisk før lansering. Det ble også avklart at en sertifikatlisens må fornyes innen 15. november.

## 2. Deltakere og roller
*   **Kari Nordmann (Prosjektleder):** Ansvarlig for prosjektledelse og styring av møtet.
*   **Ola Hansen (Systemutvikler):** Ansvarlig for bakenden og API-migrering.
*   **Ingrid Berg (Sikkerhetsarkitekt):** Ansvarlig for sikkerhetsrevisjoner og definisjon av sikkerhetskrav.
*   **Per Johansen (Økonomiansvarlig):** Ansvarlig for plattformbudsjett og økonomisk rapportering.

## 3. Gjennomgang av agenda og hovedtemaer

### 2. Status på API-migrering og OAuth2-innlogging (Ola)
*   **[00:41 – 00:53]** Ola Hansen rapporterte at overgangen til ny OAuth2-innlogging i testmiljøet ble fullført denne uken. Alle endepunkter svarer under 40 millisekunder. Det er imidlertid behov for to dager til for feilhåndtering.

### 3. Sikkerhetsrevisjon og krav om tofaktorautentisering (Ingrid)
*   **[00:53 – 01:04]** Per Johansen påpekte at sikkerhetstesten dagen før viste at tofaktorautentisering må være påkrevd for alle administratorbrukere før produktlansering.
*   **[01:04 – 01:10]** Ola Hansen bekreftet at han vil implementere tvungen tofaktorautentisering i konfigurasjonen innen fredag.
*   **[01:21 – 01:33]** Ingrid Berg informerte om at plattformen ligger 15 % under budsjett for kvartalet på grunn av samling av skyserverne i Norge. Det ble lagt frem et krav om at sertifikatlisensen må fornyes innen 15. november, med en kostnad på 40 000 kroner.

### 4. Budsjett og lisenskostnader for fjerde kvartal (Per)
*   **[01:33 – 01:39]** Per Johansen bekreftet at han vil sende over oppdatert sikkerhetsrapport og kravspesifikasjoner til Kari Nordmann og Ingrid Berg innen onsdag.

## 4. Beslutninger
*   **[01:11 – 01:21]** Det ble vedtatt at tofaktorautentisering er obligatorisk før lansering av Sikkerhetsplattform 2026.

## 5. Aksjonspunkter

| Ansvarlig | Oppgave | Frist / Status |
| :--- | :--- | :--- |
| Ola Hansen | Fullføre OAuth2-implementering og tofaktorautentisering | Fredag |
| Ingrid Berg | Levere sikkerhetsrapport | Onsdag |
| Per Johansen | Bestille sertifikatlisensen | Før 15. november |
