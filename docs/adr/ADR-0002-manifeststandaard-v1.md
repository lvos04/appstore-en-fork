# ADR-0002 — Manifeststandaard v1 (B08-001)

- **Status:** aangenomen · 2026-11-09 · aangescherpt door [ADR-0007](ADR-0007-review-p0-validator-aanscherping.md);
  punt 6 en de GPU-regel bijgewerkt voor platformcontract 1.2 door [ADR-0008](ADR-0008-contract-1-2.md)
- **Context:** contract §9 en EX47: weigeren vóór uitvoering van compose/YAML, privileged, Docker-socket, gevaarlijke
  hostpaden, host network zonder reden, `latest`, ontbrekende digest; geen voorbeeldwaarden als echte waarde (D01–D18
  onbekend). Excel B08-001: "JSON Schema + signed manifest provenance + compatibility against TrueNAS release".
- **Besluit:**
  1. **JSON Schema is normatief** (`schemas/manifest-v1.schema.json`, draft 2020-12, byte-gelijke kopie in het pakket);
     pydantic-model voor getypte verwerking; semantische regels in `appstore.manifest.rules`. Validator → stabiele
     machineleesbare codes (`findings.CODES`) + NL-uitleg; CLI `jl-manifest`.
  2. **Veilige lader:** alleen YAML-basistypen, geen ankers/aliassen, geen dubbele sleutels, max 256 KiB, datums blijven tekst.
  3. **Strenger dan TrueNAS:** letterlijke hostpaden zijn altijd fout (poolnaam onbekend, D02); hostpaden komen uit een
     parameter met `source: dataset_picker` en een `dataset_role` gelijk aan `storage[].required_dataset_role`; bij
     vertaling moet het pad onder `/mnt/<pool>/<dataset>` liggen en buiten `/etc`, `/var/run`, `/run`, `/root`, `/mnt`,
     `/mnt/.ix-apps`, `/home`, … . Capabilities zoals `SYS_ADMIN`/`SYS_MODULE`/`SYS_PTRACE` zijn altijd verboden; andere
     alleen met `privilege_reason`. `/dev/dri` nooit handmatig (alleen `gpu: intel_render`).
  4. **Geheimen nooit in het formulier:** env met geheim-achtige naam en letterlijke waarde, of een `secret`/`generated`
     parameter in `custom_app.env`, is een fout. Apps maken hun geheimen zelf (contract §3).
  5. **Risico:** `risk.install/update/uninstall` ≥ R2 (minimum uit de executor-allowlist §8). `upgrade.migrations: true`
     met `tag_only_rollback_safe: true` is een fout (EX15).
  6. **Poorten (contract 1.2 §9):** `network.ports` is de enige bron; de vertaling bouwt daar de formulierpoorten uit.
     `custom_app.ports` en `network.joins_platform_network` zijn vervallen. Overgang zonder nieuw `manifest_version`:
     nog aanwezig en onschadelijk (`custom_app.ports` gelijk, `joins_platform_network: false`) → `deprecated_field`
     (waarschuwing, fout in `--strict`); afwijkende poorten → `ports_inconsistent`; `joins_platform_network: true` →
     `platform_network_not_supported` (contract §2: geen gedeeld Docker-netwerk). *(Tot 1.1: beide verplicht en gelijk.)*
  6a. **GPU (contract 1.2 §9):** `privileges.gpu` is het maximum; `custom_app.gpu` is `none`, `intel_render` of een
     verwijzing naar een parameter van type `gpu` (wizard, D08, niet verplicht; ontbreekt de waarde → `none`).
     `intel_render` vereist `privilege_reason` en D08 in `decisions_required`; meer vragen dan het maximum →
     `gpu_exceeds_privileges` (vervangt `gpu_inconsistent`, dat gelijkheid eiste).
  7. **`validation_status` in het manifest is een claim** en wordt genegeerd (waarschuwing); de catalogus bepaalt zelf.
  8. Eigen GHCR-images zonder digest: waarschuwing bij valideren, maar installeren is pas gereed met digest.
- **Gevolgen:** sommige bestaande apps (P1-import) zullen met hun huidige instellingen geweigerd worden; dat is bedoeld —
  die blijven dan *unmanaged* (B08-002) in plaats van met verlaagde regels beheerd.
