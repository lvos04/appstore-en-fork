# appstore-en-fork

Appstore-catalogus van het **TrueNAS Home Platform V2**: B08-001 manifeststandaard (deze fase), later B08-002 import
van bestaande apps en B08-005 forkbeheer. Zelfstandige API-dienst met alleen een kleine Nederlandse statuspagina; alle
schermen staan in de Hub-UI van `ai-server-manager`.

> **Status (eerlijk):** code, unit-, integratie- en E2E-tests met simulators zijn groen in de bouwomgeving
> (**Sim-getest**). Niets is op een echte TrueNAS getest (**NIET GEVALIDEERD (echte NAS)**); de GHCR-image bestaat pas
> na de eerste release-tag. Zie `docs/platform-v2/EVIDENCE.json`.

## Wat zit erin

| Onderdeel | Waar |
|---|---|
| Manifeststandaard v1 (JSON Schema, validator, NL-meldingen) | `schemas/manifest-v1.schema.json`, `apps/api/src/appstore/manifest/`, [`docs/MANIFESTSTANDAARD.md`](docs/MANIFESTSTANDAARD.md) |
| CLI `jl-manifest validate|translate|sign|verify` | `appstore.manifest.cli` |
| Vertaling naar TrueNAS `app.create` (standaard Custom App = `ix-app`, of catalogusapp) + conflictanalyse | `manifest/translate.py`, `manifest/conflicts.py`, [`docs/TRUENAS_APP_VALUES.md`](docs/TRUENAS_APP_VALUES.md) |
| Catalogus-API `/catalog/v1` (bronnen bundled/git/http, entries, versiegeschiedenis, valideren, voorbeeld) | `apps/api/src/appstore/api/catalog.py` |
| Connectorcontract `/connector/v1` (read; propose/apply/rollback → `capability_unavailable`) | `api/connector.py` |
| Koppelen met de Hub (enrollment-token of koppelcode), Hub-JWT via JWKS, service-token | `hublink.py`, `auth.py` |
| Alles-in-één image (PostgreSQL 16 + API + worker) | `deploy/allinone/` |
| Installatie als standaard TrueNAS Custom App (formuliervelden, geen YAML) | [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md) |
| Eigen manifest | `deploy/manifest/appstore-en-fork.manifest.yaml` |
| OpenAPI | `docs/openapi/appstore-en-fork.openapi.json` |
| Besluiten | `docs/adr/ADR-0001` … `ADR-0006` |

## Installeren

Uitsluitend als **standaard TrueNAS Custom App** (image + tag, poort `30430`, opslag `/appdata`) — normaal door de Hub
via de TrueNAS-API, handmatig volgens [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md). Nooit YAML,
*Install via YAML*, `custom_compose_config` of docker compose. `deploy/dev/compose.yaml` is alleen voor ontwikkeling.

## Ontwikkelen

```
python3.12 -m venv .venv && .venv/bin/pip install -e "apps/api[dev]"
make lint typecheck test-unit
make test-integration          # PostgreSQL op 127.0.0.1:5432 (database appstore_test_<pid> wordt aangemaakt)
make test-e2e                  # echte processen + Hub-simulator
make image image-e2e           # alles-in-één image bouwen en testen (Docker)
```
Variabelen: [`.env.example`](.env.example). Licentie: nog niet gekozen (vraag aan de eigenaar).
