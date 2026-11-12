# Manifeststandaard v1 — handleiding voor app-auteurs (B08-001, platformcontract 1.3 §9)

Normatief: [`schemas/manifest-v1.schema.json`](../schemas/manifest-v1.schema.json) (JSON Schema draft 2020-12) plus de
regels in `apps/api/src/appstore/manifest/rules.py`; controlelijsten zonder installatie:
[`schemas/checklist-v1.schema.json`](../schemas/checklist-v1.schema.json). Keuzes en redenen: [ADR-0002](adr/ADR-0002-manifeststandaard-v1.md),
vertaling naar TrueNAS: [ADR-0004](adr/ADR-0004-truenas-vertaling.md), geheimen in de vertaling:
[ADR-0015](adr/ADR-0015-vault-verwijzing-geheimen-in-de-vertaling.md) en [TRUENAS_APP_VALUES.md](TRUENAS_APP_VALUES.md),
geheimen en accountgegevens weigeren: [ADR-0016](adr/ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md)
(W2, W3) en [ADR-0017](adr/ADR-0017-kruisverwijzing-en-bcrypt-vormen-weigeren.md).
Voorbeeld: [`deploy/manifest/appstore-en-fork.manifest.yaml`](../deploy/manifest/appstore-en-fork.manifest.yaml).

## Valideren

```
jl-manifest validate mijn-app.manifest.yaml            # Nederlandse uitvoer, exit 0 = geldig
jl-manifest validate --format json mijn-app.manifest.yaml
jl-manifest validate --strict …                         # release-CI: elke niet-geaccepteerde waarschuwing is een fout
jl-manifest checklist I01_TRUENAS_BASIS.checklist.yaml # kind: checklist (I-document zonder installatie)
jl-manifest translate mijn-app.manifest.yaml --params p.json --truenas-version <gemeten> --ix-app-version <gemeten>
```
Of via de API: `POST /catalog/v1/validate` en `POST /catalog/v1/preview` (voert niets uit).

## Opbouw in het kort

| Veld | Betekenis |
|---|---|
| `manifest_version` | altijd `"1"` |
| `app_id`, `name`, `owner_repo`, `category`, `description_nl`, `version` | identiteit; `app_id` = vaste naam van de app en voorgestelde TrueNAS-appnaam (`^[a-z]([-a-z0-9]*[a-z0-9])?$`, max 40) |
| `install.method` (+ `install.reason`) | `custom_app` (één image + formulierwaarden; standaard, ook voor derden-apps met één container) of `catalog_app` (officiële TrueNAS-catalogus, met reden: `meerdere_containers` of een NL-reden); compose/YAML is verboden |
| `image` | `repository` (zonder tag), `tag` (exacte versie, nooit `latest`), `digest` (`sha256:…`, verplicht voor upstream; eigen GHCR-images: release-CI) |
| `custom_app` | het standaardformulier: `storage` (host_path via parameter, ix_volume, tmpfs), `env`, `resources`, `gpu` (`none`, `intel_render` of `{param: …}` naar een `gpu`-parameter), `run_as`, `timezone`, `restart_policy`, `portal`; **geen** `ports` meer (1.2) |
| `catalog` + `values` | `train`/`app`/exacte `version` + waarden; parameters als `{"param": "<naam>"}` |
| `parameters` | getypt (`string, integer, boolean, port, path, dataset, url, timezone, duration, secret_ref, gpu`) met `source: wizard, dataset_picker, secret, generated, default`; alleen `default` mag een default hebben; `example` wordt nooit als waarde gebruikt; `secret: true` + `source: generated` = een geheim dat de Hub maakt (vault); een naam die met `HUB_` begint is gereserveerd |
| `storage` | `name`, `mount` (in de container), `kind: config, data, media, scratch`, `required_dataset_role` |
| `network` | `ports` — **de enige bron** voor de poorten van het formulier; `container_port` mag bij een catalogusapp `same_as_host` zijn —, `host_network` (+ `host_network_reason`) |
| `privileges` + `privilege_reason` | `privileged: false`, `cap_add`, `devices`, `gpu: none, intel_render` (het **maximum**; `intel_render` vereist een reden én D08 in `decisions_required`); alles behalve niets vereist een reden |
| `healthcheck` | `http` (pad + poort), `tcp` (poort) of `builtin` (HEALTHCHECK van de image) + `monitor` (monitorplan) |
| `backup` | `db_export.method` (`pg_dump, sqlite_backup, app_native, stopped_snapshot, none`), `snapshot_datasets`, `rpo`/`rto` (ISO-8601 of parameter, D16) |
| `accepted_findings`, `post_install_checks` | geaccepteerde waarschuwingen uit de vaste lijst; taken voor de beheerder na installatie (contract 1.3) |
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

