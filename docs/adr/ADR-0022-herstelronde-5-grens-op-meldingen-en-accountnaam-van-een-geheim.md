# ADR-0022 — Validator: herstelronde 5 (hooguit 1000 meldingen per rapport, accountwoord in de eigen naam van een gemarkeerd geheim)

- **Status:** aangenomen · 2026-11-12 · besluiten B-1 en B-2 van de regisseur na de vierde review van stap S-P2-520
  (R-VEILIG, R-TECH en R-MANIFEST op de herstelcommits van [ADR-0021](ADR-0021-herstelronde-4-geheimen-weigeren.md)),
  in een extra ronde op besluit van de gebruiker ("Nog één kleine ronde", 2026-11-12), binnen besluit C-12 van de
  gebruiker (2026-11-11, "strikt zonder": geen vault-weg voor een beheerder of dienstaccount) · vult ADR-0021 aan (en
  daarmee [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md) tot en met
  [ADR-0020](ADR-0020-accountwoorden-uitgebreid.md)) en wijzigt die niet · gebouwd in stap S-P2-520 (herstelronde 5).
- **Bron:** de vierde review van stap S-P2-520 (bevindingen R-VEILIG-10, R-TECH-12 en R-MANIFEST-6, en de besluitvragen
  B-1 en B-2 van R-VEILIG); `apps/api/src/appstore/manifest/findings.py` (`MAX_FINDINGS`, `finding_budget`, `error`,
  `warning`), `validator.py` (`validate_data`, `_schema_findings`), `translate.py` (`translate`, `_filled_place`),
  `conflicts.py` (`analyse`) en `rules.py` (`marked_account_param`, `_Rules.param_name_rules`, `_Rules.custom_app`,
  `_Rules.catalog_app`).
- **Validatie:** Unit-getest en via de API met PostgreSQL 16 (`apps/api/tests/unit/test_manifest_geheimen_herstel5.py`,
  `apps/api/tests/integration/test_geheimen_herstel5_api.py`). **NIET GEVALIDEERD (echte NAS):** niets is op TrueNAS of
  met een op de NAS gestarte app geprobeerd.

## Context

1. **Het aantal meldingen per rapport had geen grens (R-VEILIG-10, belangrijk; besluit B-1).** ADR-0021 begrensde één
   melding (een sleutel van meer dan 256 tekens als `<verborgen>`, een pad van meer dan 1024 tekens ingekort), maar
   niet het rapport. ADR-0021 ("Wat dit niet bewijst") schatte het antwoord daarna op "tientallen MiB", en
   `docs/MANIFESTSTANDAARD.md` zei "Zo blijft een melding klein, hoe lang een sleutel ook is en hoeveel meldingen er
   ook zijn". Dat was te laag. Gemeten op de code van ADR-0021 (commit `3227fe1`) met de vorm van de review: één
   verzoek van de laagste rol (`POST /catalog/v1/validate`, rol viewer, JSON-veld van 262 143 bytes: 28 geneste
   sleutels van 256 tekens met elk één emoji, daaronder `password: [1]` 126 111 keer) gaf 126 112 meldingen, een
   antwoord van 281 MiB en 1981 MiB extra RSS in 2,2 s (de review en deze ronde maten hetzelfde); in één proces,
   met het antwoord erbij: 282,6 MiB, RSS van 47 naar 2844 MiB. Er waren meer wegen naar veel meldingen:
   - 120 000 verkeerde items in `parameters` (240 KiB): 120 020 schemafouten, een piek van 472 MiB en 9 s (de
     validator haalde alle schemafouten op en sorteerde ze);
   - de vertaling van een geldig manifest met 3000 letterlijke vault-teksten in `values`: 3001 meldingen (ook in
     `POST /catalog/v1/preview` met een los manifest);
   - de conflictanalyse van de preview: 200 hostpaden tegenover een inventaris van 8000 volumes op hetzelfde pad
     (0,6 MiB): 1,6 miljoen meldingen, RSS van 44 naar 1188 MiB.
   De 22 bestaande manifesten en fixtures geven hooguit 23 meldingen.
