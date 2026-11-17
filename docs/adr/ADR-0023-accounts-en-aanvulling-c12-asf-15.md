# ADR-0023 — Manifeststandaard: veld `accounts` en de aanvulling C-12 (C1.3-ASF-15), aanvulling op ADR-0016

- **Status:** aangenomen · 2026-11-17 · besluiten BN-490-1, BN-490-2 en BN-490-3 van de regisseur (de gebruiker was
  afwezig) op het rapport van de verkenner van stap S-P3-490 · vult
  [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md) aan (en daarmee
  [ADR-0017](ADR-0017-kruisverwijzing-en-bcrypt-vormen-weigeren.md) tot en met
  [ADR-0022](ADR-0022-herstelronde-5-grens-op-meldingen-en-accountnaam-van-een-geheim.md)); de tekst van ADR-0016 blijft
  staan · gebouwd in stap S-P3-490.
- **Bron:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`), kop "Aanvulling C-12 (2026-11-17, blijft
  `contracts 1.3`; ADR-0031)" (C12-1, C12-2, C12-3, C12-6), kop "3.1 Uitgangspunten" (de tweede uitzondering), kop "9.
  Manifeststandaard (B08-001, eigenaar `appstore-en-fork`)" (blok "Aanvulling C-12") en kop "11. Bewijs en Excel
  (EVIDENCE.json)"; `docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md`, rij C1.3-ASF-15;
  `ai-server-manager:docs/adr/ADR-0031-contract-1-3-aanvulling-c12.md`, besluit 3 en besluit 9 (R-VEILIG-1 en
  R-VEILIG-2); ADR-0016, kop "Wat er in de manifeststandaard verandert" (W1) en "Gevolgen per repo" punt 3;
  `apps/api/src/appstore/manifest/rules.py`, `translate.py`, `validator.py`, `model.py`.
- **Validatie:** Unit-getest (`apps/api/tests/unit/test_manifest_accounts.py`,
  `apps/api/tests/unit/test_manifest_asf15.py`, `apps/api/tests/unit/test_manifest_contract_13.py`). **NIET
  GEVALIDEERD (echte NAS):** niets is op TrueNAS of met een op de NAS gestarte app geprobeerd.

## Context

1. **W1 is nog niet gebouwd.** ADR-0016 (kop "Wat er in de manifeststandaard verandert", W1) beschrijft het optionele
   veld `accounts`; W2 en W3 zijn gebouwd in stap S-P2-520 (ADR-0017 tot en met ADR-0022). Op `e634cb0` kent het
   schema geen `accounts` (een manifest met het veld geeft `unknown_field`).
2. **Het contract vraagt sinds de aanvulling C-12 meer dan ADR-0016 (C12-2, C1.3-ASF-15).** §9: "Een kluisverwijzing of
   een geheim van de Hub onder een accountnaam is een fout (`account_gegevens_verboden`, een weigering), nooit een
   waarschuwing. Een accountnaam is een geheimnaam met een woord uit de vaste woordenlijst van de standaard (ADR-0016
   W2), aangevuld met `INIT`, `LOGIN` en `BASIC_AUTH`, en met namen die de aanmelding bij de app zelf zijn, zoals
   `*_INIT_PASSWORD`, `*_BASIC_AUTH_PASSWORD`, `JUPYTER_TOKEN` en de kale naam `PASSWORD`." De review van de bron
   (ADR-0031 besluit 9, R-VEILIG-2) liet een geheim van de Hub met alleen de te accepteren waarschuwing `secret_in_form`
   door `validate` en `translate` komen onder `DOCKER_INFLUXDB_INIT_PASSWORD`, `N8N_BASIC_AUTH_PASSWORD` en
   `JUPYTER_TOKEN`. ADR-0016 W2 noemt de woordenlijst "vast; een uitbreiding is een wijziging van de standaard": dit
   ADR is die wijziging.
3. **Een kluisverwijzing in een manifest (C12-3, §3.1).** "Een manifest bevat nooit een kluisverwijzing: het noemt alleen
   de parameter; een kluisverwijzing in een manifest wordt geweigerd (`vault_ref_not_allowed`)". Op `e634cb0` weigert
   alleen de vertaling (`translate.py`, `_unmarked_vault_refs`) dat; `jl-manifest validate --strict` gaf voor een
   letterlijke `vault://app/<andere app>/…` in `custom_app.env[].value` "GELDIG" (proef p5 van de review van S-P2-C12),
   en de catalogus slaat een versie op als `report.valid` waar is.
