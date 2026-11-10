# Ontwerpreview statuspagina appstore-en-fork — Peil 1.1.0, bijgewerkt naar Peil 1.1.1

- **Wat:** onafhankelijke ontwerpreview achteraf (contract 1.2 §13) van `GET /` van appstore-en-fork, in alle
  toestanden, tegen de ontwerptaal Peil 1.1.0 (`ai-server-manager` `docs/platform-v2/ONTWERPTAAL.md`,
  `design/ontwerptaal/COMPONENTEN.md` §4/§25, ADR-0015 met de besluiten van de gebruiker, `prototypes/statuspagina*.html`
  en `shots/`).
- **Door:** de design-agent, 9 november 2026. Uitgangspunt: commit `66b7c47` (AS7 + B08-002), Peil-sjabloon
  `lvos04/ai-server-manager@172ee87` (byte-gelijke kopie, O-14).
- **Hoe:** de **draaiende** dienst met testdata (`tests/ui/review_live.py`): `python -m appstore migrate`, `serve` en
  `worker` als losse processen met een eigen `/appdata` en PostgreSQL 16, plus de Hub-simulator over echte HTTP.
  Chromium (Playwright 1.65) haalt elke pagina zelf op of verstuurt het koppelformulier zelf (echte cookie, echte
  `Origin`, echte redirect). Alleen voor de simulator op 127.0.0.1 staat de ontwikkelinstelling
  `APPSTORE_ALLOW_LOOPBACK_HUB=1` aan (op de NAS niet). Per toestand: axe-core 4.14 en metingen in de gerenderde DOM op 320/360/390/768/1024/1440 px
  × licht/donker, en schermafdrukken op 320/360/390/768/1440 (`docs/screenshots/peil/statuspagina/`). Gebruikte
  werkwijzen: design-critique, accessibility-review, ux-copy, design-system, design-handoff en dataviz (vormkeuze).
- **Uitkomst:** **Sim-getest.** NIET GEVALIDEERD: echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel
  licht, een echte Hub en een echte NAS (TrueNAS Custom App).

## 0. Bijgewerkt: Peil 1.1.1 en contract 1.3 (10 november 2026)

De statuspagina gebruikt nu het sjabloon **Peil 1.1.1** (`lvos04/ai-server-manager@0a7143b`, gepinde kopie) en volgt
contract **1.3** §3.7 (ADR-0013). De punten die deze review in 1.1.0 aan de eigenaar van het sjabloon overdroeg (§7)
zijn in 1.1.1 opgelost en hier overgenomen: een eigen blok voor een onleesbare koppelstatus (E01), "Koppelen bezig"
(E02), `link-2-off` per controlerij (E03/E04), een neutrale pil voor feiten (toon `feit`, het aantal apps), de
meldingsrol (`alert` alleen bij de fout van dít verzoek, E05) en een code los van de trace-ID (E06). Functioneel: het
formulier verdwijnt nu terwijl een tokenpoging loopt (was bevinding "formulier zichtbaar terwijl een tokenpoging loopt").

**Opnieuw gemeten** tegen de draaiende dienst (`tests/ui/review_live.py`, dezelfde opzet als hieronder): 18 toestanden ×
320/360/390/768/1024/1440 px × licht/donker = 216 metingen: **axe 0 overtredingen**, geen overloop (ook niet met DejaVu
Sans op 320 px), laagste tekstcontrast **8,34:1** (licht) en **9,32:1** (donker), laagste rand/icoon 4,86:1 en 5,69:1,
laagste focusring 7,79:1 en 8,04:1; 180 schermafdrukken (pngquant). De CI-variant (`statuspagina.spec.ts`) meet 14
toestanden uit de echte app, waaronder de nieuwe `koppelen-bezig`, `host-niet-toegestaan` en
`herkomst-host-geweigerd`. Nieuw in de live review: `herkomst-geweigerd` met de negatieve gevallen van contract 1.3
§3.7 punt 6 (allemaal 403, niets gewijzigd). `gekoppeld-melding` wordt nu gemeten in de browser die zelf koppelde (een
melding hoort bij een echte poging van die browser; `/?melding=gekoppeld` bestaat niet meer).

