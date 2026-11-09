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
| Connectorcontract §6 | ontbrekend | read gebouwd; propose/apply/rollback → `capability_unavailable` | Sim-getest |
| Auth §4 / enrollment §3 | ontbrekend | gebouwd tegen Hub-simulator | Sim-getest; echte Hub nog niet beschikbaar |
| Alles-in-één image + Custom App-instructies | ontbrekend | gebouwd | Docker-E2E; NIET GEVALIDEERD (echte NAS) |
| B08-002 Import bestaande apps (Excel-prioriteit **P0**) | ontbrekend | P0-review: `POST /catalog/v1/import/preview` (alleen lezen). **P1 (ADR-0010):** import via de Hub-appcatalogus (`GET /api/v1/apps`, service-token), idempotent register `existing_app` (`onbekend`/`kandidaat`/`adopted` = *Geïmporteerd* na eigenaarbevestiging door een beheerder), adoptiemanifest `kind: adoption` (niet installeerbaar), periodieke import in de worker. Open: de Hub geeft `GET /api/v1/apps` nog niet aan diensten (403 → `capability_unavailable`) | Sim-getest; interop met de echte Hub-code lokaal; NIET GEVALIDEERD (echte NAS) |
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
