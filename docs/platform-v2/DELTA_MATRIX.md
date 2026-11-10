# Delta-matrix appstore-en-fork (bestaand / onvolledig / ontbrekend)

Peildatum 2026-11-09. Uitgangspunt: de repo bevatte alleen documentatie (plus een niet-gecommitte, onafgemaakte
aanzet van het manifestmodel uit een afgebroken poging; beoordeeld, aangepast en hergebruikt). Er was dus niets
"bestaand" om te behouden of dubbel te bouwen.

| Onderdeel | Vóór | Nu (deze fase P0) | Validatie |
|---|---|---|---|
| B08-001 Manifeststandaard: JSON Schema + validator + CLI | ontbrekend | gebouwd | Unit-getest |
| B08-001 Weigerregels (EX47: privileged, socket, hostpaden, latest, digest, compose, voorbeeldwaarden) | ontbrekend | gebouwd; na review aangescherpt (ADR-0007: `//`-paden, apparaat-allowlist, interpolatie, catalogus-waarden, YAML-trucs) | Sim-getest (EX47) |
| B08-001 Signed provenance (uitgever + catalogus) | ontbrekend | gebouwd | Sim-getest |
| B08-001 Compatibiliteit TrueNAS/ix-app + vertaling naar `app.create` | ontbrekend | gebouwd (ix-app 1.4.8 uit broncode) | Unit-getest; NIET GEVALIDEERD (echte NAS) |
| B08-001 Conflictanalyse (poorten, datasets, afhankelijkheden) | ontbrekend | gebouwd (op gemeten inventaris; zonder → unknown) | Unit-getest |
| Catalogus-API (bronnen, entries, versies, valideren, voorbeeld) | ontbrekend | gebouwd | Sim-getest |
| Connectorcontract §6 | ontbrekend | read gebouwd; propose/apply/rollback → `capability_unavailable`; review P1: `health` totaal = slechtste check (onbekend nooit `healthy`) + check `schema` | Sim-getest |
| Auth §4 / enrollment §3 | ontbrekend | gebouwd tegen Hub-simulator | Sim-getest; echte Hub nog niet beschikbaar |
| Alles-in-één image + Custom App-instructies | ontbrekend | gebouwd | Docker-E2E; NIET GEVALIDEERD (echte NAS) |
| B08-002 Import bestaande apps (Excel-prioriteit **P0**) | ontbrekend | P0-review: `POST /catalog/v1/import/preview` (alleen lezen). **P1 (ADR-0010):** import via de Hub-appcatalogus (`GET /api/v1/apps`, service-token), idempotent register `existing_app` (`onbekend`/`kandidaat`/`adopted` = *Geïmporteerd* na eigenaarbevestiging door een beheerder), adoptiemanifest `kind: adoption` (niet installeerbaar), periodieke import in de worker. Review P1 (ADR-0012): alleen een volledige lijst uit één meting (pagina's gelijk, aantal = `totaal`), events alleen bij verandering. Open: de Hub geeft `GET /api/v1/apps` nog niet aan diensten (403 → `capability_unavailable`) | Sim-getest; interop met de echte Hub-code lokaal; NIET GEVALIDEERD (echte NAS) |
| APP-01…APP-20 beslisregister | ontbrekend | gebouwd (ADR-0010): register uit APP_REGISTER/A01…A20, controlelijst met bewijs, besluit (beheerder, controlelijst volledig), decommission-plan volgens I26 als voorbereiding (voert niets uit, nooit data verwijderen) | Sim-getest; gebruikerstest per app op de echte NAS: Geblokkeerd |
| B08-003 Install preview/dependency | ontbrekend | deels (preview + conflictanalyse, geen DAG/egress/download-gate) | — |
| B08-004 Veilige updates | ontbrekend | ontbrekend | — |
| B08-005 Forkbeheer | ontbrekend | ontbrekend (P1) | — |
| B08-006 Uninstall/move/recovery | ontbrekend | ontbrekend | — |

## Contract 1.2 (2026-11-09, `CONTRACT_1.2_CONFORMITEIT.md` §3, ADR-0008)

| Punt | Vóór (stand `2895404`) | Nu | Validatie |
|---|---|---|---|
| M1–M4 validator: `joins_platform_network`/`custom_app.ports` vervallen, `deprecated_field`, GPU-parameter | eiste beide velden; GPU alleen gelijk aan `privileges.gpu` | overgang met `deprecated_field` (fout in `--strict`), poorten uit `network.ports`, `gpu`-parameter met D08 | Unit-getest |
| M5 vertaling: tag met digest + readback, `version_unavailable` | tag met digest al aanwezig | `digest_readback` in de preview, `version_unavailable`/`version_availability_unknown`, GPU alleen met gemeten render-node | Unit-getest; Sim-getest (API); NIET GEVALIDEERD (echte NAS) |
| M6 fixtures, docs, eigen manifest | 1.1-vorm | 1.2-vorm + overgangsfixture | Unit-getest |
| AS1 enroll-verzoek/-antwoord, vingerafdruk, `services/self` | `capabilities`, JWK met `use`/`alg`, aliassen gelezen | exact §3.3, zelf berekende vingerafdruk, bevestiging | Sim-getest |
| AS2 opslag `/appdata/secrets/hub-link.json`, herstart halverwege | tabel `hub_link` (versleuteld token) | bestand 0600 atomair, eenmalige overzetting, `pending` | Sim-getest |
| AS3 inkomend: dienst-JWT, `token_use`, `jti` + replaycache, `reason`-lijst | service-token geaccepteerd; geen `jti`-controle | alleen JWT's; replaycache (migratie 0002); vaste reasons | Sim-getest |
| AS4 pinning | JWKS via HTTP voegde sleutels toe | alleen gepinde sleutels; ondertekende rotaties | Sim-getest |
| AS5 statuspagina | formulier ook na intrekking; geen vingerafdruk; alleen 5/15 min per IP | alleen vóór eerste koppeling; vingerafdruk; `Origin`; 5 per IP + 20 per dienst | Sim-getest |
| AS6 `/connector/v1/hub-link` + `/unlink`; env-token opnieuw; `Hub-Token-Rotation` | `/catalog/v1/hub-link` | gebouwd | Sim-getest |
| AS7 statuspagina in de ontwerptaal | eigen CSS en opmaak | Peil 1.1.0-sjabloon als versie-gepinde kopie (`design/ontwerptaal/`, O-14) met driftcontrole in CI; formulier alleen vóór de eerste koppeling, verplichte vingerafdruk, fout met code + trace-ID; nooit groen als niet gereed (ADR-0009); ontwerpreview achteraf (ADR-0011, `docs/ontwerp/PEIL_REVIEW.md`): Referrer-Policy `same-origin` (een echte formulier-POST gaf `Origin: null` → 403), teksten volgens de Peil-woordenlijst | Unit-getest; Sim-getest (draaiende dienst + Hub-simulator, Chromium, 18 toestanden: axe 0, contrast/randen/ring gemeten, geen overloop 320 px); NIET GEVALIDEERD (echte schermlezer, telefoon, NAS) |
| AS8 geen root-PID 1 | `dumb-init` als root | bewaker is PID 1 als 568; image-E2E per proces incl. PID 1 | Docker-E2E; NIET GEVALIDEERD (echte NAS) |

## Contract 1.3 (2026-11-10, `CONTRACT_1.3_CONFORMITEIT.md` §3, ADR-0013)

Gemeten stand vóór deze ronde: `fc682e5` (zoals de conformiteitslijst). Alleen de punten met moment **NU**; C1.3-ASF-14
(P2) staat open.

| Punt | Vóór (stand `fc682e5`) | Nu | Validatie |
|---|---|---|---|
| ALG-01 kopieën + drifttest | kopie 1.2 | byte-gelijke kopie 1.3 + conformiteitslijst 1.3 op bron-commit `02b119c`, pin in `CONTRACT_HERKOMST.json`, controle offline (unit-test) en online (CI, `tools/contract_kopie.py`) | Unit-getest |
| ALG-02 EVIDENCE §11 (1.3) | 1.2-regels | `Niet getest`, geen lege `validatie`, NIET GEVALIDEERD uit `blokkade` ook in `validatie`, ID's `C1.3-ASF-…`/`REVIEW-…`; negatieve tests | Unit-getest |
| ALG-05 Peil 1.1.1 | kopie 1.1.0 | kopie 1.1.1 (pinregel E08), renderer in één doorgang, nieuwe toestanden en tonen | Unit-getest; Sim-getest (Chromium) |
| ASF-01 healthcheck | als root, zonder wissel | exec-vorm `python -I -S`, eerst bounding set leeg + 568 + no_new_privs, `--controle`; bewaker stopt als de bounding set niet leeg is; image-E2E per proces in de boom van PID 1 en de healthcheck apart | Docker-E2E; NIET GEVALIDEERD (echte NAS) |
| ASF-02 herkomst | `Origin` exact, `null` geweigerd | + `Sec-Fetch-Site`, `Host`-beperking (`host_niet_toegestaan`); negatieve browsertests (Origin null, vreemde site, same-site, publieke naam, geen cookie) | Sim-getest (Chromium) |
| ASF-03 formulier en Gereed | `al_gekoppeld` telde niet; melding via `?melding=gekoppeld` | elke POST na de herkomstcontrole telt; 409 voor onleesbaar/al gekoppeld/bezig; melding alleen na een echte poging van deze browser; één logregel per poging | Sim-getest |
| ASF-04 koppelbestand | onleesbaar = verzonnen `first_linked_at`, overschreven door elke schrijfactie | fail-closed (ook major, velden, `app_id`, eigenaar/rechten); alleen een nieuw env-token vervangt, na bewaren van het origineel; `schema_version` 1.3; extra velden; vaste codes | Unit-getest; Sim-getest |
| ASF-05 JWT | `aud` als lijst geaccepteerd; onleesbare JSON gaf 422 vóór 401 | `aud` één string; aanmelden vóór body, rol en `Idempotency-Key`; foutcatalogus van §5 | Sim-getest |
| ASF-06 sleutels en adres | `rotation_sequence` altijd 0; `127.1` als naam geaccepteerd | `rotation_sequence` uit het antwoord; canonieke IP-vormen; verboden bereiken ook na DNS-resolutie; `base_url` alleen als geldig; testvector `HN69-…` | Unit-getest; Sim-getest |
| ASF-07 events | `service.enrollment.completed`; `sync_failed`, `synced`, `completed`, `unavailable` | `service.hub_link.linked` `{via}`; `catalog.source_sync.*`, `catalog.import.*`; `ernst`; grenzen vóór verzending | Unit-getest; Sim-getest |
| ASF-08 validator en schema | 1.2 | 1.3: `install.reason`, `reserved_env`, `secret`, `accepted_findings`/`strict_valid`, `stopped_snapshot`, `post_install_checks`, `same_as_host`, `healthcheck.monitor`, `kind: checklist` | Unit-getest |
| ASF-09 inventaris-import | 403 altijd `hub_inventaris_niet_voor_diensten`; vaste 15 min; eigen backoff bij 429 | `scope_ontbreekt` → `capability_unavailable`; `snapshot_id` + 409 opnieuw; `max_age` volgens de Hub; `Retry-After` | Sim-getest; de Hub heeft C1.3-ASM-07/08 inmiddels gebouwd (`0899cd6`, vorm gelezen en gelijk, ADR-0014); NIET GEVALIDEERD tegen een draaiende Hub |
| ASF-10 connector-health | geen `worker`/`jobs`, `detail` als tekst | vaste checks met `observed_at`, hartslag van de worker, `limits.max_age_s`, `meting_verouderd` | Sim-getest |
| ASF-11 statische `Idempotency-Key`-test | ontbrak | `tests/unit/test_idempotency_routes.py` met uitzonderingslijst | Unit-getest |
| ASF-12 Emby | adoptie-item, plan met executor-stappen | `platform_wijzigt_nooit`: plan zonder `action_id`, gates HARDE EIS/exportarchief/controlelijst | Unit-getest |
| ASF-13 eigen manifest | kop 1.2, geen monitor, licentie leeg | kop 1.3, `healthcheck.monitor`, GPL-3.0, `--strict` geldig | Unit-getest |
| ASF-14 geheime catalogus-values → vault | — | **open (P2)**; tot dan weigert de vertaling een geheime waarde | — |

**Review (ADR-0014, 2026-11-10):** alle CI-stappen opnieuw gedraaid (groen) en gericht gezocht. Gerepareerd:

| Punt | Vóór de review | Nu | Validatie |
|---|---|---|---|
| ASF-01 healthcheck | volgde `HTTP_PROXY` uit de omgeving, ook voor `127.0.0.1` (aangetoond: exit 1) | opener zonder proxy; image-E2E draait de healthcheck ook met een onbereikbare proxy in de omgeving | Docker-E2E; NIET GEVALIDEERD (echte NAS) |
| ASF-04 koppelbestand van een andere eigenaar | niet te linken en niet te lezen → elke worker-ronde een fout, herstel met een nieuw env-token onmogelijk | origineel verplaatsen (exacte bytes); mislukt de vervanging, dan komt het origineel terug | Unit-getest |
| ASF-10 `GET /connector/v1/hub-link` | gaf ook `confirmed_at` (buiten de witte lijst van §3.5) | exact de witte lijst + `pinned_kids`, `rotation_sequence`, `observed_at`; test op de exacte set | Sim-getest |
| EX16 routetest | sloeg de router `existing_apps` over (11 routes) | leest de routes van de echte app; elk pad uit het OpenAPI-schema telt mee | Sim-getest |
