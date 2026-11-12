# ADR-0018 — Validator: geheimen en accountgegevens weigeren, herstel na de review (witruimte, rekentijd, erven van namen, naam van de parameter)

- **Status:** aangenomen · 2026-11-12 · keuzes van de regisseur na de review van stap S-P2-520 (R-TECH, R-VEILIG en
  R-MANIFEST), binnen besluit C-12 van de gebruiker (2026-11-11, "strikt zonder": geen vault-weg voor een beheerder of
  dienstaccount) · vult [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md) (W2 en W3)
  en [ADR-0017](ADR-0017-kruisverwijzing-en-bcrypt-vormen-weigeren.md) aan en wijzigt die niet · gebouwd in stap
  S-P2-520 (herstel).
- **Bron:** de reviewrapporten van stap S-P2-520 (bevindingen R-VEILIG-1 tot en met -6, R-TECH-1 tot en met -4 en
  R-MANIFEST-1 tot en met -3); `apps/api/src/appstore/manifest/rules.py`, `translate.py`, `validator.py`, `loader.py`;
  `apps/api/src/appstore/api/catalog.py` (functie `validate`); `apps/api/src/appstore/catalog/service.py` (functie
  `sync_source`).
- **Validatie:** Unit-getest en via de API met PostgreSQL 16 (`apps/api/tests/unit/test_manifest_geheimen_herstel.py`,
  `apps/api/tests/integration/test_geheimen_herstel_api.py`, `apps/api/tests/integration/test_catalog_sync_per_manifest.py`).
  **NIET GEVALIDEERD (echte NAS):** niets is op TrueNAS of met een op de NAS gestarte app geprobeerd.

## Context

De bouw van W2 en W3 (ADR-0016) en ADR-0017 volgde de tekst van die ADR's. De review vond wegen waarlangs een geheim of
de aanmelding van een beheerder er toch door kwam, een regressie in rekentijd en een crash:

1. **Witruimte telde als leeg (R-VEILIG-1).** "Niet-lege waarde" was uitgelegd met Python `str.strip()`. Die ziet
   spaties, U+00A0, U+2003, U+3000, U+001C tot en met U+001F en U+0085 als leeg, maar bash, `nopt` en de apps doen dat
   niet. Een beheerderswachtwoord of `credentialSecret` van alleen zulke tekens kwam door validate en preview.
2. **Kwadratische rekentijd (R-VEILIG-2).** De patronen voor een bcrypt-hash (met `\$*` vooraan), een JSON-sleutel en
   een pad met `users` begonnen bij elke positie opnieuw en lazen tot het eind. Een viewer kon met één
   `POST /catalog/v1/validate` (het JSON-veld `manifest` ging buiten de grenzen van de lader om) de dienst minuten lang
   stilzetten.
3. **`ß` liet de validator crashen (R-VEILIG-3).** `"paß".upper()` is `"PASS"`, een teken langer; de vergelijking las
   daarna buiten de naam (`IndexError`). Eén zo'n manifest liet de synchronisatie van de hele bron mislukken.
4. **De uitzondering voor `HUB_ENROLLMENT_TOKEN` was te ruim (R-VEILIG-4).** Ze sloeg T1 en T2 over en keek niet wie de
   naam zette; een naam/waarde-paar uit de wizard met die naam (of met `HUB_URL`) kwam met een hash in de payload.
5. **Een herkende waarde in `app_id` of `version` stond in het rapport (R-VEILIG-5),** en een sleutel met een waarde
   (`credentialSecret=…`) of een hash als sleutel stond in pad en melding (R-TECH-4). Een hash als sleutel in `values`
   kwam zelfs in de payload.
6. **De naam van de plek telde alleen zelf (R-VEILIG-6, R-TECH-1, R-MANIFEST-1).** Een object onder een geheimnaam
   (`admin_password: {x: …}`), een geheim van de Hub onder `admin.password` of `adminUser.password`, en een parameter
   met een accountnaam (`admin_password`, `beheer_wachtwoord`) naar een plek zonder geheimwoord gingen door. Bij een
   geheim van de Hub is dat de vault-weg die besluit C-12 uitsluit.
