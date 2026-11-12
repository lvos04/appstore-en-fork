# ADR-0020 — Validator: vaste lijst van accountwoorden uitgebreid met samenstellingen (herstelronde 3)

- **Status:** aangenomen · 2026-11-12 · keuze van de regisseur (besluit BN-1, optie B) na herstelronde 2 van stap
  S-P2-520, binnen besluit C-12 van de gebruiker (2026-11-11, "strikt zonder": geen vault-weg voor een beheerder of
  dienstaccount) · vult [ADR-0019](ADR-0019-herstelronde-2-geheimen-weigeren.md) aan en wijzigt die niet (ADR-0016,
  ADR-0017 en ADR-0018 blijven ook staan) · gebouwd in stap S-P2-520 (herstelronde 3).
- **Bron:** ADR-0019, kop "Wat dit niet bewijst" (de bewuste grens van punt 3); `apps/api/src/appstore/manifest/rules.py`
  (`ACCOUNT_WORDS`, `has_account_word`, `is_account_name`).
- **Validatie:** Unit-getest (`apps/api/tests/unit/test_manifest_geheimen_herstel3.py`). **NIET GEVALIDEERD (echte
  NAS):** niets is op TrueNAS of met een op de NAS gestarte app geprobeerd.

## Context

ADR-0019 (punt 3) laat een accountwoord in een voorouder alleen tellen als heel naamdeel. Daardoor telde ook een echte
accountnaam niet meer als het woord aan een ander woord vastzit: een geheim van de Hub onder `administrator`,
`beheerder(s)`, `dienstaccount`, `beheeraccount`, `superadmin`, `rootuser` of `adminuser` ging met de te accepteren
waarschuwing `secret_in_form` als vault-verwijzing mee, terwijl ADR-0018 dat weigerde (`account_gegevens_verboden`).
Dat is de vault-weg voor een beheerder of dienstaccount die C-12 uitsluit.

## Besluit

1. **De vaste lijst `ACCOUNT_WORDS` krijgt zeven woorden erbij:** `ADMINISTRATOR`, `BEHEERDER`, `DIENSTACCOUNT`,
   `BEHEERACCOUNT`, `SUPERADMIN`, `ROOTUSER` en `ADMINUSER`. De dertien woorden van ADR-0016 blijven.
2. **De regels van ADR-0019 blijven gelijk.** Ook de nieuwe woorden tellen in een voorouder (en in een naam zonder
   geheimwoord) alleen als heel naamdeel, met een meervouds-`s` erachter. Dus `administrator`, `Administrator`,
   `ADMINISTRATORS`, `beheerder`, `beheerders`, `dienstaccount(s)`, `beheeraccount`, `superadmin`, `rootuser` en
   `adminuser` zijn weer een accountwoord: een geheim van de Hub daaronder geeft `account_gegevens_verboden` (niet te
   accepteren), in `values`, bij een omgevingsvariabele van een Custom App (`ADMINISTRATOR_PW`) en na het invullen in
   W3 (geen payload, geen `vault_refs`).
3. **Geen valse treffers terug.** `phpmyadmin`, `rootless`, `groot` en `administration` blijven geldig
   (`administration` is geen `administrator`); een geheim van de Hub daaronder geeft de te accepteren waarschuwing
   `secret_in_form`, zoals onder ADR-0019.
4. **De lijst blijft open voor nieuwe samenstellingen.** Een samenstelling die niet in de lijst staat (bijvoorbeeld
   `sysadmin`, `webadmin`, `rootaccount`) telt in een voorouder niet; een uitbreiding is een wijziging van de standaard
   met een nieuwe ADR. Een accountnaam (een geheimnaam met een accountwoord ergens in de naam) verandert niet: elk nieuw
   woord bevat al `ADMIN`, `ROOT`, `BEHEER` of `DIENST`.

## Gevolgen

- **Bestaande manifesten (eis 8):** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: dit
  herstel) over dezelfde 22 bestanden als ADR-0016 tot en met ADR-0019: 22 van 22 met dezelfde exitcode en byte-gelijke
  uitvoer van `jl-manifest validate --strict --format json`. De vertaling met neutrale proefwaarden gaf voor de 19
  geldige bestanden dezelfde uitkomst en `preview_hash` als onder ADR-0019.
- **Tests:** eerst rood gezien op de code van vóór dit herstel; een mutatieproef zet de lijst terug naar de oude (rood)
  en haalt elk nieuw woord apart weg (elk rood).
- **Contract:** geen nieuwe code; de uitbreiding van de lijst hoort bij dezelfde contractronde voor kop 9 als ADR-0016
  besluit 9, ADR-0017, ADR-0018 en ADR-0019 (eigenaar ai-server-manager).
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`,
  `APPSTORE_COMMIT`), geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd (NIET GEVALIDEERD, echte NAS).
- Een accountwoord dat in een voorouder aan een ander woord vastzit en zelf niet in de lijst staat, ziet de validator
  niet; dan blijft de review van het manifest de controle (ADR-0016, "Wat dit ontwerp niet bewijst").
- Of echte catalogusapps van iX sleutels hebben als `administrator` of `superadmin` (die nu een fout geven), is
  ONBEKEND; eerst vaststellen met de waardenschema's van de catalogusapps die de Hub wil aanbieden. Zo'n app vraagt een
  besluit of een nieuwe ADR, geen uitzondering in het manifest (MANIFESTSTANDAARD.md, "Bekende valse treffers").
