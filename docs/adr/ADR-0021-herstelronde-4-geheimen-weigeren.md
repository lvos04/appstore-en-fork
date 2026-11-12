# ADR-0021 — Validator: herstelronde 4 (pad pas bij een melding, lange sleutel en lang pad in een vaste vorm, boolean met geheimmarkering, gemarkeerd geheim op een plek met een accountwoord)

- **Status:** aangenomen · 2026-11-12 · keuzes van de regisseur na de derde review van stap S-P2-520 (R-VEILIG, R-TECH en
  R-MANIFEST op de herstelcommits van [ADR-0019](ADR-0019-herstelronde-2-geheimen-weigeren.md) en
  [ADR-0020](ADR-0020-accountwoorden-uitgebreid.md)), binnen besluit C-12 van de gebruiker (2026-11-11, "strikt
  zonder": geen vault-weg voor een beheerder of dienstaccount) · vult ADR-0019 en ADR-0020 aan (en daarmee
  [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md),
  [ADR-0017](ADR-0017-kruisverwijzing-en-bcrypt-vormen-weigeren.md) en
  [ADR-0018](ADR-0018-herstel-geheimen-weigeren-na-review.md)) en wijzigt die niet · gebouwd in stap S-P2-520
  (herstelronde 4).
- **Bron:** de derde review van stap S-P2-520 (bevindingen R-VEILIG-8 en -9, R-TECH-9, -10 en -11, R-MANIFEST-5, en het
  vervolgpunt van R-VEILIG over de grootte van het antwoord); `apps/api/src/appstore/manifest/rules.py` (`shown_key`,
  `PathNode`, `shown_path`, `secret_text_findings`, `param_name_counts`, `_Rules.custom_app`, `_Rules.catalog_app`),
  `translate.py` (`filled_value_findings`, `_filled_place`, `_substitute`, `_texts`), `findings.py` (`error`,
  `warning`).
- **Validatie:** Unit-getest en via de API met PostgreSQL 16 (`apps/api/tests/unit/test_manifest_geheimen_herstel4.py`,
  `apps/api/tests/integration/test_geheimen_herstel4_api.py`). **NIET GEVALIDEERD (echte NAS):** niets is op TrueNAS of
  met een op de NAS gestarte app geprobeerd.

## Context

1. **Het pad stond per knooppunt in het geheugen (R-VEILIG-8, blokkerend).** ADR-0019 (punt 1) liet het getoonde pad
   meegaan in de doorloop, om de rekentijd lineair te houden. Daardoor stond voor elk item van een lijst een eigen kopie
   van het hele pad klaar: het geheugen was het aantal knooppunten keer de lengte van het pad. Eén verzoek van de
   laagste rol (`POST /catalog/v1/validate`, rol viewer) met één sleutel van 65536 tekens boven een lijst van 98 000
   nullen (net onder 256 KiB) gaf onder een grens van 2 GiB een MemoryError; zonder grens is dat doorgerekend 6 tot
   25 GiB. De tests van ADR-0019 maten alleen de tijd en wat er na afloop bleef staan, niet de piek.
2. **Elke melding droeg het hele pad (vervolgpunt bij R-VEILIG-8).** Een sleutel van 60 000 tekens met 3000 meldingen
   eronder gaf een rapport plus tekst van 344 MiB. Dat was al zo vóór stap S-P2-520.
3. **De uitzondering voor een boolean was te ruim (R-VEILIG-9, R-TECH-10).** ADR-0019 (punt 4) nam elke parameter van
   type `boolean` uit van de naamregels, ook een parameter die het manifest zelf als geheim markeert. Een Hub-geheim
   `admin_password` van type `boolean` was daardoor geldig, ook in `--strict`; alleen de typecontrole van de vertaling
   hield het tegen, met een andere code.
4. **ADR-0019 (punt 2) was te nauw (R-TECH-9).** Een geheim dat de Hub maakt met een naam zonder geheimwoord (`admin_pw`,
   `init_value`) ging op een plek met een accountwoord (`ADMIN_PW`, `admin.pw`, `admin: {param: init_value}`) als
   vault-verwijzing mee: de vault-weg voor een beheerder die C-12 uitsluit. De validator wist dat het een geheim was (de
   parameter draagt de markering), maar gaf alleen de te accepteren waarschuwing.
5. **Vijf regels hadden geen test die kon falen (R-TECH-11).**
6. **De standaard beloofde meer dan de code doet (R-MANIFEST-5).** "Accountnaam … ergens in de naam" klopt niet voor een
   sleutel met punten: daar telt het laatste deel, net als bij een geheimnaam.

## Besluit

