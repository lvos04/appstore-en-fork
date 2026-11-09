# Changelog

## 0.1.0 — in ontwikkeling (nog niet uitgebracht)

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
- Status: Sim-getest; NIET GEVALIDEERD op een echte NAS.
