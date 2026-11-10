# appstore-en-fork installeren als standaard TrueNAS Custom App (platformcontract 1.3)

**Geen YAML, geen "Install via YAML", geen compose, geen terminal.** De app is één alles-in-één image (ingebouwde
PostgreSQL 16 op een unix-socket, API en worker) met één poort en één opslagmap.

> **Normaal installeert de Hub deze app voor je** (na de onboarding, via de executor en de TrueNAS-API, met precies
> dezelfde waarden als hieronder en een eenmalig koppeltoken). Deze pagina is de referentie van de formuliervelden en
> de handmatige terugvaloptie.
>
> **Verificatiestatus: NIET GEVALIDEERD op een echte NAS.** De veldnamen komen uit de broncode van het standaard
> Custom App-formulier (catalogusapp `ix-app` 1.4.8, `github.com/truenas/apps`, commit `f142a16a…`, zie
> `docs/TRUENAS_APP_VALUES.md`) en kunnen per TrueNAS-versie iets afwijken. De image is in Docker getest met dezelfde
> containerinstellingen als het formulier (`tests/e2e/test_allinone_image.py`).

## 0. Voorwaarden

- De image `ghcr.io/lvos04/appstore-en-fork:<versie>` bestaat pas nadat de release-workflow voor tag `v<versie>` is
  gedraaid (`release/digests.json` in de GitHub-release noemt de digest). Zolang die release er niet is, kan deze app
  niet worden geïnstalleerd.
- **Publieke image (contract 1.3 §2.1, besluit 3):** na de scan van de volledige historie worden repo en GHCR-pakket
  publiek; installeren gebruikt dan **geen** registry-login. Tot de zichtbaarheid echt is omgezet, is installeren op
  een echte NAS NIET GEVALIDEERD (en voor een privé-pakket geblokkeerd).
- *Alleen als terugvaloptie:* een registry-login in TrueNAS (Apps → Configuration → Manage Container Images /
  registry-instelling) met een token dat **alleen pakketten mag lezen**. Gedrag met GHCR-tokens is NIET GEVALIDEERD.
  Zo'n token is een geheim van jou in TrueNAS — nooit in het formulier van de app, een manifest, Git of een log.

## 1. Dataset

TrueNAS → **Datasets** → kies je pool → **Add Dataset**: naam bijvoorbeeld `appstore-en-fork`, *Dataset Preset*
**Apps**. Rechten hoef je niet aan te passen: bij de eerste start maakt de app gebruiker `568` (apps) eigenaar van zijn
eigen submappen (`postgres`, `secrets`, `backups`) en verder niets.

## 2. Formulier (Apps → Discover Apps → Custom App)

| Groep (formulier) | Veld | Waarde |
|---|---|---|
| — | Application Name | `appstore-en-fork` |
| Image Configuration | Repository | `ghcr.io/lvos04/appstore-en-fork` |
| | Tag | **`<versie>@sha256:<digest>`** uit `release/digests.json`, bv. `0.1.0@sha256:…` (64 hex) — zo draait precies de geteste image (contract 1.3 §2.2). Weigert het formulier het `@`-teken, vul dan alleen de exacte versie in (bv. `0.1.0`); de Hub controleert de digest na installatie (NIET GEVALIDEERD op een echte NAS). **Nooit `latest`.** |
| | Pull Policy | *Pull the image if it is not already present on the host* (`missing`) |
| Container Configuration | Hostname, Entrypoint, Command | leeg laten |
| | Timezone | jouw tijdzone (uit de keuzelijst) |
| | Environment Variables | Geen verplichte variabelen. Alleen om te koppelen (aanbevolen, zie stap 3): `HUB_URL` = `http://<adres-van-de-Hub>:<poort>` (zonder pad), `HUB_ENROLLMENT_TOKEN` = het **eenmalige** token uit de Hub (`enr_…`, 60 min geldig) en `HUB_KEY_FINGERPRINT` = de vingerafdruk die de Hub erbij toont (`XXXX-XXXX-XXXX-XXXX`). Nooit wachtwoorden of andere geheimen. |
| | Restart Policy | *Unless Stopped* |
| | Disable Builtin Healthcheck | **uit** (de image heeft een eigen healthcheck op `/readyz`; die wisselt als eerste handeling naar 568 met no-new-privileges, contract 1.3 §2.2) |
| | TTY / Stdin | uit |
| | Devices | geen |
| Security Context Configuration | Privileged | **uit** |
| | Capabilities → Add | niets toevoegen |
| | Custom User | **uit** (de app start als root alléén om `/appdata` aan 568 te geven; daarna leegt hij de bounding set en draaien alle processen — ook PID 1 — als 568 zonder capabilities). Het formulier zet zelf geen *no-new-privileges* en geen *cap_drop*; de image zet `PR_SET_NO_NEW_PRIVS` zelf (contract 1.3 §2.2). Met *Custom User* 568 werkt de app ook (dan kan de bounding set niet geleegd worden). |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Port Bind Mode *Publish*, Host Port `30430` (voorstel; kies een vrije poort als die bezet is), Container Port `30430`, Protocol *TCP*, Host IPs leeg |
| | Networks / Custom DNS Setup | leeg laten |
| Portal Configuration | Add | Name `Statuspagina`, Protocol *HTTP*, Use Node IP aan, Port gelijk aan de Host Port, Path `/` |
| Storage Configuration | Storage → Add → Type | **Host Path** |
| | Read Only | uit |
| | Mount Path | `/appdata` (precies zo) |
| | Host Path Configuration → Enable ACL | uit |
| | Host Path | de dataset uit stap 1 (kies hem met de mapkiezer; het pad begint met `/mnt/` + de naam van jouw pool) |
| Labels Configuration | — | leeg (bij installatie via de Hub zet die `nl.lvos04.platform.*`-labels voor herkenning) |
| Resources Configuration | Enable Resource Limits | aan: CPUs `1`, Memory (in MB) `768` |
| | GPU Configuration | geen GPU |