## Wijzigingen in contract 1.3 (validator eerst, ADR-0013)

Oude manifesten blijven tijdens de overgang **geldig**; de nieuwe eisen geven waarschuwingen, die in `--strict`
(release-CI) een fout zijn. Zo kan elke repo zijn manifest bijwerken nadat deze validator er is.

- **Catalogusapp met reden:** `install.method: catalog_app` vraagt `install.reason` (`meerdere_containers` of een
  NL-reden waarom één container een catalogusapp nodig heeft); ontbreekt hij → `install_reason_ontbreekt`. Een
  upstream-image als Custom App noemt in `privilege_reason` het restrisico "zonder no-new-privileges kan een
  setuid-programma binnen de container root worden" (anders `restrisico_nnp_ontbreekt`).
- **Gereserveerde namen:** een parameter- of `custom_app.env`-naam die met `HUB_` begint (ook een sleutel of `name` in
  de `values` van een catalogusapp) → fout `reserved_env`. `HUB_URL`, `HUB_ENROLLMENT_TOKEN` en
  `HUB_KEY_FINGERPRINT` zet de vertaling zelf als de Hub koppelt. Komt zo'n naam via een parameter binnen (bijvoorbeeld
  als `name` van een naam/waarde-paar uit de wizard), dan geeft de vertaling na het invullen `reserved_env` en is er
  geen payload (ADR-0018).
- **Geheimen die de Hub maakt:** `source: generated` + `secret: true` (contract 1.3 §3.1 a). De waarde staat nooit in
  het manifest (geen `default` of `example`, anders `secret_literal_forbidden`); in het formulier of de `values` geeft
  dat de **waarschuwing** `secret_in_form` (te accepteren) in plaats van een fout, behalve onder de naam van een
  beheerder of dienstaccount (dan de fout `account_gegevens_verboden`, zie hieronder). `secret: true` met een andere
  `source` dan `generated`/`secret` → `secret_param_invalid`. De vertaling zet voor een geheim alleen een verwijzing
  naar de vault van de Hub, nooit een waarde (P2, C1.3-ASF-14; zie "Geheimen in de vertaling" hieronder).
- **`accepted_findings: [{code, reden_nl}]`** — alleen `backup_db_export_none`, `secret_in_form` (alleen bij een
  geheim dat de Hub maakt) en `digest_pending_release` (alleen vóór de eerste release van een eigen image).
  `--strict` faalt op elke andere waarschuwing én op een geaccepteerde code die niet (meer) optreedt
  (`accepted_finding_unused`). Fouten zijn nooit te accepteren. Een losse uitzonderingenlijst buiten het manifest
  vervalt. In de uitvoer heet zo'n bevinding `GEACC.` (`accepted: true`, `accepted_reden_nl`); het rapport heeft
  `accepted` en `strict_valid`.
- **`backup.db_export.method: stopped_snapshot`** (app stoppen + ZFS-snapshot) voor apps zonder exportfunctie; vraagt
  minstens één dataset in `snapshot_datasets` (anders `stopped_snapshot_without_dataset`); de stilstand telt mee in
  `rto`.
- **`post_install_checks: [{id, soort: http|handmatig, beschrijving_nl}]`** voor open eerste-gebruik-vensters en
  runtime-kenmerken; de Hub toont ze na installatie als taak; tot de beheerder ze bevestigt is de app niet "gezond".
- **`network.ports[].container_port: same_as_host`** alleen bij een catalogusapp waarvan de container op de gekozen
  hostpoort luistert (anders `same_as_host_not_allowed`).
- **Monitorplan:** `healthcheck.monitor: {pad, verwachte_status, timeout_s, interval_s}`; ontbreekt → waarschuwing
  `healthcheck_monitor_missing`. De Hub (B09) maakt er een monitorplan van; de beheerder neemt het over in Uptime Kuma
  en bevestigt het per monitor ("handmatig bevestigd", nooit "gevalideerd door de Hub").
