# Changelog

## 0.1.0 — in ontwikkeling (nog niet uitgebracht)

- **Review contract 1.3** (ADR-0014):
  - Healthcheck negeert proxy-omgevingsvariabelen (met `HTTP_PROXY` in het formulier werd de container onterecht
    `unhealthy`); de image-E2E controleert dat.
  - Een onleesbaar koppelbestand van een andere eigenaar (niet te linken of te lezen) wordt bij een nieuw env-token
    verplaatst in plaats van dat het herstel vastloopt; mislukt de vervanging, dan komt het origineel terug.
  - `GET /connector/v1/hub-link` geeft alleen de witte lijst van §3.5 plus `pinned_kids`, `rotation_sequence` en
    `observed_at` (geen `confirmed_at` meer).
  - De test "elke route vraagt aanmelding" leest de routes van de echte app en dekt nu ook `existing-apps` en
    `decisions`.

- **Platformcontract 1.3 en Peil 1.1.1** (ADR-0013, C1.3-ASF-01…13; C1.3-ASF-14 volgt in P2):
  - Byte-gelijke kopieën van `PLATFORM_CONTRACTEN.md` (1.3) en `CONTRACT_1.3_CONFORMITEIT.md` op een vaste bron-commit
    (`CONTRACT_HERKOMST.json`, `tools/contract_kopie.py`, ook online in CI); EVIDENCE-test volgens §11 van 1.3.
  - Statuspagina: `Host`-beperking tegen DNS-rebinding (`host_niet_toegestaan`), `Sec-Fetch-Site`, exacte `Origin`
    (`herkomst_onjuist`); elke POST na de herkomstcontrole telt als poging (ook `al_gekoppeld`, `koppelen_bezig`,
    `koppelstatus_onleesbaar`); meldingen alleen na een echte poging van deze browser; Peil 1.1.1 (renderer in één
    doorgang, toestanden `koppelstatus_onleesbaar` en `koppelen_bezig`, tonen `niet-gekoppeld` en `feit`,
    `melding_rol`, `melding_code` los van de trace-ID, totaalregel E10). Negatieve browsertests in Chromium.
  - Koppelbestand: onleesbaar = fail-closed (ook andere major, ontbrekende velden, andere `app_id`, verkeerde eigenaar of
    rechten); alleen een nieuw env-token vervangt het, na het origineel te bewaren; `schema_version` 1.3; extra velden
    blijven; vaste codes `hub_adres_ongeldig`, `vingerafdruk_ongeldig`, `koppelstatus_onleesbaar`.
  - Adressen: alleen canonieke IP-vormen, slotpunt negeren, verboden bereiken ook na DNS-resolutie bij elke verbinding;
    `base_url` alleen als hij geldig is. `rotation_sequence` uit het enroll-antwoord; testvector `HN69-2PHK-XV2D-X85W`.
  - JWT: `aud` precies één string; reasons volgens §4.7; aanmelden vóór bodyvalidatie, rol en `Idempotency-Key`.
  - Foutcatalogus: `validation_error` + `details.fouten[]`, `idempotency_key_reused`, `idempotency_in_progress`,
    `Idempotent-Replay`; statische `Idempotency-Key`-test met uitzonderingslijst.
  - Events: `service.hub_link.linked` met `{via}`, vaste werkwoorden in het domein `catalog`, `payload.ernst`, grenzen
    van de inbox vóór verzending (ongeldig = dead-letter).
  - Connector-health: vaste checks `database`, `worker` (hartslag), `jobs`, `hub_link`; versheid met
    `limits.max_age_s` en `meting_verouderd`.
  - Inventaris-import: scope `inventaris.lezen` (`scope_ontbreekt` → `capability_unavailable`), momentopname met
    `snapshot_id` en 409 `snapshot_changed`, `max_age` volgens de Hub, `Retry-After` bij 429/503.
  - Emby: adoptie-item dat het platform nooit wijzigt (plan zonder executor-stappen, gates voor de HARDE EIS).
  - Validator en schema 1.3: `install.reason`, `reserved_env` voor `HUB_*`, geheimen met `source: generated` +
    `secret: true`, `accepted_findings` met `--strict`, `stopped_snapshot`, `post_install_checks`, `same_as_host`,
    `healthcheck.monitor`, documentsoort `kind: checklist` (`jl-manifest checklist`).
  - Image: healthcheck in exec-vorm (`python -I -S`) wisselt eerst naar 568 met no-new-privileges en een lege
    bounding set; de bewaker stopt als de bounding set niet leeg is; image-E2E controleert de healthcheck apart.

- B08-001 manifeststandaard v1: JSON Schema, validator met NL-meldingen, CLI `jl-manifest`, vertaling naar TrueNAS
  `app.create` (standaard Custom App via `ix-app`, of catalogusapp), conflictanalyse, ondertekende provenance.
