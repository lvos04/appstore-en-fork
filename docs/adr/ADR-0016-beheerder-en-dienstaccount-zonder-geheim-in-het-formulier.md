# ADR-0016 — Manifeststandaard: beheerder en dienstaccount zonder geheim in het formulier (besluit C-12)

- **Status:** ontwerp (stap S-P2-510) · 2026-11-11 · de punten 1 tot en met 6, 8 en 9 onder "Besluit" volgen uit de
  bronnen; punt 7 (Node-RED op de officiële image) is een besluit van de gebruiker (2026-11-11: optie A, "Wachten").
  Herzien na de reviews R-VEILIG en R-MANIFEST van deze stap en na hun herreviews (herstelpogingen 2 en 3,
  2026-11-11). Er
  is niets gebouwd; de bouw volgt in de
  stappen onder "Gevolgen per repo" (een herstelstap van de validator vóór S-P3-490, daarna S-P3-490, S-P3-445 en
  S-P3-360).
- **Bron:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`), koppen "1. Vaste keuzes (van de gebruiker)",
  "3.1 Uitgangspunten", "3.2 Overzicht enrollment (eigen nieuwe diensten)", "3.10 Credentials van andere apps" en
  "9. Manifeststandaard (B08-001, eigenaar `appstore-en-fork`)"; [ADR-0002](ADR-0002-manifeststandaard-v1.md) besluit 4;
  [ADR-0015](ADR-0015-vault-verwijzing-geheimen-in-de-vertaling.md);
  `ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/STAPPEN_P2.md`, kop "8.2 Contract- en beleidsvragen
  (voor de eigenaar van het contract of van de manifeststandaard)", rijen C-5, C-11 en C-12;
  `ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/BESLUITEN_EN_BLOKKADES.md`, kop "C-12 — Hoe krijgt
  Node-RED een beheerder en een dienstaccount?"; `jelly-leon-platform:docs/adr/ADR-0023-node-red-als-custom-app.md` en
  `jelly-leon-platform:install/manifests/node-red.manifest.yaml` en `tests/repo/test_nodered.py`;
  `ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/STAPPEN_P2.md`, stap S-P2-410 bouwpunt 3;
  `home-control-hub:apps/api/src/integrations/domains/automation/policy.py` (`define_arguments`, `build_profile`,
  `check_isolation`), `router.py` (`post_hardening_profile`), `sources.py` en `docs/b11/node-red-verificatie.json`; in
  de gepinde Node-RED-image `node_modules/@node-red/editor-api/lib/auth/index.js` en `users.js`,
  `node_modules/node-red/red.js`, `entrypoint.sh`.
- **Validatie:** bronnen gelezen, plus proeven met de huidige validator (`jl-manifest validate --strict` en
  `jl-manifest translate`) op proefmanifesten buiten de repo, met alleen testwaarden (zie "Gevolgen per repo", punt 2),
  en proeven met de argumentverwerking van de gepinde Node-RED-image (zie "Context").
  Niets gebouwd, niets op TrueNAS geprobeerd: NIET GEVALIDEERD (echte NAS).

## Context

**Besluit C-12 van de gebruiker (2026-11-11, keuzemenu, optie 1):** "Voorziening zonder geheim": de manifeststandaard
(appstore-en-fork) krijgt een voorziening voor een beheerder en een dienstaccount zonder geheim in het Custom
App-formulier; het profiel van home-control-hub (`build_profile` / `define_arguments` / `credentialSecret`) vervalt of
wordt daaraan gelijkgetrokken. GEEN contractuitzondering op kop 3.1 (optie 2 is afgewezen). Optie 2 was: het profiel
blijft de bron, met een contractuitzondering; "het formulier krijgt dan wachtwoordhashes en een `credentialSecret`".

**Aanvullend besluit van de gebruiker (2026-11-11, keuzemenu na de verkenning van deze stap):** het ontwerp van C-12
gebruikt de vault-weg van ADR-0015 (een geheim van de Hub via `custom_app.env[].value_from` in het formulier) niet.
Alleen varianten waarbij helemaal geen geheim in het Custom App-formulier komt, tellen (bijvoorbeeld: de app maakt het
geheim bij de eerste start zelf aan in `/appdata/secrets/`). Het besluit van ADR-0015 blijft voor catalogusapps en
Custom Apps verder ongewijzigd.

**Besluit C-11 van de gebruiker (2026-11-11):** "Geen anonieme toegang tot de Node-RED-editor; alleen bruikbaar via
een beheerder." Zodra C-11 in het manifest is uitgevoerd, is de editor zonder beheerder helemaal dicht, ook voor
lezen. Dat gebeurt in stap S-P2-410 (jelly-leon-platform), nog niet nu: zie "De botsing".

**Besluit van de gebruiker over punt 7 (2026-11-11, keuzemenu):** optie A, "Wachten": Node-RED krijgt voorlopig geen
beheerder en geen dienstaccount via de voorziening (punt 7 onder "Besluit").

**Vraag C-5:** het Node-RED-manifest kan geen beheerder aanmaken, de flowbundels en `HUB_URL` niet plaatsen en geen
service-token meegeven (jelly-leon-platform ADR-0023, kop "Wat het manifest niet kan (geblokkeerd)"). Punt 8 onder
"Besluit" zegt welk deel deze voorziening dekt.

**De botsing.** home-control-hub maakt met `policy.define_arguments` en `policy.build_profile` (route
`POST /integrations/v1/automation/hardening-profile`, functie `router.post_hardening_profile`) argumenten `--define=…`
voor het formulier van de Custom App van Node-RED: `adminAuth` met twee accounts (`beheerder` en `hub-dienst`, elk met
een bcrypt-hash) en een `credentialSecret`. Elk antwoord van de route bevat die argumenten opnieuw; de R-VEILIG-review
van stap S-P2-295 (proef P8) kreeg bij elke nieuwe `Idempotency-Key` dezelfde `credentialSecret` terug. Dat botst met
contract kop 3.1 ("Nooit geheimen in het Custom App-formulier"). Die route bestaat nog (home-control-hub op `23f81e8`,
2026-11-11); ze vervalt pas met S-P3-360 (punt 7 en "Gevolgen per repo", punt 5).

jelly-leon-platform zet de beveiliging van Node-RED via de omgevingsvariabele `FLOWS` (`-D sleutel=waarde`). Op
2026-11-11 (jelly-leon-platform `b1da564`) is dat
`-D adminAuth={"type":"credentials","users":[],"default":{"permissions":"read"}}`: geen gebruikers, maar wel een
anonieme gebruiker met leesrecht. De editor en de Admin-API zijn nu dus voor iedereen op het vertrouwde netwerk
leesbaar (ADR-0023 besluiten 2 en 4, "Anoniem alleen-lezen is een bewuste keuze"), en `tests/repo/test_nodered.py`
(`test_overrides_harden_the_editor_and_the_palette`) eist die `default` nog. C-11 wordt uitgevoerd in S-P2-410
bouwpunt 3: `adminAuth` wordt precies `{"type": "credentials", "users": []}`, zonder `default` (3a), en de repotest eist
dat en faalt bij een variant met `default` (3e). Tot die stap klaar is, geldt de dichte stand van punt 7 nog niet.

**Wat de bronnen over Node-RED zeggen** (image `nodered/node-red:5.0.9-24` op de digest uit het manifest van
jelly-leon-platform):

- Instellingen komen alleen binnen als argument `-D`/`--define` met JSON (jelly-leon-platform ADR-0023 besluit 2;
  home-control-hub `policy.py`, moduletekst). Een gebruiker in `adminAuth.users` heeft een bcrypt-hash (ADR-0023, kop
  "Wat het manifest niet kan (geblokkeerd)").
- De omgevingsvariabele `NODE_RED_CREDENTIAL_SECRET` doet niets. Zonder `credentialSecret` versleutelt Node-RED de
  credentials met een sleutel die het zelf maakt (`flowEncryptionType: "system"`; home-control-hub
  `docs/b11/node-red-verificatie.json`, controles `kaal_instellingen` en `kaal_env_credential_secret_genegeerd`). Die
  sleutel staat in `/data/.config.runtime.json` (manifest, `storage[data].description_nl`; ADR-0023 besluit 6).
- `FLOWS` is de enige afsluiting. De image zet zelf `FLOWS=flows.json` (`docker image inspect` van de gepinde digest),
  `entrypoint.sh` geeft `$FLOWS` ongequoot aan Node-RED, en de standaard-`settings.js` van de image heeft `adminAuth`
  uitgezet. Zonder `adminAuth` laat `needsPermission` (`lib/auth/index.js`) elk verzoek door. Ontbreekt `FLOWS` in het
  formulier of verandert het (ook bij een `app.update`), dan start Node-RED dus open, met alle rechten
  (home-control-hub `docs/b11/node-red-verificatie.json`, controles `kaal_geen_aanmelding` en `kaal_anoniem_leesbaar`;
  jelly-leon-platform `tools/nodered_rooktest.py`, controleproef N2). De healthcheck (`GET /auth/login`) slaagt dan nog:
  `login()` antwoordt zonder `adminAuth` met een leeg object. Alleen de monitor (nu 403 op `/diagnostics`; zonder
  `FLOWS` 200, ADR-0023 besluit 3) en de controle na installatie `beveiliging_actief` zien het.
- `red.js` leest zijn argumenten met `nopt` (versie 5.0.0 in de gepinde image; `knownOpts` met
  `define: [String, Array]`, `shortHands` met `D: ["--define"]`). De eigen hulptekst van Node-RED noemt alleen
  `-D, --define X=Y`, maar `nopt` neemt veel meer vormen aan. Gemeten op 2026-11-11 met de `nopt` en `@node-red/util`
  uit de image zelf (een naslag van de argumentverwerking van `red.js`, zonder Node-RED te starten; broncode
  `node_modules/nopt/lib/nopt.js`, functies `parse` en `resolveShort`) gelden deze regels:
  - elk woord dat met `-` begint (en langer is dan één teken) is een optie; een woord van alleen twee of meer
    streepjes (`--`) sluit de opties af, en de woorden daarna zijn geen optie en geen waarde meer;
  - heeft een optiewoord een `=`, dan knipt `nopt` het bij de eerste `=`: het deel daarna is de waarde van die optie;
  - `nopt` haalt alle streepjes vooraan weg (`--D` is `-D`; `-define` en `---define` zijn `--define`), slaat `no-`
    vooraan over (ook `--no-define=…` en `--no-d=…` geven een define-waarde), neemt elk ondubbelzinnig begin van een
    lange naam aan, ook met één streep (`-d`, `-de`, `--def`), en splitst een woord van losse letters die elk een
    korte optie zijn (`-vD=…` is `-v` en `-D`);
  - `define` zonder `=` (in welke vorm ook) neemt het volgende woord als waarde, ook als dat met `-` begint; dan begint
    ook de sleutel van de instelling met `-` (gemeten: `-D --credentialSecret=…` geeft de sleutel
    `--credentialSecret`, een andere sleutel dan `credentialSecret`).

  Een define-waarde is dus altijd het deel na de eerste `=` van een optiewoord, of het woord na een optie; welke opties
  `nopt` kent, maakt daarvoor niet uit. Een los woord `sleutel=waarde` (niet na een optie) neemt Node-RED niet als
  instelling aan: `nopt` zet het in `argv.remain`, en `red.js` maakt van het eerste woord daarvan de naam van het
  flowbestand (`flowFile = parsedArgs.argv.remain[0]`). De vorm `-Dsleutel=waarde` (aaneen) geeft geen define-waarde.
  `red.js` leest elke define-waarde met `/^(([^=]+)=(.+)|@(.*))$/`: als `sleutel=waarde` (de waarde gaat door
  `JSON.parse`; lukt dat niet, dan blijft het tekst), of als `@pad`: dan leest `red.js` de instellingen uit een
  JSON-bestand op dat pad in de container (`fs.readJsonSync`), buiten het zicht van de validator. Bij elke andere
  define-waarde stopt `red.js` (bij `-Dv=…` wordt de define-waarde `--verbose`). `users.js` maakt van een
  `users`-object een lijst met één gebruiker. Een gebruiker kan in `FLOWS` dus ook als object, met JSON-escapes
  (`\u0024` voor `$`, `\/` voor `/`) of als losse paden (`adminAuth.users[0].password=…`) staan. Ook met de echte
  `red.js` nagemeten, in een container zonder netwerk (uid 1000, `/data` als tmpfs): met `FLOWS` in de vorm van proef
  c14 ("Gevolgen per repo", punt 2) meldt de gebruiker uit `FLOWS` zich aan (200; anoniem 401), en is
  `flows_cred.json` met de `credentialSecret` uit `FLOWS` te ontsleutelen. W2 (T2) steunt op deze regels, niet op een
  lijst van opties. Ze gelden voor de gepinde digest; een nieuwe digest wordt opnieuw gemeten ("Gevolgen per repo",
  punt 4).
- Een eerste-gebruik-venster (standaardwachtwoord of setup-pagina), gebruikersbeheer in de editor of de Admin-API, of
  aanmelden via OIDC zonder eigen `settings.js` staat in geen gelezen bron. ONBEKEND - eerst vaststellen met een
  rooktest in jelly-leon-platform (`tools/nodered_rooktest.py`) tegen de gepinde digest.

**Bestaande praktijk bij apps van derden.** Gotify, Uptime Kuma, Beszel-hub en Authentik
(`jelly-leon-platform:install/manifests/`) krijgen hun beheerder in de app zelf, via een eerste-gebruik-venster dat als
`post_install_checks` in het manifest staat (`standaardaccount`, `eerste_beheerder`, `eerste_account`,
`initiele_setup`). Bij Authentik maakt de beheerder daarna in de app een API-token voor home-control-hub. home-control-hub
bewaart zo'n aanmelding met `PUT /integrations/v1/sources/{source_id}` in zijn vault, toont alleen de laatste 4 tekens en
zet haar niet in de verzoekhash of de audit (`home-control-hub:apps/api/src/integrations/sources.py`). Die beheerders en
tokens staan nu alleen in vrije tekst; de standaard kent ze niet.

## Eisen

1. Contract kop 3.1, letterlijk: "Nooit geheimen in het Custom App-formulier, in Git of in logs. Elke app maakt bij
   eerste start ontbrekende geheimen aan in `/appdata/secrets/` (map 0700, bestanden 0600, eigenaar 568:568, nooit een
   bestaand geheim overschrijven; …)" en "Uitzondering op "geen geheimen in het formulier": het **eenmalige
   enrollment-token** (`HUB_ENROLLMENT_TOKEN`) mag als omgevingsvariabele in de Custom App staan; het vervalt na 60 min
   of na eerste gebruik en is daarna waardeloos." Er komt geen tweede uitzondering (C-12).
2. Geen geheim en geen gebruiker als waarde in een manifest: geen wachtwoord, geen wachtwoordhash, geen sleutel zoals
   `credentialSecret`, geen token, en geen gebruikerslijst of gebruikersrecord (een lijst of object onder `users` of
   `gebruikers` dat de app als account laadt), ook niet in een omgevingswaarde zoals `FLOWS` (contract kop 9, EX47:
   "een geheime waarde in een manifest"). Een accountnaam alleen (`accounts[].id`, W1) is geen geheim en geen gebruiker
   als waarde: hij geeft niemand toegang, de app krijgt hem niet uit het manifest (de vertaling zet niets uit
   `accounts` in de payload, besluit 5), en de beheerder typt hem zelf in de app. Zo'n naam staat nu ook al in code
   (home-control-hub `policy.SERVICE_USER`). Verboden zijn de aanmeldgegevens (wachtwoord, hash, sleutel, token) en
   gebruikerslijsten.
3. Installatie alleen als standaard Custom App, nooit YAML of compose (contract kop 1).
4. De container draait als 568:568 waar de image dat toestaat (contract kop 1).
5. Fail-closed: zonder beheerder blijft de editor van Node-RED dicht, ook voor lezen (C-11). Toetsbaar: `adminAuth` in
   `FLOWS` is precies `{"type":"credentials","users":[]}`, zonder `default` (S-P2-410). Een afsluiting die van één
   formulierwaarde afhangt (`FLOWS`), moet bewaakt worden: de monitor en een `post_install_checks`-controle melden een
   open editor. Wat niet gemeten is, telt niet als in orde.
6. Algemeen: de voorziening geldt voor elke app die een beheerder of een dienstaccount nodig heeft, niet alleen voor
   Node-RED.
7. Een geheim dat iemand moet kennen, ziet hij hooguit één keer, en het staat nooit in een log, een preview-diff, een
   auditdetail, een verzoekhash, de idempotentie-opslag of EVIDENCE (contract kop 3.1; VALKUILEN V-094).
8. Bestaande manifesten zonder de voorziening valideren precies zo als nu.
9. Een kopie van een dataset waarin een app de sleutel naast de versleutelde gegevens bewaart (Node-RED: `/data`, met
   `.config.runtime.json` naast de versleutelde credentials), geldt als geheim. Een offsite-kopie is alleen versleuteld
   toegestaan, met een sleutel die niet bij die kopie staat (`ai-server-manager:docs/platform-v2/02_BOUWPLANNEN/B12_BACKUP_HERSTEL.md`,
   kop "B12-003 — App-consistent exports": "encrypt, offsite key separation";
   `ai-server-manager:docs/platform-v2/01_ARCHITECTUUR/04_GEGEVENS_EN_STORAGE.md`, kop "Back-uplagen", punt 3: "secrets
   (versleuteld)").

## Opties

Per optie: waar het geheim ontstaat, waar het staat, wie het één keer ziet, hoe het roteert of wordt ingetrokken, wat
er in het formulier staat, wat een handmatige installatie zonder Hub doet, en welke contractkop het raakt.

### Optie 1 — De beheerder zet zijn account zelf in de app; het dienstaccount gaat via de beheerder naar de afnemer

Bron: contract kop 9 (`post_install_checks` voor "open eerste-gebruik-vensters (bv. een standaardwachtwoord of een open
setup-pagina)"; "tot de beheerder ze bevestigt, is de app niet "gezond" (gate)") en kop 3.10 (credentials van andere
apps voert de gebruiker in de Hub-UI in, versleuteld, alleen de laatste 4 tekens zichtbaar); de bestaande praktijk
hierboven.

- **Ontstaat:** het beheerwachtwoord kiest de beheerder zelf in het eerste-gebruik-venster van de app. Het dienstaccount
  maakt de beheerder na zijn eigen aanmelding in de app, met de rechten uit het manifest.
- **Staat:** in de app (zoals de app het bewaart) en, voor een dienstaccount, versleuteld in de vault van de afnemer
  (home-control-hub: `PUT /integrations/v1/sources/{source_id}`).
- **Ziet één keer:** de beheerder, omdat hij het zelf typt of kopieert. Het staat niet in het formulier en niet in een
  log. Voert hij een dienstaccount in via de Hub-UI, dan gaat de aanmelding door de Hub naar de afnemer; daar gelden de
  regels van kop 3.10 ("Logs en evidence worden geredigeerd").
- **Roteren en intrekken:** in de app, door de beheerder; bij de afnemer een nieuwe aanmelding invoeren of de oude wissen
  (`clear_credential` in `sources.py`).
- **Formulier:** niets van het account.
- **Handmatig zonder Hub:** hetzelfde; de `post_install_checks` zijn dan de controlelijst in het installatiedocument,
  zonder gate.
- **Contract:** kop 3.1 ongemoeid; kop 9 en 3.10 gevolgd. Restrisico: tot de beheerder het venster sluit, kan iedereen
  op het vertrouwde netwerk beheerder worden of het standaardwachtwoord gebruiken. Dit is dus géén fail-closed weg; voor
  Node-RED botst het met C-11.

### Optie 2 — De app maakt het geheim zelf bij de eerste start

Bron: contract kop 3.1, eerste punt.

- **Ontstaat:** in de container, bij de eerste start (CSPRNG).
- **Staat:** in de eigen opslag van de app (eigen images: `/appdata/secrets/`, 0600, 568:568).
- **Ziet één keer:** niemand, zolang alleen de app het gebruikt. Moet een mens of een andere app het kennen, dan is er
  geen toegestane weg: niet de log (kop 3.1; alleen de eenmalige setupcode van de Hub staat in de logs, kop "2. Apps,
  images en poorten", alinea "Eerste installatie"), niet het formulier, en niet via de Hub (kop 3.1 punt (b): geen
  geheimen via Hub-connector, items of events).
- **Roteren en intrekken:** door de app zelf; per app ONBEKEND.
- **Formulier:** niets.
- **Handmatig zonder Hub:** hetzelfde.
- **Contract:** kop 3.1 gevolgd. Geschikt voor geheimen die de app alleen zelf gebruikt (zoals de sleutel van de
  credentials van Node-RED), niet voor een aanmelding die een mens of een andere app nodig heeft.

### Optie 3 — Een wachtwoordhash en een `credentialSecret` in het formulier (het huidige profiel van home-control-hub)

Bron: home-control-hub `policy.define_arguments` en `policy.build_profile`.

- **Ontstaat:** in home-control-hub (`secrets.token_urlsafe`); hashes en `credentialSecret` in de eigen vault
  (`vault://automation/node-red-profiel`), het dienstwachtwoord als aanmelding van de bron.