1. **Het pad wordt pas gemaakt als er een melding is.** De doorlopen `secret_text_findings`, `filled_value_findings`,
   `_substitute` en de controle op vault-verwijzingen (`_texts`) houden per knooppunt alleen de ouder en het eigen
   segment bij (`PathNode`, een gelinkte keten) en per niveau één rij kinderen. De tekst van het pad (`shown_path`) en
   de plek als reeks (`path_segments`) ontstaan alleen bij een melding of waar een plek wordt opgezocht. Het geheugen
   hangt zo niet meer af van de lengte van het pad keer het aantal knooppunten. De volgorde van de meldingen is
   ongewijzigd. Dit vervangt alleen de laatste zin van ADR-0019 punt 1 ("het getoonde pad gaat mee in de doorloop"); de
   begrensde cache van dat punt blijft.
2. **Een lange sleutel staat in één vaste vorm.** Een sleutel van meer dan 256 tekens (`SHOWN_KEY_MAX`, gelijk aan
   `CACHED_NAME_MAX`) staat in een pad of melding als `<verborgen>`, dezelfde vorm als een sleutel die een waarde kan
   dragen (ADR-0018). De sleutel wordt daarvoor niet gelezen, dus het tonen van een lange sleutel is geen rekenwerk
   meer; de rekentijd blijft lineair, ook nu het pad per melding wordt gemaakt. De regels zelf (geheimnaam,
   accountwoord, T1 op een sleutel in `values`) lezen nog steeds de hele sleutel.
3. **Een lang pad staat ingekort.** Ook met sleutels van hooguit 256 tekens kan een pad lang worden (32 niveaus). Een
   pad van meer dan 1024 tekens (`MAX_SHOWN_PATH` in `findings.py`) staat in de melding als de eerste 1024 tekens en dan
   `…`. Dat geldt voor elke melding van de validator en de vertaling. Zo is de grootte van één melding begrensd; zie
   "Gevolgen" voor wat dat meet. Dit punt staat niet in de keuze van de regisseur; het is toegevoegd omdat punt 2
   alleen het vervolgpunt over de grootte van het antwoord niet dekt (gemeten, zie "Gevolgen").
4. **De uitzondering voor een boolean geldt alleen zonder geheimmarkering.** `param_name_counts` geeft alleen `False`
   voor een parameter van type `boolean` zonder geheimmarkering (niet `secret: true`, niet `source: secret` of
   `generated`). In de validator en in de vertaling. Een Hub-geheim van type `boolean` met een accountnaam is dus weer
   `account_gegevens_verboden`, niet te accepteren, ook in `--strict`. Een gewone boolean uit de wizard
   (`password_login`) blijft geldig. Dit vult ADR-0019 punt 4 aan.
5. **Een parameter met een geheimmarkering op een plek met een accountwoord is de aanmelding van een beheerder of
   dienstaccount** (optie 1 bij R-TECH-9; volgt uit C-12). Heeft de eigen naam van de plek of een voorouder-sleutel een
   accountwoord als heel naamdeel (of is het de naam van een omgevingsvariabele met een accountwoord), dan telt de
   geheimmarkering van de parameter (`secret: true`, `source: generated` of `secret`, `type: secret_ref`) net als een
   geheimnaam: `account_gegevens_verboden`, niet te accepteren, ook als de naam van de parameter geen geheimwoord
   heeft. Dat geldt in `values` (`_Rules.catalog_app`), bij een omgevingsvariabele van een Custom App
   (`_Rules.custom_app`) en na het invullen in W3 (`_filled_place`: geen payload, geen `vault_refs`). Dit vult ADR-0019
   punt 2 aan. Een accountwoord als deel van een woord telt niet (`phpmyadmin.x` blijft geldig), en een gewone
   parameter zonder geheimmarkering en zonder geheimnaam onder `admin` ook niet.
6. **Accountnaam bij een sleutel met punten.** Voor een accountnaam telt bij een sleutel met punten of `[n]` het
   laatste deel, net als voor een geheimnaam: `phpmyadmin.password` (één sleutel) is geen accountnaam,
   `phpmyadmin_password` wel. De code deed dit al; `docs/MANIFESTSTANDAARD.md` zegt het nu ook. De zin "het accountwoord
   mag overal in die naam staan" van ADR-0019 punt 3 geldt dus binnen dat laatste deel. Geen codewijziging.
7. **Tests voor de vijf regels van R-TECH-11:** de boolean-uitzondering in W3, de grens vóór de laatste hoofdletter van
   een reeks hoofdletters (`ADMINSettings`), het accountwoord als heel naamdeel bij een omgevingsvariabele
   (`PHPMYADMIN_DB`), en het verbergen van een sleutel in het pad van `vault_ref_not_allowed` en van
   `parameter_value_missing`.
8. **ADR-0016 tot en met ADR-0020 blijven staan.** Deze ADR wijzigt ze niet; samen beschrijven ze wat de validator
   doet.

## Gevolgen