- Catalogus-API (`/catalog/v1`), connectorcontract (`/connector/v1`), koppelen met de Hub, statuspagina.
- Alles-in-één image (PostgreSQL 16 + API + worker) voor een standaard TrueNAS Custom App.
- Review fase P0 (ADR-0007): validatorlekken gedicht (`//`-hostpaden, verborgen mappen en systeemdatasets,
  apparaat-allowlist, `//etc`-koppelpad, NET_ADMIN met host network, samengestelde veranderlijke tags,
  variabele-interpolatie, catalogusapp `ix-app` en gevaarlijke velden in catalogus-waarden, YAML-merge-sleutels,
  niet-tekst-sleutels en diepe nesting); API: cursors geven 400 in plaats van 500, bodylimiet ook zonder
  `Content-Length`, koppelen alleen met de Hub uit `HUB_URL`; image-E2E toetst nu ook databehoud na herstart,
  harde kill en een nieuwe container op dezelfde `/appdata`.
- B08-002 (alleen-lezen kern): `POST /catalog/v1/import/preview` herkent bestaande apps aan de platformlabels,
  catalogusapp of image; een niet-ondersteunde app blijft unmanaged en de import plant nooit een actie. Het vastleggen
  van de eigenaarbevestiging is nog niet gebouwd (`capability_unavailable`).
- **Platformcontract 1.2** (ADR-0008):
  - Manifeststandaard: `network.joins_platform_network` en `custom_app.ports` vervallen (overgang met
    `deprecated_field`, fout in `--strict`), poorten alleen uit `network.ports`, GPU als optionele parameter (`gpu`,
    D08, begrensd door `privileges.gpu`), digest-readback in de preview en `version_unavailable` bij een verdwenen
    catalogus- of `ix-app`-versie.
  - Koppelen: enroll-verzoek en -antwoord exact §3.3 met sleutelset-vingerafdruk (`HUB_KEY_FINGERPRINT`) en
    bevestiging via `services/self`; koppelstatus in `/appdata/secrets/hub-link.json` (eenmalig overgezet uit de
    database); herstart halverwege, intrekking (pas na 401 op `services/self`), rotatie van het service-token en
    ondertekende sleutelrotaties.
  - Inkomend alleen Hub-JWT's tegen gepinde sleutels (gebruiker of dienst-JWT van de Hub); het service-token wordt
    geweigerd; `token_use` en `jti` verplicht met een replaycache in de database; vaste `reason`-waarden.
  - Statuspagina: formulier alleen vóór de eerste koppeling, met verplichte vingerafdruk, `Origin`-controle en
    5 pogingen/15 min per bron-IP + 20 per dienst. `GET /catalog/v1/hub-link` → `GET /connector/v1/hub-link` +
    `POST /connector/v1/hub-link/unlink`.
  - Image: geen `dumb-init` meer; de bewaker is PID 1 en draait, zoals alle processen, als 568 zonder capabilities
    met `no_new_privs`.
- **Review contract 1.2** (ADR-0008 §5): 1 MiB-grens vóór routering en aanmelding, ook chunked, met
  `details.max_bytes`; outbox markeert afgeleverde events weer als afgeleverd (backoff en dead-letter werken weer);
  koppelpoging atomair geclaimd (geen twee pogingen tegelijk, nooit een koppelcode na de eerste koppeling, verweesde
  poging blokkeert niet); periodieke bevestiging met `services/self` (elke 4 min) zodat een aangevraagde tokenrotatie
  binnen 5 min gebeurt; sleutelrotatie weigert een volgnummer dat geen geheel getal is. Eén lokale interop-controle
  tegen de echte Hub-code van ai-server-manager.
- **Statuspagina in Peil** (AS7, ADR-0009): `GET /` gebruikt het framework-vrije Peil-sjabloon 1.1.0 als
  versie-gepinde kopie (`design/ontwerptaal/`, herkomstkop met sha256, `HERKOMST.json`); CI faalt bij drift met
  ai-server-manager (`tools/peil_kopie.py`). Totaalstatus nooit groen als de dienst niet gereed is; "Gekoppeld" neutraal;
  een mislukte koppelpoging geeft direct de pagina met vaste code, veldfout en selecteerbare trace-ID (geen redirect,
  invoer nooit teruggetoond); formuliervelden `hub_adres`, `koppelcode`, `hub_vingerafdruk`. Nieuwe tests: renderer =
  referentie-renderer, toestanden door de echte app, Playwright met axe-core (0 overtredingen), geen overloop op 320 px,
  CSP zonder JS; schermafdrukken in `docs/screenshots/peil/statuspagina/`.