- **Documentsoort `kind: checklist`** (`install: {method: none}`) voor I-documenten zonder installatie (bv. I01, I02),
  met een eigen schema ([`schemas/checklist-v1.schema.json`](../schemas/checklist-v1.schema.json), afgeleid van het
  formaat van jelly-leon-platform, met `kind` en `install` erbij). Valideren met `jl-manifest checklist`; de validator
  van manifest v1 weigert hem (`checklist_not_installable`). Regels: stap-ID's horen bij het document, besluiten van een
  stap staan in `besluiten`, alleen-lezen TrueNAS-methoden, geen voorbeeldwaarden.

## Geheimen in de vertaling (C1.3-ASF-14, ADR-0015)

Een geheime parameter (`source: generated` of `secret`, `type: secret_ref` of `secret: true`) wordt nooit ingevuld.
`jl-manifest translate` en `POST /catalog/v1/preview` zetten op zijn plek alleen de tekst
`vault://app/<app_id>/<parameter>`, met de vaste `app_id` uit het manifest (nooit de gekozen appnaam). De Hub maakt en
bewaart de waarde (contract 1.3 §3.1 a) en de executor vult haar in.

- **Alleen bij een vaste `app_id`:** een los manifest kan zich de `app_id` van een andere app geven. Een verwijzing
  komt er alleen bij `POST /catalog/v1/preview` met `app_id` of `version_id` van een geldige catalogusversie (die bij
  een catalogus-entry met dezelfde `app_id` hoort) en bij `jl-manifest translate` (eigen bestand, installeert niets).
  `POST /catalog/v1/preview` met `manifest_yaml` geeft voor een geheim `parameter_value_not_allowed`. `vault_refs` is
  geen autorisatie: de Hub bindt de `app_id` aan de catalogusversie en aan het doel van de actie.
- **Waar:** in de `values` van een catalogusapp, en in een omgevingsvariabele van een Custom App
  (`custom_app.env[].value_from`) alleen naar een geheim dat de Hub maakt (`source: generated` + `secret: true`;
  besluit van de gebruiker van 2026-11-11, dat afwijkt van contract 1.3 §3.1 "nooit geheimen in het Custom
  App-formulier" — de contractwijziging volgt in ai-server-manager). Een geheim op een plek die een getypte waarde vraagt
  (hostpoort, hostpad, tijdzone, GPU) of van een ander type dan `string`/`secret_ref` blijft geweigerd
  (`parameter_value_not_allowed`), net als een verwijzing onder een sleutel met `.`, `[` of `]` (dubbelzinnig pad) of
  onder een sleutel `image`, `images`, `TZ`, `notes` of `labels` (openbaar of gelogd).
- **Opgegeven waarden:** voor een geheime parameter mag hooguit precies de eigen verwijzing worden meegegeven. Een
  geheime waarde, een verwijzing naar een andere app of een andere parameter, of iets anders → `parameter_value_not_allowed`,
  ook voor een geheime parameter die het manifest niet gebruikt (`parameters.<naam>`).
  De melding noemt de parameter, nooit de waarde; de waarde staat ook niet in het antwoord, het bewijs of de log.
- **Markering:** de uitvoer heeft `vault_refs`: per verwijzing precies `{path, parameter, ref}`, bijvoorbeeld
  `{"path": "values.envs[2].value", "parameter": "api_key", "ref": "vault://app/<app_id>/api_key"}`. Zonder payload is de
  lijst leeg. De `preview_hash` gaat over de payload met de verwijzingen. `path` is eenduidig: elk deel tussen de punten
  is een sleutel zonder `.`, `[` of `]`, `[i]` is een lijstpositie.
- **Alleen de vertaling maakt een verwijzing:** een letterlijke `vault://`-tekst (ook in een sleutel) in de `values` of
  in `custom_app.env[].value` → `vault_ref_not_allowed` (geen payload); de controle vergelijkt de echte plek, niet het
  pad als tekst. Een gewone parameter met `vault://` in de waarde → `parameter_value_invalid`.
- **Opnieuw getoetst na het invullen (ADR-0016 W3, ADR-0018):** de vertaling toetst de ingevulde omgevingswaarden
  (`values.envs` van een Custom App) en de `values` van een catalogusapp opnieuw met de regels hieronder. Een geheimplek
  met een waarde die geen eigen vault-verwijzing van de vertaling is → `secret_in_form`; een accountplek met een waarde,
  ook een vault-verwijzing → `account_gegevens_verboden` (ook als het accountwoord in een voorouder-sleutel staat, zoals
  `admin.password`); T1 en T2 op elke ingevulde tekst (bijvoorbeeld een wachtwoordhash uit de wizard) →
  `account_gegevens_verboden`; een parameter met een accountnaam, of een gewone parameter met een geheimnaam, wordt niet
  ingevuld (`account_gegevens_verboden` of `secret_in_form`); een parameter met een geheimnaam op een plek waarvan de
  eigen naam of een voorouder een accountwoord heeft (`admin: {param: db_password}`, ook als vault-verwijzing) →
  `account_gegevens_verboden` (ADR-0019), net als een parameter met een geheimmarkering op zo'n plek (`admin.pw` naar
  `init_value`; ADR-0021); een `HUB_*`-naam die de vertaling niet zelf zette →
  `reserved_env`. Dan is er geen payload; de melding noemt het pad, nooit de waarde. Alleen de naam
  `HUB_ENROLLMENT_TOKEN` mag een geheim dragen (het eenmalige enrollment-token, contract 1.3 §3.1), en alleen als de
  vertaling hem zelf op die plek zette (dat doet zij nu nooit); T1 en T2 gelden ook daar. `HUB_TOKEN`, `HUB_SECRET` en
  elke andere geheimnaam niet. Er komt geen veld bij in de uitvoer van `translate` of `preview`.
