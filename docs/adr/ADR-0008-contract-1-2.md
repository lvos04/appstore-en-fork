# ADR-0008 — Platformcontract 1.2 toepassen in appstore-en-fork

- **Status:** aangenomen · 2026-11-09 · Sim-getest (Hub-simulator volgens contract 1.2 §3.3) en Docker-getest; NIET
  GEVALIDEERD tegen de echte Hub (die 1.2 nog niet bouwde: H1–H6) en niet op een echte NAS.
- **Context:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` 1.2 en `CONTRACT_1.2_CONFORMITEIT.md` §3 (AS1–AS8, M1–M6) plus
  de gemeenschappelijke punten A4–A7. Deze ADR legt vast hoe dat hier gebouwd is en welke keuzes het contract open liet.

## 1. Manifeststandaard (M1–M6, eerst gebouwd: de manifesten in andere repos volgen pas daarna)

1. Geen nieuw `manifest_version`: `network.joins_platform_network` en `custom_app.ports` zijn uit `required`; aanwezig
   en onschadelijk → `deprecated_field` (waarschuwing), anders de bestaande fouten (`platform_network_not_supported`,
   `ports_inconsistent`). `--strict` maakt van elke waarschuwing een fout, dus ook van `deprecated_field`. De
   overgangsfixture `legacy-1-1-custom.manifest.yaml` bewaakt dat 1.1-manifesten geldig blijven.
2. GPU: nieuw parametertype `gpu`. Een `gpu`-parameter moet `source: wizard`, `decision: D08`, `required: false` hebben
   (`gpu_param_invalid`). Code `gpu_inconsistent` (eiste gelijkheid) is vervangen door `gpu_exceeds_privileges`
   (alleen méér vragen dan `privileges.gpu` is fout); `intel_render` zonder D08 in `decisions_required` →
   `gpu_decision_missing`.
3. Vertaling: het doel (`Target`) kreeg twee gemeten velden: `gpu_render_node` (`measured|absent|null`, D08) en
   `available_versions` (catalogusversies uit `catalog.get_app_details`). Zonder meting nooit een aanname: GPU →
   `gpu_not_measured` (blokkade); versies → `version_availability_unknown` (waarschuwing, de executor controleert vóór
   uitvoering). **Keuze (contract liet open):** bij een Custom App blokkeert `version_unavailable` als de gemeten
   `ix-app`-versie ontbreekt óf als geen enkele geteste `ix-app`-versie (`IX_APP_VERIFIED_VERSIONS`) meer in de lijst
   staat; een nog niet geteste versie naast een geteste blijft de bestaande waarschuwing `ix_app_version_unverified`.
4. De preview geeft altijd een `digest_readback` mee zodra er een digest is (`app.image.query` → `repo_digests`, stop +
   incident bij afwijking) en de terugvaltag zonder digest, omdat niet gevalideerd is dat het formulier `@` accepteert.
5. `requirements` (appdetail) toont `gpu` (het maximum) en nieuw `gpu_request` (letterlijk of de parameter).