- **Geheugen (gemeten op de ontwikkelmachine, 2026-11-12, onder een grens op het adresgeheugen).** Het manifest uit punt
  1 van "Context": vóór dit herstel een MemoryError onder een grens van 2 GiB; erna een gewone uitkomst met een piek van
  44 MiB voor het hele proces (37 MiB vóór de aanroep). Een geldig catalogusmanifest met dezelfde sleutel en lijst in
  `values`: valideren piekt op 3 MiB en vertalen op 4 MiB (tracemalloc); met teksten in plaats van getallen elk 5 MiB.
  Vóór dit herstel was dat 376 MiB bij 1500 items. Hetzelfde verzoek via `POST /catalog/v1/validate` (rol viewer,
  JSON-veld): antwoord van 6,4 KiB, piek 7,6 MiB. Via `manifest_yaml` blijft de piek rond 100 MiB voor het hele proces;
  dat is het inlezen van de YAML zelf (de derde review mat op de code van vóór ADR-0019 99 MiB voor dezelfde vorm).
- **Grootte van het antwoord.** De sleutel van 60 000 tekens met 3000 meldingen: rapport plus tekst van 344 MiB naar
  1,2 MiB. Zonder punt 3 bleef een tweede vorm groot: 26 geneste sleutels van 256 tekens met 40 000 meldingen eronder
  gaf 525 MiB (piek 1375 MiB), met punt 3 is dat 91 MiB (piek 369 MiB). Ter vergelijking: 40 000 meldingen onder korte
  sleutels geven 16 MiB (piek 102 MiB).
- **Bestaande manifesten (eis 8):** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: dit
  herstel) over dezelfde 22 bestanden als ADR-0016 tot en met ADR-0020: 22 van 22 met dezelfde exitcode en byte-gelijke
  uitvoer van `jl-manifest validate --strict --format json`. De vertaling met neutrale proefwaarden gaf voor de 19
  geldige bestanden dezelfde uitkomst en `preview_hash` als onder ADR-0020 (commit `78e24a6`).
- **Tests:** per regel eerst rood gezien op de code van vóór dit herstel. De geheugentests meten de piek (tracemalloc)
  en draaien onder een eigen grens op het adresgeheugen, zodat een terugkerende fout een MemoryError geeft en niet het
  geheugen van de machine vult. Een mutatieproef per nieuwe regel (zonder de regel rood), waaronder "het pad weer
  vooraf per knooppunt". De tijdtests van ADR-0018 en ADR-0019 en de geheugentest van ADR-0019 blijven groen.
- **Eén bestaande test is aangepast** (`test_manifest_geheimen_herstel2.py`): hij legde de grens van ADR-0019 vast die
  punt 5 nu sluit (een Hub-geheim zonder geheimnaam onder `admin` was de te accepteren waarschuwing). De test eist nu
  `account_gegevens_verboden` en bewaakt dat een gewone parameter onder `admin` geldig blijft.
- **Contract:** geen nieuwe code. `account_gegevens_verboden` dekt nu ook punt 4 en 5, en een pad kan `<verborgen>` of
  een ingekorte vorm hebben (punt 2 en 3). Dat hoort in dezelfde contractronde voor kop 9 als ADR-0016 besluit 9 en
  ADR-0017 tot en met ADR-0020 (eigenaar ai-server-manager).
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`,
  `APPSTORE_COMMIT`), geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd. Hoeveel geheugen de appstore op de NAS mag gebruiken, is ONBEKEND en NIET GEVALIDEERD
  (echte NAS). De geheugen- en tijdproeven zijn op de ontwikkelmachine gemeten.
- **Het aantal meldingen per rapport heeft geen grens.** Een manifest van 256 KiB kan tienduizenden meldingen geven;
  elke melding is nu begrensd (pad hooguit 1025 tekens, een getoonde sleutel hooguit 256), maar het antwoord groeit
  met het aantal meldingen (zie de metingen hierboven: tientallen MiB). Dat was vóór stap S-P2-520 al zo. Een grens op
  het aantal meldingen is een eigen besluit.
- De doorlopen `catalog_value_findings`, `_param_refs` en `_hub_names_in_values` bouwen het pad nog per knooppunt op,
  maar houden het niet vast (één pad tegelijk, hooguit 32 niveaus); met punt 2 is dat pad hooguit 32 keer 256 tekens.
- **Bekende valse treffer door punt 5:** een geheim dat de Hub maakt onder een sleutel als `setup`, `initial`, `owner`
  of `bootstrap` (accountwoord als heel naamdeel) geeft nu een fout, ook als het geen aanmelding is (bijvoorbeeld een
  API-sleutel onder `setup`). Of echte catalogusapps van iX zulke sleutels hebben, is ONBEKEND; eerst vaststellen met de
  waardenschema's van de catalogusapps die de Hub wil aanbieden. Zo'n app vraagt een besluit of een nieuwe ADR, geen
  uitzondering in het manifest (MANIFESTSTANDAARD.md, "Bekende valse treffers").
- Een gecodeerde of gesplitste hash, en een geheim onder een naam zonder geheimwoord dat via een gewone parameter
  (zonder geheimmarkering) of letterlijk in het manifest staat, ziet de validator nog steeds niet (ADR-0016, "Wat dit
  ontwerp niet bewijst"). De lijst van accountwoorden blijft open (ADR-0020 punt 4).