7. **Een cijfer na het geheimwoord telde niet (R-MANIFEST-2):** `DB_PASSWORD2`, `ADMIN_TOKEN1`.
8. **Uitleg zonder test of tekst (R-TECH-2, R-MANIFEST-3):** dat ja/nee geen waarde is (a), dat de naam van de app niet
   meetelt bij het verbergen (c), en de indexvorm `['0']`. **Valse treffers (R-TECH-3):** een index in een adres,
   `*_FILE` met een pad, `naam=` met een geheimwoord in een gewone optie.

## Besluit

1. **Elke niet-lege tekst telt.** Een tekst is een waarde zodra hij niet leeg is (`value != ""`), ook als hij alleen
   uit witruimte of stuurtekens bestaat; ja/nee en `null` tellen nog steeds niet. Het vangnet van T2 stopt alleen op
   de IFS-witruimte van bash (spatie, tab, regeleinde): `naam=` gevolgd door U+00A0 of U+001F is een waarde, net als
   een JSON-tekst met alleen zulke tekens. Na de `:` van een JSON-sleutel en binnen een lege lijst of een leeg object
   telt alleen JSON-witruimte (spatie, tab, regeleinde, terugloop) als leeg.
2. **Lineaire rekentijd en grenzen.** T1 zoekt een bcrypt-hash zonder `\$*` vooraan (dezelfde treffers). Het vangnet
   leest JSON-teksten en paden met `users`/`gebruikers` één keer van links naar rechts, met dezelfde uitkomst als
   voorheen (elke `"` kan een sleutel beginnen, fail-closed). Een tekst langer dan 65536 tekens toetsen T1 en T2 niet:
   die is fail-closed een fout (`account_gegevens_verboden`, reden "te lang om op geheimen te toetsen").
   `POST /catalog/v1/validate` past op het JSON-veld `manifest` dezelfde grenzen toe als op `manifest_yaml`:
   `MAX_MANIFEST_BYTES` (256 KiB, gemeten als compacte JSON) en `MAX_DEPTH` (32), met de bestaande codes
   `manifest_too_large` en `yaml_too_deep`.
3. **Geheimnaam zonder lengteverschil, en met een cijfer.** Een geheimwoord telt alleen als het stuk van de naam
   precies zo lang is als het woord; `paß`, `Reisepaß` en `ß` zijn geen geheimnaam en geven geen uitzondering. Behalve
   het einde, `_`, `-`, `.` en een hoofdletter na een kleine letter, telt ook een cijfer na het geheimwoord
   (`DB_PASSWORD2`, `ADMIN_TOKEN1`).
4. **Fail-closed per manifest bij het synchroniseren.** Een onverwachte fout bij het vastleggen van één manifest weigert
   alleen dat manifest (nieuwe code `manifest_niet_verwerkt` in het resultaat van de synchronisatie); een savepoint
   draait zijn wijzigingen terug en de andere manifesten van de bron gaan door. De log noemt het bestand en de soort
   fout, nooit de tekst van de fout (die kan waarden uit het manifest bevatten).
5. **`HUB_*` na het invullen.** De uitzondering voor `HUB_ENROLLMENT_TOKEN` geldt alleen voor de geheimnaamregel en
   alleen op een plek waar de vertaling de naam zelf zette (de vertaling doet dat nu nooit); T1 en T2 gelden altijd. Een
   `HUB_*`-naam die via een parameter binnenkomt (een sleutel of een `name`, ook in een naam/waarde-paar) geeft na het
   invullen `reserved_env`, net als in de validator; dan is er geen payload.