4. **Herkenning op naam blijft een heuristiek (vervolgpunt van de herreview van S-P2-C12).** Een geheim van de Hub in
   een omgevingsvariabele van een Custom App onder een naam die de validator niet als geheimnaam herkent (bijvoorbeeld
   `APP_KEY`), krijgt nu alleen de te accepteren waarschuwing `secret_in_form`. Dan kan de validator niet zien of het
   de aanmelding van een beheerder of dienstaccount is.
5. **"Enige uitzondering" is verouderd.** ADR-0016 eis 1, besluit 9 en W3 noemen het eenmalige enrollment-token de
   enige uitzondering op "geen geheimen in het formulier". Sinds C12-3 heeft §3.1 een tweede uitzondering: een geheim
   dat de Hub maakt, als vault-verwijzing in een omgevingsvariabele van een Custom App (ADR-0015), nooit voor een
   beheerder of dienstaccount. ADR-0031 besluit 9 wijst het rechtzetten aan deze repo toe.
6. **B-6 (C12-6).** De vaste lijst van §11 kent `NIET GEVALIDEERD (echte Hub)`; `validatie_nu` in
   `checklist-v1.schema.json` (beide kopieën) kent die waarde niet, en geen test koppelt die lijst aan §11.

**Nulmeting (2026-11-17, op `e634cb0`, vóór de eerste codewijziging).** `jl-manifest validate --strict --format json`
op de 15 manifesten (`deploy/manifest/*.manifest.yaml` van de repos en `jelly-leon-platform:install/manifests/
*.manifest.yaml`), op `jelly-leon-platform:install/manifests/overgang-1.3.yaml` en op de 6 fixtures in
`apps/api/tests/fixtures/manifests/`. Daarnaast een meting buiten de repo met de functies van de validator zoals ze
op `e634cb0` zijn (geen nieuwe validatorcode), met een positieve controle die elke variant raakt:

| Meting | Geraakt (van 22 bestanden) |
|---|---|
| B: een geheim van de Hub in `custom_app.env[].value_from` onder een naam die geen geheimnaam zonder accountnaam is | 0 (geen bestaande Custom App zet een geheim van de Hub in een omgevingsvariabele; alleen Authentik en `valid-catalog-1-3` gebruiken geheimen van de Hub, in catalogus-`values`) |
| L: `PASSWORD` ook als laatste sleuteldeel (een sleutel `password` in `values`, of het laatste deel van een naam met punten) | 0 |
| W: `INIT`, `LOGIN` en `BASIC_AUTH` als woord, `JUPYTER_TOKEN` en `PASSWORD` als hele naam | 0 |
| V: een letterlijke `vault://`-tekst of -sleutel | 0 |

## Besluit

1. **W1 precies volgens ADR-0016.** Optioneel veld `accounts` (hooguit 16) met `$defs/Account`
   (`additionalProperties: false`) in beide schemakopieën, het model `Account` en `Manifest.accounts` (standaard leeg),
   en een nieuwe methode `_Rules.accounts` (aangeroepen vanuit `_Rules.run`). Velden, typen en verplichting zoals de
   tabel van W1: `id` (`$defs/Name`), `rol` (`beheerder` | `dienst`), `aanmaak` (`eerste_gebruik` | `door_beheerder`),
   `afnemer` (`$defs/AppId`), `rechten` (1 tot 32 unieke teksten met patroon `^[a-z][a-z0-9_.:-]{0,63}$`), `controle`
   (`$defs/Name`) en `beschrijving_nl` (10 tot 1000 tekens); verplicht in het schema zijn `id`, `rol`, `aanmaak`,
   `controle` en `beschrijving_nl`. Codes (fout; geen ervan te accepteren):
   - `account_ongeldig`: `dienst` zonder `afnemer` of zonder `rechten`, `beheerder` met `afnemer` of `rechten`,
     `eerste_gebruik` bij een `dienst` of `door_beheerder` bij een `beheerder`, een tweede `beheerder`, `afnemer`
     gelijk aan de eigen `app_id`, en een `dienst` zonder `beheerder` in hetzelfde manifest. Per regel één melding,
     op het pad van het veld (`accounts[i].afnemer`, `accounts[i].rol`, …);
   - `account_controle_ontbreekt`: `controle` noemt geen `post_install_checks[].id` (pad `accounts[i].controle`);
   - `account_controle_te_lang`: de `beschrijving_nl` van een controle die door een account wordt genoemd, is langer
     dan 500 tekens (één melding per controle, pad `post_install_checks[j].beschrijving_nl`); dezelfde tekst bij een
     controle zonder account blijft geldig;
   - een dubbele `id`: de bestaande code `duplicate_name` (pad `accounts[i].id`).
   Een melding noemt het pad en de regel, nooit de waarde van een veld; een schemafout onder `accounts` noemt de waarde
   niet (dat deed `recognised_secret` al). `accounts` komt niet in de payload van `translate` of
   `POST /catalog/v1/preview`, niet in `requirements()` (`catalog/service.py`) en in geen antwoordmodel: geen
   OpenAPI-wijziging (ADR-0016 besluit 5 en keuze (a)). Een manifest zonder `accounts` valideert precies zoals
   vóór deze stap.