**Install.** De eerste start duurt ongeveer 10–20 seconden (database aanmaken). Daarna *Running* en na de healthcheck
*Healthy*.

## 3. Openen en koppelen (contract 1.3 §3.2, §3.7)

Open `http://<IP-adres-van-je-NAS>:<Host Port>` — **via het IP-adres** (of een naam zonder punt, of onder `.local`,
`.lan`, `.internal`, `.home.arpa`). Koppelen via een eigen domeinnaam (bv. `nas.fritz.box`) weigert de app met
`host_niet_toegestaan`: zo kan een website op je LAN de app niet via DNS-rebinding aan een vreemde "Hub" koppelen
(contract 1.3 §3.7, keuze K1). Met een koppeltoken in het formulier (hieronder, aanbevolen) speelt dat niet. De statuspagina (ontwerptaal Peil, zonder JavaScript) toont bovenaan de
totaalstatus — **Niet gereed** zolang de app niet gekoppeld is, nooit groen — en daaronder **Niet gekoppeld**.

- **Aanbevolen (geen wedloop):** maak in de Hub (Instellingen → Diensten) een enrollment-token voor
  `appstore-en-fork` en zet `HUB_URL`, `HUB_ENROLLMENT_TOKEN` en `HUB_KEY_FINGERPRINT` in het formulier (stap 2). De
  app koppelt zichzelf, controleert de vingerafdruk en bevestigt bij de Hub. Installeert de Hub de app zelf, dan
  geeft hij die drie waarden altijd mee.
- **Alternatief, alleen zolang de app nog nooit gekoppeld is:** maak in de Hub een **koppelcode** en vul op de
  statuspagina het Hub-adres, de koppelcode (`XXXX-XXXX-XXXX`) én de **Hub-vingerafdruk** in die de Hub ernaast toont.
  Na de eerste koppeling verdwijnt dit formulier.

Daarna toont de pagina **Gekoppeld** (neutraal, niet groen), het Hub-adres en de Hub-vingerafdruk (vergelijk die met de
Hub-UI); het service-token staat nooit op de pagina. **Ontkoppelen of opnieuw koppelen gaat daarna alleen via de Hub** (of met een nieuw
`HUB_ENROLLMENT_TOKEN` in het formulier: wie het formulier kan wijzigen is TrueNAS-beheerder). Er is geen
"ontkoppelen" op de statuspagina.

Op de statuspagina betekent **Gereed**: `/readyz` in orde én gekoppeld en door de Hub bevestigd. `/readyz` zelf kijkt
niet naar de koppeling (TrueNAS herstart een niet-gekoppelde app dus niet).

## Bestaande apps importeren (B08-002)

Na het koppelen leest de app elke 15 minuten de app-inventaris van de Hub (alleen lezen; nooit een actie op de NAS) en
houdt een register en beslisregister bij voor de bestaande apps (APP-01…APP-20). Er is daarvoor **geen** extra
formulierveld. De schermen staan in de Hub-UI; zie [`docs/BESTAANDE_APPS.md`](../../docs/BESTAANDE_APPS.md).
Geeft de Hub zijn inventaris (nog) niet aan diensten, dan meldt de app-log
`periodieke import: capability_unavailable (hub_inventaris_niet_voor_diensten)` of — als de Hub deze dienst de
leesscope `inventaris.lezen` niet geeft — `capability_unavailable (scope_inventaris_ontbreekt)`, en blijft het register
ongewijzigd. Dat is nooit een intrekking van de koppeling.

## Updaten en terugdraaien

- **Updaten:** app → **Edit** → Tag naar de nieuwe versie → **Update**. Bij openstaande databasemigraties maakt de app
  eerst een `pg_dump` in `/appdata/backups` (`voor-upgrade-*.dump`, nieuwste 3 blijven). Mislukt die back-up, dan
  migreert de app **niet** en blijft de database ongewijzigd (melding in de app-log). Een ZFS-snapshot vooraf blijft
  verstandig.
