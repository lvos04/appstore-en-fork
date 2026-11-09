# ADR-0011 — Ontwerpreview van de statuspagina tegen Peil 1.1.0 (contract §13, achteraf)

- **Status:** aangenomen · 2026-11-09 · Sim-getest (echte processen `serve` + `worker`, PostgreSQL 16, Hub-simulator
  over echte HTTP, Chromium). NIET GEVALIDEERD: echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel licht,
  echte Hub en echte NAS.
- **Context:** contract 1.2 §13 (elke UI-bouwstap krijgt achteraf een ontwerpreview door de design-agent), ONTWERPTAAL
  §4, §5, §11, §12 en §13.2, COMPONENTEN §4 en §25 (C-55), ADR-0009 (statuspagina in Peil). Bevindingen en reparaties:
  [`docs/ontwerp/PEIL_REVIEW.md`](../ontwerp/PEIL_REVIEW.md).

## Besluit

1. **Review tegen de draaiende dienst, niet alleen tegen vastgelegde HTML.** `tests/ui/review_live.py` start de dienst
   zoals in de image (migrate, `serve`, `worker`, eigen `/appdata` en database) en de Hub-simulator, en brengt hem in
   18 toestanden. Chromium haalt de pagina zelf op of verstuurt het koppelformulier zelf (echte cookie, echte
   `Origin`, echte redirect). `tests/ui/statuspagina.live.ts` meet daarna hetzelfde document in licht en donker op
   320/360/390/768/1024/1440 px, zonder herladen (een POST wordt nooit opnieuw verstuurd). Alleen voor een kapot
   `hub-link.json` wordt een fout geïnjecteerd.
2. **Metingen in de gerenderde DOM** (`tests/ui/meting.ts`, gedeeld met de CI-spec): axe-core 4.14 met `wcag2a`,
   `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` en `best-practice`; tekstcontrast samengesteld over alle
   achtergrondlagen (≥ 4,5:1); randen van pillen, velden, knoppen, meldingen en het trace-ID-vak plus elk icoon
   (≥ 3:1); focusring per Tab-stop (≥ 3 px, ≥ 3:1, halo, in beeld, niet bedekt); geen overloop van de pagina én geen
   tekst buiten zijn kaart; doelen ≥ 44 × 44 px (alleen inline links in lopende tekst uitgezonderd); tekst ≥ 14 px;
   statusvorm (icoon dat bij de toon hoort + woord; groen alleen bij "Gezond"/"Alles gezond"; onbekend nooit groen);
   één h1, geen kopsprongen, dubbele id's of kapotte verwijzingen; woorden buiten de Peil-woordenlijst. Daarnaast op
   320 px een tweede reflow-meting met een **breed lettertype** (DejaVu Sans): het systeemlettertype verschilt per
   apparaat en de CI-runner vond een overloop die lokaal (Noto Sans) niet optrad.
3. **`Referrer-Policy: same-origin` in plaats van `no-referrer`.** Bij `no-referrer` serialiseert een browser de
   `Origin` van een formulier-POST als `null` (Fetch, "serializing a request origin"); de Origin-controle van
   `POST /koppelen` weigerde daardoor **elke** echte koppelpoging met `sessie_verlopen` (403). De bestaande tests
   stuurden de header zelf mee en zagen dit niet. `same-origin` stuurt de eigen herkomst alleen naar de dienst zelf;
   naar de Hub-link en andere sites gaat nog steeds geen referrer. `Origin: null` blijft geweigerd (veiligste keuze:
   de controle is niet versoepeld). Dit is een functionele reparatie buiten "alleen uiterlijk"; gekozen omdat het
   formulier anders onbruikbaar is en de wijziging één header betreft.
4. **Teksten (Peil §11).** Geen Engelse bronsoort meer ("bundled" → "Ingebouwde catalogus", `git`/`http` →
   "Git-repository"/"HTTP-adres"); de regel met aantallen heet "Catalogusgegevens" en zegt wat er gemeten is
   ("Gelezen uit de eigen database: …"), zodat groen niet "beschikbaar" betekent (§4.1 regel 1); een besluit van de
   Hub (ontkoppeld, ingetrokken) heet niet "Deels onbekend" maar "De dienst weigert elke opdracht: …"; de uitkomst van
   de Hub na het formulier spreekt van de koppelcode (niet van "het token") en zegt wat je kunt doen; een opgeslagen
   fout krijgt een vervolgstap; de onleesbare koppelstatus noemt "koppeltoken" in plaats van "enrollment-token"; de
   veldfout bij het Hub-adres is korter en zonder "loopback/link-local". **Geen paden of variabelenamen in
   meldingstekst** (die is platte tekst zonder afbreekpunt): `(HUB_ENROLLMENT_TOKEN)` stak op 320 px buiten de
   melding (CI-runner; lokaal nagebootst met DejaVu Sans); de exacte namen staan in de controle en in CUSTOM_APP.md.
5. **Het sjabloon blijft byte-gelijk (O-14).** Bevindingen die alleen in het sjabloon op te lossen zijn, worden niet
   lokaal omzeild maar gemeld aan de eigenaar (ai-server-manager): leeg blok "Koppeling met de Hub" bij een onleesbare
   koppelstatus, icoon `circle-help` in plaats van `link-2-off` voor "Niet gekoppeld" in de controles, geen
   mono-opmaak/`<wbr>` voor paden in meldingen, geen neutrale pil voor feiten in het dienstblok.
6. **Schermafdrukken** komen voortaan uit de draaiende dienst: 18 toestanden × 320/360/390/768/1440 × licht/donker in
   `docs/screenshots/peil/statuspagina/` (verkleind met pngquant). De CI-spec maakt alleen nog afdrukken als
   `SCHERMAFDRUKKEN=1` en schrijft dan dezelfde namen voor de vijf verplichte toestanden.
7. **CI:** de job `statuspagina-ui` draait naast de vastgelegde toestanden ook `review_live.py` (zonder afdrukken),
   zodat een echte formulier-POST in Chromium voortaan elke run getest wordt.

## Gevolgen

- Andere statuspagina's met `Referrer-Policy: no-referrer` en een strikte Origin-controle hebben hetzelfde risico
  (gemeld als vervolgpunt; alleen gelezen, niet gewijzigd).
- De UI-tests zijn strenger (contrast, randen, ring, statusvorm, woordenlijst); een regressie in een van die punten
  laat CI falen.