2. **Woordenlijst en aanmeldnamen (BN-490-1, optie 1: een vaste lijst).**
   - `INIT`, `LOGIN` en `BASIC_AUTH` komen als woord in `ACCOUNT_WORDS` en gelden daar zoals de andere woorden: ergens in
     een geheimnaam maakt het van die naam een accountnaam (`DOCKER_INFLUXDB_INIT_PASSWORD`, `N8N_BASIC_AUTH_PASSWORD`,
     `LOGIN_TOKEN`); als heel naamdeel in een voorouder-sleutel, in een naam zonder geheimwoord en in de eigen naam van
     een parameter met een geheimmarkering (ADR-0019, ADR-0021, ADR-0022).
   - `JUPYTER_TOKEN` en `PASSWORD` (`LOGIN_NAMES`) tellen als accountnaam alleen als **hele naam** (hoofdletterongevoelig,
     zonder lengteverschil, zoals de andere naamregels) van een omgevingsvariabele (`custom_app.env[].name`, de `name`
     van een naam/waarde-paar in `values`) of van een parameter. Een sleutel in de boom van `values` (zoals
     `db: {password: …}`) en het laatste deel van een naam met punten tellen niet: dat zijn sleutels van de app van iX
     of van een instelling, en daar staat juist het geheim van de Hub van §3.1 (a). De meting (tabel hierboven, regel L)
     toont dat `PASSWORD` als laatste sleuteldeel op dit moment 0 van de 22 bestanden zou raken; dat blijft een
     vervolgpunt, geen deel van dit besluit.
   - Gevolg: een geheim van de Hub, een kluisverwijzing of een gewone parameter onder zo'n naam is
     `account_gegevens_verboden` (N3), in `validate` en in `translate` (W3); nooit de waarschuwing `secret_in_form`.
   De lijst blijft vast. Een aanmeldnaam buiten de lijst die wel een geheimnaam is (bijvoorbeeld `WEBPASSWORD`) of een
   samenstelling zonder `_` (`basicAuthPassword`: `BASICAUTH` staat niet in de lijst) blijft bij de review van het
   manifest; een uitbreiding is opnieuw een wijziging van de standaard.
3. **Een kluisverwijzing in een manifest is ook in `validate` een fout (C12-3).** Elke tekst in het manifest en elke
   sleutel in `values` waarin `vault://` staat (hoofdletterongevoelig, dezelfde vorm als in `translate.py`), geeft de
   fout `vault_ref_not_allowed`: op de ruwe gegevens, net als de tekstregels van W2 (dus ook als het schema faalt), met
   het pad en zonder de tekst (bij een sleutel het pad van het object). De vertaling houdt haar eigen controle
   (`_unmarked_vault_refs`): alleen een verwijzing die zij zelf zet, mag in de payload staan.
4. **Variant B: een geheim van de Hub in een omgevingsvariabele van een Custom App alleen onder een toegestane naamvorm
   (BN-490-2; de meting raakt 0 van 22 bestanden, dus B en niet A).** De tweede uitzondering van §3.1 geldt alleen onder
   een naam die de validator kan beoordelen: een geheimnaam (ADR-0016 W2) die geen accountnaam is. Onder een
   accountnaam blijft het `account_gegevens_verboden`. Onder elke andere naam (geen geheimnaam, bijvoorbeeld `APP_KEY`
   of `X`) is het de fout `secret_in_form` (niet te accepteren; fail-closed: de validator kan dan niet uitsluiten dat het
   een aanmelding is). De vertaling toetst hetzelfde na het invullen (W3): een eigen vault-verwijzing in `values.envs`
   onder een naam die geen geheimnaam is, geeft `secret_in_form` en geen payload. Dit geldt alleen voor de
   omgevingsvariabelen van een Custom App; de sleutels in de `values` van een catalogusapp (§3.1 (a)) legt de app van iX
   vast en blijven zoals ze zijn. Wat blijft: een aanmeldnaam die wel een geheimnaam maar geen accountnaam is
   (`WEBPASSWORD`), komt met de te accepteren waarschuwing door; de `reden_nl` van `accepted_findings` en de review van
   het manifest blijven daar de controle. Variant C (de catalogus slaat een versie alleen op bij `strict_valid`) wordt
   niet gebouwd (besluit van de regisseur).
