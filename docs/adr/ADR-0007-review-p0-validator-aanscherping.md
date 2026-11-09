# ADR-0007 — Aanscherping van validator, vertaling en API na de review van fase P0

- **Status:** aangenomen · 2026-11-09 (onafhankelijke review/QA van fase P0)
- **Context:** een gerichte poging om de manifestvalidator te omzeilen vond echte lekken. Elk lek heeft nu een
  regressietest in `apps/api/tests/unit/test_manifest_bypass.py` of `apps/api/tests/integration/test_hardening.py`.
  Gevonden:
  1. `//mnt/.ix-apps/docker` als waarde van een `path`-parameter (datasetkiezer) werd geaccepteerd: POSIX
     `normpath` laat precies twee leidende slashes staan, dus de prefixvergelijking met `/mnt/.ix-apps` faalde. Zo kon
     een app de Docker-data en de app-configuraties (met geheimen) van alle apps koppelen. Idem `/mnt//pool`,
     `/mnt/pool/.zfs/snapshot`, `/mnt/pool/ix-applications`, newline/tab/spatie/backslash in het pad.
  2. Apparaten `/dev/./sda` en `/dev/../dev/sda` omzeilden de denylist (niet-normaliseerbaar pad viel terug op de
     ruwe tekst). De denylist liet verder o.a. `/dev/watchdog` (herstart de NAS), `/dev/ipmi0`, `/dev/mei0`,
     `/dev/vfio/*` (DMA), `/dev/mmcblk0`, `/dev/nbd0`, `/dev/tty0`, `/dev/input/*` en `/dev/uinput` door.
  3. Koppelpad `//etc` in de container.
  4. `NET_ADMIN` samen met `host_network` (wijzigt het netwerk van de NAS zelf).
  5. Tags `alpine-latest`, `stable-alpine`, `nightly-2024` en `alpine` werden niet als veranderlijk herkend.
  6. `$VAR`, `$(cmd)` en `$$` in omgevingswaarden: TrueNAS zet ze in een compose-bestand, waar interpolatie geldt.
  7. Catalogusapp `ix-app` met ruwe `values` omzeilde het getypte Custom App-model volledig. In `values` werden
     `privileged: "true"`/`1`, `pid`/`ipc`/`network_mode: host`, `security_opt: [seccomp=unconfined]`,
     `capabilities`/`cap_add`, `sysctls`, `devices: [/dev/sda]`, `command: [sh, -c, …]`, `docker_compose`/
     `compose_file` en een tekstparameter op een hostpad-veld geaccepteerd.
  8. YAML: een merge-sleutel `<<: {privileged: true}` zonder anker werd samengevoegd; een lijst of object als sleutel
     liet de lader crashen (`TypeError`), net als diepe nesting (`RecursionError`).
  9. API: gemanipuleerde cursors gaven 500 (`list_versions`, connector-`items`), een chunked verzoek zonder
     `Content-Length` omzeilde de bodylimiet van 1 MiB, en op de statuspagina kon met een willekeurige Hub worden
     gekoppeld, ook als de app met een vaste `HUB_URL` was geïnstalleerd.
  10. Integratietests werden stil overgeslagen (`skip`) als PostgreSQL ontbrak, ook in CI.
- **Besluit (veiligste uitleg van contract §9/§10 waar het contract zwijgt):**
  1. **Strikte padnormalisatie** (`rules.normalize_abs_path`): lege segmenten, `.`/`..`, stuur-/opmaaktekens
     (Unicode-categorie C*), backslashes en spaties aan begin/eind van een segment maken een pad ongeldig. Hostpaden
     worden hoofdletterongevoelig vergeleken (SMB-datasets kunnen hoofdletterongevoelig zijn). Verborgen mappen en de
     systeemdatasets `ix-applications`, `ix-apps` en `ix-virt` zijn verboden, ook via de datasetkiezer.
  2. **Apparaten via een allowlist** in plaats van een denylist: seriële USB-adapters (`ttyUSB`, `ttyACM`, `ttyAMA`,
     `serial/by-id|by-path`), video-capture (`video*`), DVB-tuners en de Coral-TPU (`apex_*`), altijd met
     `privilege_reason`. `ttyS*` (seriële console van de server) en `hidraw*` (toetsenborden) staan er bewust niet op.
     Een ander apparaat vereist eerst een contractwijziging.
  3. `NET_ADMIN` + `host_network` → `capability_forbidden`.
  4. **Veranderlijke tags:** een tag zonder exacte versie (`x.y.z`) is fout als een deel ervan `latest`, `stable`,
     `edge`, `nightly`, `main`, `master`, `dev`, `develop`, `rolling`, `current`, `head`, `testing`, `unstable` of `lts`
     is, of als er geen cijfer in staat. Voorversies (`2.0.0-rc.1`) en `x.y.z-lts` blijven geldig.
  5. **Geen interpolatie:** `$` gevolgd door `{`, `(`, een letter, `_` of `$` is fout (`interpolation_forbidden`) in
     omgevingswaarden, catalogus-waarden en ingevulde parameterwaarden.
  6. **Catalogusapps:** `catalog.app: ix-app` is fout (`install_method_forbidden`). Gevaarlijke compose-/Docker-velden in
     `values` (genormaliseerde naam, ongeacht hoofdletters, `-` of `_`) zijn fout zodra ze inhoud hebben
     (`catalog_value_forbidden`); `privileged` mag alleen `false` zijn; elk veld met `compose` in de naam is
     `compose_forbidden`. Na het invullen van parameters controleert `translate` de waarden opnieuw
     (verdediging in de diepte).
  7. **YAML-lader:** merge-sleutels (`yaml_merge_key_forbidden`), sleutels die geen tekst zijn (`yaml_key_not_string`)
     en nesting dieper dan 32 (`yaml_too_deep`) worden geweigerd zonder crash.
  8. **API:** cursors worden volledig gevalideerd (400 `cursor_invalid`); de bodylimiet telt de werkelijk ontvangen
     bytes (ASGI-middleware `BodyLimit`, 413 `payload_too_large`); als `HUB_URL` gezet is, accepteert de statuspagina
     alleen dat Hub-adres (`andere_hub`). Elke niet-publieke route wordt in een test zonder en met ongeldige auth
     aangeroepen (401) en met de rollen `kind`/geen/onbekend (403).
  9. **CI:** met `CI` of `APPSTORE_REQUIRE_DB=1` faalt de integratiesuite als PostgreSQL ontbreekt.
  10. **Conflictanalyse:** bij `host_network` tellen de containerpoorten als bezette NAS-poorten; een inventaris met een
      tijdstip in de toekomst is `unknown` (net als een verouderde).
- **Gevolgen:** manifesten van upstream-apps met een tag als `alpine` of een apparaat buiten de allowlist worden
  geweigerd en blijven bij import (B08-002) *unmanaged*. Dat is bewust: liever niet beheren dan met zwakkere regels.
  Een catalogusapp die een veld uit de verboden lijst echt nodig heeft, vereist eerst een contractwijziging.
- **Niet opgelost (bekend restrisico):** een `string`-parameter op een veld dat de catalogusapp intern als pad gebruikt
  maar dat niet `path`/`host_path` heet, kan de validator niet herkennen; de vertaling controleert alleen velden met
  die namen. Echte waardenschema's per catalogusapp (via `catalog.get_app_details` op de NAS) zijn nodig om dit te
  dichten — NIET GEVALIDEERD (echte NAS).
