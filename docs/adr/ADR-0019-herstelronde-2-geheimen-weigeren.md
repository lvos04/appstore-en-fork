# ADR-0019 — Validator: herstelronde 2 na de herreview (begrensde cache, accountwoord als heel naamdeel, geheimnaam van de parameter onder een accountwoord, grens op de vertaling)

- **Status:** aangenomen · 2026-11-12 · keuzes van de regisseur na de herreview van stap S-P2-520 (R-TECH, R-VEILIG en
  R-MANIFEST op de herstelcommits van [ADR-0018](ADR-0018-herstel-geheimen-weigeren-na-review.md)), binnen besluit C-12
  van de gebruiker (2026-11-11, "strikt zonder": geen vault-weg voor een beheerder of dienstaccount) · vult ADR-0018 aan
  (en daarmee [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md) en
  [ADR-0017](ADR-0017-kruisverwijzing-en-bcrypt-vormen-weigeren.md)) en wijzigt die niet · gebouwd in stap S-P2-520
  (herstelronde 2).
- **Bron:** de herreview van stap S-P2-520 (bevindingen R-VEILIG-7, R-TECH-5 tot en met -8 en R-MANIFEST-4, en het
  vervolgpunt van R-VEILIG over de grootte van de vertaling); `apps/api/src/appstore/manifest/rules.py`
  (`is_secret_name`, `shown_key`, `has_account_word`, `Place`, `_Rules.custom_app`, `_Rules.catalog_app`,
  `secret_text_findings`), `translate.py` (`_Resolver.get`, `filled_value_findings`, `_substitute`, `translate`).
- **Validatie:** Unit-getest en via de API met PostgreSQL 16 (`apps/api/tests/unit/test_manifest_geheimen_herstel2.py`,
  `apps/api/tests/integration/test_geheimen_herstel2_api.py`). **NIET GEVALIDEERD (echte NAS):** niets is op TrueNAS of
  met een op de NAS gestarte app geprobeerd.

## Context

1. **De cache hield lange sleutels vast (R-VEILIG-7, R-TECH-5).** ADR-0018 zette een `lru_cache` van 4096 plaatsen op
   `is_secret_name` en `shown_key`, om paden en meldingen snel te houden. Die cache bewaarde elke naam, ook een sleutel
   van 250 000 tekens uit `POST /catalog/v1/validate` (rol viewer) of een parameternaam van bijna 1 MiB uit
   `POST /catalog/v1/preview` (rol operator). Na 300 validaties stond er ongeveer 140 MiB extra in het proces, tot een
   herstart.
2. **ADR-0018 was te nauw (R-TECH-6).** Een geheim van de Hub met een geheimnaam als parameternaam (`db_password`) op een
   plek met een accountwoord maar zonder geheimwoord (`admin: {param: db_password}`, `admin.credentials`,
   `adminUser.pw`, een paar met de naam `ADMIN_PW`) ging als vault-verwijzing mee: de vault-weg voor een beheerder die
   C-12 uitsluit.
3. **ADR-0018 was op punten te ruim (R-TECH-7, R-MANIFEST-4).** Een accountwoord telde ook midden in een woord
   (`phpmyadmin`, `rootless`, `groot`, `administration`), en een parameter van type `boolean` met een geheimwoord in de
   naam (`password_login`) gaf een fout, terwijl een geheim nooit een boolean is (uitleg (a)). Het erven van een
   camelCase-geheimnaam (`passwordPolicy.minLength`, `tokenSettings.ttl`) en de vaste sleutels van een catalogusapp van
   iX stonden niet bij de bekende valse treffers.
4. **Drie regels hadden geen test die kon falen (R-TECH-8):** het vangnet op een JSON-tekst die niet sluit, het
   verbergen van een sleutel met alleen een `=`, en `reserved_env` op een `HUB_*`-sleutel in W3.
5. **De vertaling had geen grens (vervolgpunt R-VEILIG-2).** Eén wizardwaarde van 4096 tekens op duizenden plekken gaf
   een payload van tientallen MB en tientallen seconden rekentijd per `preview`.

## Besluit

1. **Cache alleen korte namen.** `is_secret_name` en `shown_key` gebruiken een cache (elk hooguit 4096 namen) alleen
   voor een naam van hooguit 256 tekens (`CACHED_NAME_MAX`); een langere naam wordt direct getoetst. De cache is daarmee
   begrensd, wat een verzoek ook meestuurt. Om de rekentijd ook voor lange namen lineair te houden, toetst en toont elke
   doorloop elke naam één keer: het getoonde pad gaat mee in de doorloop (`secret_text_findings`, de vertaling met
   `_substitute`, `filled_value_findings` en de controle op vault-verwijzingen), en een plek draagt haar getoonde
   geheimnaam mee (`Place.shown_secret`).
2. **Een parameter met een geheimnaam onder een accountwoord is een accountplek.** Heeft de eigen naam van de plek of
   een voorouder-sleutel een accountwoord, en heeft de parameter een geheimnaam, dan is dat de aanmelding van een
   beheerder: `account_gegevens_verboden`, niet te accepteren, ook bij een geheim dat de Hub maakt. Dat geldt in
   `values` (`_Rules.catalog_app`), bij een omgevingsvariabele van een Custom App (`ADMIN_PW` met `value_from`) en na het
   invullen in W3 (ook voor een vault-verwijzing; dan geen payload en geen `vault_refs`).
