# ADR-0002 — Manifeststandaard v1 (B08-001)

- **Status:** aangenomen · 2026-11-09
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
  6. **Dubbele poortopgave:** het contract vraagt zowel `custom_app.ports` (formulier) als `network.ports`; ze moeten gelijk
     zijn (`ports_inconsistent`). `joins_platform_network: true` is een fout (contract §2: geen gedeeld Docker-netwerk).
  7. **`validation_status` in het manifest is een claim** en wordt genegeerd (waarschuwing); de catalogus bepaalt zelf.
  8. Eigen GHCR-images zonder digest: waarschuwing bij valideren, maar installeren is pas gereed met digest.
- **Gevolgen:** sommige bestaande apps (P1-import) zullen met hun huidige instellingen geweigerd worden; dat is bedoeld —
  die blijven dan *unmanaged* (B08-002) in plaats van met verlaagde regels beheerd.