Nog steeds **NIET GEVALIDEERD:** echte schermlezer (NVDA/TalkBack), echte telefoon in fel licht, echte Hub en NAS.

## 1. Samenvatting

De pagina volgt het Peil-sjabloon trouw: status eerst, het eerste signaal is nooit groen zolang de dienst niet gereed
is, "Gekoppeld" is neutraal, onbekend is grijs met een stippelrand en altijd met een reden, en elke status heeft icoon +
woord + vorm. Contrast, focus, doelen en reflow halen ruim de eisen (zie §4).

De review vond **één blokkerende fout die alleen met een echte browser zichtbaar is**: door
`Referrer-Policy: no-referrer` stuurde Chromium bij het koppelformulier `Origin: null`, waarna de Origin-controle
**elke** echte koppelpoging weigerde met "Het formulier is verlopen" (403). De bestaande tests stuurden de
`Origin`-header zelf mee en zagen dit niet. Gerepareerd (`same-origin`), met een regressietest en een CI-stap die het
formulier voortaan in Chromium verstuurt.

Daarnaast vond de nieuwe meting in CI dat een lange variabelenaam in een melding op 320 px buiten de melding steekt
met een breder systeemlettertype (#17); gerepareerd, en beide specs meten nu ook met een breed lettertype.

Verder: zeven tekst- en semantiekpunten (Engelse vaktermen, "token" waar een koppelcode bedoeld is, groen dat als "beschikbaar" las,
"Deels onbekend" bij een besluit van de Hub, ontbrekende vervolgstap, te lange veldfout) zijn gerepareerd. Vier punten
zitten in het gedeelde sjabloon en zijn **niet lokaal omzeild** (de kopie blijft byte-gelijk, O-14); ze staan in §7 voor
de eigenaar van het sjabloon. Eén functioneel punt (formulier zichtbaar terwijl een tokenpoging loopt) is gemeld.

## 2. Toestanden (18: zeventien langs de echte weg, één met foutinjectie)

| # | Toestand | Hoe bereikt (draaiende dienst) | HTTP | Eerste signaal |
|---|---|---|---|---|
| 1 | `niet-gekoppeld` | eerste start zonder `HUB_URL` | 200 | Niet gereed (grijs, stippel) |
| 2 | `koppelen-invoerfouten` | formulier: adres met pad, code "123", vingerafdruk "ABCD" | 422 | Niet gereed + foutmelding |
| 3 | `vingerafdruk-onjuist` | geldige code, vingerafdruk van een andere Hub | 400 | Niet gereed + foutmelding |
| 4 | `hub-onbereikbaar` | adres waar niets luistert | 502 | Niet gereed + foutmelding |
| 5 | `koppelen-mislukt` | onbekende koppelcode | 400 | Niet gereed + foutmelding |
| 6 | `laatste-poging-mislukt` | herladen na de mislukte poging | 200 | Niet gereed + opgeslagen fout |
| 7 | `te-veel-pogingen` | zesde poging binnen 15 min | 429 | Niet gereed + foutmelding |
| 8 | `sessie-verlopen` | formulier verstuurd zonder cookie | 403 | Niet gereed + foutmelding |
| 9 | `al-gekoppeld` | tab 1 open, tab 2 koppelt, tab 1 verstuurt | 409 | Niet gereed (nog niet bevestigd) + foutmelding |
| 10 | `gekoppeld-niet-bevestigd` | gekoppeld, Hub bevestigt (`services/self`) nog niet | 200 | Niet gereed |
| 11 | `gekoppeld-melding` | `/?melding=gekoppeld` na bevestiging | 200 | Alles gezond + info |
| 12 | `gekoppeld` | gekoppeld en bevestigd | 200 | Alles gezond |
| 13 | `catalogus-bronnen` | bronnen via de catalogus-API: onbereikbaar, uit, nog nooit | 200 | Beperkt |
| 14 | `niet-gereed` | `serve` met onbereikbare database | 200 | Storing |
| 15 | `ingetrokken` | Hub trekt in; worker krijgt 401 op `services/self` | 200 | Niet gereed |
| 16 | `koppelstatus-onleesbaar` | foutinjectie: kapot `hub-link.json` | 200 | Storing + foutmelding |
| 17 | `koppelen-loopt` | 2e installatie met `HUB_URL` + koppeltoken, Hub onbereikbaar, lege catalogus | 200 | Niet gereed + info |
| 18 | `ontkoppeld` | Hub ontkoppelt via `POST /connector/v1/hub-link/unlink` | 200 | Niet gereed |

Elke toestand: 2 thema's × 6 breedtes = 12 meetplekken (216 in totaal), Tab-doorloop op 390 en 1440 px.

## 3. Bevindingen en reparaties

Ernst: 🔴 blokkerend · 🟡 middel · 🟢 klein. Status: **Gerepareerd** (in deze repo, met test) · **Gemeld** (buiten deze
repo of buiten "alleen uiterlijk") · **Akkoord** (bewust zo).

| # | Ernst | Bevinding | Regel | Status en reparatie |
|---|---|---|---|---|
| 1 | 🔴 | Elke echte koppelpoging via het formulier gaf 403 `sessie_verlopen`. Oorzaak: `Referrer-Policy: no-referrer` laat de browser `Origin: null` sturen bij een formulier-POST; de Origin-controle weigert `null`. Alleen zichtbaar met een echte browser. | contract §3.7 (koppelformulier), WCAG 3.3.1 (de melding gaf een verkeerde oorzaak) | **Gerepareerd:** `Referrer-Policy: same-origin` (nog steeds geen referrer naar andere sites); `Origin: null` blijft geweigerd. Test `test_referrer_policy_keeps_the_browser_origin_and_null_origin_stays_refused`; de live review (CI) verstuurt het formulier voortaan in Chromium. Functionele reparatie buiten "alleen uiterlijk", zie ADR-0011 §3. |
| 2 | 🟡 | "Bron ingebouwd (bundled)": Engelse API-waarde op de pagina. | Peil §11.1 | **Gerepareerd:** "Ingebouwde catalogus"; andere bronnen "Bron <naam> (Git-repository)" / "(HTTP-adres)". |
| 3 | 🟡 | "Apps in de catalogus — Gezond — 1 app": groen las als "beschikbaar", niet als "gemeten en goed". | Peil §4.1 regel 1 | **Gerepareerd:** "Catalogusgegevens — Gezond — Gelezen uit de eigen database: 2 apps in de catalogus; 1 manifestversie geweigerd door de validator." (wat gemeten is staat erbij; database weg = Onbekend, geen 0; leeg = gate "Nog leeg"). |
| 4 | 🟡 | Na het formulier: "De Hub weigerde: de koppelcode of het token is onbekend…" — in het formulier is geen token gebruikt; geen vervolgstap. | Peil §11.2 (fout = wat + waarom + wat je kunt doen) | **Gerepareerd:** eigen teksten voor de uitkomst van de Hub na het formulier (`HUB_POGING`), bv. "De Hub weigerde de koppelcode: verlopen, al gebruikt of onbekend. Maak in de Hub een nieuwe koppelcode en probeer het opnieuw." |
| 5 | 🟢 | Opgeslagen fout op `GET /` zonder vervolgstap. | Peil §11.2 | **Gerepareerd:** vervolgstap per code (`VERVOLG`), bv. "Maak in de Hub een nieuwe koppelcode of een nieuw koppeltoken." |
| 6 | 🟢 | Onleesbare koppelstatus: "maak in de Hub een nieuw enrollment-token". | Peil §11.1 (NL-termen) | **Gerepareerd:** "Het koppelbestand in /appdata/secrets is onleesbaar. Daarom vertrouwt deze dienst niets van de Hub en voert hij niets uit. Herstel: maak in de Hub een nieuw koppeltoken en zet het in het formulier van de Custom App." Ook "Koppelen loopt" noemt nu "het koppeltoken uit het formulier van de Custom App". De variabelenaam `HUB_ENROLLMENT_TOKEN` staat bewust niet in de melding (zie #17); hij staat in CUSTOM_APP.md. |
| 7 | 🟢 | Ingetrokken/ontkoppeld: "Deels onbekend — koppeling ingetrokken door de Hub." Er is niets onbekend: het is een besluit van de Hub. | Peil §1 (eerlijk), §11 | **Gerepareerd:** "De dienst weigert elke opdracht: koppeling ingetrokken door de Hub." Pil blijft grijs "Niet gereed" (geen gemeten falen, dus geen rood). |
| 8 | 🟢 | Veldfout Hub-adres: vier regels vet op 320 px, met "loopback- of link-local-adres". | Peil §11 (rustig, feitelijk) | **Gerepareerd:** "Geen geldig Hub-adres. Vul het in als http(s)://<NAS-adres>:<poort>, zonder pad. Een adres van dit apparaat zelf (zoals localhost) werkt niet." |
| 9 | 🟡 | Onleesbare koppelstatus: blok "Koppeling met de Hub" staat **leeg** (alleen de kop). Het sjabloon kent precies vier koppeltoestanden. | Peil §4.3 (onbekend altijd met reden) | **Gemeld** (sjabloon, §7). Melding bovenaan en de controle "Koppeling met de Hub: Storing — Onleesbaar" geven de reden wél. |
| 10 | 🟢 | "Niet gekoppeld"/"Ingetrokken" in de lijst met controles krijgen `circle-help`; COMPONENTEN §4 koppelt "Niet gekoppeld" aan `link-2-off`. Het koppelblok gebruikt wel `link-2-off`. | COMPONENTEN §4, Peil §7 | **Gemeld** (sjabloon: `ELK:checks` gebruikt altijd `#i-<toon>`). Vorm, woord en grijze stippel kloppen. |
| 11 | 🟢 | Paden in meldingen (`/appdata/secrets/hub-link.json`) zijn gewone tekst: geen mono en geen `<wbr>`; op 320 px breekt de regel na "hub-", wat op een afbreekstreepje lijkt. | Peil §5 (paden mono, `<wbr>` na `/`) | **Gemeld** (sjabloon: `{{melding_tekst}}` is altijd platte tekst). Hier opgelost met tekst: de melding noemt alleen de map ("Het koppelbestand in /appdata/secrets"); het volledige pad staat in de controle "Koppeling met de Hub", waar het op 320 px op één regel past. |
| 12 | 🟢 | Een feit (aantal) kan in het dienstblok alleen met een gezondheidspil; er is geen neutrale pil met icoon voor de rijen. | Peil §4.1 regel 1 | **Gemeld** (sjabloon). Hier opgelost met tekst (#3). |
| 13 | 🟢 | Terwijl een poging met het koppeltoken loopt (`koppelen-loopt`) staat het formulier er nog; versturen geeft 409 "Er loopt al een koppelpoging". | contract §3.7 | **Gemeld** (functioneel; buiten "alleen uiterlijk"). De info-melding zegt "Laad de pagina over een minuut opnieuw". |
| 14 | 🟢 | De UI-tests maten geen contrast, randen, focusring-contrast, statusvorm of woordenlijst, en geen tekst die binnen de pagina buiten zijn kaart steekt. | ONTWERPTAAL §12 | **Gerepareerd:** `tests/ui/meting.ts` (gedeeld door de CI-spec en de live review). |
| 17 | 🟡 | Een lange naam zonder afbreekpunt in meldingstekst (`(HUB_ENROLLMENT_TOKEN)`, 22 tekens) stak op 320 px buiten de melding zodra het systeemlettertype breder is. Gevonden door de nieuwe meting "tekst buiten zijn kaart" in CI-run 42672438260 (ubuntu-24.04; lokaal met Noto Sans paste het nog). Lokaal nagebootst met DejaVu Sans: dezelfde fout. | Peil §5 (codes breken, nooit buiten beeld), WCAG 1.4.10 | **Gerepareerd:** geen variabelenamen of paden in meldingstekst; extra controle met een breed lettertype (DejaVu Sans) op 320 px in beide specs, zodat dit niet meer van het lettertype van de testmachine afhangt. Sjabloonpunt: zie §7.3. |
| 15 | — | Rijen in het blok Catalogus hebben geen tijdkolom, rijen in Status wel; de pillen staan daardoor niet in één lijn. | prototype `statuspagina-gekoppeld.html` | **Akkoord:** gelijk aan het prototype (dienstblok zonder tijd). |
| 16 | — | Tijden met "UTC" (prototype: "11:56"). | ADR-0009 §10 | **Akkoord:** de dienst kent de tijdzone van de lezer niet; eerlijk boven kort. |

Wat goed is (blijft zo): status eerst met één groot totaalsignaal; "Gezond" zonder vlak, afwijkingen met vlak en 2 px
rand; onbekend altijd grijs met stippelrand en reden; "Gekoppeld" neutraal; foutmelding met vaste code en selecteerbare
trace-ID (gelijk aan `X-Trace-Id`); ingevulde waarden nooit teruggetoond; skiplink springt naar het eerste foute veld;
geen JavaScript en geen extern verzoek (CSP `default-src 'none'`).

**Vergelijking met de prototypes** (`prototypes/statuspagina.html`, `statuspagina-gekoppeld.html` en de 16
afdrukken in `shots/`): zelfde opbouw (kop → melding → Status → Koppeling met de Hub → Koppelen → dienstblok → voet),
zelfde klassen en byte-gelijke CSS, zelfde pillen en meldingen in licht en donker. Bewuste verschillen: tijden met
"UTC" (#16), een extra controle "Databaseschema" (dezelfde meting als `/readyz`), geen label "Voorbeeld" (dit is de
echte dienst), en het dienstblok heet "Catalogus". Bij het prototype ontbreekt een toestand met een onleesbare
koppelstatus; daar ontstaat het lege blok (#9).

**Vormkeuze (dataviz):** het blok Catalogus toont een handvol aantallen en per bron een stand. Volgens de vormregel
(één huidige waarde → geen grafiek; > 7 klassen → tabel) en ONTWERPTAAL §10 is een grafiek hier niet passend; de pagina
heeft bovendien geen JavaScript. Geen grafiek, dus geen kleurkeuze voor reeksen.

## 4. Gemeten (draaiende dienst, na de reparaties)

<!-- BEGIN:metingen -->
Uit `tests/ui/review_live.py` (18 toestanden × 6 breedtes × 2 thema's = 216 meetplekken, Chromium, draaiende dienst).
Alle eisen gehaald in alle 18 toestanden (Playwright-uitkomst per toestand: 0 = geslaagd). Contrast is gemeten in de
gerenderde pagina (samengestelde achtergrond), niet uit de tokens gelezen; de waarden liggen ruim boven de AA-eis en
de tekstparen halen ook de Peil-eis van 7:1.

| Meting | Licht | Donker |
|---|---|---|
| Meetplekken (toestand × breedte) | 108 | 108 |
| axe-core-overtredingen (WCAG 2.0/2.1/2.2 A+AA + best-practice) | 0 | 0 |
| Tekstelementen gemeten | 5926 | 5926 |
| Laagste tekstcontrast (eis ≥ 4,5:1) | 8.34:1 — /healthz (proces leeft) (#123da3 op #eef1f5) | 9.32:1 — Koppelen met de Hub (#06101f op #8db6ff) |
| Laagste rand van een component (eis ≥ 3:1) | 4.86:1 — code.selecteer (#5b6575 op #fbe4e4) | 5.37:1 — code.selecteer (#8e99a8 op #132440) |
| Laagste icoon (eis ≥ 3:1) | 5.71:1 — svg in div.melding.melding-fout | 7.58:1 — svg in div.melding.melding-info |
| Laagste focusring tegen zijn omgeving (eis ≥ 3:1, 3 px + halo) | 7.79:1 | 8.04:1 |
| Tab-stops gecontroleerd (390 en 1440 px) | 200 | 200 |
| Kleinste tekst (eis ≥ 14 px) | 14 px | 14 px |
| Grootste horizontale overloop (eis ≤ 0) | 0 px | 0 px |
| Tekst buiten zijn kaart / doelen < 44 px | 0 / 0 | 0 / 0 |
| Statuspillen gecontroleerd / fouten in de statusvorm | 882 / 0 | 882 / 0 |
| Woorden buiten de Peil-woordenlijst | 0 | 0 |
| Reflow met een breed lettertype (DejaVu Sans, 320 px): overloop / tekst buiten kaart | — | 0 / 0 (18 toestanden) |

| Toestand | HTTP | Titel (tabblad) |
|---|---|---|
| `niet-gekoppeld` | 200 | Appstore en fork — niet gereed, niet gekoppeld |
| `koppelen-invoerfouten` | 422 | Appstore en fork — koppelen is niet gestart · niet gereed, niet gekoppeld |
| `vingerafdruk-onjuist` | 400 | Appstore en fork — koppelen is mislukt · niet gereed, niet gekoppeld |
| `hub-onbereikbaar` | 502 | Appstore en fork — koppelen is mislukt · niet gereed, niet gekoppeld |
| `koppelen-mislukt` | 400 | Appstore en fork — koppelen is mislukt · niet gereed, niet gekoppeld |
| `laatste-poging-mislukt` | 200 | Appstore en fork — laatste koppelpoging is mislukt · niet gereed, niet gekoppeld |
| `te-veel-pogingen` | 429 | Appstore en fork — te veel pogingen · niet gereed, niet gekoppeld |
| `sessie-verlopen` | 403 | Appstore en fork — het formulier is verlopen · niet gereed, niet gekoppeld |
| `al-gekoppeld` | 409 | Appstore en fork — koppelen kan hier niet meer · niet gereed, gekoppeld |
| `gekoppeld-niet-bevestigd` | 200 | Appstore en fork — niet gereed, gekoppeld |
| `gekoppeld-melding` | 200 | Appstore en fork — alles gezond, gekoppeld |
| `gekoppeld` | 200 | Appstore en fork — alles gezond, gekoppeld |
| `catalogus-bronnen` | 200 | Appstore en fork — beperkt, gekoppeld |
| `niet-gereed` | 200 | Appstore en fork — storing, gekoppeld |
| `ingetrokken` | 200 | Appstore en fork — niet gereed, ingetrokken |
| `koppelstatus-onleesbaar` | 200 | Appstore en fork — storing, koppelstatus onleesbaar |
| `koppelen-loopt` | 200 | Appstore en fork — niet gereed, niet gekoppeld |
| `ontkoppeld` | 200 | Appstore en fork — niet gereed, ontkoppeld |

De afdrukken van `koppelen-loopt` en `koppelstatus-onleesbaar` zijn na reparatie #17 opnieuw gemaakt; de andere
toestanden veranderden daarbij niet (alleen tijden en trace-ID's zouden verschillen).

**Controle van de controles (mutatietest):** in een kopie van `niet-gekoppeld` zijn zes fouten aangebracht (gedempte
hulptekst, focusring in de vlakkleur, lichte veldrand, groene pil voor "Niet gekoppeld", het woord "bundled", een
onbreekbaar woord in een kaart). `statuspagina.spec.ts` faalt op alle zes (tekstcontrast, ring < 3:1 per Tab-stop,
rand < 3:1, statusvorm, woordenlijst, tekst buiten zijn kaart en overloop) en daarnaast op axe. De live review faalde
vóór reparatie #1 op elke formulier-POST (403 in plaats van 400/409/422/429/502).

**Vóór de reparaties** (zelfde metingen over de vastgelegde antwoorden van commit `66b7c47`, 11 toestanden): contrast,
randen, ring, doelen, overloop en statusvorm al in orde (laagste tekst 8,34:1, laagste rand 4,86:1, laagste ring
7,79:1); alleen de woordenlijst faalde ("bundled" in elke toestand, "enrollment-token" bij de onleesbare koppelstatus).
De blokkerende fout #1 was daar niet te zien, omdat die antwoorden zonder echte browser-POST zijn gemaakt.
<!-- END:metingen -->

## 5. Toegankelijkheid (WCAG 2.2 AA + Peil-eisen)

| Eis | Uitkomst |
|---|---|
| 1.1.1 / A-01 iconen | Elk icoon `aria-hidden` naast een woord; geen afbeeldingen. |
| 1.3.1 / A-02 structuur | Eén h1, h2 per blok, geen kopsprongen; `dl` voor feiten; `ul` met `aria-label="Controles"`; labels boven velden met `aria-describedby` voor hulp en fout. |
| 1.4.1 / A-04 niet alleen kleur | Elke pil: icoon dat bij de toon hoort + woord + vorm (stippel = onbekend, hoekig = gate). Automatisch gecontroleerd per pil. |
| 1.4.3 / 1.4.11 contrast | Zie §4: tekst, randen, iconen en focusring gemeten in de DOM. |
| 1.4.10 / A-06 reflow | Geen horizontale overloop en geen tekst buiten zijn kaart op 320 px; trace-ID en adressen breken. |
| 2.1.1 / 2.4.3 toetsenbord | Alle links, velden en de knop bereikbaar met Tab in leesvolgorde; eerste Tab = skiplink ("Naar inhoud" of "Naar het veld … (fout)"). |
| 2.4.7 / 2.4.11 focus | Ring 3 px + halo op elke Tab-stop, in beeld, niet bedekt (er zijn geen vaste balken). |
| 2.5.8 / Peil 44 px | Elk bedienbaar element ≥ 44 × 44 px; alleen inline links in lopende tekst ("Hub openen", "Naar het veld …") vallen onder de uitzondering. |
| 3.3.1 / 3.3.3 fouten | Melding bovenaan (`role=alert`) met link naar het veld, veldfout bij het veld (`aria-invalid`, `aria-describedby`), vaste code + trace-ID. |
| 4.1.2 naam/rol/waarde | axe-core 0 overtredingen (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`, `best-practice`). |
| Forced colors / reduced motion | Sjabloon zet `border-color: CanvasText` voor pil/melding/blok; geen animatie. Niet apart gemeten in deze review. |
| Echte schermlezer en telefoon | **NIET GEVALIDEERD** (hardware). |

## 6. Microcopy voor en na

| Plek | Voor | Na |
|---|---|---|
| Bron in blok Catalogus | Bron ingebouwd (bundled) | Ingebouwde catalogus · Bron community (HTTP-adres) · Bron eigen (Git-repository) |
| Aantallen | Apps in de catalogus — 1 app; geen manifestversies geweigerd. | Catalogusgegevens — Gelezen uit de eigen database: 2 apps in de catalogus; 1 manifestversie geweigerd door de validator. |
| Totaal bij ingetrokken | Deels onbekend — koppeling ingetrokken door de Hub. | De dienst weigert elke opdracht: koppeling ingetrokken door de Hub. |
| Hub weigert (formulier) | De Hub weigerde: de koppelcode of het token is onbekend, verlopen of al gebruikt. | De Hub weigerde de koppelcode: verlopen, al gebruikt of onbekend. Maak in de Hub een nieuwe koppelcode en probeer het opnieuw. |
| Verkeerde vingerafdruk (formulier) | De sleutelset-vingerafdruk van de Hub klopt niet; er is niets opgeslagen. | De vingerafdruk past niet bij de Hub op dit adres. Er is niets opgeslagen. Controleer het adres en neem de vingerafdruk over die de Hub naast de koppelcode toont. |
| Hub onbereikbaar (formulier) | De Hub was niet bereikbaar (netwerkfout, time-out of storing bij de Hub). | Op dit adres gaf geen Hub antwoord. Controleer het adres en of de Hub draait, en probeer het opnieuw. |
| Opgeslagen fout | … Tijdstip: 9 nov 2026, 17:53 UTC. | … Tijdstip: 9 nov 2026, 17:53 UTC. Maak in de Hub een nieuwe koppelcode of een nieuw koppeltoken. |
| Veldfout Hub-adres | Vul het adres in als http(s)://<NAS-adres>:<poort>, zonder pad. Geen localhost, geen loopback- of link-local-adres. | Geen geldig Hub-adres. Vul het in als http(s)://<NAS-adres>:<poort>, zonder pad. Een adres van dit apparaat zelf (zoals localhost) werkt niet. |
| Onleesbare koppelstatus | Het bestand /appdata/secrets/hub-link.json is onleesbaar; de dienst vertrouwt niets van de Hub. Herstel: maak in de Hub een nieuw enrollment-token en zet het in het formulier van de Custom App (HUB_ENROLLMENT_TOKEN). | Het koppelbestand in /appdata/secrets is onleesbaar. Daarom vertrouwt deze dienst niets van de Hub en voert hij niets uit. Herstel: maak in de Hub een nieuw koppeltoken en zet het in het formulier van de Custom App. |
| Koppelen loopt | Er loopt een koppelpoging via het token uit het formulier van de Custom App. | Er loopt een koppelpoging via het koppeltoken uit het formulier van de Custom App. |
| Volgorde bronnen | op naam (de ingebouwde catalogus tussen de andere bronnen) | ingebouwde catalogus eerst, daarna op naam |

De opgeslagen meldingen van `hublink_store.ERROR_MESSAGES` (witte lijst §3.5, ook in de API) zijn niet gewijzigd.

## 7. Overdracht aan de eigenaar van het sjabloon (ai-server-manager, Peil)

Niet lokaal omzeild: de kopie in `design/ontwerptaal/` blijft byte-gelijk aan `172ee87` (O-14, `tools/peil_kopie.py`).

1. **Blok voor een onleesbare koppelstatus** (`koppelstatus_onbekend`): pil Onbekend/Storing met `circle-help` en een
   vaste zin ("De koppelstatus is onleesbaar; deze dienst vertrouwt niets van de Hub. Herstel gaat via een nieuw
   koppeltoken."). Nu is het blok "Koppeling met de Hub" leeg (#9). Eerder ook gemeld door de bouwer (ADR-0009 §9).
2. **Icoon per rij** in `ELK:checks`/`ELK:dienst_blok` (bv. `{{icoon}}` naast `{{toon}}`), zodat "Niet gekoppeld",
   "Ingetrokken" en "Ontkoppeld" `link-2-off` krijgen zoals COMPONENTEN §4 vraagt (#10), en een **neutrale pil** voor
   feiten in het dienstblok (#12).
3. **Mono-plek in meldingen** (bv. `{{melding_pad}}` als `<code>` met `<wbr>` na elke `/`), voor paden,
   bestandsnamen en variabelenamen (#11, #17). Platte tekst in `.melding` heeft geen `overflow-wrap`; een lange naam
   zonder afbreekpunt steekt op 320 px met een breed lettertype buiten de melding.
4. **Origin en Referrer-Policy** in de beschrijving van het sjabloon vastleggen: wie de statuspagina met
   `Referrer-Policy: no-referrer` serveert en `Origin` streng controleert, weigert elke echte formulier-POST (#1). In
   ai-server-manager eist de executor (`apps/executor/src/executor/app.py`) `Origin` = eigen adres en zet hij
   `no-referrer`; dat lijkt hetzelfde probleem (alleen gelezen, niet getest). home-control-hub en
   zoeken-en-persoonlijke-ai accepteren `Origin: null` (de laatste met `Sec-Fetch-Site`).

## 8. Niet gedaan / NIET GEVALIDEERD

- Echte schermlezer (NVDA, TalkBack) en een echte Android-telefoon in fel licht (ONTWERPTAAL §12, 08_UI): niet
  beschikbaar in deze omgeving.
- Een echte Hub en een op de NAS geïnstalleerde Custom App: alle toestanden komen van de Hub-simulator.
- Forced colors en 200 %-zoom zijn niet apart gemeten (de breedtes 320–1440 dekken de reflow).

## 9. Opnieuw uitvoeren

```bash
cd tests/ui && npm ci && cd ../..
PATH=/usr/lib/postgresql/16/bin:$PATH .venv/bin/python tests/ui/review_live.py \
    --afdrukken docs/screenshots/peil/statuspagina --meting /tmp/peil-review
# CI-variant over vastgelegde antwoorden van de echte app:
make test-ui
```
