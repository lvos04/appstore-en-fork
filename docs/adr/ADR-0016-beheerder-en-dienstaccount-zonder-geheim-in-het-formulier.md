# ADR-0016 — Manifeststandaard: beheerder en dienstaccount zonder geheim in het formulier (besluit C-12)

- **Status:** ontwerp (stap S-P2-510) · 2026-11-11 · de punten 1 tot en met 6, 8 en 9 onder "Besluit" volgen uit de
  bronnen; punt 7 (Node-RED op de officiële image) wacht op een besluit van de gebruiker, en tot dat besluit geldt de
  veiligste stand. Er is niets gebouwd; de bouw volgt na P2 (S-P3-490, daarna S-P3-445 en S-P3-360).
- **Bron:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`), koppen "1. Vaste keuzes (van de gebruiker)",
  "3.1 Uitgangspunten", "3.2 Overzicht enrollment (eigen nieuwe diensten)", "3.10 Credentials van andere apps" en
  "9. Manifeststandaard (B08-001, eigenaar `appstore-en-fork`)"; [ADR-0002](ADR-0002-manifeststandaard-v1.md) besluit 4;
  [ADR-0015](ADR-0015-vault-verwijzing-geheimen-in-de-vertaling.md);
  `ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/STAPPEN_P2.md`, kop "8.2 Contract- en beleidsvragen
  (voor de eigenaar van het contract of van de manifeststandaard)", rijen C-5, C-11 en C-12;
  `ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/BESLUITEN_EN_BLOKKADES.md`, kop "C-12 — Hoe krijgt
  Node-RED een beheerder en een dienstaccount?"; `jelly-leon-platform:docs/adr/ADR-0023-node-red-als-custom-app.md` en
  `jelly-leon-platform:install/manifests/node-red.manifest.yaml`; `home-control-hub:apps/api/src/integrations/domains/automation/policy.py`
  (`define_arguments`, `build_profile`, `check_isolation`) en `router.py` (`post_hardening_profile`).
- **Validatie:** alleen bronnen gelezen. Niets gebouwd, niets getest, niets op TrueNAS geprobeerd: NIET GEVALIDEERD
  (echte NAS).

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
een beheerder." Zonder beheerder is de editor dus helemaal dicht.

**Vraag C-5:** het Node-RED-manifest kan geen beheerder aanmaken, de flowbundels en `HUB_URL` niet plaatsen en geen
service-token meegeven (jelly-leon-platform ADR-0023, kop "Wat het manifest niet kan (geblokkeerd)"). Punt 8 onder
"Besluit" zegt welk deel deze voorziening dekt.

**De botsing.** home-control-hub maakt met `policy.define_arguments` en `policy.build_profile` (route
`POST /integrations/v1/automation/hardening-profile`, functie `router.post_hardening_profile`) argumenten `--define=…`
voor het formulier van de Custom App van Node-RED: `adminAuth` met twee accounts (`beheerder` en `hub-dienst`, elk met
een bcrypt-hash) en een `credentialSecret`. Elk antwoord van de route bevat die argumenten opnieuw; de R-VEILIG-review
van stap S-P2-295 (proef P8) kreeg bij elke nieuwe `Idempotency-Key` dezelfde `credentialSecret` terug. Dat botst met
contract kop 3.1 ("Nooit geheimen in het Custom App-formulier"). jelly-leon-platform zet de beveiliging van Node-RED via
de omgevingsvariabele `FLOWS` (`-D sleutel=waarde`), met `adminAuth` zonder gebruikers (ADR-0023 besluit 2).

**Wat de bronnen over Node-RED zeggen** (image `nodered/node-red:5.0.9-24` op de digest uit het manifest van
jelly-leon-platform):

- Instellingen komen alleen binnen als argument `-D`/`--define` met JSON (jelly-leon-platform ADR-0023 besluit 2;
  home-control-hub `policy.py`, moduletekst). Een gebruiker in `adminAuth.users` heeft een bcrypt-hash (ADR-0023, kop
  "Wat het manifest niet kan (geblokkeerd)").
- De omgevingsvariabele `NODE_RED_CREDENTIAL_SECRET` doet niets. Zonder `credentialSecret` versleutelt Node-RED de
  credentials met een sleutel die het zelf maakt (`flowEncryptionType: "system"`; home-control-hub
  `docs/b11/node-red-verificatie.json`, controles `kaal_instellingen` en `kaal_env_credential_secret_genegeerd`). Die
  sleutel staat in `/data/.config.runtime.json` (manifest, `storage[data].description_nl`; ADR-0023 besluit 6).
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
   `credentialSecret`, geen token en geen gebruikerslijst, ook niet in een omgevingswaarde zoals `FLOWS` (contract
   kop 9, EX47: "een geheime waarde in een manifest").
3. Installatie alleen als standaard Custom App, nooit YAML of compose (contract kop 1).
4. De container draait als 568:568 waar de image dat toestaat (contract kop 1).
5. Fail-closed: zonder beheerder blijft de editor van Node-RED dicht (C-11). Wat niet gemeten is, telt niet als in orde.
6. Algemeen: de voorziening geldt voor elke app die een beheerder of een dienstaccount nodig heeft, niet alleen voor
   Node-RED.
7. Een geheim dat iemand moet kennen, ziet hij hooguit één keer, en het staat nooit in een log, een preview-diff, een
   auditdetail, een verzoekhash, de idempotentie-opslag of EVIDENCE (contract kop 3.1; VALKUILEN V-094).
8. Bestaande manifesten zonder de voorziening valideren precies zo als nu.

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
  beoordelen. **Niet gekozen** voor de standaard; voor Node-RED een mogelijke keuze van de gebruiker (punt 7).

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
  hoeft er niets voor te kunnen); voor Node-RED een mogelijke keuze van de gebruiker (punt 7).

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
     precies de rechten uit het manifest, en voert de aanmelding één keer in bij de afnemer. De afnemer bewaart haar
     versleuteld, toont alleen de laatste 4 tekens en zet haar nooit in een log, verzoekhash, idempotentie-opslag,
     audit of antwoord (zoals home-control-hub `sources.py` al doet).
   Een derde aanmaakwijze (bijvoorbeeld "de app maakt het account zelf en draagt het over") komt er alleen met een
   nieuw besluit (punt 7).
4. **Accountgegevens worden overal geweigerd.** Elke wachtwoordhash, elke sleutel die een geheim noemt met een niet-lege
   waarde, en elke niet-lege gebruikerslijst in een omgevingswaarde of in de waarden van een catalogusapp is een fout
   (`account_gegevens_verboden`), ook als het manifest geen `accounts` heeft. Zo kan `FLOWS` geen bcrypt-hash of
   `credentialSecret` meer dragen; nu controleert de validator alleen de naam van een omgevingsvariabele
   (`rules._SECRET_ENV`), niet de inhoud. Precieze regel: W2.
5. **De vertaling voegt niets toe.** `accounts` komt niet in de payload; de vertaling controleert na het invullen van de
   parameters opnieuw op accountgegevens (W3). Geen nieuw veld in de uitvoer van `translate` of `preview`.
6. **Het profiel van home-control-hub vervalt.** Elke vorm ervan zet hashes, gebruikers of een `credentialSecret` in het
   formulier, en er blijft niets over om gelijk te trekken: het formulier van Node-RED komt uit het manifest van
   jelly-leon-platform (`STAPPEN_P2.md` kop 8.2, rij C-11: `NODE_RED_CUSTOM_APP.md` verwijst naar dat manifest, stap
   S-P2-290), en het dienstaccount komt volgens punt 3 via de beheerder. Een `credentialSecret` is niet nodig: Node-RED
   maakt zijn sleutel zelf (`flowEncryptionType: "system"`; optie 2). De isolatiecontrole van home-control-hub trekt dat
   gelijk (zie "Gevolgen per repo").
7. **Node-RED op de officiële image: open besluit van de gebruiker.** Volgens de gelezen bronnen kan deze image geen van
   beide aanmaakwijzen leveren: er is geen eerste-gebruik-venster, gebruikers bestaan alleen als bcrypt-hash in de
   instellingen (`-D adminAuth=…`), en gebruikersbeheer in de app is niet gevonden (ONBEKEND, zie "Context"). C-11
   sluit een venster bovendien uit. Elke weg die wel werkt, vraagt een besluit dat niet uit de bronnen volgt (zie de
   vraag hieronder). **Tot dat besluit geldt de veiligste stand:** Node-RED krijgt geen beheerder en geen
   dienstaccount, de editor blijft dicht (C-11) en home-control-hub kan Node-RED niet lezen. I10-10-01 blijft
   "Geblokkeerd" en het deel "beheerder en dienstaccount" van B11-001 blijft open (gemeten op 2026-11-11: I10-10-01
   "Geblokkeerd", B11-001 "Bezig"; deze ADR wijzigt geen EVIDENCE).
   De vraag aan de gebruiker: "Node-RED kan zelf geen beheerder of dienstaccount maken, en het formulier mag geen
   geheim krijgen. Hoe gaan we verder?"
   - **A. Node-RED wacht** (veiligste stand; geen werk). De editor blijft dicht en home-control-hub leest niets, tot er
     een andere weg is. S-P3-445 gebruikt de voorziening niet; alleen teksten en verwijzingen gaan mee.
   - **B. Eigen dunne image bovenop de officiële Node-RED-digest** (optie 7). Die image maakt de accounts bij de eerste
     start zelf. Gevolg: B08 kop 5 ("overige apps alleen officiële images") en jelly-leon-platform ADR-0023 ("Afgewezen")
     veranderen; er komt een eigen release met digest en een rooktest bij elke nieuwe Node-RED-digest; en hoe de
     beheerder en home-control-hub de aanmelding krijgen zonder log of formulier, vraagt een eigen ontwerp. Bevat dat
     ontwerp een venster op het netwerk, dan moet de gebruiker bevestigen dat dit binnen C-11 past.
   - **C. Startopdracht via het formulier op de officiële image** (optie 6). Gevolg: een nieuw veld in de standaard en
     in contract kop 9, opstartcode in een manifest, gedrag van de image ONBEKEND (eerst een rooktest), en dezelfde
     overdrachtsvraag als bij B.
8. **C-5: wat deze voorziening dekt.** Wel: de beheerder en het dienstaccount, zodra punt 7 een weg geeft. Niet:
   - de flowbundels: dat is inhoud, geen account; het importhulpmiddel heeft een aangemelde beheerder nodig
     (jelly-leon-platform ADR-0023 besluiten 5 en 6);
   - `HUB_URL`: het importhulpmiddel zet die als globale omgevingsvariabele van Node-RED (ADR-0023 besluit 6), dus ook
     alleen met een aangemelde beheerder. Via het formulier kan het niet: `HUB_URL` is geen manifestparameter en alleen
     my_house heeft een uitzondering zonder koppeling (contract kop 3.2);
   - het service-token: een flowruntime kan zich niet inschrijven (kop 3.3 vraagt een Ed25519-sleutel) en kop 3.1
     verbiedt een langlevend token in het formulier.
   Deze drie blijven geblokkeerd onder C-5.
9. **Contract.** Kop 3.1 verandert niet: de voorziening zet niets in het formulier, en het enrollment-token blijft de
   enige uitzondering. Kop 9 krijgt een aanvulling: het nieuwe optionele veld `accounts`, de twee aanmaakwijzen en de
   weigering `account_gegevens_verboden` (een nieuw manifestveld valt onder P-CONTRACT). Dat is contractwerk voor
   ai-server-manager; het moet klaar zijn voordat S-P3-490 begint (zie de regel "Vooraf" van S-P3-490). De melding van
   ADR-0015 (een uitzondering op kop 3.1 voor geheimen van de Hub in een omgevingsvariabele van een Custom App) blijft
   zoals ze is; deze ADR verandert daar niets aan.

## Wat er in de manifeststandaard verandert

| # | Wijziging | Schema (`schemas/manifest-v1.schema.json` en de byte-gelijke kopie `apps/api/src/appstore/manifest/manifest-v1.schema.json`) | Model en regels | Vertaling (`translate.py`) | `docs/MANIFESTSTANDAARD.md` | Schemawijziging | Contract |
|---|---|---|---|---|---|---|---|
| W1 | veld `accounts` | ja: eigenschap `accounts` en `$defs/Account` | `model.py` (`Account`), `rules.py` (nieuwe controle) | niets in de payload | nieuwe kop + rij in "Opbouw in het kort" | ja | kop 9: aanvulling |
| W2 | accountgegevens weigeren | nee | `rules.py` (controle op de ruwe gegevens), `findings.py` | — | weigerregel + codes | nee | kop 9: in dezelfde aanvulling (voert EX47 uit) |
| W3 | opnieuw controleren na het invullen | nee | — | ja | zin in "Geheimen in de vertaling" | nee | nee |

**W1 — veld `accounts` (optioneel; ontbreekt het, dan verandert er niets).** Een lijst van hooguit 16 objecten
`$defs/Account` met `additionalProperties: false`:

| Veld | Type | Verplicht | Regel |
|---|---|---|---|
| `id` | `$defs/Name` | ja | uniek binnen `accounts`; de naam van het account in de app waar de app een naam laat kiezen; geen geheim |
| `rol` | `beheerder` \| `dienst` | ja | hooguit één `beheerder` per manifest |
| `aanmaak` | `eerste_gebruik` \| `door_beheerder` | ja | `eerste_gebruik` alleen bij `beheerder`, `door_beheerder` alleen bij `dienst` |
| `afnemer` | `$defs/AppId` | alleen bij `dienst` | de vaste `app_id` van de app die de aanmelding gebruikt (contract kop 2); niet de eigen `app_id` |
| `rechten` | lijst van 1 tot 32 unieke teksten, patroon `^[a-z][a-z0-9_.:-]{0,63}$` | alleen bij `dienst` | de rechten in de app; het patroon laat geen `*` toe; de beheerder heeft geen lijst (alle rechten) |
| `controle` | `$defs/Name` | ja | een `id` uit `post_install_checks`; meerdere accounts mogen dezelfde controle noemen |
| `beschrijving_nl` | tekst, 10 tot 1000 tekens | ja | wat de beheerder doet, zonder waarde |

Regels in `rules.py` (een nieuwe methode van `_Rules`, aangeroepen vanuit `_Rules.run`):
- `account_ongeldig` (fout): een combinatie die de tabel verbiedt (bijvoorbeeld `dienst` zonder `afnemer` of `rechten`,
  `beheerder` met `afnemer` of `rechten`, `eerste_gebruik` bij `dienst`, twee keer `beheerder`, `afnemer` gelijk aan de
  eigen `app_id`), of een `dienst` zonder een `beheerder` in hetzelfde manifest (er moet iemand zijn die het account
  maakt).
- `account_controle_ontbreekt` (fout): `controle` noemt geen bestaande `post_install_checks[].id`.
- Dubbele `id`: de bestaande code `duplicate_name`.
- Geen van deze codes is een waarschuwing en geen ervan staat op de lijst van `accepted_findings`.

**W2 — accountgegevens weigeren (`account_gegevens_verboden`, fout; altijd, ook zonder `accounts`).** De controle loopt
op de ruwe gegevens, net als `validator._compose_findings`, zodat de melding er ook is als het schema al faalt. Een fout
is:
- een wachtwoordhash in welke tekst van het manifest ook: bcrypt (`$2a$`, `$2b$`, `$2y$` met kosten en 53 tekens) en
  hashes in PHC-vorm (`$argon2…`, `$scrypt$`, `$pbkdf2…`);
- in een `custom_app.env[].value`, in een tekst in `values` en in de `default` of `example` van een parameter: een
  sleutel die een geheim noemt (dezelfde woorden als `rules._SECRET_ENV`, plus `wachtwoord`) met een niet-lege
  tekstwaarde. Dat geldt voor een JSON-sleutel (`"credentialSecret":"…"`) en voor `-D sleutel=waarde` en
  `--define=sleutel=waarde` (ook `-D adminAuth.users[0].password=…`); bij een sleutel met punten telt het laatste deel;
- in dezelfde plekken: een sleutel `users` of `gebruikers` met een niet-lege lijst (een gebruiker als waarde).
Geen fout zijn een `{param: …}`-verwijzing (bijvoorbeeld `db_password: {param: db_password}` van Authentik) en een
vault-verwijzing die de vertaling zelf zet (ADR-0015 punten 4 en 5). De melding noemt het pad en de reden, nooit de
waarde. Ook een schemafout (`invalid_format`, `invalid_value`) noemt de waarde niet als die onder `accounts` staat of
door W2 als accountgegeven wordt herkend; nu zet `validator._schema_findings` de waarde altijd in die meldingen.
Bestaande manifesten mogen door W2 niet anders uitvallen (eis 8): een nieuwe regel die een bestaand manifest van een
andere repo afkeurt, is een contractwijziging. Een zoekopdracht op 2026-11-11 over de 15 manifesten in
`deploy/manifest/` van de repos en in `jelly-leon-platform:install/manifests/` vond alleen
`db_password: {param: db_password}` van Authentik (een verwijzing, geen waarde); S-P3-490 meet het met de validator.

**W3 — vertaling.** `translate` en `POST /catalog/v1/preview` zetten niets uit `accounts` in de payload. Na het
invullen van de parameters (waarden uit de wizard) controleert de vertaling de omgevingswaarden en de `values` opnieuw
met W2; een treffer geeft `account_gegevens_verboden` met het pad, zonder waarde, en dan is er geen payload. De
`preview_hash`, `vault_refs` en de antwoordmodellen blijven gelijk: geen veld erbij (V-046) en geen OpenAPI-wijziging.

**Kopieën.** W1 wijzigt het schema. De kopie in jelly-leon-platform (`apps/api/src/appstore/manifest/manifest-v1.schema.json`)
volgt in S-P3-445 met `tools/validator_kopie.py`. Repos die een commit van deze repo pinnen, houden het oude gedrag tot
zij hun pin verzetten (gemeten op 2026-11-11: `ASF_COMMIT` 7b3159d in ai-server-manager, `APPSTORE_COMMIT` 7b3159d in
jelly-leon, `VALIDATOR_COMMIT` cca4403 in home-control-hub en zoeken-en-persoonlijke-ai, `ASF_COMMIT` 1967069 in
jelly-leon-platform). Een oudere validator weigert een manifest met `accounts` (`unknown_field`); alleen een repo die
het veld gebruikt, moet zijn pin dus verzetten.

**Voorstel voor de aanvulling van contract kop 9 (voor de eigenaar van het contract).** "**`accounts: [{id, rol:
beheerder|dienst, aanmaak: eerste_gebruik|door_beheerder, afnemer, rechten, controle, beschrijving_nl}]`** (optioneel)
voor een beheerder of dienstaccount van een app: het manifest verklaart alleen wat er moet bestaan, nooit een waarde.
De beheerder zet zijn account in de app (eerste-gebruik-venster, met een `post_install_checks`-controle); een
dienstaccount maakt hij daarna in de app met precies `rechten` en voert hij één keer in bij `afnemer` (versleuteld, §3.10).
Een wachtwoord, wachtwoordhash, geheime sleutel of gebruikerslijst in het manifest of in een omgevingswaarde wordt
geweigerd (`account_gegevens_verboden`)."

## Gevolgen per repo

Voorgestelde volgorde. De stap-ID's bestaan al in
`ai-server-manager:docs/platform-v2/OVERDRACHT/UITVOERINGSPLAN/STAPPEN_P3.md`; waar er geen stap is, staat dat erbij.

1. **ai-server-manager (contract).** Aanvulling van kop 9 zoals hierboven; kop 3.1 ongewijzigd; kopieën byte-gelijk in
   alle repos (P-CONTRACT). Geen stap-ID: het is de contractronde die `BESLUITEN_EN_BLOKKADES.md`, kop "C-12 — Hoe
   krijgt Node-RED een beheerder en een dienstaccount?", punt "Na het besluit", vóór de gate van P2 plaatst.
   Reviewers: R-MANIFEST en R-VEILIG.
2. **appstore-en-fork: S-P3-490.** W1, W2 en W3, met Nederlandse codes in `findings.py` en de docs. Tests bovenop die
   van S-P3-490: een `dienst` zonder `afnemer` of `rechten`, `eerste_gebruik` bij een `dienst`, twee beheerders en een
   onbekende `controle` geven elk hun code; `rechten: ["*"]` faalt in het schema; een bcrypt-hash in `FLOWS`, een
   `-D credentialSecret=…`, een `"password":"…"`, een niet-lege `users`-lijst, een hash in een parameter-`default` en
   in `description_nl` geven `account_gegevens_verboden`, en de testwaarde staat in geen melding, geen uitvoer van
   `jl-manifest` en geen antwoord van de API; een wizardwaarde met een hash geeft bij `translate` dezelfde fout zonder
   payload; elk bestaand manifest in de werkmap valideert vóór en na met dezelfde uitkomst. Reviewers: R-TECH, R-VEILIG
   en R-MANIFEST.
3. **jelly-leon-platform: S-P3-445.** Hangt af van punt 7.
   - Bij A: de validatorkopie volgt S-P3-490; het Node-RED-manifest krijgt géén `accounts` (Node-RED kan ze niet
     maken). De commentaarkop, `post_install_checks` id `beheerder_en_flows` (nu "wachten op een contractuitbreiding")
     en ADR-0023 kop "Wat het manifest niet kan (geblokkeerd)" verwijzen naar C-12 en deze ADR. I10-10-01 blijft
     "Geblokkeerd". De rechten van een later dienstaccount liggen dan nog niet vast; let op: home-control-hub gebruikt
     nu `SERVICE_PERMISSIONS` met `flows.write`, terwijl S-P3-445 "alleen lezen" verwacht.
   - Bij B of C: een eigen ontwerpstap en bouwstappen; daar is nog geen stap-ID voor.
   - Los van punt 7: Gotify, Uptime Kuma, Beszel-hub en Authentik kunnen hun beheerder (en Authentik het token voor
     home-control-hub als `dienst`) in `accounts` verklaren. Geen stap-ID; melden aan de regisseur.
   Reviewers: R-TECH, R-MANIFEST en R-VEILIG.
4. **home-control-hub: S-P3-360.** Het profiel vervalt (punt 6):
   - `policy.define_arguments`, `policy.build_profile`, `Profile` en wat alleen zij gebruiken gaan weg, net als de route
     `POST /integrations/v1/automation/hardening-profile` (daarna 404), met de OpenAPI-export;
   - het bestaande vault-item `vault://automation/node-red-profiel` wordt niet automatisch verwijderd; het wordt niet
     meer gelezen;
   - de aanmelding van het dienstaccount komt binnen via de bestaande route `PUT /integrations/v1/sources/{source_id}`
     (bron `node_red`), zodra punt 7 een dienstaccount mogelijk maakt;
   - `check_isolation`, controle `credentials_versleuteld`: in orde alleen bij `flowEncryptionType: "system"` (de sleutel
     die Node-RED zelf maakt); `user` betekent dat er een `credentialSecret` via het formulier is gezet en is een
     afwijking;
   - `anoniem_geweigerd` (401) blijft (C-11). `dienstaccount_minimaal` gaat vergelijken met `accounts[].rechten` van het
     Node-RED-manifest, en `SERVICE_USER` wordt `accounts[].id`, zodra dat manifest een dienstaccount heeft;
   - `deploy/truenas/NODE_RED_CUSTOM_APP.md` en de eigen ADR-0016 van home-control-hub
     (`docs/adr/ADR-0016-b11-node-red-automatisering.md`) krijgen een alinea over C-12.
   Het vervallen van het profiel hangt niet af van punt 7. S-P3-360 wacht nu op S-P3-445 (regel "Vooraf"); de regisseur
   kan dat deel eerder laten lopen. Reviewers: R-TECH en R-VEILIG.