5. **"Enige uitzondering" rechtgezet (ADR-0031 besluit 9).** Lees in ADR-0016 eis 1, besluit 9 en W3 "het
   enrollment-token is de enige uitzondering van §3.1" als: C-12 voegt voor een beheerder of dienstaccount geen
   uitzondering toe; de uitzonderingen van §3.1 zijn het eenmalige enrollment-token en, sinds C12-3, een geheim dat de
   Hub maakt als vault-verwijzing in een omgevingsvariabele van een Custom App (ADR-0015), nooit onder een accountnaam
   en (punt 4) alleen onder een geheimnaam. Het gedrag van W3 verandert daardoor niet: een vault-verwijzing die de
   vertaling zelf zet, gaat door; `HUB_ENROLLMENT_TOKEN` blijft de enige naam die W3 met een geheim laat staan als de
   vertaling hem zelf zette. `docs/MANIFESTSTANDAARD.md` noemt de vault-weg niet meer "afwijkend van §3.1".
6. **B-6: `NIET GEVALIDEERD (echte Hub)` in `validatie_nu` (C12-6).** Beide kopieën van `checklist-v1.schema.json`
   krijgen de waarde; een test eist dat de `NIET GEVALIDEERD (…)`-waarden van de lijst precies die van de tabel van §11
   zijn en dat elke waarde van de lijst in die tabel staat.
7. **Niet in dit besluit (BN-490-3 en de opdracht).** C12-5 (een controlelijst nooit naast een installeerbaar manifest
   van hetzelfde I-document) en de regel tegen een YAML-installatieweg in een controlelijst horen bij een eigen kleine
   stap. Het bronvertrouwen van de Hub (C1.3-ASM-24) hoort bij S-P2-050. De pins en de kopieën in andere repos
   (`ASF_COMMIT`, `VALIDATOR_COMMIT`, `APPSTORE_COMMIT`, de validatorkopie in jelly-leon-platform) wijzigen hier niet.

## Gevolgen

- Nieuwe codes in `findings.py` en in `docs/MANIFESTSTANDAARD.md`: `account_ongeldig`, `account_controle_ontbreekt` en
  `account_controle_te_lang`. `vault_ref_not_allowed` en `secret_in_form` gelden nu ook in `validate` op de plekken
  van punt 3 en 4.
- `docs/MANIFESTSTANDAARD.md` krijgt een kop voor `accounts`, een rij in "Opbouw in het kort", de nieuwe woorden en
  aanmeldnamen, de regel van punt 4 en de bekende valse treffers van `INIT` en `LOGIN` binnen een woord in een
  geheimnaam (`AFFINITY_TOKEN`, `CATALOGINDEX_SECRET`; fail-closed, kies een andere naam).
- De kopieën en pins in andere repos houden het oude gedrag tot zij hun pin verzetten. Een oudere validator weigert
  een manifest met `accounts` (`unknown_field`). Volgens de nulmeting valt geen bestaand manifest door dit ADR anders
  uit; de stap meet dat opnieuw met de gebouwde validator (voor en na, per bestand).
- Afnemers: S-P3-445 (jelly-leon-platform: validatorkopie, checklist-schema en pin) en S-P3-360 (home-control-hub).

## Wat dit niet bewijst

- Niets is op TrueNAS of met een echte Hub geprobeerd: NIET GEVALIDEERD (echte NAS). Of de Hub-UI de `afnemer` en de
  `rechten` van een dienstaccount toont (ADR-0016 "Gevolgen per repo" punt 6), is ONBEKEND tot dat scherm er is.
- Herkenning op naam blijft een heuristiek, ook met variant B: een aanmeldnaam die een geheimnaam is maar niet in de
  lijst staat (`WEBPASSWORD`, `basicAuthPassword`), een geheim onder een sleutel in catalogus-`values`, en `PASSWORD`
  als laatste sleuteldeel ziet de validator niet als aanmelding. Daar blijft de review van het manifest de controle.
- De validator toetst niet of `afnemer` een bestaande `app_id` is, en niet of de app de `rechten` kent.