- **Staat:** ook in het formulier van de Custom App (argumenten) en in elk antwoord van de route.
- **Ziet één keer:** het beheerwachtwoord de beheerder (in het antwoord); de hashes en de `credentialSecret` iedereen die
  het formulier of het antwoord ziet, telkens opnieuw.
- **Roteren:** alleen het beheerwachtwoord (`nieuw_beheerwachtwoord`); de `credentialSecret` nooit.
- **Formulier:** hashes, gebruikers en `credentialSecret`.
- **Handmatig zonder Hub:** de beheerder zet de argumenten met de hand in het formulier; zo is het profiel bedoeld
  (`Profile.form`: "TrueNAS → Apps → Discover Apps → Custom App").
- **Contract:** botst met kop 3.1. **Afgewezen** door C-12 (dit was optie 2 van het menu).

### Optie 4 — De Hub maakt het geheim en de executor zet het in het formulier (vault-weg)

Bron: contract kop 3.1 punt (a); ADR-0015 besluit 2 (`custom_app.env[].value_from` naar een geheim dat de Hub maakt).

- **Ontstaat:** in de Hub (CSPRNG, minstens 256 bit), in zijn vault als `vault://app/<app_id>/<parameter>`.
- **Staat:** na het invullen door de executor als omgevingsvariabele in het formulier.
- **Ziet één keer:** niemand in de Hub-UI; een omgevingsvariabele in het formulier is "zichtbaar in de TrueNAS-UI"
  (jelly-leon-platform ADR-0023, kop "Afgewezen").
