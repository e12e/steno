# Møtereferat: Statusmøte Sikkerhetsplattform 2026

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
