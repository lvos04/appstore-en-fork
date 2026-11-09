# Manifeststandaard v1 — handleiding voor app-auteurs (B08-001, platformcontract 1.2 §9)

Normatief: [`schemas/manifest-v1.schema.json`](../schemas/manifest-v1.schema.json) (JSON Schema draft 2020-12) plus de
regels in `apps/api/src/appstore/manifest/rules.py`. Keuzes en redenen: [ADR-0002](adr/ADR-0002-manifeststandaard-v1.md),
vertaling naar TrueNAS: [ADR-0004](adr/ADR-0004-truenas-vertaling.md) en [TRUENAS_APP_VALUES.md](TRUENAS_APP_VALUES.md).
Voorbeeld: [`deploy/manifest/appstore-en-fork.manifest.yaml`](../deploy/manifest/appstore-en-fork.manifest.yaml).

## Valideren

```
jl-manifest validate mijn-app.manifest.yaml            # Nederlandse uitvoer, exit 0 = geldig
jl-manifest validate --format json mijn-app.manifest.yaml
jl-manifest validate --strict …                         # waarschuwingen tellen als fout (release-CI)
jl-manifest translate mijn-app.manifest.yaml --params p.json --truenas-version <gemeten> --ix-app-version <gemeten>
```
Of via de API: `POST /catalog/v1/validate` en `POST /catalog/v1/preview` (voert niets uit).

## Opbouw in het kort

| Veld | Betekenis |
|---|---|
| `manifest_version` | altijd `"1"` |
| `app_id`, `name`, `owner_repo`, `category`, `description_nl`, `version` | identiteit; `app_id` = vaste naam van de app en voorgestelde TrueNAS-appnaam (`^[a-z]([-a-z0-9]*[a-z0-9])?$`, max 40) |
| `install.method` | `custom_app` (één image + formulierwaarden) of `catalog_app` (officiële TrueNAS-catalogus); compose/YAML is verboden |
| `image` | `repository` (zonder tag), `tag` (exacte versie, nooit `latest`), `digest` (`sha256:…`, verplicht voor upstream; eigen GHCR-images: release-CI) |
| `custom_app` | het standaardformulier: `storage` (host_path via parameter, ix_volume, tmpfs), `env`, `resources`, `gpu` (`none`, `intel_render` of `{param: …}` naar een `gpu`-parameter), `run_as`, `timezone`, `restart_policy`, `portal`; **geen** `ports` meer (1.2) |
| `catalog` + `values` | `train`/`app`/exacte `version` + waarden; parameters als `{"param": "<naam>"}` |
| `parameters` | getypt (`string, integer, boolean, port, path, dataset, url, timezone, duration, secret_ref, gpu`) met `source: wizard, dataset_picker, secret, generated, default`; alleen `default` mag een default hebben; `example` wordt nooit als waarde gebruikt |
| `storage` | `name`, `mount` (in de container), `kind: config, data, media, scratch`, `required_dataset_role` |
| `network` | `ports` — **de enige bron** voor de poorten van het formulier —, `host_network` (+ `host_network_reason`) |
| `privileges` + `privilege_reason` | `privileged: false`, `cap_add`, `devices`, `gpu: none, intel_render` (het **maximum**; `intel_render` vereist een reden én D08 in `decisions_required`); alles behalve niets vereist een reden |
| `healthcheck` | `http` (pad + poort), `tcp` (poort) of `builtin` (HEALTHCHECK van de image) |
| `backup` | `db_export` (`pg_dump, sqlite_backup, app_native, none`), `snapshot_datasets`, `rpo`/`rto` (ISO-8601 of parameter, D16) |
| `dependencies`, `sso`, `risk` (≥ R2), `upgrade` (`migrations`, `tag_only_rollback_safe`), `decisions_required` (D01–D18), `docs`, `validation_status`, `compatibility`, `provenance` | beheer en herkomst |

## Wijzigingen in contract 1.2 (overgang, ADR-0008)

- `network.joins_platform_network` **vervalt** (apps delen nooit een Docker-netwerk). Staat het er nog met `false`, dan
  geeft de validator `deprecated_field` (waarschuwing); met `true` blijft het de fout `platform_network_not_supported`.
- `custom_app.ports` **vervalt**: de vertaling bouwt de formulierpoorten uit `network.ports`. Staat het er nog en is het
  gelijk aan `network.ports`, dan `deprecated_field`; wijkt het af, dan de fout `ports_inconsistent`.
  `custom_app.portal.port` en `healthcheck.port` verwijzen naar een naam in `network.ports`.