- **Roteren:** de Hub maakt een nieuwe waarde en de executor voert een `app.update` uit.
- **Formulier:** het geheim, na het invullen.
- **Handmatig zonder Hub:** de gebruiker vult het geheim zelf in TrueNAS in (contract kop 3.1 punt (a)).
- **Contract:** vraagt de uitzondering op kop 3.1 die ADR-0015 meldt. **Afgewezen** door het aanvullende besluit van de
  gebruiker: geen geheim in het Custom App-formulier, ook niet via de vault-weg.

### Optie 5 — De executor zet de aanmelding als bestand in de dataset van de app

Bron: contract kop 3.1 punt (a) (de Hub maakt en bewaart, de executor levert), met een bestand als bestemming in plaats
van de `values`.

- **Ontstaat:** in de Hub; **staat:** in zijn vault en daarna in de dataset van de app.
- **Ziet één keer:** de Hub kan het één keer tonen (kop 3.1: "precies één keer getoond"); de app leest het bestand.
- **Roteren:** een nieuwe uitvoering van dezelfde actie.
- **Formulier:** niets.
- **Handmatig zonder Hub:** niet mogelijk zonder bestandstoegang op de NAS, en die sluit de installatieregel uit
  (contract kop 1).
- **Contract:** vraagt een nieuwe actie die bestanden op de NAS schrijft (kop 8, tabel met acties; de risicoklasse is een
  besluit). De TrueNAS-methode daarvoor staat in geen gelezen bron (ONBEKEND). Het geheim gaat door Hub en executor.
  **Niet gekozen:** vraagt een contractwijziging en verbreedt wat de executor mag.

### Optie 6 — Een startopdracht in het formulier maakt de accounts (officiële image)

Bron: `docs/TRUENAS_APP_VALUES.md`, kop "Formulierwaarden (ix-app 1.4.8) en hoe het manifest ze vult", en
`apps/api/src/appstore/manifest/truenas.py`, klasse `IxAppValues` (velden `entrypoint` en `command`); jelly-leon-platform
ADR-0023, kop "Wat het manifest niet kan (geblokkeerd)" ("geen startopdracht").

- **Ontstaat:** in de container, door opstartcode die in het manifest staat; **staat:** in de opslag van de app.
- **Ziet één keer:** hetzelfde probleem als optie 2: er is geen toegestane weg naar beheerder of afnemer.
- **Roteren en intrekken:** ONBEKEND (hangt af van de opstartcode).
- **Formulier:** de opstartcode (geen geheim).
- **Handmatig zonder Hub:** dezelfde startopdracht met de hand in het formulier.
- **Contract:** een nieuw veld in kop 9; de standaard weigert nu bewust `command` en `entrypoint` in de waarden van een
  catalogusapp (`apps/api/src/appstore/manifest/rules.py`, `FORBIDDEN_VALUE_KEYS`). Code in een manifest is moeilijk te
  beoordelen. **Niet gekozen** voor de standaard; voor Node-RED door de gebruiker afgewezen (punt 7, optie C).

### Optie 7 — Een eigen image bovenop de officiële image

Bron: `ai-server-manager:docs/platform-v2/02_BOUWPLANNEN/B08_APPSTORE_LIFECYCLE.md`, kop "5. Niet-regressie,
randgevallen en verbodsbepalingen" ("Eigen apps gebruiken GitHub repositories, overige apps alleen officiële images");
jelly-leon-platform ADR-0023, kop "Afgewezen" ("Eigen image met `settings.js` ingebakken").

- **Ontstaat:** in de container, door eigen startcode; **staat:** in de opslag van de app.
- **Ziet één keer:** hetzelfde probleem als optie 2; de overdracht vraagt een eigen ontwerp.
- **Roteren en intrekken:** in de eigen startcode, nog te ontwerpen.
- **Formulier:** niets.
- **Handmatig zonder Hub:** dezelfde eigen image in het formulier.
- **Contract:** kop 3.1 gevolgd; botst met B08 kop 5 en met ADR-0023. **Niet gekozen** voor de standaard (de standaard
  hoeft er niets voor te kunnen); voor Node-RED door de gebruiker afgewezen (punt 7, optie B).

## Besluit

1. **Een account gaat nooit door het formulier, het manifest of Git.** Een beheerder of dienstaccount komt er volgens
   optie 1: de beheerder zet het in de app zelf, en een dienstaccount voert hij één keer in bij de afnemer. Een geheim
   dat alleen de app zelf gebruikt (zoals de sleutel van de credentials) maakt de app bij de eerste start (optie 2).
   Reden: alleen deze twee voldoen aan eis 1 en 2 zonder contractwijziging. Optie 3 en 4 heeft de gebruiker afgewezen,
   optie 5 vraagt een contractwijziging, en optie 6 en 7 zijn voor de standaard niet nodig.
2. **Nieuw optioneel veld `accounts` in manifest v1.** Het manifest verklaart welke accounts er moeten komen, met welke
   rechten, voor welke afnemer en met welke controle na installatie. Het bevat nooit een waarde van een account. Precieze
   vorm: kop "Wat er in de manifeststandaard verandert", W1.
3. **Twee aanmaakwijzen, geen andere.**
   - `eerste_gebruik` (alleen `rol: beheerder`): de app opent bij de eerste start zelf een eerste-gebruik-venster
     (standaardwachtwoord of setup-pagina) en de beheerder zet daar zijn eigen wachtwoord. Dat venster is open tot de
     beheerder het sluit; daarom is de gekoppelde `post_install_checks`-controle verplicht, en is de app tot de
     bevestiging niet "gezond" (contract kop 9; de gate van de Hub). Een app waarvoor een besluit "geen anonieme
     toegang" geldt (C-11: Node-RED), gebruikt deze aanmaakwijze niet. De validator kent zo'n besluit niet; de repotest
     van die app moet het bewaken (voor Node-RED: jelly-leon-platform `tests/repo/test_nodered.py`, in S-P3-445).
   - `door_beheerder` (alleen `rol: dienst`): de beheerder maakt het account na zijn eigen aanmelding in de app, met
     precies de rechten uit het manifest (de Hub toont ze hem bij de controle; zie "Catalogus-API en beleid van de
     Hub"), en voert de aanmelding één keer in bij de afnemer. De afnemer bewaart haar versleuteld, toont alleen de
     laatste 4 tekens en zet haar nooit in een log, verzoekhash, idempotentie-opslag,
     audit of antwoord (zoals home-control-hub `sources.py` al doet). Omdat elke lezer van de afnemer die laatste 4
     tekens ziet (home-control-hub: `list_sources` en `get_source` met `require_reader`, dus ook `viewer`; contract
     kop 3.10), is het wachtwoord of token van een dienstaccount minstens 32 willekeurige tekens uit een generator (van
     de app zelf, of een wachtwoordgenerator), nooit een zelfbedacht wachtwoord. Ook in de vorm `gebruiker:wachtwoord`
     telt dat voor het wachtwoord. De afnemer weigert voor zo'n account een korter wachtwoord of token (home-control-hub
     eist nu minstens 8 tekens: `SourceConfig.credential`).
   Een derde aanmaakwijze (bijvoorbeeld "de app maakt het account zelf en draagt het over") komt er alleen met een
   nieuw besluit (punt 7).
4. **Geheimen en accountgegevens worden overal geweigerd.** Een geheim onder een naam die een geheim noemt, langs welke
   weg ook (letterlijk, via een gewone parameter, of als naam/waarde-paar), een geheim onder een naam van een beheerder
   of dienstaccount (ook een geheim dat de Hub maakt), elke wachtwoordhash, elke sleutel die een geheim noemt met een
   niet-lege waarde binnen een tekst, en elke niet-lege gebruikerslijst is een fout, ook als het manifest geen
   `accounts` heeft. Zo kan `FLOWS` geen bcrypt-hash, gebruiker of `credentialSecret` meer dragen in een vorm die
   Node-RED op de gepinde digest als instelling aanneemt. T2 gebruikt daarvoor geen lijst van optievormen: hij toetst
   het deel na de eerste `=` van elk woord dat met `-` begint, en elk woord zonder `-` vooraan, als mogelijke
   instelling (dus ook `-d=`, `--D=`, `-define=`, `---define=`, `--no-define=` en `-vD=`), met JSON en escapes
   opgelost en met een vangnet op de tekst zelf, en hij weigert een instelling uit een bestand (`@`). Een los woord
   `sleutel=waarde` neemt Node-RED niet als instelling aan (het wordt de naam van het flowbestand, zie "Context"); T2
   toetst het toch als instelling (fail-closed), want zonder optielijst ziet hij niet of er een optie voor staat. Er
   komt ook geen beginwachtwoord in het formulier. De regels van `nopt` waarop T2 steunt, zijn gemeten op de gepinde
   digest en worden bij een nieuwe digest opnieuw gemeten (T2 stap 3). Precieze regels: W2 en W3.
   Wat de validator nu doet (`apps/api/src/appstore/manifest/rules.py`): hij weigert een geheimnaam
   (`_SECRET_ENV`, alleen aan het eind van de naam) alleen met een letterlijke waarde, in `_Rules.custom_app` (een
   `env.value`) en in `catalog_value_findings` (een sleutel in `values`, alleen vóór het invullen: na het invullen slaat
   de vertaling die controle over). Een geheimnaam met `value_from` of `{param}` naar een gewone parameter, een
   naam/waarde-paar in `values`, een naam als `SECRET_KEY` en een geheim van de Hub onder een accountnaam laat hij door
   (proeven p1 tot en met p7, "Gevolgen per repo", punt 2). Naar een geheim, een hash of een gebruiker binnen een tekst
   zoekt hij niet; alleen `_INTERPOLATION` (`interpolation_forbidden`) weigert daarbij toevallig een bcrypt-hash waarvan
   de salt met een letter begint, en een argon2-hash, maar niet een bcrypt-hash waarvan de salt met `.`, `/` of een
   cijfer begint (proef n1), en ook geen `-D credentialSecret=…` of `users`-lijst, in welke vorm ook (proeven n2 tot en
   met n10, en c01 tot en met c15). Die gaten zijn er nu al, los van `accounts`; ze sluiten in de herstelstap vóór
   S-P3-490 ("Gevolgen per repo", punt 2), niet in deze stap.
5. **De vertaling voegt niets toe.** `accounts` komt niet in de payload; de vertaling controleert na het invullen van de
   parameters opnieuw op geheimen en accountgegevens (W3). Geen nieuw veld in de uitvoer van `translate` of `preview`.
6. **Het profiel van home-control-hub vervalt.** Elke vorm ervan zet hashes, gebruikers of een `credentialSecret` in het
   formulier, en er blijft niets over om gelijk te trekken: het formulier van Node-RED komt uit het manifest van
   jelly-leon-platform (`STAPPEN_P2.md` kop 8.2, rij C-11: `NODE_RED_CUSTOM_APP.md` verwijst naar dat manifest, stap
   S-P2-290), en het dienstaccount komt volgens punt 3 via de beheerder. Een `credentialSecret` is niet nodig: Node-RED
   maakt zijn sleutel zelf (`flowEncryptionType: "system"`; optie 2). De isolatiecontrole van home-control-hub trekt dat
   gelijk (zie "Gevolgen per repo").