2. **Een gemarkeerd geheim met een accountwoord in de eigen naam (besluit B-2).** ADR-0021 punt 5 keek naar de plek: een
   parameter met een geheimmarkering is een accountgegeven op een plek met een accountwoord. Op een plek zonder
   accountwoord bleef een Hub-geheim met de naam `admin_pw` of `root_key` (geen geheimwoord, dus geen accountnaam)
   geldig, ook in `--strict`, en ging het als vault-verwijzing mee (`x: {param: admin_pw}`, de omgevingsvariabele `X`
   naar `admin_pw`; `x: {param: beheer_code}` met `type: secret_ref` en `source: secret` gaf de niet te accepteren
   waarschuwing). Dat is de vault-weg voor een beheerder die C-12 uitsluit. De regisseur koos voor dichtmaken.
3. **Eén regel had geen test die kon falen (R-TECH-12, klein).** T2 op `parameters[].default` en `parameters[].example`
   (diepte 3 in `secret_text_findings`): zonder die regel bleven alle tests groen (mutatie W08: 530 passed op `3227fe1`).
4. **De standaard beloofde een ruimere uitkomst dan de code (R-MANIFEST-6, klein).** "elders de te accepteren
   waarschuwing `secret_in_form`" klopt alleen voor een geheim dat de Hub maakt; een ander gemarkeerd geheim geeft
   een niet te accepteren waarschuwing (catalogusapp) of een fout (Custom App).

## Besluit

1. **Hooguit 1000 meldingen per rapport** (`MAX_FINDINGS = 1000` in `findings.py`; besluit B-1, optie 1). Elk rapport
   heeft een eigen budget (`finding_budget`): `error` en `warning` tellen elke melding die binnen dat rapport wordt
   gemaakt. Bij melding nummer 1001 stopt het verzamelen meteen (`TooManyFindingsError`); het rapport houdt de eerste
   1000 meldingen en krijgt één vaste fout `te_veel_meldingen` (pad `(root)`), samen dus hooguit 1001 regels. Dat is
   fail-closed: het rapport is ongeldig en er is geen model; een begrensde vertaling heeft geen payload, `preview_hash`
   of `vault_refs` en is niet klaar voor installatie; een begrensde conflictanalyse heeft status `conflict`. Een rapport
   is:
   - de validatie (`validate_data`; ook `validate_text`, dus `jl-manifest validate`, `POST /catalog/v1/validate` met
     het JSON-veld `manifest` of met `manifest_yaml`, de validatie in `POST /catalog/v1/preview` en de
     catalogussynchronisatie, die aan het begrensde rapport alleen haar eigen controles op handtekening en bron
     toevoegt);
   - de vertaling (`translate`; `jl-manifest translate` en `POST /catalog/v1/preview`);
   - de conflictanalyse van de preview (`analyse`).
   Elke schemafout geeft minstens één melding; de validator haalt daarom niet meer dan 1001 schemafouten op voordat hij
   ze sorteert (bij hooguit 1000 schemafouten is de uitkomst gelijk aan vroeger). Een controlelijst (`kind:
   checklist`, `jl-manifest checklist`) valt niet onder dit besluit en is niet gewijzigd.
