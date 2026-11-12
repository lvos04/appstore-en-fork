# ADR-0017 — Validator: kruisverwijzing en elke bcrypt-vorm weigeren (maakt ADR-0016 besluit 4 waar)

- **Status:** aangenomen · 2026-11-12 · besluit van de gebruiker (keuzemenu, 2026-11-12: "Ja, nu dichtmaken") · vult
  [ADR-0016](ADR-0016-beheerder-en-dienstaccount-zonder-geheim-in-het-formulier.md) aan (kop "Wat er in de
  manifeststandaard verandert", W2, regels T1 en T2) en wijzigt die ADR niet · gebouwd in stap S-P2-520, samen met W2 en
  W3 van ADR-0016.
- **Bron:** ADR-0016, kop "Besluit" punt 4 ("Zo kan `FLOWS` geen bcrypt-hash, gebruiker of `credentialSecret` meer
  dragen in een vorm die Node-RED op de gepinde digest als instelling aanneemt") en kop "Wat dit ontwerp niet bewijst";
  de gepinde Node-RED-image `nodered/node-red:5.0.9-24` op digest
  `sha256:2ccd38b4066648bd5e17430c50a2f8102c566a82fd4f2fc5c342e61664d6896f` (de digest van het Node-RED-manifest van
  jelly-leon-platform): `node_modules/@node-red/util/lib/util.js`, functie `normalisePropertyExpression`, en
  `node_modules/@node-rs/bcrypt` (de bcrypt die `@node-red/editor-api/lib/auth/users.js` gebruikt).
- **Validatie:** Unit-getest en via de API met PostgreSQL 16 (`apps/api/tests/unit/test_manifest_geheimen.py`,
  `apps/api/tests/integration/test_geheimen_preview.py`). **NIET GEVALIDEERD (echte NAS):** niets is op TrueNAS of met
  een op de NAS gestarte Node-RED geprobeerd.

## Context

ADR-0016 besluit 4 belooft dat `FLOWS` geen bcrypt-hash, gebruiker of `credentialSecret` meer kan dragen in een vorm die
Node-RED op de gepinde digest als instelling aanneemt. De regels T1 en T2 zoals ADR-0016 ze beschrijft, maken dat niet
helemaal waar; de review van die ADR vond twee wegen die erlangs gaan:

1. **Kruisverwijzing in de sleutel.** `red.js` zet elke define-instelling met `setObjectProperty` op het
   instellingenobject. `normalisePropertyExpression` leest daarbij een deel tussen haken dat met `msg.` of `msg[` begint
   als verwijzing naar een eerder gezette waarde (gelezen in de gepinde image: "If it starts with msg it is a nested
   expression", met `/^msg[.\[]/`), en een deel tussen haken met een cijfer of aanhalingsteken, of een deel na een punt
   dat alleen uit cijfers bestaat, als index. Zo zet `-D k=users -D p=password -D adminAuth={"type":"credentials"}
   -D adminAuth[msg.k][0].username=… -D adminAuth[msg.k][0][msg.p]="<hash>"` een gebruiker, terwijl de woorden `users`
   en `password` niet in de tekst staan (gemeten op 2026-11-11 met de gepinde image bij de herreview van ADR-0016;
   proeven kr1 en kr2). Opnieuw gemeten op 2026-11-12 met de define-lus van `red.js` en `@node-red/util` uit de gepinde
   image (zonder Node-RED te starten, alleen testwaarden):

   | Sleutel | Uitkomst |
   |---|---|
   | `adminAuth[msg.k][0].username` en `adminAuth[msg.k][0][msg.p]` (na `k=users`, `p=password`) | gebruiker met hash in `adminAuth.users[0]` |
   | `adminAuth.users.0.username` en `adminAuth.users["0"].username` | gebruiker in `adminAuth.users[0]` (index) |
   | `adminAuth[msg["k"]][0]…`, `adminAuth[ msg.k]…`, `adminAuth[MSG.k]…` | fout in `normalisePropertyExpression`; `red.js` stopt |
2. **bcrypt-vormen die T1 niet kende.** T1 noemde alleen `$2a$`, `$2b$` en `$2y$` met kosten en 53 tekens. De bcrypt van
   Node-RED neemt meer vormen aan. Gemeten op 2026-11-12 met `@node-rs/bcrypt` uit de gepinde image (container zonder
   netwerk, alleen testwaarden; `compare` met het juiste wachtwoord):

   | Vorm (na het voorvoegsel 53 tekens uit het bcrypt-alfabet) | Aangenomen |
   |---|---|
   | `$2a$10$`, `$2b$10$`, `$2y$10$`, `$2x$10$` | ja |
   | `2b$10$` (zonder `$` vooraan), `$$2b$$10$$`, `$$$2b$10$` | ja |
   | `$2b$010$` (voorloopnul), `$2b$+10$` (plusteken) | ja |
   | `$2X$10$`, `$2B$10$`, `$2$10$`, `$2c$10$` | nee |
   | `$2b$-10$`, `$2b$ 10$`, een extra teken achter de 53, een `$` tussen salt en hash | nee |

Voor Node-RED zelf is dit nu niet actueel: volgens ADR-0016 punt 7 (besluit van de gebruiker: "Wachten") krijgt het
manifest geen gebruikers en geen `accounts`, en de repotest van jelly-leon-platform dwingt `adminAuth` zonder
gebruikers af. De validator is wel de algemene controle voor elk manifest; zonder deze ADR belooft besluit 4 meer dan
de validator doet.

## Besluit

1. **T1 kent elke bcrypt-vorm die de bcrypt van Node-RED aanneemt.** Een wachtwoordhash is (naast de PHC-vormen van
   ADR-0016) elke tekst die past op `\$*2[abxy]\$+\+?[0-9]+\$+[./A-Za-z0-9]{53}`: nul of meer `$`, versie `2a`, `2b`,
   `2x` of `2y`, één of meer `$`, kosten met eventueel `+` en voorloopnullen, één of meer `$` en 53 tekens uit het
   bcrypt-alfabet. Er staat geen grens achter de 53 tekens en geen grens vóór het begin (fail-closed): een langere tekst
   of een woord dat er toevallig mee begint, telt ook. T1 geldt zoals in ADR-0016 op elke tekst van het manifest en, via
   T2, ook na het oplossen van JSON-escapes.
2. **T2 weigert een kruisverwijzing of een index in de sleutel van een instelling.** Bij elke instelling die T2 stap 1
   herkent (het deel na de eerste `=` van een woord met `-` vooraan, of een woord zonder `-` met een `=`), is het een
   fout als de sleutel een kruisverwijzing bevat (`[msg.` of `[msg[`; ook met spaties of hoofdletters, fail-closed,
   hoewel Node-RED die laatste drie vormen zelf weigert) of een index (`[0]`, `["0"]`, `['0']`, of een deel `.0` tussen
   punten of aan het eind). De index-vormen zijn de vergelijkbare vormen uit de opdracht van de gebruiker ("`[<cijfer>]`
   en vergelijkbare kruisverwijzingen"): `normalisePropertyExpression` leest `.0` en `["0"]` net als `[0]` (tabel
   hierboven). Instellingen die een lijst nodig hebben, geven die als JSON-waarde (`-D nodesIncludes=[…]`), niet per
   index.
3. **Code en melding.** Beide regels geven `account_gegevens_verboden` (fout, niet te accepteren), met het pad en de
   reden, nooit de waarde. Er komt geen nieuwe code en geen nieuw veld.
4. **Geen lijst van optievormen.** Zoals ADR-0016 besluit 4: T2 kijkt niet naar de naam van de optie. De bcrypt-vormen
   en de kruisverwijzing zijn gemeten op de gepinde digest; bij een nieuwe digest meet de rooktest van
   jelly-leon-platform de kandidaatvormen opnieuw (ADR-0016, "Gevolgen per repo" punt 4), en een vorm die T1 of T2 dan
   mist, is een wijziging van de standaard.
5. **ADR-0016 blijft staan.** Deze ADR wijzigt ADR-0016 niet; samen beschrijven ze wat de validator doet. De andere
   grenzen uit ADR-0016, kop "Wat dit ontwerp niet bewijst", blijven gelden.

## Gevolgen

- **Bestaande manifesten:** gemeten op 2026-11-12 met de validator zelf (vóór: commit `132398f`; na: deze stap) over de
  22 bestanden van ADR-0016 (de manifesten in `deploy/manifest/` van de repos, `jelly-leon-platform:install/manifests/`
  met `overgang-1.3.yaml`, en de zes fixtures in `apps/api/tests/fixtures/manifests/`): 22 van 22 met dezelfde
  exitcode en dezelfde meldingen (`jl-manifest validate --strict --format json`, byte-gelijk). Geen enkel bestand bevat
  `[msg.`, een index in een define-sleutel of een bcrypt-hash.
- **Tests:** kr1 (`$2x$`-hash via de kruisverwijzing) en kr2 (`$2y$`), de kruisverwijzing zonder hash, de index-vormen,
  en elke aangenomen bcrypt-vorm in een tekst (`apps/api/tests/unit/test_manifest_geheimen.py`); via de API zonder
  payload en zonder de testwaarde in het antwoord (`apps/api/tests/integration/test_geheimen_preview.py`).
- **Contract:** de code `account_gegevens_verboden` staat al in het voorstel voor contract kop 9 (ADR-0016 besluit 9);
  deze ADR voegt alleen gevallen toe. De contractronde (eigenaar ai-server-manager) neemt T1 in deze vorm mee.
- **Andere repos:** pas als een repo zijn pin op deze repo verzet (`ASF_COMMIT`, `VALIDATOR_COMMIT`, `APPSTORE_COMMIT`),
  geldt dit daar; volgens de meting hierboven valt daardoor geen bestaand manifest anders uit.

## Wat dit niet bewijst

- Niets is op TrueNAS geprobeerd; of het echte Custom App-formulier een waarde met `[msg.`, `\/` of U+00A0 ongewijzigd
  doorgeeft: NIET GEVALIDEERD (echte NAS).
- De bcrypt-vormen zijn alleen gemeten met `@node-rs/bcrypt` uit de gepinde digest; een andere bcrypt of een andere
  digest kan andere vormen aannemen.
- Een gecodeerde of gesplitste hash en een geheim onder een naam zonder geheimwoord ziet de validator nog steeds niet
  (ADR-0016, "Wat dit ontwerp niet bewijst"); dan blijft de review van het manifest de controle.
