# ADR-0014 — Onafhankelijke review van contract 1.3 en Peil 1.1.1 in deze repo

- **Status:** aangenomen · 2026-11-10 · vult ADR-0013 aan
- **Bron:** `PLATFORM_CONTRACTEN.md` (`contracts 1.3`, bron-commit `02b119c`, byte-gelijk met de werkbranch van
  ai-server-manager op het moment van de review) en `CONTRACT_1.3_CONFORMITEIT.md` hoofdstuk 3 (C1.3-ASF-01…14).
- **Validatie:** Unit-getest, Sim-getest (Hub-simulator, PostgreSQL 16, Chromium) en de alles-in-één image in Docker.
  **NIET GEVALIDEERD (echte NAS):** geen echte TrueNAS, geen draaiende echte Hub, geen schermlezer of telefoon. De
  vergelijking met de Hub hieronder is **gelezen** (broncode van ai-server-manager), niet uitgevoerd.

## Context

De bouwer meldde alle NU-punten C1.3-ASF-01…13 als gebouwd en getest. Deze review draaide alle stappen van de CI
opnieuw (lint, typecheck, unit, integratie, dienst-E2E, Alembic op/neer/op, OpenAPI-drift, `--strict`, pip-audit,
actionlint, contract- en Peil-drift, Playwright, de live ontwerpreview en de image-E2E) en zocht daarna gericht naar
gaten. Alles was groen; de vier bevindingen hieronder vielen niet op door een rode test, maar door een test die het
geval niet dekte.

## Bevindingen en besluiten

1. **Healthcheck volgde een proxy uit de omgeving** (C1.3-ASF-01, contract §5: uitgaande calls negeren
   proxy-omgevingsvariabelen). `urllib.request.urlopen` gebruikt `HTTP_PROXY` ook voor `127.0.0.1`. Aangetoond met de
   image van vóór de review: met `HTTP_PROXY` in de omgeving gaf de healthcheck exit 1, zonder exit 0. Wie in het
   formulier van de Custom App een proxy zet, kreeg dus een container die "unhealthy" werd en door TrueNAS herstart.
   **Besluit:** een opener zonder proxy (`ProxyHandler({})`); de image-E2E draait de healthcheck nu ook met een
   onbereikbare `HTTP_PROXY` in de omgeving.
2. **Herstel van een onleesbaar koppelbestand van een andere eigenaar liep vast** (C1.3-ASF-04, §3.5/§3.8). Het
   origineel werd bewaard met een harde link of, als dat niet mocht, een kopie. Is het bestand van een andere eigenaar
   en niet leesbaar (bv. van root met 0600 na een herstel als root, met *Custom User* 568), dan faalden beide: elke
   worker-ronde gaf een fout en een nieuw env-token kon het bestand nooit vervangen, terwijl de documentatie juist dat
   als herstel noemt. **Besluit:** derde stap: het origineel **verplaatsen** (zelfde inode, exacte bytes en eigenaar;
   alleen de map hoeft schrijfbaar te zijn). De vervanging wordt binnen dezelfde vergrendeling direct geschreven;
   mislukt dat, dan komt het origineel terug, zodat er nooit een ontbrekend bestand ontstaat (dat zou "nooit
   gekoppeld" lijken en het formulier tonen). Unit-test met een nagebootste weigering van link en lezen, ook voor het
   mislukte schrijven.
3. **`GET /connector/v1/hub-link` gaf `confirmed_at` mee** (C1.3-ASF-10, §3.5: de witte lijst is een maximum; §6 voegt
   alleen `pinned_kids` en `rotation_sequence` toe, §6.2 `observed_at`). Het veld is geen geheim en de Hub leest het
   niet (gelezen: `ai-server-manager:apps/api/src/hub/routers/services.py`, `HUB_LINK_FIELDS`), maar het staat niet in
   de lijst. **Besluit:** weggehaald; de test controleert nu de exacte set velden. home-control-hub doet hetzelfde.
4. **De test "elke route vraagt aanmelding" sloeg de router `existing_apps` over** (EX16; 5 GET- en 6 POST-routes onder
   `/catalog/v1/existing-apps` en `/catalog/v1/decisions`). De routes zelf waren goed beveiligd (gemeten: 401 zonder
   geldige aanmelding, 403 voor `kind` en zonder rol), maar een nieuwe router zou ongemerkt buiten de test vallen.
   **Besluit:** de test leest de routes van de echte app (`create_app`, ook ingesloten routers) en controleert dat elk
   pad uit het OpenAPI-schema erin zit.

## Vergelijking met de gebouwde Hub (gelezen, niet uitgevoerd)

ADR-0013 en de EVIDENCE zeiden dat de Hub C1.3-ASM-07/08 (scope `inventaris.lezen`, paginering over een momentopname)
nog niet had. Inmiddels staat het in ai-server-manager (`0899cd6`). Gelezen in `hub/routers/catalog.py`, `hub/deps.py`,
`hub/scopes.py` en `hub/inventory/store.py`: `items`, `totaal`, `snapshot_id` en `next_cursor` op elke pagina,
`inventaris.{status, reason_code, observed_at, verouderd}`, 409 `snapshot_changed`, 403 `scope_ontbreekt` voor een
dienst zonder scope, `limit` ≤ 200, appstore-en-fork heeft de scope. Dat is de vorm die `catalog/hubinventory.py`
verwacht. De Hub geeft geen `inventaris.max_age_s`; deze dienst houdt dan 900 s aan. Ook de enroll-respons
(`schema_version` "1.3", `rotation_sequence`), de JWT's (één `aud`, `service_id`), `GET /catalog/v1/entries` (velden en
`limit=200`) en het lezen van de connector-health (vaste checks, versheid per check met `limits.max_age_s`) passen.
Tegen een **draaiende** Hub is de import niet getest; dat blijft NIET GEVALIDEERD.

Eén afwijking tussen de repos, geen fout in deze dienst: de Hub leest bij `last_error` uit `GET /connector/v1/hub-link`
de sleutel `melding` (`services.py`, `DienstenScreen.tsx`); deze dienst en home-control-hub sturen `message` (de sleutel
van het foutformaat in §5), jelly-leon-platform `message_nl` en de executor `melding`. §3.5 zegt alleen "code + vaste
NL-melding". Gevolg: de Hub-UI toont de NL-melding van deze dienst niet (wel de code). Contractmelding: de naam
vastleggen in een volgende contractversie; tot dan zou de Hub beide moeten lezen. Hier niet eenzijdig veranderd.

## Gevolgen

- EVIDENCE: C1.3-ASF-01, -04, -09 en -10 en de nieuwe regel `REVIEW-appstore-en-fork-contract-1.3` bijgewerkt;
  geen status hoger dan `Bezig`/`Geblokkeerd`.
- Niets aan de API-vorm veranderd behalve het weglaten van `confirmed_at` in `GET /connector/v1/hub-link` (de OpenAPI
  beschrijft dat antwoord als vrij object en blijft gelijk).