- **Grenzen (ADR-0019):** hooguit 256 plekken krijgen een parameterwaarde, en de payload is hooguit 256 KiB (canonieke
  JSON, zoals voor `preview_hash`; even groot als het grootste manifest dat de lader aanneemt). Daarboven →
  `vertaling_te_groot`, zonder payload (fail-closed); na de 256e plek vult de vertaling niets meer in. De bestaande
  manifesten hebben hooguit 11 plekken en een payload van een paar KiB.

## Geheimen en accountgegevens weigeren (ADR-0016 W2, ADR-0017, ADR-0018, ADR-0019, ADR-0020, ADR-0021)

Een account (beheerder of dienstaccount) gaat nooit door het manifest, het formulier of Git (ADR-0016, besluit 1).
Daarom weigert de validator geheimen en accountgegevens overal, ook als het manifest geen `accounts` heeft. De melding
noemt het pad en de reden, nooit de waarde; ook een schemafout (`invalid_format`, `invalid_value`) noemt een waarde
niet als deze regels haar herkennen. De naam van de app (`name` bovenaan) is daarbij geen plek met een naam: een
schemafout verbergt een waarde alleen onder een plek met een geheimnaam (of onder `accounts`) of als T1/T2 haar
herkent (ADR-0018). Een
sleutel met een `=` of met iets dat T1/T2 herkent, staat in pad en melding als `<verborgen>` (ook bij `unknown_field`),
en een herkende waarde in `app_id`, `version` of `install.method` staat niet in het rapport (het veld is dan leeg).
Een sleutel van meer dan 256 tekens staat in pad en melding ook als `<verborgen>` (één vaste vorm; de regels zelf
lezen de hele sleutel), en een pad van meer dan 1024 tekens staat ingekort in de melding: de eerste 1024 tekens en dan
`…` (ADR-0021). Zo blijft een melding klein, hoe lang een sleutel ook is en hoeveel meldingen er ook zijn.

- **Geheimnaam:** een naam waarin `PASS`, `PASSWD`, `PASSWORD`, `SECRET`, `TOKEN`, `API_KEY`, `APIKEY`, `PRIVATE_KEY`,
  `PRIVATEKEY`, `CREDENTIAL`, `AUTH_KEY`, `SALT` of `WACHTWOORD` staat, gevolgd door het einde van de naam of door `_`,
  `-` of `.` (hoofdletterongevoelig), of door een hoofdletter na een kleine letter (camelCase: `secretKey`,
  `passwordHash`, `apiKeyValue`), of door een cijfer (`DB_PASSWORD2`, `ADMIN_TOKEN1`; ADR-0018). Niet: `SECRETS_DIR`,
  `TOKENS`, `PASSWORDLESS`. Het stuk van de naam heeft precies de lengte van het woord: een teken dat in hoofdletters
  langer wordt (`ß` → `SS`) maakt er geen geheimwoord van (`paß`, `Reisepaß`). Bij een sleutel met punten of `[n]`
  telt het laatste deel (`adminAuth.users[0].password` → `password`). Een naam als `BYPASS_…` telt ook (fail-closed:
  kies dan een andere naam).