7. **Node-RED op de officiële image: besluit van de gebruiker (2026-11-11, keuzemenu): optie A, "Wachten".** Volgens
   de gelezen bronnen kan deze image geen van beide aanmaakwijzen leveren: er is geen eerste-gebruik-venster,
   gebruikers bestaan alleen als bcrypt-hash in de instellingen (`-D adminAuth=…`), en gebruikersbeheer in de app is
   niet gevonden (ONBEKEND, zie "Context"). C-11 sluit een venster bovendien uit. Elke weg die wel werkt, vraagt een
   besluit dat niet uit de bronnen volgt. De gebruiker koos daarom A: Node-RED krijgt voorlopig geen beheerder en geen
   dienstaccount via de voorziening. De rest van deze ADR (het veld `accounts`, W1 tot en met W3, het vervallen van het
   profiel van home-control-hub) gaat door.
   - **De veiligste stand, toetsbaar:** `adminAuth` in `FLOWS` is precies `{"type":"credentials","users":[]}` (zonder
     `default` en zonder gebruikers), en het Node-RED-manifest heeft geen `accounts`, dus ook geen `eerste_gebruik`.
     Dan is de editor zonder beheerder helemaal dicht, ook voor lezen (C-11), en kan home-control-hub Node-RED niet
     lezen. De repotest van jelly-leon-platform dwingt dat af ("Gevolgen per repo", punt 4).
   - **Voorwaarde: S-P2-410.** Op 2026-11-11 is het manifest nog niet in die stand: `FLOWS` geeft een anonieme
     gebruiker leesrecht (`"default":{"permissions":"read"}`, ADR-0023 besluit 4), dus de editor en de Admin-API zijn
     op het vertrouwde netwerk zonder beheerder leesbaar. De dichte stand geldt pas als S-P2-410 bouwpunt 3 klaar is
     (3a: `adminAuth` zonder `default`; 3e: de repotest eist die gelijkheid en faalt bij een `default`). S-P3-445 begint
     pas na S-P2-410 (regel "Vooraf" van S-P3-445).
   - **Restrisico: `FLOWS` is de enige afsluiting.** Ontbreekt `FLOWS` of verandert het in het formulier (ook bij een
     `app.update`), dan start Node-RED zonder `adminAuth`, en dan is de editor voor iedereen open met alle rechten (zie
     "Context"). Dat vraagt rechten op het formulier of een goedgekeurde update, maar de healthcheck ziet het niet.
     Daarom blijven de monitor (`healthcheck.monitor`) en de controle na installatie `beveiliging_actief` verplicht, en
     moeten ze een beveiligde van een onbeveiligde Node-RED onderscheiden (S-P2-410 bouwpunt 3c, met controleproef N2;
     de status zonder anonieme rechten wordt daar gemeten, niet aangenomen). De gate van de Hub op `post_install_checks`
     (S-P2-080 bouwpunt 3) en het monitorplan melden een open editor.
   - **De route `POST /integrations/v1/automation/hardening-profile` van home-control-hub bestaat nog** (op `23f81e8`,
     functie `router.post_hardening_profile`). Tot S-P3-360 levert ze een beheerder en een dienstaccount als
     bcrypt-hash plus een `credentialSecret`, als argumenten voor het Custom App-formulier (`policy.define_arguments`;
     `Profile.form`: "TrueNAS → Apps → Discover Apps → Custom App"). Wie die argumenten gebruikt, zet geheimen in het
     formulier: dat gaat tegen C-12 en contract kop 3.1 in, en het is geen weg uit deze stand. Het vervallen van het
     profiel hangt niet af van dit punt; deze ADR stelt voor dat deel eerder te laten lopen ("Gevolgen per repo",
     punt 5).
   - **Gevolg:** I10-10-01 blijft "Geblokkeerd" en het deel "beheerder en dienstaccount" van B11-001 blijft open
     (gemeten op 2026-11-11: I10-10-01 "Geblokkeerd", B11-001 "Bezig"; deze ADR wijzigt geen EVIDENCE). S-P3-445
     gebruikt de voorziening voor Node-RED niet; alleen de validatorkopie, teksten en verwijzingen gaan mee.
   - **Afgewezen (keuze van de gebruiker, 2026-11-11):**
     - *B. Eigen dunne image bovenop de officiële Node-RED-digest* (optie 7), die de accounts bij de eerste start zelf
       maakt. Gevolg zou zijn: B08 kop 5 ("overige apps alleen officiële images") en jelly-leon-platform ADR-0023
       ("Afgewezen") veranderen, een eigen release met digest en een rooktest bij elke nieuwe Node-RED-digest, en een
       eigen ontwerp voor de overdracht van de aanmelding zonder log of formulier. Een eigen image zou de afsluiting in
       de image zelf kunnen zetten, zodat ze niet van `FLOWS` afhangt (ONBEKEND tot dat ontwerp en een rooktest). Een
       venster op het netwerk (zolang het open is, kan iedereen op het vertrouwde netwerk beheerder worden) is door
       C-11 uitgesloten, net als bij punt 3: B zou de beheerder zonder venster moeten leveren. Een venster kan er
       alleen komen met een nieuw besluit van de gebruiker dat C-11 wijzigt.
     - *C. Startopdracht via het formulier op de officiële image* (optie 6). Gevolg zou zijn: een nieuw veld in de
       standaard en in contract kop 9, opstartcode in een manifest, gedrag van de image ONBEKEND (eerst een rooktest),
       en dezelfde overdrachtsvraag als bij B. Ook hier sluit C-11 een venster uit.
     De gebruiker kan later opnieuw kiezen (bijvoorbeeld als een rooktest gebruikersbeheer of aanmelden via OIDC in de
     officiële image aantoont). Dan komt er eerst een eigen ontwerpstap, met R-VEILIG en R-MANIFEST; deze ADR wordt
     daarvoor niet stil aangepast.
8. **C-5: wat deze voorziening dekt.** Wel: de beheerder en het dienstaccount, voor Node-RED pas als de gebruiker
   later een weg kiest (punt 7: nu A, wachten). Niet:
   - de flowbundels: dat is inhoud, geen account; het importhulpmiddel heeft een aangemelde beheerder nodig
     (jelly-leon-platform ADR-0023 besluiten 5 en 6);
   - `HUB_URL`: het importhulpmiddel zet die als globale omgevingsvariabele van Node-RED (ADR-0023 besluit 6), dus ook
     alleen met een aangemelde beheerder. Via het formulier kan het niet: `HUB_URL` is geen manifestparameter en alleen
     my_house heeft een uitzondering zonder koppeling (contract kop 3.2);
   - het service-token: een flowruntime kan zich niet inschrijven (kop 3.3 vraagt een Ed25519-sleutel) en kop 3.1
     verbiedt een langlevend token in het formulier.
   Deze drie blijven geblokkeerd onder C-5.
9. **Contract.** Kop 3.1 verandert niet: de voorziening zet niets in het formulier, en het enrollment-token blijft de
   enige uitzondering. Kop 9 krijgt een aanvulling: het nieuwe optionele veld `accounts`, de twee aanmaakwijzen, de
   weigering `account_gegevens_verboden`, en dat een geheim dat de Hub maakt nooit de aanmelding van een beheerder of
   dienstaccount is (W2, regel N3; een nieuw manifestveld valt onder P-CONTRACT). Dat is contractwerk voor
   ai-server-manager. Termijn (BESLUITEN_EN_BLOKKADES, kop "C-12 — …", punt "Na het besluit"; S-P3-490, regel
   "Vooraf"): de contractronde is klaar vóór de gate van P2 (S-P2-910), en de wijziging is vóór het begin van P3 in
   alle repos overgenomen, want het contract verandert niet tijdens P3 (V-018). De melding van
   ADR-0015 (een uitzondering op kop 3.1 voor geheimen van de Hub in een omgevingsvariabele van een Custom App) blijft
   zoals ze is; deze ADR verandert daar niets aan.

## Wat er in de manifeststandaard verandert

| # | Wijziging | Schema (`schemas/manifest-v1.schema.json` en de byte-gelijke kopie `apps/api/src/appstore/manifest/manifest-v1.schema.json`) | Model en regels | Vertaling (`translate.py`) | `docs/MANIFESTSTANDAARD.md` | Catalogus-API en beleid van de Hub | Schemawijziging | Contract | Bouw |
|---|---|---|---|---|---|---|---|---|---|
| W1 | veld `accounts` | ja: eigenschap `accounts` en `$defs/Account` | `model.py` (`Account`), `rules.py` (nieuwe controle) | niets in de payload | nieuwe kop + rij in "Opbouw in het kort" | geen wijziging (zie hieronder) | ja | kop 9: aanvulling | S-P3-490 |
| W2 | geheimen en accountgegevens weigeren | nee | `rules.py` (naamregels in `_Rules.custom_app`, `_Rules.catalog_app` en `catalog_value_findings`; tekstregels op de ruwe gegevens), `findings.py` | — | weigerregels + codes | geen wijziging | nee | kop 9: in dezelfde aanvulling (voert EX47 en kop 3.1 uit) | herstelstap vóór S-P3-490 |
| W3 | opnieuw controleren na het invullen | nee | — | ja | zin in "Geheimen in de vertaling" | geen wijziging | nee | nee | herstelstap vóór S-P3-490 |

