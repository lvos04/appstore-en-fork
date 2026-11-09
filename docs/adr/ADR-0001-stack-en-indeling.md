# ADR-0001 — Stack en repo-indeling

- **Status:** aangenomen · 2026-11-09
- **Context:** platformcontract 1.1 §1/§10 legt Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16 vast;
  alle schermen staan in de Hub-UI, andere diensten hebben alleen een kleine statuspagina.
- **Besluit:** pakket `appstore` in `apps/api/src`, migraties in `apps/api/alembic`, tests in `apps/api/tests/{unit,
  integration}` en `tests/e2e`. Eén proces-image (`deploy/allinone`) met API (uvicorn) en worker als aparte processen.
  Geen eigen web-UI; `GET /` is een statische NL-statuspagina zonder JavaScript of externe bronnen (CSP `default-src
  'none'`). Swagger-UI staat uit (geen CDN); `/openapi.json` en `docs/openapi/` blijven beschikbaar.
- **Gevolgen:** de Hub bouwt de catalogusschermen (Catalogus, Appdetail, Versies, …) op `/catalog/v1`. Afhankelijkheden
  staan exact vast in `pyproject.toml` en met hashes in `apps/api/requirements.lock`.
