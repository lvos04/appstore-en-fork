# ADR-0004 — Vertaling naar TrueNAS `app.create`

- **Status:** aangenomen · 2026-11-09 · NIET GEVALIDEERD op een echte NAS
- **Context:** contract §2/§8: installatie via `app.create` als standaard Custom App (ix-app-waarden) of catalogusapp;
  nooit `custom_compose_config`. Methoden/schema's verschillen per TrueNAS-versie.
- **Besluit:**
  1. Bron: `truenas/apps` (ix-app 1.4.8, commit `f142a16a…`) en `truenas/middleware` (commit `ece9d190…`), zie
     `docs/TRUENAS_APP_VALUES.md`. `custom_app` → `{app_name, custom_app: false, catalog_app: "ix-app", train: "stable",
     version: <gemeten ix-app-versie>, values}`; `catalog_app` → `catalog_app/train/version` uit het manifest.
  2. Waarden zijn getypte pydantic-modellen (`extra=forbid`, `privileged: Literal[False]`); parameters worden als
     getypte waarde ingevuld, nooit via tekst-templating. Onbekende of ontbrekende wizardwaarden → blokkade.
  3. **Digest vastzetten:** het formulier kent alleen *Repository* en *Tag*; de bibliotheek bouwt `repo:tag`. Daarom
     `tag = "<versie>@sha256:<digest>"` zodat precies de gevalideerde image draait. Risico: de update-detectie van de
     TrueNAS-UI kan zo'n tag anders tonen — op een echte NAS te controleren (open punt).
  4. Zonder gemeten TrueNAS-versie (D01) of ix-app-versie: geen payload / niet gereed. Ondersteunde ix-app-reeks
     `>=1.2.0,<2.0.0`; andere versies binnen de reeks dan 1.4.8 → waarschuwing `ix_app_version_unverified`.
  5. Labels `nl.lvos04.platform.{app_id,manifest_sha256,managed_by}` voor herkenning bij import (B08-002).
  6. `gpu: intel_render` → `resources.gpus.use_all_gpus: true` (het formulier kan niet per GPU kiezen: A310 én UHD 770).
  7. SMB/NFS-opslag wordt niet ondersteund (vereist een wachtwoord in het formulier).
  8. De appstore voert niets uit; `preview` geeft `executed: false`. Uitvoering is aan de executor (R2 + goedkeuring),
     die het waardenschema op de NAS opnieuw moet controleren.