2. **De teksten over het aantal meldingen kloppen weer.** De zin in ADR-0021 ("het antwoord groeit met het aantal
   meldingen (zie de metingen hierboven: tientallen MiB)") was te laag: zonder grens was het honderden MiB per
   verzoek en ongeveer 2 GiB geheugen (punt 1 van "Context"). ADR-0021 blijft staan zoals het is (de gewoonte van deze
   repo); dit besluit vervangt die zin. In `docs/MANIFESTSTANDAARD.md` is "hoeveel meldingen er ook zijn" vervangen
   door de grens per rapport, en de zin over geheugen en rekentijd noemt nu beide grenzen (ADR-0021 en dit besluit).
3. **Een parameter met een geheimmarkering waarvan de eigen naam een accountwoord als heel naamdeel heeft, is de
   aanmelding van een beheerder of dienstaccount** (`marked_account_param`; besluit B-2, volgt uit C-12). De parameter
   draagt `secret: true`, `source: generated` of `secret`, of `type: secret_ref`, en zijn naam heeft een woord uit de
   vaste lijst als heel naamdeel (`has_account_word`, ADR-0019: `admin_pw`, `root_key`, `beheer_code`). Hij telt dan
   als een parameter met een accountnaam: `account_gegevens_verboden` op elke plek, ook op een plek zonder accountwoord,
   niet te accepteren, ook in `--strict`. Dat geldt in de validator voor de naam van elke gebruikte parameter
   (`_Rules.param_name_rules`, bv. de tijdzone van een Custom App), voor een omgevingsvariabele van een Custom App
   (`_Rules.custom_app`) en voor `values` (`_Rules.catalog_app`), en in de vertaling na het invullen (W3,
   `_filled_place`): geen payload, geen `vault_refs`. Een accountwoord als deel van een woord telt niet
   (`phpmyadmin_pw`, `rootless_key`, `groot_sleutel`, `administration_key`), en een gewone parameter zonder
   geheimmarkering valt er niet onder. Dit vult ADR-0021 punt 5 aan.
4. **Test voor T2 op de default en het voorbeeld van een parameter (R-TECH-12).** Een parameter met als `default` en
   `example` de tekst `credentialSecret=<testwaarde>` (T1 vindt daar niets, alleen T2) geeft
   `account_gegevens_verboden` op `parameters[n].default` en `parameters[n].example`, zonder de testwaarde in het
   rapport. Geen codewijziging.
5. **De zin over `secret_in_form` (R-MANIFEST-6).** "Wat de validator niet ziet" zegt nu: op een plek met een
   accountwoord (ADR-0021) of met een accountwoord in de eigen naam (punt 3) een fout, "elders `secret_in_form` (te
   accepteren alleen bij een geheim dat de Hub maakt)". Geen codewijziging.
6. **ADR-0016 tot en met ADR-0021 blijven staan.** Deze ADR wijzigt ze niet; samen beschrijven ze wat de validator
   doet.

## Gevolgen

- **Geheugen (gemeten op de ontwikkelmachine, 2026-11-12).** De vorm uit punt 1 van "Context" via de API (rol viewer,
  JSON-veld): status 200, 1001 meldingen, antwoord 2,2 MiB, piek 17 MiB (tracemalloc, het hele verzoek), RSS 19 MiB
  extra in 0,1 s (was 281 MiB en ongeveer 2 GiB). In één proces, `validate_data` met het antwoord als JSON plus
  tekst: piek 14 tot 16 MiB voor de vormen met `password: [1]`, `"$a"`, `credentialSecret=x` en `{name: HUB_X}` onder
  de 28 sleutels. De 120 000 schemafouten: piek 5 MiB in 0,2 s (was 472 MiB en 9 s). De vertaling met zo veel
  vault-teksten als in 256 KiB past: piek 13 MiB. De conflictanalyse van 200 hostpaden tegen 8000 volumes: 1001
  meldingen, piek onder 1 MiB. Via `manifest_yaml` blijft het inlezen van de YAML zelf de grootste post (piek 77 MiB
  met tracemalloc; zie ADR-0021); het antwoord is begrensd.
- **Bestaande manifesten (eis 8):** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: dit
  herstel) over dezelfde 22 bestanden als ADR-0016 tot en met ADR-0021: 22 van 22 met dezelfde exitcode en
  byte-gelijke uitvoer van `jl-manifest validate --strict --format json` (15 keer exitcode 0, 7 keer 1). Ze gebruiken
  als Hub-geheim alleen `db_password` en `secret_key` (geen accountwoord in de naam) en geven hooguit 23 meldingen.