- **Accountnaam:** een geheimnaam waarin ook `BOOTSTRAP`, `DEFAULTUSER`, `DEFAULT_USER`, `ADMIN`, `ROOT`,
  `SUPERUSER`, `OWNER`, `INITIAL`, `SETUP`, `BEHEER`, `SERVICE_ACCOUNT`, `SERVICEACCOUNT` of `DIENST` staat, of
  (ADR-0020) `ADMINISTRATOR`, `BEHEERDER`, `DIENSTACCOUNT`, `BEHEERACCOUNT`, `SUPERADMIN`, `ROOTUSER` of `ADMINUSER`
  (vaste lijst), ergens in de naam (bij een sleutel met punten of `[n]` in het laatste deel, zoals bij Geheimnaam),
  bijvoorbeeld `GOTIFY_DEFAULTUSER_PASS`, `AUTHENTIK_BOOTSTRAP_PASSWORD`, `ADMINISTRATOR_PASSWORD` of
  `beheerwachtwoord`. De sleutel `phpmyadmin.password` (één sleutel met een punt) is dus geen accountnaam: het laatste
  deel is `password` (ADR-0021); `phpmyadmin_password` is het wel.
- **Accountwoord als heel naamdeel (ADR-0019):** in een voorouder-sleutel, en in een naam zonder geheimwoord (een
  sleutel, de `name` van een naam/waarde-paar of van een omgevingsvariabele), telt een woord uit die lijst alleen als
  heel naamdeel: de naam wordt gesplitst op elk teken dat geen letter of cijfer is (`_`, `-`, `.`, spatie), op een
  camelCase-grens en tussen letter en cijfer; een meervouds-`s` mag erachter. Dus `admin`, `Admin_ui`, `adminUser`,
  `root user`, `beheer`, `setup`, `initialSync`, `admins` en `admin2` wel, en door de woorden van ADR-0020 ook
  `administrator`, `beheerders`, `dienstaccount`, `beheeraccount`, `superadmin`, `rootuser` en `adminuser`;
  `phpmyadmin`, `rootless`, `groot`, `administration` en `webroot` niet.
- **Plek met een naam:** `custom_app.env[]` (`name`), een sleutel in `values`, een naam/waarde-paar in `values` (een
  object met `name` en `value`, zoals `additional_envs`) en een parameter (`name`). Een **gewone parameter** heeft geen
  geheimmarkering (niet `secret: true`, niet `type: secret_ref`, niet `source: secret` of `generated`).
- **Geheimplek en accountplek (ADR-0018, ADR-0019, ADR-0021):** wat onder een geheim- of accountnaam ligt, erft die naam (een
  object net als een lijst: `admin_password: {x: …}` is een geheim onder `admin_password`). Een geheimplek is een
  accountplek als haar eigen naam een accountnaam is, of als haar eigen naam of een voorouder-sleutel een accountwoord
  als heel naamdeel heeft (`admin.password`, `adminUser.password`). De naam van de parameter telt ook: een gebruikte
  parameter met een accountnaam geeft `account_gegevens_verboden` (ook een geheim dat de Hub maakt, niet te
  accepteren), net als een parameter met een geheimnaam op een plek waarvan de eigen naam of een voorouder een
  accountwoord heeft (`admin: {param: db_password}`, `admin.credentials`, `adminUser.pw`, een paar of
  omgevingsvariabele met de naam `ADMIN_PW`; ADR-0019); en een gewone parameter met een geheimnaam die ergens wordt
  gebruikt, geeft de fout `secret_in_form`. Op een plek waarvan de eigen naam of een voorouder een accountwoord heeft,
  telt ook de geheimmarkering van de parameter: een parameter met `secret: true`, `type: secret_ref` of `source:
  secret`/`generated` geeft daar `account_gegevens_verboden`, ook als zijn naam geen geheimwoord heeft (`admin_pw` naar
  de omgevingsvariabele `ADMIN_PW`, `admin.pw` of `admin: {param: init_value}`; ADR-0021, binnen besluit C-12). De
  regels voor de naam van de parameter gelden niet voor een parameter van type `boolean` zonder geheimmarkering (uitleg
  (a): een geheim is nooit een boolean; ADR-0019); een boolean met een geheimmarkering valt er wel onder (ADR-0021).
  De regels voor de plek zelf gelden altijd.
