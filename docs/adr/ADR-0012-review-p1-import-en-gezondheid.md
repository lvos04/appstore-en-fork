# ADR-0012 — Review fase P1: volledige Hub-meting, gezondheid zonder groen bij onbekend, minder events

- **Status:** aangenomen · 2026-11-09 (onafhankelijke review/QA van fase P1: B08-002, APP-01…APP-20, AS7)
- **Validatie:** Sim-getest (Hub-simulator, PostgreSQL 16, echte processen, Chromium) en de alles-in-één image in
  Docker. NIET GEVALIDEERD (echte NAS): geen echte Hub-installatie, geen echte NAS-inventaris.
- **Context:** de review controleerde vooral dat de import idempotent is, niets aanraakt en geen versies of eigenaren
  verzint, en dat de statuspagina Peil en contract 1.2 §3.7 volgt. Alle CI-stappen zijn lokaal opnieuw gedraaid
  (ruff, mypy, unit, integratie, dienst-E2E, Alembic op/neer/op, OpenAPI-drift, manifest, pip-audit, Peil-drift,
  Playwright, live ontwerpreview, image bouwen + image-E2E, actionlint).

## Bevindingen en besluit

1. **Een mengsel van twee metingen of een afgekapte lijst werd als volledige meting gebruikt.** De Hub pagineert
   `GET /api/v1/apps` met een offset over zijn laatste meting (`hub.routers.catalog.list_apps`). De client controleerde
   alleen de `inventaris` van de eerste pagina. Kwam er tussen twee pagina's een nieuwe meting, of gaf de Hub te vroeg
   geen `next_cursor`, dan werden de ontbrekende apps als **verdwenen** gemarkeerd (`present = false`): een onterechte
   wijziging van het register, precies wat B08-002 niet mag.
   **Besluit:** elke pagina moet dezelfde `inventaris` (status, `reason_code`, `observed_at`, `verouderd`) en
   hetzelfde `totaal` melden als de eerste, en het aantal gelezen apps moet gelijk zijn aan `totaal`. Anders `unknown`
   met `inventaris_gewijzigd_tijdens_lezen` of `inventaris_onvolledig`, en verandert er niets. De Hub-simulator houdt
   `observed_at` nu gelijk tot een nieuwe meting, zoals de echte Hub. Test:
   `test_existing_apps.py::test_mixed_or_truncated_pages_are_never_used` (faalt zonder de reparatie).
2. **`GET /connector/v1/health` gaf `healthy` terwijl een check `unknown` was** (koppeling nog niet bevestigd, een
   bron nooit gesynchroniseerd). De Hub neemt het totaal over (`hub.monitoring.sources.judge_connector_health`), dus
   onbekend werd groen (contract 1.2 §1: "Onbekend is unknown, nooit groen"). Een openstaande migratie gaf ook
   `healthy`, terwijl `/readyz` en de statuspagina dan "niet gereed"/"Storing" zeggen.
   **Besluit:** totaal = slechtste check (`healthy` < `unknown` < `degraded` < `down`), net als de totaalstatus van de
   statuspagina (Peil §4.3); nieuwe check `schema` met dezelfde meting als `/readyz` (verouderd = `down`, niet leesbaar
   = `unknown`). Tests: `test_connector.py::test_health_total_is_worst_check_and_unknown_is_never_healthy`,
   `test_health_schema_behind_is_down` (falen zonder de reparatie).
3. **Elke importronde zette een event in de outbox**, ook zonder verandering. De worker leest elke 15 minuten; met de
   huidige Hub (403 voor diensten) kreeg de Hub zo 96 keer per dag `catalog.import.unavailable`.
   **Besluit:** bewijs (`hub_import`) en audit blijven per ronde; een event alleen als (`status`, `reason_code`) anders
   is dan bij de vorige ronde of als het register veranderde. Het antwoord noemt `event_type` (of `null`). Test:
   `test_existing_apps.py::test_unchanged_repeat_records_evidence_but_no_new_event`. ADR-0010 §2 is bijgewerkt.

## Gecontroleerd en in orde (geen wijziging)

- Import: tweede ronde met dezelfde meting laat `row_version` gelijk; alleen `GET` naar `/api/v1/apps*` met het
  service-token; een verdwenen app wordt nooit verwijderd; herkenning alleen op exacte catalogusapp of
  image-repository; versies, tags en digests alleen uit de meting (niet gemeten → `null`); eigenaar alleen uit het
  register of een manifest en pas na bevestiging door een beheerder (gebruikers-JWT `admin`, `If-Match`,
  `Idempotency-Key`; een dienst-JWT krijgt 403); adoptiemanifest nooit installeerbaar.
- Statuspagina (§3.7, Peil 1.1.0): formulier alleen zolang `first_linked_at` leeg is; drie verplichte velden;
  CSRF (double-submit, `SameSite=Strict`, HttpOnly) + `Origin`; 5 per bron-IP en 20 per dienst per 15 min (één
  API-proces in de image); ingevulde waarden worden nooit teruggetoond of bewaard; totaal nooit groen als niet gereed;
  "Gekoppeld" neutraal; CSP zonder script. Peil-kopie byte-gelijk aan `ai-server-manager@172ee87`.
- Elke niet-publieke route vraagt authenticatie (`test_hardening.py`); geen shell, geen `sudo`, geen Docker-socket
  (de procesbewaker start alleen vaste argumentlijsten); base-images op digest; geen `latest`.

## Niet gewijzigd (gemeld)

- **De statuspagina toont de importstatus van de bestaande apps niet.** Met de huidige Hub is die op elke echte
  installatie `capability_unavailable`, terwijl de pagina "Alles gezond" kan tonen. Peil §4.3 kent daarvoor "Niet
  beschikbaar" (telt als Onbekend). Een extra rij is een UI-wijziging en vraagt eerst een ontwerpstap (contract §13);
  de Hub-UI kan de status nu al tonen via `laatste_import` van `GET /catalog/v1/existing-apps`.
- **DNS-rebinding vergroot het restrisico van §3.7.** Vóór de eerste koppeling kan een website die een gebruiker op
  het LAN bezoekt via DNS-rebinding dezelfde oorsprong krijgen (Origin = Host) en de dienst aan een eigen "Hub"
  koppelen. De dienst kent zijn eigen adres niet, dus een Host-allowlist kan hier niet zonder gegevens uit de wizard.
  Beperking blijft: env-enrollment (aanbevolen) en de getoonde vingerafdruk; herstel via de Hub.
- Een `429` van de Hub bij het lezen van de inventaris volgt de eigen backoff (max. 3 pogingen) en niet `Retry-After`;
  na 3 pogingen is de uitkomst `unknown` en verandert er niets.
