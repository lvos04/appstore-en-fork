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
| B08-002 Import bestaande apps (Excel-prioriteit **P0**) | ontbrekend | alleen-lezen kern gebouwd bij de review: `POST /catalog/v1/import/preview` (labels → beheerd, catalogusapp/image → kandidaat, anders unmanaged; plant nooit acties). Ontbreekt: eigenaarbevestiging vastleggen (status *Geïmporteerd*) en de Hub-adapter die `app.query`/`app.config` levert | Sim-getest (classificatie); NIET GEVALIDEERD (echte NAS) |
| B08-003 Install preview/dependency | ontbrekend | deels (preview + conflictanalyse, geen DAG/egress/download-gate) | — |
| B08-004 Veilige updates | ontbrekend | ontbrekend | — |
| B08-005 Forkbeheer | ontbrekend | ontbrekend (P1) | — |
| B08-006 Uninstall/move/recovery | ontbrekend | ontbrekend | — |