- **GPU als optionele parameter:** `privileges.gpu` is het maximum. `custom_app.gpu` is `none`, `intel_render` of
  `{param: <naam>}` naar een parameter van type `gpu` (waarden `none|intel_render`, `source: wizard`,
  `decision: D08`, `required: false`); ontbreekt de waarde, dan `none`. Vraagt `custom_app.gpu` meer dan het maximum:
  `gpu_exceeds_privileges`. Bij de vertaling (`preview`) kan `intel_render` alleen als het doel een door D08 **gemeten**
  render-node meldt (`target.gpu_render_node: measured`), anders `gpu_not_measured`; met meting geeft de preview
  `gpu_all_render_devices` (het formulier zet `use_all_gpus` en kan niet per GPU kiezen; NIET GEVALIDEERD).
- `jl-manifest validate --strict` (release-CI) faalt op elke waarschuwing, dus ook op `deprecated_field`.
- **Vertaling:** de tag wordt `<tag>@sha256:<digest>`; de preview bevat een verplichte digest-readback
  (`digest_readback`: `app.image.query`, `repo_digests`, verwachte digest, terugvaltag zonder digest; afwijking → stop
  en incident). Meldt het doel welke versies de catalogus nog heeft (`target.available_versions`) en ontbreekt de
  vastgezette versie (of bij een Custom App elke geteste `ix-app`-versie), dan de blokkade `version_unavailable` met
  een voorstel; er wordt nooit stil een nieuwere versie gekozen. Niet gemeten: `version_availability_unknown`.

## Adoptiemanifest van een bestaande app (B08-002, ADR-0010)

Voor een **bestaande** app die de eigenaar heeft bevestigd, geeft `GET /catalog/v1/existing-apps/{id}/manifest` een
apart document met `kind: adoption` en `installable: false` (schema
[`schemas/adoption-manifest-v1.schema.json`](../schemas/adoption-manifest-v1.schema.json)). `install.method` volgt wat
de app **nu** is (`custom_app` met `image`, of `catalog_app` met `catalog`); poorten, opslag, digest en versie komen
uitsluitend uit de gemeten Hub-inventaris (een niet-gemeten digest blijft `null`). Het is alleen voor inventaris en
beslisregister: deze validator weigert het met `adoption_manifest_not_installable`, dus ook de preview — er wordt nooit
iets mee geïnstalleerd of herinstalleerd. Een app via *Install via YAML* (compose) krijgt geen adoptiemanifest.

## Weigerregels in detail (aangescherpt na de review van fase P0, ADR-0007)

- **Paden** worden eerst strikt genormaliseerd: geen `.`/`..`, geen lege segmenten (`//etc`, `/mnt//pool`), geen
  stuur- of opmaaktekens (newline, tab, bidi, zero-width), geen backslash en geen spaties aan begin of eind van een
  segment. Een hostpad moet onder `/mnt/<pool>/<dataset>` liggen; verborgen mappen (`.zfs`, `.system`, `.ix-apps`) en
  TrueNAS-systeemdatasets (`ix-applications`, `ix-apps`, `ix-virt`) zijn verboden, hoofdletterongevoelig vergeleken.
  Dezelfde controle geldt bij het invullen van een `path`-parameter (datasetkiezer) in `translate`/`preview`.
- **Apparaten**: alleen een allowlist, met `privilege_reason`: `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyAMA*`,
  `/dev/serial/by-id|by-path/…`, `/dev/video*`, `/dev/dvb/adapterN/…`, `/dev/apex_N`. GPU gaat via `gpu: intel_render`.
- **Capabilities**: `NET_ADMIN` samen met `host_network` is verboden.
- **Tags**: naast `latest`/`stable`/… ook samengestelde varianten (`alpine-latest`, `stable-alpine`, `nightly-2024`)
  en tags zonder versienummer (`alpine`, `bookworm`). Een tag met een exacte versie (`2.479.1-lts-jdk17`) mag wel.
- **Omgevingswaarden en catalogus-waarden**: geen variabele-interpolatie (`$VAR`, `${…}`, `$(…)`, `$$`); TrueNAS zet de
  waarden in een compose-bestand.
- **Catalogusapps**: `catalog.app: ix-app` is verboden (gebruik `install.method: custom_app`). In `values` zijn
  verboden: `privileged` (elke waarde behalve `false`), host network zonder reden, `pid`/`ipc`/`uts`/`userns`/
  `network_mode`, `security_opt`/`apparmor`/`seccomp` en elke `unconfined`-waarde, `cap_add`/`capabilities`, `sysctls`,
  `command`/`entrypoint`, elk veld met `compose` in de naam, apparaten buiten de allowlist en koppelpaden over
  systeemmappen. Een `{"param": …}` op een hostpad-veld moet een `path`-parameter uit de datasetkiezer zijn; apparaten
  en koppelpaden moeten letterlijk in het manifest staan. Na het invullen van de parameters worden de waarden
  opnieuw gecontroleerd.