3. **Een accountwoord in een voorouder of in een naam zonder geheimwoord telt alleen als heel naamdeel** (keuze van de
   regisseur). De naam wordt gesplitst op elk teken dat geen letter of cijfer is (`_`, `-`, `.`, spatie), op een
   camelCase-grens (`adminUser`, ook `ADMINUser`) en tussen een letter en een cijfer; een meervouds-`s` mag achter het
   woord staan. Dus `admin`, `Admin_ui`, `adminUser`, `root user`, `beheer`, `setup`, `initialSync`, `admins` en
   `admin2` wel; `phpmyadmin`, `rootless`, `groot`, `administration`, `webroot` en `ownership` niet. Voor de keuzes die
   de regisseur niet noemde, is de strengste uitleg gebouwd: elk ander leesteken en de grens letter/cijfer splitsen ook,
   en de meervouds-`s` houdt `admins[0].password` (een geval dat de herreview als echt beheerdersaccount aanwees)
   geweigerd. Een **accountnaam** (een geheimnaam met een accountwoord, zoals `ADMINISTRATOR_PASSWORD`,
   `beheerwachtwoord` of `ADMINPASSWORD`) blijft zoals ADR-0016 hem noemt: het accountwoord mag overal in die naam
   staan.
4. **Geen naamregel voor de naam van een parameter van type `boolean`** (uitleg (a): een geheim is nooit een boolean).
   Dat geldt voor de regels van ADR-0018 (accountnaam, geheimnaam zonder geheimmarkering) en voor punt 2, in de
   validator en in de vertaling. De regels voor de plek zelf blijven gelden (`admin_password: {param: <boolean>}` blijft
   `account_gegevens_verboden`).
5. **Een grens op de vertaling.** Hooguit 256 plekken krijgen een parameterwaarde (`MAX_FILLED_PLACES`; de bestaande
   manifesten hebben er hooguit 11), en de payload is hooguit 256 KiB als canonieke JSON (`MAX_PAYLOAD_BYTES`, gelijk aan
   de grens van de lader voor een manifest). Daarboven geeft `translate` (dus ook `jl-manifest translate` en
   `POST /catalog/v1/preview`) de nieuwe code `vertaling_te_groot`, zonder payload (fail-closed); na de 256e plek vult de
   vertaling niets meer in.
6. **Het erven van een geheimnaam blijft strikt.** Een object onder een geheimnaam, ook in camelCase
   (`passwordPolicy`, `tokenSettings`, `token: {ttl: 3600}`), is een geheimplek. Dat staat nu met een test onder
   "Bekende valse treffers" in `docs/MANIFESTSTANDAARD.md`, samen met de vaste sleutels van iX bij een catalogusapp, het
   accountwoord als heel naamdeel en de boolean; daar staat ook dat een schemafout een waarde onder `accounts` verbergt.
7. **ADR-0016, ADR-0017 en ADR-0018 blijven staan.** Deze ADR wijzigt ze niet; samen beschrijven ze wat de validator
   doet.

## Gevolgen

- **Bestaande manifesten (eis 8):** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: dit
  herstel) over dezelfde 22 bestanden als ADR-0016, ADR-0017 en ADR-0018: 22 van 22 met dezelfde exitcode en
  byte-gelijke uitvoer van `jl-manifest validate --strict --format json`. De vertaling met neutrale proefwaarden gaf voor
  de 19 geldige bestanden dezelfde uitkomst en dezelfde `preview_hash`.
- **Tests:** per regel eerst rood gezien op de code van vóór dit herstel, en een mutatieproef per nieuwe regel (zonder
  de regel rood). De tijdtests van ADR-0018 blijven groen.
- **Contract:** `vertaling_te_groot` is een nieuwe code in de uitvoer van `translate` en `preview`, en
  `account_gegevens_verboden` dekt nu ook punt 2. Beide horen in de contractronde voor kop 9 (ADR-0016 besluit 9;
  eigenaar ai-server-manager).
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`,
  `APPSTORE_COMMIT`), geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd. Hoeveel geheugen de appstore op de NAS mag gebruiken, en of de grenzen daar passen,
  is ONBEKEND en NIET GEVALIDEERD (echte NAS). De geheugen- en tijdproeven zijn op de ontwikkelmachine gemeten.
- **Bewuste grens van punt 3:** een accountwoord dat in een voorouder aan een ander woord vastzit, telt niet meer. Een
  geheim van de Hub onder `administrator`, `beheerder`, `beheerders`, `dienstaccount`, `beheeraccount`, `superadmin`,
  `rootuser` of `adminuser` geeft daardoor weer de te accepteren waarschuwing `secret_in_form` in plaats van
  `account_gegevens_verboden` (onder ADR-0018 geweigerd). Een accountnaam zelf (`ADMINISTRATOR_PASSWORD`,
  `beheerder_wachtwoord`) blijft geweigerd. Of zulke woorden in de vaste lijst moeten, is een besluit (een uitbreiding
  van de lijst is een wijziging van de standaard).
- Of echte catalogusapps van iX sleutels hebben als `passwordPolicy`, `tokenSettings`, `setup` of `initialSync`, is
  ONBEKEND; eerst vaststellen met de waardenschema's van de catalogusapps die de Hub wil aanbieden.
- Een gecodeerde of gesplitste hash en een geheim onder een naam zonder geheimwoord (ook in de parameternaam) ziet de
  validator nog steeds niet (ADR-0016, "Wat dit ontwerp niet bewijst").