- **Niet-lege waarde:** elke tekst behalve de lege tekst, dus ook een tekst van alleen spaties, U+00A0, U+2003 of
  stuurtekens zoals U+001C en U+001F (bash, `nopt` en de apps zien die als waarde; ADR-0018), een getal, of een
  niet-lege lijst of object; ja/nee en `null` tellen niet.

Naamregels (per plek één code):

| Code | Wanneer |
|---|---|
| `secret_literal_forbidden` (N1) | een geheimplek met een letterlijke waarde, ook een accountplek: `custom_app.env[].value`, een sleutel of naam/waarde-paar in `values` (ook wat eronder ligt), en een parameter met een geheimnaam zonder geheimmarkering met een `default` of `example` |
| `secret_in_form` (N2, fout) | een geheimplek die geen accountplek is en haar waarde krijgt via `value_from` of `{param}` van een gewone parameter, of een gewone parameter met een geheimnaam die ergens wordt gebruikt |
| `account_gegevens_verboden` (N3, fout, niet te accepteren) | een accountplek (ook via een voorouder) die haar waarde krijgt via `value_from` of `{param}`, van welke parameter ook, dus ook van een geheim dat de Hub maakt, of een gebruikte parameter met een accountnaam, of een parameter met een geheimnaam (ADR-0019) of met een geheimmarkering (ADR-0021) op een plek waarvan de eigen naam of een voorouder een accountwoord heeft; op dat pad geen `secret_in_form` |

Geen fout: een geheimnaam die geen accountnaam is, naar een geheim dat de Hub maakt (zoals `db_password` en
`secret_key` van Authentik), op een plek zonder accountwoord in de eigen naam of een voorouder; daar blijft de te
accepteren waarschuwing `secret_in_form`.

Tekstregels (`account_gegevens_verboden`, fout; een sleutel valt onder de naamregels, alleen een sleutel in `values`
krijgt daarnaast T1):

- **T1, op elke tekst** (ook `description_nl` en `beschrijving_nl`) **en op elke sleutel in `values`** (ADR-0018): een
  wachtwoordhash. bcrypt in elke vorm die de
  bcrypt van Node-RED aanneemt: `$2a$`, `$2b$`, `$2y$` en `$2x$`, ook zonder `$` vooraan, met dubbele `$` en met kosten
  als `+10` of `010`, gevolgd door 53 tekens (ADR-0017); en hashes in PHC-vorm (`$argon2…`, `$scrypt$`, `$pbkdf2…`).
- **T2, op `custom_app.env[].value`, elke tekst in `values` en de `default` of `example` van een parameter:** de tekst
  wordt gelezen zoals Node-RED `FLOWS` leest, zonder lijst van optievormen. Splitsen op spatie, tab en regeleinde; het
  deel na de eerste `=` van een woord met `-` vooraan, en elk woord zonder `-` met een `=`, telt als instelling
  `sleutel=waarde` (ook een los woord, fail-closed); de waarde gaat door JSON (escapes opgelost). Fout is: een sleutel
  met een geheimnaam en een waarde, een sleutel `users` of `gebruikers` met inhoud, een hash volgens T1, instellingen uit
  een bestand (`@`), en (ADR-0017) een kruisverwijzing (`[msg.…]`, `[msg[…]]`) of een index (`[0]`, `["0"]`, `['0']`,
  `.0`) in de sleutel. Daarnaast een **vangnet** op de tekst zelf en op dezelfde tekst met alle JSON-escapes opgelost:
  een JSON-sleutel met een geheimnaam en een waarde, een JSON-sleutel `users`/`gebruikers` met inhoud, elke `naam=` met
  een waarde waarin `naam` een geheimnaam is, elk pad met een deel `users`/`gebruikers` vóór een `=` met een waarde, en
  een hash volgens T1. Het vangnet stopt alleen op spatie, tab en regeleinde (de IFS van bash); U+00A0 of een
  stuurteken na een `=` of binnen een JSON-tekst is een waarde (ADR-0018).
- **Lengte en rekentijd (ADR-0018):** de tekstregels lopen in lineaire tijd. Een tekst langer dan 65536 tekens toetsen
  ze niet: die is fail-closed een fout (`account_gegevens_verboden`, "te lang om op geheimen te toetsen").
  Het pad van een melding wordt pas gemaakt als er een melding is (ADR-0021): het geheugen en de rekentijd hangen niet
  af van de lengte van een sleutel keer het aantal waarden eronder.
  `POST /catalog/v1/validate` past op het JSON-veld `manifest` dezelfde grenzen toe als op `manifest_yaml`
  (256 KiB als compacte JSON en 32 niveaus: `manifest_too_large`, `yaml_too_deep`).
