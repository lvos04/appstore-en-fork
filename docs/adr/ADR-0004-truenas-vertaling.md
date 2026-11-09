# ADR-0004 — Vertaling naar TrueNAS `app.create`

- **Status:** aangenomen · 2026-11-09 · NIET GEVALIDEERD op een echte NAS · punten 3, 6, 9 en 10 bijgewerkt voor
  platformcontract 1.2 ([ADR-0008](ADR-0008-contract-1-2.md))
- **Context:** contract §2/§8: installatie via `app.create` als standaard Custom App (ix-app-waarden) of catalogusapp;
  nooit `custom_compose_config`. Methoden/schema's verschillen per TrueNAS-versie.
- **Besluit:**
  1. Bron: `truenas/apps` (ix-app 1.4.8, commit `f142a16a…`) en `truenas/middleware` (commit `ece9d190…`), zie
     `docs/TRUENAS_APP_VALUES.md`. `custom_app` → `{app_name, custom_app: false, catalog_app: "ix-app", train: "stable",
     version: <gemeten ix-app-versie>, values}`; `catalog_app` → `catalog_app/train/version` uit het manifest.
  2. Waarden zijn getypte pydantic-modellen (`extra=forbid`, `privileged: Literal[False]`); parameters worden als
     getypte waarde ingevuld, nooit via tekst-templating. Onbekende of ontbrekende wizardwaarden → blokkade.
  3. **Digest vastzetten:** het formulier kent alleen *Repository* en *Tag*; de bibliotheek bouwt `repo:tag`. Daarom
     `tag = "<versie>@sha256:<digest>"` zodat precies de gevalideerde image draait (contract 1.2 §2.2). Risico: de
     update-detectie van de TrueNAS-UI kan zo'n tag anders tonen en of het formulier `@` accepteert is onbekend —
     NIET GEVALIDEERD (echte NAS). Daarom geeft de preview altijd een **digest-readback** mee (`digest_readback`:
     `app.image.query` → `repo_digests` moet de verwachte digest bevatten, anders stop + incident) en de terugvaltag
     zonder digest; met die terugval is de readback de enige controle.
  4. Zonder gemeten TrueNAS-versie (D01) of ix-app-versie: geen payload / niet gereed. Ondersteunde ix-app-reeks
     `>=1.2.0,<2.0.0`; andere versies binnen de reeks dan 1.4.8 → waarschuwing `ix_app_version_unverified`.
  5. Labels `nl.lvos04.platform.{app_id,manifest_sha256,managed_by}` voor herkenning bij import (B08-002).
  6. GPU (contract 1.2 §9): de waarde komt uit `custom_app.gpu` (letterlijk of een `gpu`-parameter; zonder waarde
     `none`). `intel_render` → `resources.gpus.use_all_gpus: true`, maar **alleen** als het doel een door D08 gemeten
     render-node meldt (`target.gpu_render_node: measured`; anders blokkade `gpu_not_measured`). Het formulier kan
     niet per GPU kiezen; de preview meldt `gpu_all_render_devices` (NIET GEVALIDEERD).
  7. SMB/NFS-opslag wordt niet ondersteund (vereist een wachtwoord in het formulier).
  8. De appstore voert niets uit; `preview` geeft `executed: false`. Uitvoering is aan de executor (R2 + goedkeuring),
     die het waardenschema op de NAS opnieuw moet controleren.
  9. **Poorten** komen alleen uit `network.ports` (contract 1.2 §9); een vervallen `custom_app.ports` wordt niet gebruikt.
  10. **Verdwenen versies (contract 1.2 §2.2):** meldt het doel de gemeten versies in de catalogus
      (`target.available_versions`, `catalog.get_app_details`), dan geeft een vastgezette catalogusversie die er niet
      meer in staat — of bij een Custom App een gemeten `ix-app`-versie die ontbreekt, of geen enkele geteste
      `ix-app`-versie meer — de blokkade `version_unavailable` met een voorstel (manifest-update met nieuwe vaste versie
      + test, of de vertaling voor de nieuwe `ix-app`-versie testen). Nooit stil een nieuwere versie of `latest`. Niet
      gemeten → waarschuwing `version_availability_unknown`; de executor controleert dan vóór uitvoering.
