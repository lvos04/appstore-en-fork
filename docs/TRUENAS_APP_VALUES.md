# TrueNAS `app.create`-waarden voor een standaard Custom App — bron en vertaling

**Status: NIET GEVALIDEERD op een echte NAS.** De structuur hieronder is nagelopen in de publieke broncode van
TrueNAS (alleen gelezen). Of jouw TrueNAS precies deze versie heeft, weet niemand tot de Hub het meet (D01): de
vertaling weigert daarom zonder gemeten TrueNAS- en ix-app-versie (`truenas_version_unknown`, `ix_app_version_unknown`).

## Bronnen (peildatum 2026-11-09)

| Bron | Versie / commit | Wat |
|---|---|---|
| `github.com/truenas/apps` → `ix-dev/stable/ix-app/` | ix-app **1.4.8**, `lib_version` 2.3.18, commit `f142a16a1e15da5f8517658f5a9ad34400972fc4` (2026-11-06) | `questions.yaml` (formuliervelden), `templates/docker-compose.yaml` (gebruik van de waarden), `app.yaml` (`min_scale_version: 24.10.2.2`), `app_migrations.yaml` (poortstructuur sinds 1.2.0) |
| `github.com/truenas/apps` → `library/2.3.18/validations.py` | idem | verboden hostpaden (`RESTRICTED`), apparaten die niet handmatig mogen (`/dev/dri`, `/dev/kfd`, `/dev/bus/usb`, `/dev/snd`, `/dev/net/tun`), geldige capabilities |
| `github.com/truenas/middleware` → `src/middlewared/middlewared/api/v28_0_0/app.py` en `v25_10_0/app.py` | commit `ece9d19057ea867ad16e94160bbf27862cc4183c` (2026-11-06) | `AppCreate`: `custom_app`, `values`, `custom_compose_config`, `custom_compose_config_string`, `catalog_app`, `app_name`, `train`, `version`; `AppDelete.remove_ix_volumes` |
| idem → `plugins/apps/crud.py`, `custom_app_utils.py` | idem | `custom_app=true` vereist `custom_compose_config` (= compose → **verboden**). Het formulier "Custom App" in de web-UI installeert de catalogusapp `ix-app` |

## Methode

```
app.create {
  "app_name": "<naam>",            # ^[a-z]([-a-z0-9]*[a-z0-9])?$, max 40
  "custom_app": false,             # NOOIT true (dat is custom_compose_config)
  "catalog_app": "ix-app",
  "train": "stable",
  "version": "<gemeten ix-app-versie>",
  "values": { …formulierwaarden… }
}
```

Catalogusapp (bijv. meerdere containers): `catalog_app`/`train`/`version` uit het manifest (vaste versie, nooit
`latest`) + `values` met getypte parameterwaarden.

## Formulierwaarden (ix-app 1.4.8) en hoe het manifest ze vult