6. **Geen herkende waarde in uitvoer.** Herkent W2 de waarde van `app_id`, `version` of `install.method`, dan is dat
   veld in het rapport leeg (`null`), dus ook in de tekst, de uitvoer van `jl-manifest`, het antwoord en het bewijs. T1
   geldt ook op elke sleutel in `values`. Een sleutel met een `=` of met iets dat T1 of T2 herkent, staat in pad en
   melding als `<verborgen>` (ook bij `unknown_field` en in de meldingen van de vertaling); het pad in `vault_refs`
   blijft het echte pad.
7. **Een naam geldt ook voor wat eronder ligt, en de naam van de parameter telt.** In `values` (en na het invullen in
   W3) is een plek een geheimplek als haar eigen naam of de naam van een voorouder een geheimnaam is (een object erft de
   naam, net als een lijst), en een accountplek als daarnaast haar eigen naam of een voorouder-sleutel een accountwoord
   heeft (`admin.password`, `adminUser.password`). De naamregels N1, N2 en N3 gelden voor geheimplekken en
   accountplekken. Daarnaast telt de naam van de parameter: een gebruikte parameter met een accountnaam geeft
   `account_gegevens_verboden` (ook bij `source: generated` of `secret`, niet te accepteren), en een gewone parameter
   met een geheimnaam die ergens wordt gebruikt geeft de fout `secret_in_form`; per plek één code. De vertaling vult
   zo'n parameter ook zelf niet in (verdediging in de diepte).
8. **Geen uitzondering voor `*_FILE`.** Een naam als `POSTGRES_PASSWORD_FILE` blijft een geheimnaam; de bekende valse
   treffers staan in `docs/MANIFESTSTANDAARD.md` met de raad een andere naam of een parameter te kiezen.
9. **Uitleg vastgelegd.** (a) zoals punt 1; (b) zoals ADR-0017 punt 2, met de indexvormen `[0]`, `["0"]`, `['0']` en
   `.0`; (c) de naam van de app (`name` bovenaan) is geen plek met een naam: een schemafout verbergt een waarde alleen
   onder een plek met een geheimnaam (of onder `accounts`) of als T1/T2 haar herkent.
10. **ADR-0016 en ADR-0017 blijven staan.** Deze ADR wijzigt ze niet; samen beschrijven ze wat de validator doet.

## Gevolgen

- **Bestaande manifesten (eis 8):** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: dit
  herstel) over dezelfde 22 bestanden als ADR-0016 en ADR-0017 (de manifesten in `deploy/manifest/` van de repos,
  `jelly-leon-platform:install/manifests/` met `overgang-1.3.yaml`, en de zes fixtures in
  `apps/api/tests/fixtures/manifests/`): 22 van 22 met dezelfde exitcode en byte-gelijke uitvoer van
  `jl-manifest validate --strict --format json`. Ook de vertaling met neutrale proefwaarden gaf voor elk geldig bestand
  dezelfde uitkomst en dezelfde `preview_hash`.
- **Tests:** per regel eerst rood gezien op de code van vóór het herstel en een mutatieproef (zonder de regel rood).
- **Contract:** `account_gegevens_verboden` dekt nu ook de gevallen van punt 1, 2, 6 en 7; `manifest_niet_verwerkt`
  is een nieuwe code in het resultaat van de synchronisatie (niet in de uitvoer van validate of preview). Beide horen
  in de contractronde voor kop 9 (ADR-0016 besluit 9; eigenaar ai-server-manager).
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`,
  `APPSTORE_COMMIT`), geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd: of het echte Custom App-formulier een waarde van alleen witruimte of stuurtekens
  ongewijzigd doorgeeft, en of een app als Gotify zo'n wachtwoord trimt, is ONBEKEND en NIET GEVALIDEERD (echte NAS).
  De weigering is daarom fail-closed.
- De grens van 65536 tekens en de tijdproeven (256 KiB in minder dan 1 s) zijn op de ontwikkelmachine gemeten.
- Een gecodeerde of gesplitste hash en een geheim onder een naam zonder geheimwoord (ook in de parameternaam) ziet de
  validator nog steeds niet (ADR-0016, "Wat dit ontwerp niet bewijst"); dan blijft de review van het manifest de
  controle.