**Catalogus-API en beleid van de Hub: geen wijziging (keuze (a) uit de review R-MANIFEST).** `accounts` blijft binnen de
validator. De catalogus-API geeft de eisen van een app met een vaste set sleutels (`apps/api/src/appstore/catalog/service.py`,
`requirements()`, met o.a. `post_install_checks`, zonder `accounts`), en het beleid van de Hub laat in
`parameters.manifest` alleen een vaste witte lijst toe (`ai-server-manager:apps/policy/src/asm_policy/params.py`,
`validate_manifest_meta`, met o.a. `post_install_checks`, zonder `accounts`). Beide blijven zoals ze zijn. Reden uit de
bronnen: contract kop 9 maakt `post_install_checks` de gate voor wat de beheerder na installatie moet doen ("tot de
beheerder ze bevestigt, is de app niet "gezond""), en die controles komen al bij de Hub; elk account noemt zijn controle
(`controle`, W1); een nieuw veld in een antwoord of in de witte lijst van het beleid vraagt een exacte veldtest
(V-046), een OpenAPI-wijziging en een wijziging van het beleid (P-VEILIG) in een andere repo, zonder dat het iets
veiliger maakt; en besluit 5 zet al geen nieuw veld in de uitvoer. Daarom moet de gekoppelde controle in haar
`beschrijving_nl` zelf zeggen wat de beheerder doet; bij een dienst noemt ze de afnemer (`afnemer`) en verwijst ze naar
de rechten in het manifest (`accounts[].rechten`) in plaats van ze op te sommen. Het volledige manifest, dus ook
`accounts` (zonder waarde), blijft leesbaar via `GET /catalog/v1/versions/{version_id}` (veld `manifest`); dat is geen
nieuw veld. **Waar de beheerder de rechten ziet:** het scherm van de Hub met de controles na installatie toont bij een
controle die door een account met `rol: dienst` wordt genoemd, ook de `afnemer` en de `rechten` van dat account, gelezen
uit het veld `manifest` van die route. Die route vraagt dezelfde rol (`viewer`) als `GET /catalog/v1/entries`, die de
Hub al leest (`apps/api/src/appstore/api/catalog.py`, functies `list_entries` en `get_version`;
`ai-server-manager:apps/api/src/hub/catalog/appstore.py`). Dat is geen nieuw veld en geen wijziging van het beleid;
keuze (a) blijft ("Gevolgen per repo", punt 6). Zonder die lijst op het scherm kan de beheerder niet "precies de rechten
uit het manifest" geven (besluit 3). home-control-hub leest `accounts` niet tijdens het draaien ("Gevolgen per repo",
punt 5). Grens van de Hub: het schema laat voor `post_install_checks[].beschrijving_nl` 1000 tekens toe, maar het beleid
van de Hub weigert in `validate_manifest_meta` meer dan 500 (`_text(…, max_len=500)`), en dat is al bekend
(R-MANIFEST). Voor een controle die door een account wordt genoemd, mag een manifest dat hier geldig is, daar niet op
die lengte vallen. Daarom blijft de tekst van zo'n controle binnen 500 tekens, zolang het contract die grens niet
gelijktrekt, en bewaakt W1 dat met de code `account_controle_te_lang` (hieronder). De verwijzing naar de rechten in
plaats van een opsomming houdt de tekst kort (een dienst mag tot 32 rechten van elk 64 tekens hebben). De overige
verschillen tussen schema en beleid regelt deze ADR niet: de lengte van een controle zonder account, een stuurteken in
`beschrijving_nl` (het beleid weigert stuurtekens zoals een regelterugloop, met `CONTROL_RE` in `params.py`; het schema
niet) en meer dan 20 controles (het beleid: "hooguit 20 controles"; het schema heeft geen grens). Die blijven een
contractmelding.

**W1 — veld `accounts` (optioneel; ontbreekt het, dan verandert er niets).** Een lijst van hooguit 16 objecten
`$defs/Account` met `additionalProperties: false`:

| Veld | Type | Verplicht | Regel |
|---|---|---|---|
| `id` | `$defs/Name` | ja | uniek binnen `accounts`; de naam van het account in de app waar de app een naam laat kiezen. Een naam, geen aanmeldgegeven: geen geheim en geen gebruikersrecord (eis 2); de app krijgt hem niet uit het manifest |
| `rol` | `beheerder` \| `dienst` | ja | hooguit één `beheerder` per manifest |
| `aanmaak` | `eerste_gebruik` \| `door_beheerder` | ja | `eerste_gebruik` alleen bij `beheerder`, `door_beheerder` alleen bij `dienst` |
| `afnemer` | `$defs/AppId` | alleen bij `dienst` | de vaste `app_id` van de app die de aanmelding gebruikt (contract kop 2); niet de eigen `app_id` |
| `rechten` | lijst van 1 tot 32 unieke teksten, patroon `^[a-z][a-z0-9_.:-]{0,63}$` | alleen bij `dienst` | de rechten in de app; het patroon laat geen `*` toe; de beheerder heeft geen lijst (alle rechten) |
| `controle` | `$defs/Name` | ja | een `id` uit `post_install_checks`; meerdere accounts mogen dezelfde controle noemen; de `beschrijving_nl` van die controle zegt wat de beheerder doet (de Hub toont die tekst, en bij een `dienst` daarnaast `afnemer` en `rechten` uit het manifest; zie "Catalogus-API en beleid van de Hub"), bij een `dienst` met de afnemer en een verwijzing naar `rechten` (geen opsomming), en is hooguit 500 tekens (de grens van het beleid van de Hub; `account_controle_te_lang`) |
| `beschrijving_nl` | tekst, 10 tot 1000 tekens | ja | wat de beheerder doet, zonder waarde |

Regels in `rules.py` (een nieuwe methode van `_Rules`, aangeroepen vanuit `_Rules.run`):
- `account_ongeldig` (fout): een combinatie die de tabel verbiedt (bijvoorbeeld `dienst` zonder `afnemer` of `rechten`,
  `beheerder` met `afnemer` of `rechten`, `eerste_gebruik` bij `dienst`, twee keer `beheerder`, `afnemer` gelijk aan de
  eigen `app_id`), of een `dienst` zonder een `beheerder` in hetzelfde manifest (er moet iemand zijn die het account
  maakt).
- `account_controle_ontbreekt` (fout): `controle` noemt geen bestaande `post_install_checks[].id`.
- `account_controle_te_lang` (fout): de `beschrijving_nl` van een `post_install_checks`-controle die door een account
  wordt genoemd, is langer dan 500 tekens (de grens van `validate_manifest_meta` in het beleid van de Hub; het schema
  blijft op 1000 voor controles zonder account). De regel vervalt zodra contract en beleid dezelfde grens hebben.
- Dubbele `id`: de bestaande code `duplicate_name`.
- Geen van deze codes is een waarschuwing en geen ervan staat op de lijst van `accepted_findings`.

**W2 — geheimen en accountgegevens weigeren (altijd, ook zonder `accounts`).** De regels lopen op de ruwe gegevens, net
als `validator._compose_findings`, zodat de melding er ook is als het schema al faalt. Ze sluiten de gaten die de
huidige validator al heeft (besluit 4; proeven in "Gevolgen per repo", punt 2) en hebben het veld `accounts` niet nodig.

*Begrippen.*
- Een **geheimnaam** is een naam waarin een van de woorden van `rules._SECRET_ENV` (`PASS`, `PASSWD`, `PASSWORD`,
  `SECRET`, `TOKEN`, `API_KEY`, `APIKEY`, `PRIVATE_KEY`, `PRIVATEKEY`, `CREDENTIAL`, `AUTH_KEY`, `SALT`) of `WACHTWOORD`
  staat, gevolgd door het einde van de naam of door `_`, `-` of `.`, zonder verschil tussen hoofd- en kleine letters,
  of gevolgd door een hoofdletter terwijl de letter ervoor een kleine letter is (camelCase: `secretKey`,
  `passwordHash`, `apiKeyValue`; niet `SECRETS_DIR`, `TOKENS` of `PASSWORDLESS`). Zo telt ook `SECRET_KEY`, dat
  `_SECRET_ENV` (alleen aan het eind van de naam) nu mist (proef p7), en `secretKey`, dat ook `_SECRET_ENV` mist. Bij
  een sleutel met punten of `[n]` telt het laatste deel (`adminAuth.users[0].password` → `password`).
- Een **accountnaam** is een geheimnaam waarin ook een van deze woorden staat: `BOOTSTRAP`, `DEFAULTUSER`,
  `DEFAULT_USER`, `ADMIN`, `ROOT`, `SUPERUSER`, `OWNER`, `INITIAL`, `SETUP`, `BEHEER`, `SERVICE_ACCOUNT`,
  `SERVICEACCOUNT`, `DIENST` (vaste lijst; een uitbreiding is een wijziging van de standaard). Voorbeelden:
  `GOTIFY_DEFAULTUSER_PASS`, `AUTHENTIK_BOOTSTRAP_PASSWORD`, `AUTHENTIK_BOOTSTRAP_TOKEN`.
- Een **plek met een naam** is: een `custom_app.env[]` (de naam is `name`), een sleutel in de boom van `values` (een
  YAML-sleutel), een **naam/waarde-paar** in `values` (een object met de sleutels `name` en `value`, zoals
  `additional_envs` van Authentik; de naam is de tekst onder `name`) en een parameter (de naam is `name`).
- Een **gewone parameter** is een parameter zonder geheimmarkering: niet `secret: true`, niet `type: secret_ref` en niet
  `source: secret` of `generated`, welke `source` ook (`wizard`, `default`, `dataset_picker`). De vertaling vult hem in,
  dus zijn waarde komt in het formulier of in de `values`.
- Een **geheim van de Hub** is een parameter met `source: generated` en `secret: true` (contract kop 3.1 punt (a);
  ADR-0015).

*Naamregels* (per plek één code):
- **N1 `secret_literal_forbidden`** (fout, bestaande code): een geheimnaam met een niet-lege letterlijke waarde, ook als
  het een accountnaam is. Dat doen `_Rules.custom_app` (`env.value`) en `catalog_value_findings` (een sleutel in
  `values`) nu al, en dat blijft zo (tegenproef p5b); nieuw zijn de bredere geheimnaam (p7), het naam/waarde-paar met
  een letterlijke `value` (p2), en een parameter met een geheimnaam zonder geheimmarkering met een niet-lege `default`
  of `example` (de parameter `beheer_wachtwoord` in p5).
- **N2 `secret_in_form`** (fout, bestaande code; als fout nooit te accepteren): een geheimnaam die geen accountnaam is,
  en die zijn waarde krijgt via `value_from` of `{param}` van een gewone parameter (bijvoorbeeld `APP_DB_PASSWORD` uit
  een wizardparameter). Een parameter mét geheimmarkering houdt de bestaande regels (een fout `secret_in_form`, of bij
  een geheim van de Hub de te accepteren waarschuwing).
- **N3 `account_gegevens_verboden`** (fout; niet te accepteren, ook niet met `accepted_findings: secret_in_form`): een
  accountnaam die zijn waarde krijgt via `value_from` of `{param}`, van welke parameter ook, dus ook van een geheim van
  de Hub (p1, p3, p4, p5 voor de omgevingsvariabele, p6). Op dat pad geeft de validator dan geen N2 en geen
  waarschuwing `secret_in_form`. Zo is de vault-weg voor een beheerder en een dienstaccount dicht (aanvullend besluit van
  de gebruiker); de aanmelding van een account komt alleen volgens punt 3.
- **Geen fout:** een geheimnaam die geen accountnaam is, met `value_from` of `{param}` naar een geheim van de Hub
  (contract kop 3.1 punt (a); ADR-0015), zoals `db_password: {param: db_password}` en `secret_key: {param: secret_key}`
  van Authentik. Daar blijft de bestaande waarschuwing `secret_in_form` gelden.

*Tekstregels* (`account_gegevens_verboden`, fout). Op elke tekst van het manifest (ook `description_nl` en
`beschrijving_nl`) T1; op `custom_app.env[].value`, elke tekst in `values` en de `default` of `example` van een parameter
ook T2:
- **T1** een wachtwoordhash: bcrypt (`$2a$`, `$2b$`, `$2y$` met kosten en 53 tekens) en hashes in PHC-vorm (`$argon2…`,
  `$scrypt$`, `$pbkdf2…`).
