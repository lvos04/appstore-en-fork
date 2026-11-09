# appstore-en-fork — API-dienst

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16. Pakket `appstore`.

- `appstore.manifest` — manifeststandaard v1 (B08-001): JSON Schema, veilige lader, validator, vertaling naar
  TrueNAS `app.create` (standaard Custom App = `ix-app`, of catalogusapp), conflictanalyse, ondertekende provenance.
- `jl-manifest validate|translate|sign|verify` — CLI voor ontwikkeling/CI.
- `appstore serve|worker|migrate|openapi` — dienst (zie de README in de hoofdmap).
