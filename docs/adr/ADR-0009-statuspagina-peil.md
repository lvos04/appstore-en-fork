# ADR-0009 — Statuspagina in de ontwerptaal Peil (AS7, O-14)

- **Status:** aangenomen · 2026-11-09 · Sim-getest (echte app + Hub-simulator, PostgreSQL, Chromium). NIET GEVALIDEERD:
  echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel licht, echte Hub en echte NAS.
- **Context:** `CONTRACT_1.2_CONFORMITEIT.md` AS7 ("statuspagina-CSS uit de ontwerptaaltokens", was geblokkeerd tot de
  ontwerptaal er was), `ONTWERPTAAL.md` §11.2, §12 en §13.2 (Peil 1.1.0, ADR-0015 van de Hub), contract 1.2 §3.5/§3.7
  en §13. Bron: `lvos04/ai-server-manager` `design/ontwerptaal/statuspagina.html` + `statuspagina.css` op commit
  `172ee87f15406edbe1a72dda30908abde7488e94` (branch `ccr-c133d7a0-kuz1cv`; nog niet op `main`).

## Besluit

1. **Versie-gepinde kopie (O-14).** `design/ontwerptaal/statuspagina.html` en `statuspagina.css` zijn byte-gelijk aan de
   bron, met één extra eerste regel: de herkomstkop
   `Herkomst: Peil 1.1.0 · sha256:<64 hex> · bron lvos04/ai-server-manager@<commit> <pad> · …`. `HERKOMST.json` pint
   versie, bronrepo, commit, bronbranch en per bestand de sha256. Bijwerken gaat alleen met
   `tools/peil_kopie.py bijwerken --bron <checkout> --commit <sha> --ref <branch>` (nooit met de hand).
2. **Drift (CI).** `tools/peil_kopie.py controleer` (offline, elke run en in `test_peil_kopie.py`): kop, inhoud,
   `HERKOMST.json` en de in het sjabloon ingebedde `<style>` ↔ `statuspagina.css` moeten kloppen.
   `vergelijk --online` (CI-stap in de job `python`): de bron op de gepinde commit moet exact gelijk zijn; op de
   bronbranch (daarna `main`) is **dezelfde versie met andere inhoud drift → fout**, een **nieuwere versie is een
   waarschuwing** (bewust bijwerken: §14 "nieuwe versie → kopieën bijwerken"). Keuze, omdat "faalt bij drift" en
   "versie-gepind" anders botsen zodra de bron een nieuwe versie uitbrengt.
3. **Waar de kopie staat tijdens het draaien.** In de broncode leest `appstore.peil` `design/ontwerptaal/`; de image
   kopieert dezelfde twee bestanden naar `appstore/ontwerptaal/` naast het pakket en controleert bij het bouwen de
   sha256 (`RUN python -c "… peil.sjabloon() … assert s.intact"`). Geen omgevingsvariabele: het sjabloonpad is niet via
   het Custom App-formulier te wijzigen. Een afwijkende sha256 tijdens het draaien geeft alleen een foutregel in de log
   (de pagina blijft bruikbaar); CI en de image-bouw falen erop.
4. **Renderer in één doorgang.** `appstore.peil.render` volgt `bouw/sjabloon.mjs` (`{{x}}` altijd HTML-escaped,
   `ALS`/`NIET`/`ELK`), maar leest een ingevulde waarde nooit opnieuw als sjabloon (de referentie doet dat wel: een
   bronnaam `{{csrf_token}}` zou daar de CSRF-waarde tonen). Voor beide Peil-prototypes is de uitvoer byte-gelijk aan
   de referentie (sha256 vastgelegd in `test_peil_kopie.py`). Toonwaarden alleen uit `gezond|beperkt|storing|onbekend|gate`
   (anders `ValueError`).