- **T2** decoderen zoals Node-RED en dan toetsen, met een vangnet op de tekst zelf. Drie stappen, elk een fout bij een
  treffer:
  1. *Splitsen en decoderen, zonder optielijst.* De tekst wordt gesplitst op spatie, tab en regeleinde (de
     standaard-`IFS` van bash, zoals `entrypoint.sh` `$FLOWS` ongequoot doorgeeft); andere witruimte, zoals U+00A0,
     splitst niet en blijft in het woord. Daarna per woord, zonder te kijken welke optie het is (zie "Context": een
     define-waarde is altijd het deel na de eerste `=` van een optiewoord, of het woord na een optie):
     - een woord dat met `-` begint en een `=` heeft: het deel na de eerste `=` telt als instelling als het zelf de
       vorm `sleutel=waarde` heeft (`-D=credentialSecret=…`, `--def=…`, `-d=…`, `--D=…`, `-define=…`,
       `---define=…`, `--no-define=…`, `-vD=…`); begint dat deel met `@`, dan is het een fout;
     - een woord dat niet met `-` begint en een `=` heeft, telt als instelling. Na een optie zonder `=`
       (`-D sleutel=waarde`, `--define sleutel=waarde`, `-d sleutel=waarde`) is het dat voor Node-RED ook. Een los
       woord neemt Node-RED niet als instelling aan (het wordt de naam van het flowbestand); T2 toetst het toch als
       instelling (fail-closed), want zonder optielijst ziet hij niet of er een optie voor staat;
     - een woord dat niet met `-` begint en met `@` begint, is altijd een fout: na een optie leest `red.js` dan
       instellingen uit een bestand dat de validator niet kan zien (fail-closed);
     - een woord zonder `-` vooraan en zonder `=` dat met `{` of `[` begint, wordt met JSON gedecodeerd.

     De waarde van een instelling wordt met JSON gedecodeerd (zoals `red.js` met `JSON.parse`; lukt dat niet, dan
     blijft het tekst). In het resultaat (met de JSON-escapes opgelost, dus ook `\u0024` voor `$` en `\/` voor
     `/`), en ook in de `sleutel` zelf, is een fout:
     - een sleutel met een geheimnaam en een niet-lege waarde (`"credentialSecret":"…"`, `-D credentialSecret=…`,
       `--define credentialSecret=…`, `--def=credentialSecret=…`, `-D=credentialSecret=…`, `-d=credentialSecret=…`,
       een los woord `credentialSecret=…`, `--define=adminAuth.users[0].password=…`);
     - een sleutel `users` of `gebruikers` met een niet-lege lijst of een niet-leeg object (`users.js` maakt van een
       object een lijst met één gebruiker);
     - een hash volgens T1 in een gedecodeerde tekst.
  2. *Vangnet op de ruwe tekst.* Los van stap 1 geldt op de hele tekst, en op dezelfde tekst met alle JSON-escapes
     opgelost (`\uXXXX`, en ook `\/`, `\"`, `\\`, `\b`, `\f`, `\n`, `\r` en `\t`), ook als geen woord als
     instelling is herkend:
     - een JSON-sleutel (`"naam":`) met een geheimnaam en een niet-lege waarde;
     - een JSON-sleutel `users` of `gebruikers` met een niet-lege lijst of een niet-leeg object;
     - elke `naam=` met een niet-lege waarde (direct na de `=` staat een teken dat geen witruimte is), waarin `naam`
       een geheimnaam is; `naam` is het laatste stuk letters, cijfers en `_` vóór de `=`, ook als er nog `"`, `'` of
       `]` tussen staat (`-vD=credentialSecret=…`, `adminAuth.users[0].password=…`, `settings["credentialSecret"]=…`);
     - elk pad met een deel `users` of `gebruikers` (ook tussen aanhalingstekens of haken), gevolgd door `[` of `.`,
       vóór een `=` met een niet-lege waarde (`adminAuth.users[0].username=…`);
     - een hash volgens T1, ook als die pas na het oplossen van de escapes zichtbaar is (een bcrypt-hash met `\/`).

     Zo vangt de validator ook een vorm die stap 1 niet als instelling herkent (een nieuwe optie, een ander
     scheidingsteken, een sleutel die met `-` begint), een gebruiker met een ge-escapete hash (proeven n7 en n9) en een
     gebruiker of sleutel als los pad (proeven c13 en c14).
  3. *Geen optielijst; wat wel gemeten is.* T2 hangt niet af van de opties die `nopt` kent, wel van twee regels die op
     2026-11-11 op de gepinde digest zijn gemeten (zie "Context"): een define-waarde is altijd het deel na de eerste
     `=` van een optiewoord, of het woord na een optie; en alleen een define-waarde die met `@` begint, leest een
     bestand. Bij elke nieuwe Node-RED-digest meet de rooktest van jelly-leon-platform een vaste set kandidaatvormen
     ("Gevolgen per repo", punt 4). Klopt een van die twee regels niet meer, dan is dat een wijziging van de standaard
     en wordt het aan appstore-en-fork gemeld; tot T2 is aangepast, geldt het vangnet van stap 2.

*Verhouding tot de bestaande codes.* Een sleutel in de boom van `values` (een YAML-sleutel, geen tekst) valt onder de
naamregels; de tekstregels gelden alleen binnen een tekst. Zo meldt de validator één oorzaak niet twee keer.
`interpolation_forbidden` blijft zoals het is (een andere reden: TrueNAS zet omgevingswaarden in een compose-bestand);
die regel vangt een bcrypt-hash nu alleen als de salt met een letter begint, dus op hetzelfde pad kunnen
`interpolation_forbidden` en `account_gegevens_verboden` samen staan, elk met een eigen reden.

*Geen fout* zijn verder een `{param: …}`-verwijzing die niet onder N2 of N3 valt, en een vault-verwijzing die de
vertaling zelf zet (ADR-0015 punten 4 en 5). De melding noemt het pad en de reden, nooit de
waarde. Ook een schemafout (`invalid_format`, `invalid_value`) noemt de waarde niet als die onder `accounts` staat of
door W2 wordt herkend; nu zet `validator._schema_findings` de waarde altijd in die meldingen.

*Bestaande manifesten* mogen door W2 niet anders uitvallen (eis 8): een nieuwe regel die een bestaand manifest van een
andere repo afkeurt, is een contractwijziging (POORTEN, P-appstore-en-fork kop 7). Gemeten op 2026-11-11 met een
nabootsing van W2 buiten de repo (geen validatorcode) over de 15 manifesten in `deploy/manifest/` van de repos en in
`jelly-leon-platform:install/manifests/`, plus de 6 fixtures in `apps/api/tests/fixtures/manifests/`: de naamregels
treffen alleen `db_password` en `secret_key` van Authentik en `db_password` van `valid-catalog-1-3.manifest.yaml`, alle
drie een `{param}` naar een geheim van de Hub zonder accountnaam (dus geen fout); de tekstregels treffen niets. In
herstelpoging 2 (2026-11-11) opnieuw gemeten met een nabootsing van de tekst hierboven (camelCase-geheimnaam, alle
define-vormen, losse woorden `sleutel=waarde`, `@`, het vangnet): dezelfde drie (geen fout), 0 van 21 geweigerd; de
proeven p1 tot en met p7, p5b en n1 tot en met n10 worden alle geweigerd. In herstelpoging 3 (2026-11-11)
opnieuw gemeten met een nabootsing van de tekst hierboven (T2 zonder optielijst en het uitgebreide vangnet): 0 van 22
bestaande bestanden geweigerd (de 16 bestanden in `deploy/manifest/` van de repos en in
`jelly-leon-platform:install/manifests/`, nu met `overgang-1.3.yaml`, en de 6 fixtures), met dezelfde drie geheimnamen
zonder fout; de proeven c01 tot en met c15 (15 van 15) en n1 tot en met n10, n6b, p1 tot en met p7, p5b, `--d=`,
`--de `, de camelCase-sleutel en n7 en n9 achter `-d=` (24 van 24) worden alle geweigerd. De herstelstap meet het
opnieuw met de validator zelf.

**W3 — vertaling.** `translate` en `POST /catalog/v1/preview` zetten niets uit `accounts` in de payload. Na het
invullen van de parameters (waarden uit de wizard) toetst de vertaling de ingevulde omgevingswaarden (`values.envs` van
een Custom App) en de `values` van een catalogusapp opnieuw: een geheimnaam met een niet-lege waarde die geen
vault-verwijzing van de vertaling zelf is, geeft `secret_in_form`; een accountnaam met een waarde, ook een
vault-verwijzing, geeft `account_gegevens_verboden`; en T1 en T2 gelden op elke ingevulde tekst. Nu slaat
`catalog_value_findings` na het invullen de geheimnaam over (`resolved=True`), en zet `_custom_app_values` een
ingevulde waarde zonder toets in `envs` (proeven p1 tot en met p6: de testwaarde stond in de payload). Bij een treffer
noemt de melding het pad, zonder waarde, en is er geen payload. De `preview_hash`, `vault_refs` en de antwoordmodellen
blijven gelijk: geen veld erbij (V-046) en geen OpenAPI-wijziging. Eén uitzondering, en alleen die: zet de vertaling
ooit zelf `HUB_URL` en `HUB_ENROLLMENT_TOKEN` in de payload (`docs/MANIFESTSTANDAARD.md`, kop "Gereserveerde namen",
zegt dat nu al; `translate.py` en `truenas.py` doen het op 2026-11-11 niet), dan laat W3 precies de naam
`HUB_ENROLLMENT_TOKEN` door: het eenmalige enrollment-token is de enige uitzondering van contract kop 3.1 (eis 1).
Elke andere `HUB_*`-naam en elke andere geheimnaam blijft onder W3; de herstelstap test dat de uitzondering alleen
die naam dekt.

**Kopieën.** W1 wijzigt het schema; W2 en W3 wijzigen de regels en de vertaling. De kopie in jelly-leon-platform
(`apps/api/src/appstore/manifest/manifest-v1.schema.json` en de validator) volgt in S-P3-445 met `tools/validator_kopie.py`.
Repos die een commit van deze repo pinnen, houden het oude gedrag tot zij hun pin verzetten (gemeten op 2026-11-11:
`ASF_COMMIT` 7b3159d in ai-server-manager, `APPSTORE_COMMIT` 7b3159d in jelly-leon, `VALIDATOR_COMMIT` cca4403 in
home-control-hub en zoeken-en-persoonlijke-ai, `ASF_COMMIT` 1967069 in jelly-leon-platform). Een oudere validator weigert
een manifest met `accounts` (`unknown_field`); alleen een repo die het veld gebruikt, moet zijn pin dus verzetten. Een
repo die zijn pin verzet, krijgt ook W2 en W3; volgens de meting onder W2 valt daardoor geen bestaand manifest anders uit.

