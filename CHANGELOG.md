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
- Status: Sim-getest; NIET GEVALIDEERD op een echte NAS.