5. **Gereed en totaal.** *Gereed* = database en schema in orde (dezelfde meting als `/readyz`, `health.measure`) **én**
   gekoppeld **én** door de Hub bevestigd (`confirmed_at`). `/readyz` zelf blijft ongewijzigd (database + schema), zodat
   de TrueNAS-healthcheck niet van de koppeling afhangt. Totaal = slechtste regel (Storing > Beperkt > Onbekend >
   Gezond; een gate verslechtert niet); niet gereed ⇒ minstens Onbekend met label **Niet gereed** ("Deels onbekend —
   wacht op koppeling met de Hub") of Storing/Beperkt met "Niet gereed — …". *Alles gezond* alleen als gereed en elke
   regel gezond. De regel "Koppeling met de Hub" staat alleen in de controles als er iets ontbreekt (niet gekoppeld,
   niet bevestigd, ontkoppeld, ingetrokken, onleesbaar): verbonden is geen gezondheid, "Gekoppeld" is de neutrale pil
   in het koppelblok.
6. **Dienstblok "Catalogus".** "Apps in de catalogus": Gezond (aantal + geweigerde manifestversies), **gate** "Nog leeg"
   (geen storing), of Onbekend als de database weg is (geen 0). Per bron: Gezond (laatst gesynchroniseerd), Beperkt
   (laatste synchronisatie mislukt), Onbekend (nog nooit), gate "Uit". De foutmelding van een bron
   (`last_sync_error`) staat niet op de pagina. Meer dan 100 bronnen: één samenvattende regel met de slechtste stand.
7. **Koppelformulier (§3.7).** Veldnamen uit het sjabloon: `hub_adres`, `koppelcode`, `hub_vingerafdruk`, `csrf`
   (de oude namen `hub_url`/`vingerafdruk` vervallen; alleen dit eigen formulier gebruikt ze). Een **mislukte poging geeft
   direct de pagina terug** (geen redirect) met een passende HTTP-status (403 sessie, 409 al gekoppeld/bezig, 422 invoer,
   429 te veel pogingen met `Retry-After: 900`, 400/429/502 uitkomst van de Hub), een vaste code, de veldfout bij het
   juiste veld (`aria-invalid`, spronglink en melding-link) en de **trace-ID van dat verzoek** (ook `X-Trace-Id` en een
   logregel `koppelen via de statuspagina: <code> (bron-IP …, trace_id …)`). Alle velden worden tegelijk gecontroleerd;
   de rate-limit telt zoals voorheen vóór de controle. Ingevulde waarden worden nooit teruggetoond (het sjabloon heeft
   geen `value`); daardoor is het Hub-adres uit `HUB_URL` ook niet meer vooringevuld. Geslaagd → `303 /?melding=gekoppeld`;
   alleen die vaste waarde wordt gelezen en alleen getoond als de dienst echt gekoppeld is.
8. **Opgeslagen fouten op `GET /`.** `last_error` (witte lijst §3.5: code + vaste NL-melding + tijdstip) verschijnt als
   melding met de code en de trace-ID van **dit paginaverzoek** (met logregel `statuspagina toont melding <code>`).
   `hub-link.json` krijgt géén trace-ID erbij: het bestandsformaat en de witte lijst van §3.5 blijven zoals ze zijn.
   Een intrekking (`ingetrokken_door_hub`) staat al in het koppelblok en geeft geen tweede melding. Een lopende poging
   (`pending`) geeft de info-melding "Koppelen loopt".
9. **Onleesbaar `hub-link.json`.** Het sjabloon kent precies vier koppeltoestanden; voor "onleesbaar" (fail-closed,
   ADR-0008 §2.4) is er geen blok. Dan: geen koppelblok en geen formulier, wel de foutmelding `koppelstatus_onleesbaar`
   met het herstel en de controle "Koppeling met de Hub: Storing — Onleesbaar".
10. **Tijden** in UTC met "UTC" erbij ("9 nov 2026, 11:56 UTC"): de dienst weet niet in welke tijdzone de gebruiker zit.
11. **Headers:** `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self';
    base-uri 'none'; frame-ancestors 'none'`, `Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer`; geen
    JavaScript, geen externe bron (getest in Chromium: geen CSP-melding, geen verzoek buiten de pagina).

## Tests

- `apps/api/tests/unit/test_peil_kopie.py`: kopie gepind en intact, drift in inhoud/kop/pin faalt, lokaal tegen de
  checkout van ai-server-manager (overgeslagen zonder checkout), renderer = referentie, escaping en één doorgang.
- `apps/api/tests/unit/test_statuspagina_model.py`: nooit groen als niet gereed (7 oorzaken), precies één koppeltoestand,
  formulier alleen vóór de eerste koppeling, witte lijst (nooit het service-token), trace-ID alleen bij een code.
- `apps/api/tests/integration/test_statuspagina.py` (echte app + PostgreSQL + Hub-simulator): niet gekoppeld, koppelen
  mislukt (code + trace-ID = `X-Trace-Id`, invoer niet teruggetoond), drie invoerfouten, sessie/rate-limit, gekoppeld
  (neutrale pil, Alles gezond), ingetrokken, ontkoppeld, niet gereed (database weg → Storing), onleesbare koppelstatus.
- `tests/ui/` (Playwright 1.65 + axe-core 4.14, Chromium): `genereer_toestanden.py` legt elf toestanden vast uit de echte
  app; `statuspagina.spec.ts` toetst per toestand × licht/donker × 320/360/390/768/1024/1440 px: axe (wcag2a, wcag2aa,
  wcag21a, wcag21aa, wcag22aa, best-practice) = 0, geen horizontale overloop, doelen ≥ 44 px, tekst ≥ 14 px, één h1,
  geen dubbele id's, geen JS/CSP-melding/extern verzoek, focusring ≥ 3 px op elke Tab-stop (390 en 1440) en het eerste
  signaal. Schermafdrukken: `docs/screenshots/peil/statuspagina/` (5 toestanden × 360/768/1440 × licht/donker).

## Gevolgen

- Andere diensten (home-control-hub, jelly-leon-platform, zoeken-en-persoonlijke-ai, executor) kunnen hetzelfde patroon
  gebruiken; hier is alleen appstore-en-fork omgezet.
- Een nieuwe Peil-versie geeft een CI-waarschuwing; bijwerken = `tools/peil_kopie.py bijwerken` + tests + afdrukken.
- Open (gemeld als contractpunt): het sjabloon heeft geen blok voor een onleesbare koppelstatus en geen plek voor
  `service_token_last4` (de witte lijst staat het toe; de pagina toont het nu niet meer).
