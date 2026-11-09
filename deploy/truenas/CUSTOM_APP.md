# appstore-en-fork installeren als standaard TrueNAS Custom App

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
- GitHub maakt een nieuw pakket eerst **privé**. Eenmalig: GitHub → Packages → `appstore-en-fork` → Package settings →
  Change visibility → Public.

## 1. Dataset

TrueNAS → **Datasets** → kies je pool → **Add Dataset**: naam bijvoorbeeld `appstore-en-fork`, *Dataset Preset*
**Apps**. Rechten hoef je niet aan te passen: bij de eerste start maakt de app gebruiker `568` (apps) eigenaar van zijn
eigen submappen (`postgres`, `secrets`, `backups`) en verder niets.

## 2. Formulier (Apps → Discover Apps → Custom App)

| Groep (formulier) | Veld | Waarde |
|---|---|---|
| — | Application Name | `appstore-en-fork` |
| Image Configuration | Repository | `ghcr.io/lvos04/appstore-en-fork` |
| | Tag | de exacte versie, bv. `0.1.0` — **nooit `latest`**. Wil je de digest vastzetten: `0.1.0@sha256:<digest uit release/digests.json>` |
| | Pull Policy | *Pull the image if it is not already present on the host* (`missing`) |
| Container Configuration | Hostname, Entrypoint, Command | leeg laten |
| | Timezone | jouw tijdzone (uit de keuzelijst) |
| | Environment Variables | **leeg laten** (geen verplichte variabelen; nooit wachtwoorden of tokens hier) |
| | Restart Policy | *Unless Stopped* |
| | Disable Builtin Healthcheck | **uit** (de image heeft een eigen healthcheck op `/readyz`) |
| | TTY / Stdin | uit |
| | Devices | geen |
| Security Context Configuration | Privileged | **uit** |
| | Capabilities → Add | niets toevoegen |
| | Custom User | **uit** (de app start als root alléén om `/appdata` aan 568 te geven en draait daarna als 568 zonder capabilities) |
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

## 3. Openen en koppelen

Open `http://<adres-van-je-NAS>:<Host Port>`. De statuspagina toont de status en **Niet gekoppeld**. Maak in de Hub
(Instellingen → Diensten) een koppelcode voor `appstore-en-fork`, vul op de statuspagina het Hub-adres en de koppelcode in
en kies **Koppelen**. Daarna toont de pagina **Gekoppeld** (alleen de laatste 4 tekens van het service-token).
Installeert de Hub de app zelf, dan geeft hij `HUB_URL` en een eenmalig token mee en koppelt de app automatisch.

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