- **B08-002 bestaande apps importeren + APP-01…APP-20** (ADR-0010):
  - Inventaris via de Hub (`GET /api/v1/apps` + detail, service-token, alleen lezen, begrensd, ≤ 3 pogingen); alleen
    een volledige, verse meting telt, anders `unknown`/`capability_unavailable` en verandert er niets.
  - Register `existing_app` (migratie 0003), idempotent: gemeten tag/effectieve digest/readback, installatiewijze,
    poorten, opslag, datasets; historie van image-wijzigingen; verdwenen apps blijven staan (`present: false`).
  - Herkenning alleen op catalogusapp of exacte image-repository (bronnen vastgelegd in
    `bestaande_apps/register.yaml`), nooit op naam of op de herkenning door de Hub; onbekend blijft `onbekend`.
    `adopted` (*Geïmporteerd*) pas na bevestiging van de eigenaar door een beheerder; `release` maakt dat ongedaan.
  - Adoptiemanifest `kind: adoption` (`schemas/adoption-manifest-v1.schema.json`), nooit installeerbaar; de validator
    weigert het met `adoption_manifest_not_installable`.
  - Beslisregister per app (`decision_record`): voorgenomen besluit uit APP_REGISTER, controlelijst letterlijk uit de
    A-documenten, afvinken alleen met bewijs door een gebruiker, besluit alleen door een beheerder en pas met een
    volledige controlelijst; decommission-plan volgens I26 als voorbereiding (executor-`action_id`'s, back-up-gate
    altijd `unknown`, nooit data verwijderen). Events `catalog.import.*`, `catalog.app.*`, `catalog.check.updated`,
    `catalog.decision.recorded`, `catalog.decommission.planned`.
  - Worker: beslisregister bij de start, periodieke import (`APPSTORE_IMPORT_INTERVAL_S`, standaard 900 s, 0 = uit).
  - Interop lokaal tegen de echte Hub-code (ai-server-manager `efb7f3b`): koppelen + bevestigen werkt; met het
    service-token geeft de Hub op `GET /api/v1/apps` 403 → `capability_unavailable` (contractpunt); met een
    gebruikerssessie verwerkt dezelfde client-code de echte Hub-vorm (4 apps). Daarbij gevonden en gerepareerd: de
    effectieve digest staat in `image.digest` van de Hub, niet in `images[0].digest`.
- **Ontwerpreview statuspagina** (contract §13, ADR-0011, `docs/ontwerp/PEIL_REVIEW.md`):
  - Gevonden met een echte browser tegen de draaiende dienst: door `Referrer-Policy: no-referrer` stuurde Chromium bij
    het koppelformulier `Origin: null` en faalde **elke** echte koppelpoging met "Het formulier is verlopen" (403).
    Nu `same-origin`; `Origin: null` blijft geweigerd.
  - Teksten volgens de Peil-woordenlijst: "Ingebouwde catalogus" en "Git-repository"/"HTTP-adres" in plaats van
    "bundled"/"git"/"http"; "Catalogusgegevens — Gelezen uit de eigen database: …"; geen "Deels onbekend" bij een
    besluit van de Hub (ontkoppeld/ingetrokken); uitkomst van de Hub na het formulier noemt de koppelcode en wat je
    kunt doen; "koppeltoken" in plaats van "enrollment-token"; kortere veldfout bij het Hub-adres; geen lange
    variabelenamen of paden meer in meldingen (staken op 320 px met een breed lettertype buiten de melding).
  - Nieuwe review-tooling: `tests/ui/review_live.py` + `statuspagina.live.ts` (18 toestanden, echte formulier-POST,
    ook in CI); gedeelde metingen `tests/ui/meting.ts` (contrast in de DOM, randen/iconen ≥ 3:1, focusring, tekst
    buiten kaart, statusvorm, woordenlijst, reflow met een breed lettertype) in beide specs. Schermafdrukken: 18 toestanden × 320/360/390/768/1440 ×
    licht/donker uit de draaiende dienst.
- **Review fase P1** (ADR-0012):
  - Import: alleen een volledige lijst uit één Hub-meting telt. Elke pagina moet dezelfde `inventaris` en hetzelfde
    `totaal` melden en het aantal gelezen apps moet gelijk zijn aan `totaal`; anders `unknown`
    (`inventaris_gewijzigd_tijdens_lezen`/`inventaris_onvolledig`) en verandert er niets. Eerder maakte een nieuwe
    meting tussen twee pagina's of een afgekapte lijst apps ten onrechte "verdwenen".
  - `GET /connector/v1/health`: totaal = slechtste check (onbekend is nooit `healthy`), nieuwe check `schema` (zelfde
    meting als `/readyz`; een openstaande migratie is `down`).
  - Import-events alleen bij een andere uitkomst of een registerwijziging (bewijs en audit blijven per ronde); het
    antwoord noemt `event_type`.
- Status: Sim-getest; NIET GEVALIDEERD op een echte NAS.