**Voorstel voor de aanvulling van contract kop 9 (voor de eigenaar van het contract).** "**`accounts: [{id, rol:
beheerder|dienst, aanmaak: eerste_gebruik|door_beheerder, afnemer, rechten, controle, beschrijving_nl}]`** (optioneel)
voor een beheerder of dienstaccount van een app: het manifest verklaart alleen wat er moet bestaan, nooit een waarde.
De beheerder zet zijn account in de app (eerste-gebruik-venster, met een `post_install_checks`-controle); een
dienstaccount maakt hij daarna in de app met precies `rechten` en voert hij één keer in bij `afnemer` (versleuteld, §3.10).
Een wachtwoord, wachtwoordhash, geheime sleutel of gebruikerslijst in het manifest of in een omgevingswaarde wordt
geweigerd (`account_gegevens_verboden`). Een waarde onder een naam die een geheim noemt, komt nooit letterlijk, via een
gewone parameter of als naam/waarde-paar in het manifest of het formulier (`secret_literal_forbidden`, `secret_in_form`).
Een geheim dat de Hub maakt (`source: generated` + `secret: true`) is nooit de aanmelding van een beheerder of
dienstaccount (`account_gegevens_verboden`; niet te accepteren)."

## Gevolgen per repo

Voorgestelde volgorde. De stap-ID's bestaan al in
`ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/STAPPEN_P3.md`; waar er geen stap is, staat dat erbij.

1. **ai-server-manager (contract en platformdocumenten).** Contractwerk, met reviewers R-MANIFEST en R-VEILIG:
   - de aanvulling van kop 9 zoals hierboven; kop 3.1 ongewijzigd; kopieën byte-gelijk in alle repos (P-CONTRACT).
     Geen stap-ID: het is de contractronde die `BESLUITEN_EN_BLOKKADES.md`, kop "C-12 — Hoe krijgt Node-RED een beheerder
     en een dienstaccount?", punt "Na het besluit", vóór de gate van P2 (S-P2-910) plaatst; de wijziging is vóór het
     begin van P3 in alle repos overgenomen (S-P3-490, regel "Vooraf"; zie besluit 9);
   - `docs/platform-v2/03_INSTALLATIE/I10_NODE_RED.md`, kop "1. Vooraf controleren" ("secrets key als aparte managed
     secret") en "Stap 1. Container en secrets" ("credentials secret"): botst met besluit 6 en met jelly-leon-platform
     ADR-0023 besluit 6. Een aparte sleutel kan alleen via het formulier of de vault-weg, en die zijn afgewezen; I10
     moet zeggen dat Node-RED zijn sleutel zelf maakt in `/data` en dat de bescherming de toegang tot de dataset en
     haar kopieën is (eis 9). Geen stap-ID; melden aan de regisseur;
   - back-up (`02_BOUWPLANNEN/B12_BACKUP_HERSTEL.md`): een snapshot of offsite-kopie van de Node-RED-dataset bevat de
     sleutel en de versleutelde credentials samen en geldt dus als geheim (eis 9): offsite alleen versleuteld, met een
     sleutel die er niet naast staat. Geen stap-ID; melden aan de regisseur.
2. **appstore-en-fork: herstelstap van de validator vóór S-P3-490 (nog geen stap-ID; de regisseur plant hem).** De
   huidige validator laat al door wat contract kop 9 (EX47: "een geheime waarde in een manifest") en kop 3.1 weigeren.
   Gemeten op 2026-11-11 door R-VEILIG van deze stap en opnieuw in het herstel, op proefmanifesten buiten de repo
   (afgeleid van de manifesten van Gotify, Authentik en Node-RED in jelly-leon-platform, alleen testwaarden):
   `jl-manifest validate --strict` gaf bij elke proef hieronder "geldig" met 0 fouten, en bij p1 tot en met p6 gaf
   `jl-manifest translate` `ready_for_install: true` met de testwaarde (bij p6 de vault-verwijzing) in de payload.
   - p1: `custom_app.env` `GOTIFY_DEFAULTUSER_PASS` met `value_from` naar een wizardparameter van type `string`
     (`beheer_wachtwoord`);
   - p5: hetzelfde via een parameter met `source: default` en een letterlijke `default`;
   - p2: een naam/waarde-paar in `values` (`authentik.additional_envs` met `name: AUTHENTIK_BOOTSTRAP_PASSWORD` en een
     letterlijke `value`);
   - p3: hetzelfde paar, met de `value` via een wizardparameter;
   - p4: een sleutel `authentik.bootstrap_password` in `values` via een wizardparameter;
   - p6: een beginwachtwoord voor de beheerder via de vault-weg: `GOTIFY_DEFAULTUSER_PASS` met `value_from` naar een
     geheim van de Hub, met `accepted_findings: secret_in_form` (de gebruiker wees die weg voor beheerder en
     dienstaccount af);
   - p7: een letterlijke `custom_app.env`-waarde onder `APP_SECRET_KEY` (de naam eindigt niet op een geheimwoord, dus
     `_SECRET_ENV` ziet hem niet);
   - n1 tot en met n4 (`FLOWS` van Node-RED): een `users`-lijst met een bcrypt-hash waarvan de salt met `.` begint,
     `-D credentialSecret=…`, een `users`-object met `\u0024` in plaats van `$`, en
     `--define=adminAuth.users[0].password=…`;
   - n5 tot en met n10 (`FLOWS` van Node-RED; gevonden in de herreview R-VEILIG en in herstelpoging 2, elk met de
     `nopt` uit de gepinde image nagemeten als een instelling die Node-RED aanneemt): n5 `--define credentialSecret=…`
     (met een spatie, de vorm uit de hulptekst van Node-RED), n6 `--def=credentialSecret=…`, n6b
     `--defi credentialSecret=…`, n7 `--define adminAuth=…` met een `users`-lijst waarvan de hash met `\u0024` is
     ge-escapet, n8 `-D=credentialSecret=…`, n9 `-D adminAuth=…` met een gebruikersnaam met U+00A0 en dezelfde
     ge-escapete hash, en n10 `-D @/data/extra.json` (instellingen uit een bestand). Daarnaast de afkortingen `--d=`
     en `--de ` (ook instellingen), een los woord `credentialSecret=…` zonder optie (geen instelling voor Node-RED:
     het wordt de naam van het flowbestand; T2 toetst het fail-closed toch), en `-D settings={"secretKey":"…"}`
     (camelCase).
   - c01 tot en met c15 (`FLOWS` van Node-RED; gevonden in de herreview R-VEILIG na herstelpoging 2 en in
     herstelpoging 3 opnieuw gemeten met de `nopt` uit de gepinde image; bij elk gaf `jl-manifest validate --strict`
     "geldig" met 0 fouten): c01 `-d=credentialSecret=…`, c02 `-de=credentialSecret=…`, c03
     `-define=credentialSecret=…`, c04 `--D=credentialSecret=…`, c05 `---define=credentialSecret=…`, c06
     `--no-define=credentialSecret=…`, c07 `--no-d=credentialSecret=…` en c08 `-vD=credentialSecret=…` geven elk de
     instelling `credentialSecret`, net als c09 `-d credentialSecret=…` en c10 `-D=credentialSecret=…`; c11, een los
     woord `credentialSecret=…` zonder optie, geeft geen instelling (het komt in `argv.remain`); bij c12
     `-Dv=credentialSecret=…` stopt `red.js` (de define-waarde wordt `--verbose`); c13 is een gebruiker als losse
     paden (`-d=adminAuth.users[0].username=…`, `-d=adminAuth.users[0].password="…"` met een bcrypt-hash waarin `\/`
     staat, en `-d=adminAuth.users[0].permissions="*"`), c14 dezelfde gebruiker met `--D=` plus
     `-define=credentialSecret=…`, en c15 `-d=@/data/extra.json` (instellingen uit een bestand). Met de echte `red.js`
     (container zonder netwerk) werken c13 en c14: de gebruiker meldt zich aan (200; anoniem 401), en bij c14 is
     `flows_cred.json` met de sleutel uit `FLOWS` te ontsleutelen.
   De tegenproef p5b (dezelfde waarde letterlijk in `env.value`) gaf terecht `secret_literal_forbidden`. De herstelstap
   bouwt W2 en W3 (zij hebben `accounts` niet nodig), met de codes en meldingen in `findings.py` en
   `docs/MANIFESTSTANDAARD.md`. Tests: p1, p3, p4, p5 (de omgevingsvariabele) en p6 geven
   `account_gegevens_verboden` (N3; p6 ook met `accepted_findings`); p2, p5 (de parameter) en p7 geven
   `secret_literal_forbidden` (N1), net als dezelfde weg als p7 met `APP_SECRET_KEY` vervangen door een
   camelCase-naam (`secretKey`, `passwordHash`, `apiKeyValue`), terwijl `SECRETS_DIR`, `TOKENS` en `PASSWORDLESS`
   met een gewone waarde geldig blijven; dezelfde wegen als p1, p3 en p4 met een geheimnaam zonder accountwoord geven
   `secret_in_form` (N2); n1 tot en met n10, de afkortingen `--d=` en `--de `, het losse woord (fail-closed), de
   camelCase-sleutel, een hash in een parameter-`default` en in `description_nl`, en een wizardwaarde met een hash (bij
   `translate`) geven `account_gegevens_verboden` (T1, T2); n7 en n9 ook met alleen het vangnet (T2 stap 2), dus
   zonder dat een woord als instelling is herkend; c01 tot en met c08, c13, c14 en c15 geven elk
   `account_gegevens_verboden`, en c13 toetst daarbij dat `\/` in een hash T1 niet breekt (ook na het oplossen van
   `\/` wordt de hash herkend); telkens bij `validate` en bij `translate`/`preview` (zonder payload); de
   testwaarde staat in geen melding, geen uitvoer van `jl-manifest` en geen antwoord van de API; p5b blijft
   `secret_literal_forbidden` geven; W3 laat na het invullen alleen de naam `HUB_ENROLLMENT_TOKEN` door en weigert
   `HUB_TOKEN`, `HUB_SECRET` en elke andere geheimnaam (ook als de vertaling `HUB_*` nog niet zelf zet, test het de
   uitzondering op een ingevulde `envs`-lijst);
   `db_password: {param: db_password}` en `secret_key: {param: secret_key}` van Authentik blijven geldig (met de
   bestaande waarschuwing); een bcrypt-hash waarvan de salt met `.` of een cijfer begint, geeft
   `account_gegevens_verboden` ook waar `interpolation_forbidden` niets ziet; elk bestaand manifest in de werkmap
   valideert vóór en na met dezelfde uitkomst (gemeten met een nabootsing: in herstelpoging 2 0 van 21 anders, in
   herstelpoging 3 0 van 22). T2 krijgt geen lijst van optievormen in `rules.py`. De herstelstap legt de kandidaatvormen
   c01 tot en met c15 vast als tests, met de digest waarop hun uitkomst bij `nopt` is gemeten, zodat de rooktest van
   jelly-leon-platform dezelfde vormen bij een nieuwe digest kan meten (punt 4).
   N1 en N2 voeren regels uit die kop 3.1 en kop 9 al hebben; N3 en de
   tekstregels staan ook in het voorstel voor kop 9: of de herstelstap op de contractronde wacht, beslist de regisseur.
   Daarna: de pins van de andere repos (POORTEN, P-appstore-en-fork kop 7). Reviewers: R-TECH, R-VEILIG en
   R-MANIFEST. Deze stap (S-P2-510) herstelt de validator niet.
3. **appstore-en-fork: S-P3-490.** W1, met Nederlandse codes in `findings.py` en de docs; W2 en W3 staan er dan al
   (punt 2), hun tests blijven in de suite. Tests bovenop die van S-P3-490: een `dienst` zonder `afnemer` of `rechten`,
   `eerste_gebruik` bij een `dienst`, twee beheerders en een onbekende `controle` geven elk hun code; `rechten: ["*"]`
   faalt in het schema; een controle die een account noemt met een `beschrijving_nl` van 501 tekens geeft
   `account_controle_te_lang` en met 500 tekens niet, terwijl dezelfde tekst van 501 tekens bij een controle zonder
   account in de validator geldig blijft (het beleid van de Hub weigert hem wel; dat verschil blijft een
   contractmelding, zie "Catalogus-API en beleid van de Hub"); een schemafout onder `accounts` noemt de waarde niet;
   `accounts` staat in geen payload, geen antwoordmodel en niet in `requirements()`; elk bestaand manifest in de
   werkmap valideert vóór en na met dezelfde uitkomst. Reviewers: R-TECH, R-VEILIG en R-MANIFEST.
4. **jelly-leon-platform: S-P3-445** (na S-P2-410, regel "Vooraf"). Volgens het besluit van de gebruiker (punt 7: A):
   - de validatorkopie volgt punt 2 en S-P3-490; het Node-RED-manifest krijgt géén `accounts` (dus ook geen
     `eerste_gebruik`);
   - de repotest (`tests/repo/test_nodered.py`) eist wat S-P2-410 bouwpunt 3e invoert (`adminAuth` precies
     `{"type": "credentials", "users": []}`, en een variant met `default` faalt) en daarbij dat het manifest geen
     `accounts` heeft, zodat de veiligste stand niet ongemerkt verandert;
   - `healthcheck.monitor` en de controle `beveiliging_actief` blijven een beveiligde van een onbeveiligde Node-RED
     onderscheiden (S-P2-410 bouwpunt 3c, controleproef N2): dat is het enige signaal als `FLOWS` ontbreekt (punt 7);
   - bij elke nieuwe Node-RED-digest meet de rooktest (`tools/nodered_rooktest.py`) een vaste set kandidaatvormen,
     minstens c01 tot en met c08, c12 en c15 (punt 2), met dezelfde naslag als in deze stap: de `nopt` en
     `@node-red/util` uit die image, zonder Node-RED te starten. Hij kijkt dus niet alleen naar `knownOpts` en
     `shortHands` in `red.js`. Per vorm legt hij vast welke define-waarde `nopt` geeft en of `red.js` stopt, en toetst
     hij de twee regels waarop T2 steunt (W2, T2 stap 3): een define-waarde is altijd het deel na de eerste `=` van een
     optiewoord of het woord na een optie, en alleen een define-waarde die met `@` begint, leest een bestand (op de
     gepinde digest geven c01 tot en met c08 `credentialSecret=…`, laat c12 `red.js` stoppen en geeft c15 `@…`). Klopt
     een regel niet meer, dan is dat een wijziging van de manifeststandaard: melden aan appstore-en-fork, en de digest
     niet verhogen tot T2 de nieuwe vorm weigert;
   - de commentaarkop, `post_install_checks` id `beheerder_en_flows` (nu "wachten op een contractuitbreiding") en
     ADR-0023 kop "Wat het manifest niet kan (geblokkeerd)" verwijzen naar C-12, het besluit van punt 7 en deze ADR.
     I10-10-01 blijft "Geblokkeerd". De rechten van een later dienstaccount liggen nog niet vast; let op:
     home-control-hub gebruikt nu `SERVICE_PERMISSIONS` met `flows.write`, terwijl S-P3-445 "alleen lezen" verwacht;
   - B en C zijn afgewezen (punt 7); daarvoor komt geen stap, tenzij de gebruiker later opnieuw kiest;
   - los van punt 7: Gotify, Uptime Kuma, Beszel-hub en Authentik kunnen hun beheerder (en Authentik het token voor
     home-control-hub als `dienst`) in `accounts` verklaren. Geen stap-ID; melden aan de regisseur.
   Reviewers: R-TECH, R-MANIFEST en R-VEILIG.
5. **home-control-hub: S-P3-360.** Het profiel vervalt (punt 6):
   - `policy.define_arguments`, `policy.build_profile`, `Profile` en wat alleen zij gebruiken gaan weg, net als de route
     `POST /integrations/v1/automation/hardening-profile` (daarna 404), met de OpenAPI-export;
   - het bestaande vault-item `vault://automation/node-red-profiel` wordt niet automatisch verwijderd; het wordt niet
     meer gelezen;
   - de aanmelding van een dienstaccount komt binnen via de bestaande route `PUT /integrations/v1/sources/{source_id}`
     (voor Node-RED: bron `node_red`, pas als de gebruiker bij punt 7 een weg kiest). Voor een account uit `accounts`
     weigert die route een wachtwoord of token van minder dan 32 tekens (punt 3, `door_beheerder`; nu eist
     `SourceConfig.credential` minstens 8 tekens); met een test;
   - `check_isolation`, controle `credentials_versleuteld`: in orde alleen bij `flowEncryptionType: "system"` (de sleutel
     die Node-RED zelf maakt); `user` betekent dat er een `credentialSecret` via het formulier is gezet en is een
     afwijking;
   - `anoniem_geweigerd` (401) blijft (C-11). `dienstaccount_minimaal` gaat vergelijken met `accounts[].rechten` van het
     Node-RED-manifest, en `SERVICE_USER` wordt `accounts[].id`, zodra dat manifest een dienstaccount heeft. De weg
     waarlangs home-control-hub `accounts[]` leest: alleen in zijn repotest, tegen het Node-RED-manifest van
     jelly-leon-platform op een gepinde commit (zoals `apps/api/tests/unit/test_deploy_artifacts.py` nu de verwijzing
     naar `install/manifests/node-red.manifest.yaml` in `deploy/truenas/NODE_RED_CUSTOM_APP.md` toetst); die test houdt
     de constanten voor de naam en de rechten van het dienstaccount gelijk aan het manifest. Tijdens het draaien leest
     home-control-hub geen manifest en roept het de catalogus-API niet aan (keuze (a): `accounts` komt niet via een
     API bij een dienst);
   - `deploy/truenas/NODE_RED_CUSTOM_APP.md` en de eigen ADR-0016 van home-control-hub
     (`docs/adr/ADR-0016-b11-node-red-automatisering.md`) krijgen een alinea over C-12.
   Het vervallen van het profiel hangt niet af van punt 7. Zolang de route bestaat, houdt ze een weg open naar
   geheimen in het formulier (punt 7); daarom stelt deze ADR voor dit deel eerder te laten lopen. S-P3-360 wacht nu op
   S-P3-445 (regel "Vooraf"); de regisseur beslist of dat deel wordt losgemaakt. Reviewers: R-TECH en R-VEILIG.