- **YAML**: naast ankers/aliassen ook merge-sleutels (`<<`, ook zonder anker), sleutels die geen tekst zijn
  (`on:` → `True`, een lijst of object als sleutel) en nesting dieper dan 32 niveaus.

## Alle meldingscodes

| Code | Betekenis |
|---|---|
| `yaml_invalid` | Het bestand is geen geldige YAML. |
| `yaml_alias_forbidden` | YAML-ankers/aliassen zijn niet toegestaan. |
| `yaml_duplicate_key` | Een sleutel komt dubbel voor. |
| `yaml_custom_tag_forbidden` | Eigen YAML-tags zijn niet toegestaan. |
| `yaml_merge_key_forbidden` | YAML-merge-sleutels (<<) zijn niet toegestaan. |
| `yaml_key_not_string` | Een sleutel moet tekst zijn. |
| `yaml_too_deep` | Het manifest is te diep genest. |
| `manifest_too_large` | Het manifest is te groot. |
| `manifest_not_mapping` | Het manifest moet een object (mapping) zijn. |
| `required_field_missing` | Verplicht veld ontbreekt. |
| `unknown_field` | Onbekend veld. |
| `wrong_type` | Verkeerd type. |
| `invalid_value` | Ongeldige waarde. |
| `invalid_format` | Ongeldig formaat. |
| `out_of_range` | Waarde buiten het toegestane bereik. |
| `forbidden_combination` | Deze velden mogen niet samen voorkomen. |
| `model_mismatch` | Intern: schema en model oordelen verschillend. |
| `deprecated_field` | Vervallen veld uit een eerdere contractversie (waarschuwing; fout in --strict). |
| `compose_forbidden` | Compose/YAML-installatie is verboden. |
| `install_method_forbidden` | Installatiemethode is niet toegestaan. |
| `adoption_manifest_not_installable` | Adoptiemanifest van een bestaande app: alleen inventaris, nooit installeren. |
| `tag_missing` | Image-tag ontbreekt. |
| `mutable_tag_forbidden` | Veranderlijke tag (zoals latest) is verboden. |
| `tag_in_repository` | Tag of digest hoort niet in het repository-veld. |
| `digest_missing` | Digest ontbreekt voor een upstream-image. |
| `digest_pending_release` | Digest van eigen image wordt door de release-CI ingevuld. |
| `imprecise_version_tag` | Tag lijkt geen exacte versie. |
| `mutable_version_forbidden` | Catalogusversie moet exact zijn. |
| `privileged_forbidden` | Privileged-modus is verboden. |
| `docker_socket_forbidden` | Docker-socket in een container is verboden. |
| `capability_forbidden` | Deze Linux-capability is verboden. |
| `capability_without_reason` | Extra capability zonder privilege_reason. |
| `capability_unknown` | Onbekende Linux-capability. |
| `device_forbidden` | Dit apparaat mag niet aan een container worden gegeven. |
| `device_without_reason` | Apparaat zonder privilege_reason. |
| `gpu_exceeds_privileges` | custom_app.gpu vraagt meer GPU dan privileges.gpu toestaat. |
| `gpu_without_reason` | GPU-toegang zonder privilege_reason. |
| `gpu_decision_missing` | GPU-toegang zonder D08 in decisions_required. |
| `gpu_param_invalid` | GPU-parameter moet uit de wizard komen, met D08 en niet verplicht. |
| `run_as_image_default_upstream` | Upstream-image draait als de standaardgebruiker van de image. |
| `host_network_without_reason` | Host network zonder host_network_reason. |
| `host_network_requested` | Host network gevraagd. |
| `platform_network_not_supported` | Apps zitten niet in een gedeeld Docker-netwerk. |
| `ports_inconsistent` | Vervallen custom_app.ports wijkt af van network.ports. |
| `duplicate_port` | Poort komt dubbel voor. |
| `port_privileged_range` | Hostpoort onder 1024. |
| `literal_host_path` | Hostpad moet uit de datasetkiezer komen. |
| `forbidden_host_path` | Hostpad is verboden. |
| `forbidden_mount_path` | Koppelpad in de container is verboden. |
| `dataset_role_missing` | Opslag zonder datasetrol. |
| `dataset_role_inconsistent` | Datasetrol van parameter en opslag verschilt. |
| `storage_unknown_reference` | Opslag verwijst naar onbekende naam. |
| `storage_not_mapped` | Gedeclareerde opslag heeft geen formulierregel. |
| `duplicate_name` | Naam komt dubbel voor. |
| `host_path_param_wrong_source` | Hostpad-parameter moet uit de datasetkiezer komen. |
| `unknown_parameter` | Verwijzing naar onbekende parameter. |
| `parameter_type_mismatch` | Parameter heeft het verkeerde type voor dit veld. |
| `parameter_default_missing` | Parameter met source 'default' zonder default. |
| `parameter_default_not_allowed` | Default is niet toegestaan bij deze source. |
| `parameter_unused` | Parameter wordt nergens gebruikt. |
| `example_value_as_real` | Voorbeeldwaarde gebruikt als echte waarde. |
| `secret_literal_forbidden` | Geheim als letterlijke waarde in het manifest. |
| `secret_in_form` | Geheim zou in het Custom App-formulier terechtkomen. |
| `interpolation_forbidden` | Variabele-interpolatie ($VAR, ${…}, $(…)) is niet toegestaan. |
| `decision_not_listed` | Besluit ontbreekt in decisions_required. |
| `risk_below_policy_minimum` | Risicoklasse lager dan het platformminimum. |
| `tag_only_rollback_with_migrations` | Tag-only rollback met databasemigraties is onveilig. |
| `backup_unknown_dataset` | Back-up verwijst naar onbekende opslag. |
| `backup_dataset_not_covered` | Data-opslag zit niet in de snapshotlijst. |
| `backup_db_export_none` | Geen database-export opgegeven. |
| `healthcheck_unknown_port` | Healthcheck verwijst naar onbekende poort. |
| `portal_unknown_port` | Portal verwijst naar onbekende poort. |
| `dependency_self` | App hangt van zichzelf af. |
| `compatibility_missing` | Geen TrueNAS-compatibiliteit opgegeven. |
| `validation_status_claim_ignored` | Geclaimde validatiestatus wordt niet overgenomen. |
| `timezone_missing` | Tijdzone ontbreekt. |
| `catalog_value_forbidden` | Verboden instelling in de waarden van een catalogusapp. |
| `signature_missing` | Handtekening ontbreekt terwijl de bron die vereist. |
| `signature_invalid` | Handtekening klopt niet. |
| `app_id_claimed_by_other_source` | App-ID hoort bij een andere catalogusbron. |
| `parameter_value_missing` | Waarde voor een verplichte parameter ontbreekt. |
| `parameter_value_invalid` | Waarde voor een parameter is ongeldig. |
| `parameter_value_unknown` | Waarde opgegeven voor een onbekende parameter. |
| `parameter_value_not_allowed` | Deze parameter mag niet via de installatie worden meegegeven. |
| `manifest_invalid` | Het manifest is niet geldig; vertalen is geweigerd. |
| `ix_app_version_unknown` | Versie van de TrueNAS-catalogusapp ix-app is onbekend. |
| `ix_app_version_unsupported` | Versie van ix-app wordt niet ondersteund. |
| `ix_app_version_unverified` | Waardenstructuur van deze ix-app-versie is niet geverifieerd. |
| `truenas_version_unknown` | TrueNAS-versie is onbekend (D01). |
| `truenas_version_incompatible` | App is niet compatibel met deze TrueNAS-versie. |
| `truenas_version_invalid` | TrueNAS-versie heeft een onbekend formaat. |
| `digest_not_pinned` | Image zonder digest; installatie zou een veranderlijke tag gebruiken. |
| `version_unavailable` | De vastgezette versie staat niet (meer) in de gemeten TrueNAS-catalogus. |
| `version_availability_unknown` | Niet gemeten welke versies de TrueNAS-catalogus nog heeft. |
| `gpu_not_measured` | GPU gevraagd, maar D08 heeft geen render-node gemeten. |
| `gpu_all_render_devices` | De app krijgt elke render-GPU van de NAS (het formulier kan niet per GPU kiezen). |
| `inventory_missing` | Geen gemeten TrueNAS-inventaris; conflicten zijn onbekend. |
| `inventory_stale` | TrueNAS-inventaris is verouderd. |
| `app_name_taken` | Er bestaat al een app met deze naam. |
| `port_in_use` | Hostpoort is al in gebruik. |
| `port_reserved_platform` | Hostpoort is gereserveerd voor een platform-app. |
| `host_path_in_use` | Hostpad wordt al door een andere app gebruikt. |
| `host_path_overlap` | Hostpad overlapt met dat van een andere app. |
| `dependency_missing` | Vereiste afhankelijkheid is niet geïnstalleerd. |
| `dependency_optional_missing` | Optionele afhankelijkheid is niet geïnstalleerd. |
| `dependency_version_unknown` | Versie van een afhankelijkheid is onbekend. |
| `dependency_version_too_low` | Versie van een afhankelijkheid is te laag. |