- De regels van `nopt` waarop T2 steunt, zijn gemeten op de gepinde Node-RED-digest (ADR-0016, "Context"); de
  kandidaatvormen c01 tot en met c15 staan met die digest in `apps/api/tests/geheimen_proeven.py`. Bij een nieuwe digest
  meet jelly-leon-platform ze opnieuw.
- `interpolation_forbidden` blijft zoals het was; op hetzelfde pad kunnen beide codes staan, elk met een eigen reden.

Wat de validator niet ziet: een gecodeerde of gesplitste hash, een geheim onder een naam zonder geheimwoord dat via
een gewone parameter (zonder geheimmarkering) of letterlijk in het manifest staat, en (ADR-0019, ADR-0020) een
accountwoord dat in een voorouder aan een ander woord vastzit en zelf niet in de vaste lijst staat. Een parameter met
een geheimmarkering ziet de validator wel: op een plek met een accountwoord is dat een fout (ADR-0021), elders de te
accepteren waarschuwing `secret_in_form`. De samenstellingen `administrator`, `beheerder`, `dienstaccount`, `beheeraccount`, `superadmin`, `rootuser` en
`adminuser` staan sinds ADR-0020 in de lijst en tellen weer. De lijst blijft open voor nieuwe samenstellingen: een
geheim van de Hub onder bijvoorbeeld `sysadmin`, `webadmin` of `rootaccount` geeft de te accepteren waarschuwing
`secret_in_form`, tot de lijst met een nieuwe ADR is uitgebreid. Dan blijft de review van het manifest de controle
(ADR-0016, "Wat dit ontwerp niet bewijst").

Bekende valse treffers (fail-closed; de regels gelden voor elke app, niet alleen voor Node-RED). Kies dan een andere
naam, of buiten een geheimplek een parameter; er is geen uitzondering (ADR-0018, ADR-0019):

- een index of een deel `.0` in de "sleutel" van een adres in een tekst die T2 leest, zoals
  `https://h.test/api/v1.0.2?x=1` of `https://h.test/zoek?ids[0]=1` (T2 leest elk woord met een `=` als instelling);
- een naam met een geheimwoord en het achtervoegsel `_FILE` met een pad als waarde (`POSTGRES_PASSWORD_FILE` met
  `/run/secrets/db`): de naam is een geheimnaam (`secret_literal_forbidden`), en in een tekst telt
  `POSTGRES_PASSWORD_FILE=/…` als instelling;
- een `naam=` met een geheimwoord in een gewone optie, zoals `PASSWORD_MIN_LENGTH=8` in een tekst;
- een object onder een geheimnaam, ook in camelCase (`passwordPolicy`, `tokenSettings`): alles eronder is een
  geheimplek (het object erft de naam, ADR-0018). `passwordPolicy: {minLength: 8}`, `tokenSettings: {ttl: 3600}` en
  `token: {ttl: 3600}` geven `secret_literal_forbidden`; een parameter helpt daar niet (een gewone parameter geeft
  `secret_in_form`). Dit is een geheimwoord, geen accountwoord, en het blijft strikt (ADR-0019);
- een accountwoord als heel naamdeel in een voorouder telt altijd (`setup`, `initialSync`, `admin_ui`): een geheim
  onder `setup.database.password`, `initialSync.api_key` of `admin_ui.db_password` is een accountplek
  (`account_gegevens_verboden`). Sinds ADR-0021 geldt dat ook voor een parameter met een geheimmarkering zonder
  geheimwoord in de naam of op de plek (`setup.sleutel`, `owner.id` of de omgevingsvariabele `SETUP_KEY` naar een
  geheim dat de Hub maakt). Als deel van een woord (`phpmyadmin`, `rootless`, `groot`, `administration`) telt het
  sinds ADR-0019 niet meer: `phpmyadmin.db_password`, `rootless.cache.secret`, `groot.password` en
  `administration.db.password` naar een geheim dat de Hub maakt, geven weer de te accepteren waarschuwing
  `secret_in_form`;
- een parameter van type poort of getal met een geheimwoord in de naam (`token_poort`, `password_min_length`) valt
  onder de naamregels (`secret_in_form` waar hij wordt gebruikt); kies een andere naam. Een parameter van type
  `boolean` zonder geheimmarkering niet (`password_login`; ADR-0019, ADR-0021);