6. **ai-server-manager (Hub).** De Hub, de executor en de vault van de Hub krijgen geen rol in het aanmaken van een
   account (keuze (a), zie "Catalogus-API en beleid van de Hub"): de Hub krijgt `accounts` niet als veld;
   `validate_manifest_meta` in `apps/policy/src/asm_policy/params.py` blijft gelijk. De gate op `post_install_checks`
   (S-P2-080 bouwpunt 3) dekt de accounts al via `controle`, en de Hub toont de `beschrijving_nl` van die controles.
   Wel één wijziging in de Hub-UI (geen stap-ID; melden aan de regisseur; eerst een ontwerpstap in Peil): het scherm met
   de controles na installatie toont bij een controle die door een account met `rol: dienst` wordt genoemd, ook de
   `afnemer` en de `rechten` van dat account, gelezen uit `GET /catalog/v1/versions/{version_id}` van appstore-en-fork
   (veld `manifest`; geen nieuw veld). De Hub leest die route nu niet: `apps/api/src/hub/catalog/appstore.py` leest
   alleen `GET /catalog/v1/entries`. Kan de Hub het manifest niet lezen, dan toont hij dat de rechten onbekend zijn,
   nooit een lege lijst. Met een UI-test. Los daarvan: voor het invoeren van een aanmelding bij home-control-hub vanuit
   de Hub-UI is een scherm nodig (eerst een ontwerpstap in Peil);
   `grep -rn 'integrations/v1/sources' <WERKMAP>/ai-server-manager/apps` gaf op 2026-11-11 geen treffer, en er is geen stap-ID gevonden. Reviewers: R-TECH, R-VEILIG en R-ONTWERP.

## Wat dit ontwerp niet bewijst

- Niets is gebouwd of op TrueNAS geprobeerd. Het gedrag van het echte Custom App-formulier is NIET GEVALIDEERD
  (echte NAS). De proeven p1 tot en met p7, n1 tot en met n10 en c01 tot en met c15 tonen alleen wat de huidige
  validator doorlaat; W2 en W3 zijn niet gebouwd. De meting onder W2 is een nabootsing van de tekst van deze ADR
  buiten de repo, geen validatorcode en geen bewijs voor de herstelstap of S-P3-490.
- De regels van `nopt` onder "Context" en de uitkomst van de kandidaatvormen c01 tot en met c15 zijn gemeten met de
  `nopt` uit de gepinde Node-RED-digest (een naslag van `red.js`); alleen c13 en c14 zijn ook met de echte `red.js`
  gestart, in een container zonder netwerk, als uid 1000 met een tmpfs als `/data` (niet als 568:568 op een dataset).
  Een andere digest kan andere regels hebben (T2 stap 3). Of het echte Custom App-formulier een omgevingswaarde met
  U+00A0, `--define k=v`, `-d=…`, `\/` of `*` ongewijzigd doorgeeft: NIET GEVALIDEERD (echte NAS).
- Over Node-RED is ONBEKEND: een eerste-gebruik-venster, gebruikersbeheer in de app en aanmelden via OIDC zonder eigen
  `settings.js` (eerst een rooktest in jelly-leon-platform), en de rechten en de eigenaar van `/data/.config.runtime.json`
  (eerst vaststellen met `stat` in die rooktest). Kop 3.1 noemt 0600 en 568:568 voor `/appdata/secrets/` van eigen
  images; of de officiële Node-RED-image dat voor zijn eigen sleutel ook doet, is niet gemeten. I10 vraagt nu nog een
  aparte sleutel (zie "Gevolgen per repo", punt 1).
- Het gedrag van Node-RED zonder `FLOWS` en zonder anonieme rechten is afgeleid uit de broncode van de gepinde image en
  uit eerdere metingen (home-control-hub `docs/b11/node-red-verificatie.json`; rooktest N2); Node-RED is daarvoor niet
  zonder `FLOWS` gestart (in herstelpoging 3 alleen met `FLOWS` van c13 en c14). Tot S-P2-410 klaar is, is de editor
  anoniem leesbaar; ontbreekt `FLOWS`, dan is ze open met alle rechten (punt 7, restrisico).
- W2 vindt alleen wat herkenbaar is. Een gecodeerde of gesplitste hash, een geheim onder een naam zonder geheimwoord,
  en een beginwachtwoord onder een geheimnaam zonder accountwoord (bijvoorbeeld `<APP>_PASSWORD`) dat een geheim van de
  Hub krijgt, ziet de validator niet; dan blijft de review van het manifest (R-VEILIG, R-MANIFEST) de controle. Een
  naam als `BYPASS_…` of een camelCase-naam als `tokenStore` kan juist ten onrechte als geheimnaam tellen (fail-closed:
  de schrijver kiest een andere naam). Het veld `accounts` zelf heeft geen veld voor een waarde.
- Het eerste-gebruik-venster blijft een open venster tot de beheerder het sluit (restrisico van contract kop 9). De gate
  van de Hub (S-P2-080) is hier niet getest.
- Of de Hub-UI een aanmelding voor home-control-hub doorgeeft zonder haar te loggen, en of het scherm met de
  controles na installatie de `afnemer` en `rechten` van een dienstaccount toont, is ONBEKEND tot die schermen er zijn.
- De route `POST /integrations/v1/automation/hardening-profile` van home-control-hub levert tot S-P3-360 nog geheimen
  voor het formulier (punt 7).
- Over punt 7 heeft de gebruiker besloten (A, wachten). Node-RED heeft daardoor geen beheerder en geen dienstaccount;
  of en wanneer er een andere weg komt, is een nieuwe keuze van de gebruiker.