5. **ai-server-manager (Hub).** De gate op `post_install_checks` (S-P2-080 bouwpunt 3) dekt ook de accounts, via
   `controle`; de Hub mag per account tonen wat de beheerder moet doen, uit het manifest, zonder geheim. De executor en
   de vault van de Hub krijgen geen rol. Voor het invoeren van een aanmelding bij home-control-hub vanuit de Hub-UI is
   een scherm nodig (eerst een ontwerpstap in Peil); `grep -rn 'integrations/v1/sources' <WERKMAP>/ai-server-manager/apps`
   gaf op 2026-11-11 geen treffer, en er is geen stap-ID gevonden. Reviewers: R-TECH, R-VEILIG en R-ONTWERP.

## Wat dit ontwerp niet bewijst

- Niets is gebouwd, getest of op TrueNAS geprobeerd. Het gedrag van het echte Custom App-formulier is NIET GEVALIDEERD
  (echte NAS).
- Over Node-RED is ONBEKEND: een eerste-gebruik-venster, gebruikersbeheer in de app en aanmelden via OIDC zonder eigen
  `settings.js` (eerst een rooktest in jelly-leon-platform), en de rechten en de eigenaar van `/data/.config.runtime.json`
  (eerst vaststellen met `stat` in die rooktest). Kop 3.1 noemt 0600 en 568:568 voor `/appdata/secrets/` van eigen
  images; of de officiële Node-RED-image dat voor zijn eigen sleutel ook doet, is niet gemeten. I10
  (`ai-server-manager:docs/platform-v2/03_INSTALLATIE/I10_NODE_RED.md`, kop "1. Vooraf controleren") vraagt "secrets key
  als aparte managed secret"; met de sleutel in `/data` is dat niet zo (ADR-0023 besluit 6: de bescherming is de
  toegang tot de dataset).
- W2 vindt alleen wat herkenbaar is: een gecodeerde of gesplitste hash, of een geheim onder een sleutel zonder
  geheimwoord, ziet de validator niet. Het veld `accounts` zelf heeft geen veld voor een waarde; de review van een
  manifest blijft nodig.
- Het eerste-gebruik-venster blijft een open venster tot de beheerder het sluit (restrisico van contract kop 9). De gate
  van de Hub (S-P2-080) is hier niet getest.
- Of de Hub-UI een aanmelding voor home-control-hub doorgeeft zonder haar te loggen, is ONBEKEND tot dat scherm er is.
- Niemand heeft een besluit genomen over punt 7. Tot dat besluit heeft Node-RED geen beheerder en geen dienstaccount.