- bij een catalogusapp legt de app van iX de sleutels in `values` vast: "kies een andere naam" kan daar niet, en een
  parameter onder een geheimnaam geeft ook een fout. Zo'n app vraagt een besluit of een nieuwe ADR, geen uitzondering
  in het manifest.

Catalogussynchronisatie: een manifest dat door een onverwachte fout niet te verwerken is, wordt alleen zelf geweigerd
(`manifest_niet_verwerkt`); de andere manifesten van de bron gaan gewoon door (fail-closed per manifest, ADR-0018).

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
| `checklist_not_installable` | Controlelijst (kind: checklist) zonder installatie: eigen schema, nooit installeren. |
| `install_reason_ontbreekt` | Catalogusapp zonder install.reason (waarschuwing; fout in --strict). |
| `restrisico_nnp_ontbreekt` | Upstream-image als Custom App noemt het restrisico zonder no-new-privileges niet. |
| `accepted_finding_unused` | Geaccepteerde code treedt niet (meer) op; haal hem uit accepted_findings. |
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
| `account_gegevens_verboden` | Aanmelding van een beheerder of dienstaccount, wachtwoordhash, geheime sleutel of gebruikerslijst in het manifest of de vertaling. |
| `secret_param_invalid` | secret: true alleen bij source generated (de Hub maakt hem) of secret. |
| `reserved_env` | HUB_* is gereserveerd voor het koppelen en geen parameter of omgevingsvariabele. |
| `interpolation_forbidden` | Variabele-interpolatie ($VAR, ${…}, $(…)) is niet toegestaan. |
| `decision_not_listed` | Besluit ontbreekt in decisions_required. |
| `risk_below_policy_minimum` | Risicoklasse lager dan het platformminimum. |
| `tag_only_rollback_with_migrations` | Tag-only rollback met databasemigraties is onveilig. |
| `backup_unknown_dataset` | Back-up verwijst naar onbekende opslag. |
| `backup_dataset_not_covered` | Data-opslag zit niet in de snapshotlijst. |
| `backup_db_export_none` | Geen database-export opgegeven. |
| `stopped_snapshot_without_dataset` | stopped_snapshot zonder dataset in snapshot_datasets. |
| `healthcheck_unknown_port` | Healthcheck verwijst naar onbekende poort. |
| `healthcheck_monitor_missing` | Geen monitorplan (healthcheck.monitor) voor Uptime Kuma. |
| `same_as_host_not_allowed` | container_port same_as_host alleen bij een catalogusapp. |
| `portal_unknown_port` | Portal verwijst naar onbekende poort. |
| `dependency_self` | App hangt van zichzelf af. |
| `compatibility_missing` | Geen TrueNAS-compatibiliteit opgegeven. |
| `validation_status_claim_ignored` | Geclaimde validatiestatus wordt niet overgenomen. |
| `timezone_missing` | Tijdzone ontbreekt. |
| `catalog_value_forbidden` | Verboden instelling in de waarden van een catalogusapp. |
| `signature_missing` | Handtekening ontbreekt terwijl de bron die vereist. |
| `signature_invalid` | Handtekening klopt niet. |
| `app_id_claimed_by_other_source` | App-ID hoort bij een andere catalogusbron. |
| `manifest_niet_verwerkt` | Een onverwachte fout bij dit manifest tijdens de synchronisatie: alleen dit manifest is geweigerd (fail-closed, ADR-0018). |
| `parameter_value_missing` | Waarde voor een verplichte parameter ontbreekt. |
| `parameter_value_invalid` | Waarde voor een parameter is ongeldig. |
| `parameter_value_unknown` | Waarde opgegeven voor een onbekende parameter. |
| `parameter_value_not_allowed` | Deze parameter mag niet via de installatie worden meegegeven. |
| `vault_ref_not_allowed` | Vault-verwijzing die de vertaling niet zelf heeft gemaakt (alleen voor een geheime parameter). |
| `manifest_invalid` | Het manifest is niet geldig; vertalen is geweigerd. |
| `vertaling_te_groot` | De vertaling is te groot: meer dan 256 plekken krijgen een parameterwaarde, of de payload is groter dan 256 KiB (canonieke JSON); geweigerd, zonder payload (fail-closed, ADR-0019). |
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