- **Terugdraaien:** alleen de tag terugzetten is na een databasemigratie **niet** veilig. Zet de ZFS-snapshot van vóór
  de update terug (of laat de Hub de `voor-upgrade`-dump herstellen) en zet dan pas de oude tag terug.

## Problemen

| Melding in de app-log | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | De opslagregel ontbreekt of de Mount Path is niet precies `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/...` | *Custom User* staat aan met een gebruiker zonder rechten: zet het uit, of geef 568 schrijfrechten. |
| `oude postmaster.pid ... verwijderd` | Geen fout: de vorige stop was onverwacht; de database start gewoon. |
| `back-up vóór de databasemigratie is mislukt` | Maak ruimte vrij op de dataset of zet de vorige tag terug; de database is niet aangeraakt. |
| Poort bezet bij opslaan | Kies een andere vrije Host Port (Container Port blijft `30430`). |
| Image ophalen mislukt (`denied`) | Het GHCR-pakket is nog privé (stap 0) of de release bestaat nog niet. |
| Formulier weigert de tag met `@sha256:` | Vul alleen de exacte versie in; de Hub leest de digest na installatie terug. |
| Statuspagina: "De sleutelset-vingerafdruk van de Hub klopt niet" | De ingevulde vingerafdruk (of `HUB_KEY_FINGERPRINT`) past niet bij de Hub op dat adres: controleer het adres en neem de vingerafdruk opnieuw over uit de Hub-UI. Er is niets opgeslagen. |
| Statuspagina: "Het token is na 60 minuten niet gelukt" of "onbekend, verlopen of al gebruikt" | Maak in de Hub een nieuw koppeltoken (enrollment-token) en zet het in `HUB_ENROLLMENT_TOKEN`; een gebruikt of geweigerd token wordt nooit opnieuw geprobeerd. |
| Statuspagina na het formulier: "De Hub weigerde de koppelcode: verlopen, al gebruikt of onbekend" | Maak in de Hub een nieuwe koppelcode (Instellingen → Diensten) en vul hem opnieuw in, samen met het adres en de vingerafdruk. |
| Statuspagina: koppeling **Onleesbaar** ("De koppelstatus van deze dienst is onleesbaar", code `koppelstatus_onleesbaar`) | `/appdata/secrets/hub-link.json` is kapot, van een andere eigenaar of te ruim leesbaar. De dienst vertrouwt de Hub niet meer (fail-closed) en toont geen formulier; `/readyz` blijft gezond. Maak in de Hub een nieuw koppeltoken en zet het in `HUB_ENROLLMENT_TOKEN`: de app bewaart het oude bestand als `hub-link.json.onleesbaar-<tijd>` en koppelt opnieuw. |
| Statuspagina na het formulier: "Koppelen kan niet via dit adres" (`host_niet_toegestaan`) | Open de pagina via het IP-adres van de NAS, of koppel met een koppeltoken (`HUB_ENROLLMENT_TOKEN`). |
| Statuspagina na het formulier: "Het formulier kwam niet van deze pagina" (`herkomst_onjuist`) | Het verzoek kwam niet van de statuspagina zelf (andere site, ander tabblad of een browserinstelling zonder herkomst). Laad de pagina opnieuw en vul de velden opnieuw in. |
| Statuspagina: "HUB_URL is geen geldig Hub-adres" (`hub_adres_ongeldig`) of "HUB_KEY_FINGERPRINT heeft niet de vorm…" (`vingerafdruk_ongeldig`) | Verbeter de waarde in het formulier van de Custom App (adres als `http://<IP>:<poort>`, geen `localhost`, geen afgekorte IP-vorm zoals `127.1`). Het token is dan nog niet gebruikt en werkt daarna gewoon. |
| Statuspagina: **Ingetrokken** ("De Hub heeft de koppeling ingetrokken") | De app vertrouwt de Hub niet meer (fail-closed) en houdt zijn eigen data; opnieuw koppelen gaat via de Hub. |
| Statuspagina: melding met **Code** en **Trace-ID** | Selecteer de trace-ID en zoek hem op in de app-log (TrueNAS → Apps → deze app → Logs): daar staat dezelfde code bij het mislukte verzoek. De ingevulde waarden worden nooit bewaard of teruggetoond; vul ze opnieuw in. |
| `periodieke import: capability_unavailable (hub_inventaris_niet_voor_diensten)` in de app-log | De Hub-versie geeft `GET /api/v1/apps` nog niet aan diensten. Geen fout van deze app; het register blijft ongewijzigd tot de Hub dat toestaat. |
| `periodieke import: unknown (inventaris_verouderd)` | De Hub heeft geen verse meting van de NAS; vernieuw de inventaris in de Hub. Er wordt niets als "verdwenen" gemarkeerd. |
| `HUB_URL wordt niet gebruikt` in de app-log | Het adres heeft een pad, is een loopback-/link-local-adres of bevat inloggegevens; gebruik `http(s)://adres[:poort]`. |