| Formulier (UI) | `values`-sleutel | Uit manifest |
|---|---|---|
| Image → Repository / Tag / Pull Policy | `image.repository`, `image.tag`, `image.pull_policy` | `image.repository`; `image.tag` + `@<digest>` (ADR-0004, contract 1.3 §2.2) met digest-readback na installatie; altijd `missing` |
| Timezone | `TZ` | `custom_app.timezone` (parameter, wizard); zonder: `Etc/UTC` + waarschuwing |
| Environment Variables | `envs[] {name, value}` | `custom_app.env` (letterlijk of getypte parameter; een geheim alleen als vault-verwijzing naar een geheim dat de Hub maakt, ADR-0015) |
| Restart Policy (+ Maximum Retry Count) | `restart_policy` (`no`/`unless-stopped`/`on-failure`/`always`), `max_retry_count` alleen bij `on-failure` | `custom_app.restart_policy` |
| Disable Builtin Healthcheck / TTY / Stdin | `disable_builtin_healthcheck`, `tty`, `stdin` | altijd `false` |
| Devices | `devices[] {host_device, container_device}` | `privileges.devices` (met reden; `/dev/dri` nooit handmatig) |
| Privileged | `privileged` | altijd `false` (model staat niets anders toe) |
| Capabilities → Add | `capabilities.add[]` | `privileges.cap_add` (met reden; SYS_ADMIN e.d. verboden) |
| Custom User + User/Group ID | `run_as_custom_user`, `run_as {user, group}` | `custom_app.run_as` (`custom` → 568:568 e.d.; root kan niet) |
| Host Network | `host_network` | `network.host_network` (alleen met reden) |
| Ports | `ports[] {bind_mode: published, port_number, container_port, protocol, host_ips: []}` | `network.ports` (contract 1.3 §9: de enige bron; hostpoort via parameter, standaard het contractvoorstel) |
| Networks / Custom DNS | `networks: []`, `dns_config {nameservers, searches, options}` | leeg (geen gedeeld Docker-netwerk, contract §2) |
| Portals | `portals[] {name, scheme, use_node_ip: true, host: "", port, path}` | `custom_app.portal` |
| Storage → Host Path | `storage[] {type: host_path, read_only, mount_path, host_path_config {acl_enable: false, path}}` | `custom_app.storage` + `storage[].mount`; pad uit de datasetkiezer |
| Storage → ixVolume | `… {type: ix_volume, ix_volume_config {acl_enable: false, dataset_name}}` | idem |
| Storage → tmpfs | `… {type: tmpfs, tmpfs_config {size}}` (MiB) | alleen voor `kind: scratch` |
| Labels | `labels[] {key, value}` | `nl.lvos04.platform.app_id`, `.manifest_sha256`, `.managed_by` (herkenning bij import, B08-002) |
| Resources | `resources {enable_resource_limits, limits {cpus, memory (MB)}, gpus {use_all_gpus, nvidia_gpu_selection}}` | `custom_app.resources`; `custom_app.gpu` (letterlijk of `gpu`-parameter) = `intel_render` → `use_all_gpus: true`, alleen met door D08 gemeten render-node |
| Notes | `notes` | korte NL-notitie met manifest-hash |

SMB/NFS-opslag (`cifs`/`nfs`) wordt bewust niet ondersteund: die vereisen een wachtwoord in het formulier.

## Bekende eigenschappen van het standaardformulier (relevant voor veiligheid)

- ix-app 1.4.8 verwijdert `no-new-privileges` en de standaard `cap_drop: ALL` van de bibliotheek (`clear_caps()`,
  `remove_security_opt("no-new-privileges")`). Het formulier kan ze niet terugzetten. Eigen alles-in-één images
  zetten daarom zelf `PR_SET_NO_NEW_PRIVS` en laten alle capabilities vallen voordat ze naar 568 gaan (ADR-0003).
- `use_all_gpus` geeft alle niet-NVIDIA-GPU's (`/dev/dri`: A310 én UHD 770); kiezen per GPU kan het formulier niet.
- Hostpaden worden door TrueNAS zelf tegen o.a. `/`, `/mnt`, `/mnt/.ix-apps`, `/root`, `/home`, `/var/log` geweigerd;
  de manifestvalidator is strenger (ook `/etc`, `/var/run`, `/run`, sockets, alles buiten `/mnt/<pool>/<dataset>`).

## Compatibiliteit

- **Verdwijnende versies (contract 1.3 §2.2, bronwaarneming, NIET GEVALIDEERD):** in de gelezen catalogus-commit houdt
  een trein alleen de nieuwste versie per app. Zonder gemeten lijst (`target.available_versions`) waarschuwt de
  vertaling (`version_availability_unknown`); met een lijst waarin de vastgezette versie of elke geteste `ix-app`-versie
  ontbreekt, blokkeert ze (`version_unavailable`).

- Ondersteunde ix-app-reeks: `>= 1.2.0` en `< 2.0.0` (zelfde formulierstructuur); letterlijk nagelopen: `1.4.8`.
  Andere versies binnen de reeks geven `ix_app_version_unverified` (waarschuwing); de executor moet dan zelf het
  waardenschema op de NAS lezen (`catalog.get_app_details` / `app.latest`-versie) vóór uitvoering.
- Minimale TrueNAS-versie voor ix-app 1.4.8: `24.10.2.2`.
- Methodenamen en schema's verschillen per TrueNAS-versie: de executor ontdekt ze via `core.get_methods` (contract §8).