- **Verschilfuzz:** 6000 willekeurige manifesten (twee vaste seeds, catalogusapp en Custom App, met geheimnamen,
  T1/T2-teksten, naam/waarde-paren, `HUB_`-namen, vault-teksten en een Hub-geheim zonder accountwoord, maar zonder
  invoer die deze ronde raakt: hooguit 34 meldingen per rapport en geen accountwoord in de naam van een gemarkeerde
  parameter) door validatie en vertaling, met de code van `3227fe1` en met dit herstel: byte-gelijke uitvoer, 0
  verschillen.
- **Tests:** eerst rood gezien op de code van `3227fe1` (met alleen de nieuwe namen erbij, zonder gedrag): 21 van de 31
  unittests en alle 4 API-tests rood; de geheugentest via de API faalde daar met een MemoryError. Mutatieproef op de
  nieuwe code: zonder grens, zonder tellen, zonder budget in de validatie, de vertaling of de conflictanalyse, zonder
  de grens vóór het sorteren van de schemafouten of zonder het opruimen van het budget is telkens minstens één test
  rood; zonder grens zijn ook alle 4 API-tests rood (de test van het JSON-veld met een MemoryError). Hetzelfde voor de
  regel van punt 3 in `param_name_rules`, `_Rules.custom_app`, `_Rules.catalog_app` en W3, en voor de twee bewakers
  (accountwoord als deel van een woord, gewone parameter); W08 van R-TECH-12 is nu rood.
- **Vier bestaande tests zijn aangepast**, omdat ze meer dan 1000 meldingen in één rapport verwachtten: in
  `test_manifest_geheimen_herstel4.py` meten `test_answer_under_a_long_key_stays_small` en
  `test_a_long_path_is_shortened_in_a_finding` nu met 900 treffers (alle in het rapport; de grootte en het ingekorte
  pad worden zo nog steeds getoetst), en meten `test_long_keys_with_many_findings_stay_fast` en (in
  `test_manifest_geheimen_herstel2.py`) `test_long_keys_on_many_paths_stay_linear` de tijd van de regels zelf
  (`secret_text_findings` en `check_manifest`, zonder de grens per rapport) met hetzelfde aantal treffers als vroeger;
  daarnaast eisen ze dat `validate_data` op dezelfde invoer `te_veel_meldingen` geeft.
- **Contract:** één nieuwe code, `te_veel_meldingen`, en `account_gegevens_verboden` dekt nu ook punt 3. Dat hoort in
  dezelfde contractronde voor kop 9 als ADR-0016 besluit 9 en ADR-0017 tot en met ADR-0021 (eigenaar
  ai-server-manager).
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`,
  `APPSTORE_COMMIT`), geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd. Hoeveel geheugen de appstore op de NAS mag gebruiken, is ONBEKEND en NIET
  GEVALIDEERD (echte NAS). De metingen zijn op de ontwikkelmachine gedaan.
- Parallelle verzoeken zijn niet gemeten. Elk verzoek is nu begrensd; de heavy-limiter staat 120 verzoeken per minuut
  per gebruiker toe.
- Het inlezen van een YAML-tekst van 256 KiB (`manifest_yaml`, de synchronisatie) kost nog steeds tientallen MiB; dat
  is de YAML-lader, niet het aantal meldingen.
- Bekende valse treffer door punt 3: een Hub-geheim met een accountwoord als heel naamdeel in de naam (`root_ca`,
  `owner_id`, `setup_sleutel`, `initial_seed`) geeft nu een fout, ook als het geen aanmelding is. De naam van een
  parameter kiest het manifest zelf; kies dan een naam zonder accountwoord (MANIFESTSTANDAARD.md, "Bekende valse
  treffers"). De lijst van accountwoorden blijft open (ADR-0020 punt 4).
- Een controlelijst (`kind: checklist`) heeft geen grens op het aantal meldingen (buiten dit besluit).
