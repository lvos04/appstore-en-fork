# ADR-0010 — Bestaande apps importeren en beslisregister (B08-002, APP-01…APP-20)

- **Status:** aangenomen · 2026-11-09 · Sim-getest (Hub-simulator, PostgreSQL, echte processen) plus één lokale
  interop-controle tegen de echte Hub-code (ai-server-manager `efb7f3b`, met zijn eigen fake TrueNAS). NIET
  GEVALIDEERD: echte NAS-inventaris, echte Hub-installatie en een gebruikerstest.
- **Context:** Excel B08-002 ("Discover installed workloads via official API, match ID by namespace and container
  labels, user confirmation of owner"; acceptatie "Nog niet ondersteunde app blijft unmanaged zonder dataverlies"),
  APP-01…APP-20 (A01…A20 en `APP_REGISTER.md`), I26 (decommission-procedure), contract 1.2 §2.3, §4.4, §6, §8, §9 en
  03_RISICO_AUTONOMIE. De appstore heeft **geen** TrueNAS-sleutel; de Hub meet de NAS (B01-002, appcatalogus).

## Besluit

1. **Bron = de appcatalogus van de Hub.** `GET {HUB_URL}/api/v1/apps?alleen_aanwezig=false&limit=200` (gepagineerd)
   en `GET /api/v1/apps/{id}` (detail), met `Authorization: Bearer <service-token>` (§4.4). Geen redirects, geen
   proxy, antwoordgrootte begrensd (lijst 4 MiB, detail 1 MiB, ≤ 10 pagina's, ≤ 1000 apps), hooguit 3 pogingen bij
   5xx/429/netwerk (§5). Alleen een **volledige, verse** meting (`inventaris.status = ok`, niet verouderd, ≤ 15 min
   oud, niet in de toekomst) wordt verwerkt; anders `unknown` en verandert er **niets** (ook geen "verdwenen").
   "Volledig" (aangescherpt in de P1-review): de Hub pagineert met een offset over zijn laatste meting, dus elke pagina
   moet dezelfde `inventaris` en hetzelfde `totaal` melden en het aantal gelezen apps moet gelijk zijn aan `totaal`;
   anders `inventaris_gewijzigd_tijdens_lezen` of `inventaris_onvolledig` (`unknown`).
2. **Uitkomsten zijn nooit gesimuleerd succes.** Niet gekoppeld, 403 (de Hub geeft dit niet aan diensten) of 404
   (oude Hub) → `capability_unavailable`; storing, 401, redirect, ongeldig of onvolledig antwoord → `unknown`. Elke
   ronde legt bewijs (`hub_import`) en audit vast. Een event (`catalog.import.completed` of
   `catalog.import.unavailable`) gaat alleen mee als de uitkomst (`status` + `reason_code`) anders is dan bij de vorige
   ronde of als het register veranderde (aangescherpt in de P1-review: de worker leest elke 15 minuten en stuurde de
   Hub anders elke ronde hetzelfde bericht, ook bij een blijvende 403); het antwoord noemt `event_type` of `null`. Een 401 leidt pas tot `ingetrokken` na 401 op `services/self` (§3.8, bestaande code).
3. **Register `existing_app`, idempotent.** Identiteit = TrueNAS-appnaam (`runtime_id`). Alleen gemeten velden
   (image/tag/**effectieve** digest uit de Hub, `digest_bron`, readback, catalogusapp/trein/versie, poorten, opslag,
   datasets, runtime-staat) met een hash; zelfde meting ⇒ geen wijziging (`row_version` gelijk). Nieuwe tag/digest ⇒
   historie (max. 20). Verdwenen ⇒ `present = false` + `gone_since`, **nooit verwijderen**. Een advisory lock
   voorkomt twee imports tegelijk (409 `import_bezig`).
4. **Herkennen = voorstellen, nooit gokken.** Basis alleen (a) de exacte naam van de TrueNAS-catalogusapp bij
   installatiewijze `catalog_app`, of (b) een exacte image-repository (genormaliseerd: Docker Hub-voorvoegsels en
   LinuxServer-spiegels). **Nooit** op appnaam en **nooit** op de herkenning door de Hub (`app_id` van de Hub wordt
   alleen ter informatie getoond). Meerdere passende apps → `onbekend` met kandidaten. Bronnen van de regels (in
   `bestaande_apps/register.yaml`): TrueNAS-catalogus `truenas/apps@69dfbda` (stable/community), images uit contract
   §2/§2.3 en de release-workflows van MediaGuard/MSO/my_house, en upstream-images waarvan het bestaan op 2026-11-09
   met `docker buildx imagetools inspect` is gecontroleerd (niet-bestaande kandidaten zijn weggelaten). Snapotter,
   Teeltboek en Uren krijgen **geen** regel (A06: "geen afleiding uit appnaam"; ook de catalogusapp `snapotter` kan
   een andere app zijn) en zijn alleen handmatig te koppelen.
5. **Statussen.** `onbekend` (niet of niet eenduidig herkend; blijft ongemoeid = "unmanaged") → `kandidaat`
   (herkend) → `adopted` (UI: **Geïmporteerd**) pas na **bevestiging van de eigenaar door een beheerder**
   (`POST …/adopt`, gebruikers-JWT `admin`, `If-Match` + `Idempotency-Key`; een dienst-JWT — dus de Hub of de AI — kan
   dit nooit). Een `onbekend` app aan een bekende app koppelen kan alleen met expliciet `handmatig: true` (basis
   `gebruiker`). `release` maakt de adoptie ongedaan (alleen het register). Een latere meting die niet meer past bij
   een adoptie wijzigt de status niet, maar zet `adoption_warning_nl`. **Eigenaar** komt uit het register
   (`soort`, `repo`, bron) of het manifest en wordt bij adoptie aangevuld met wie/wanneer bevestigde; nooit uit de
   meting. **Laatste gebruik** wordt niet gemeten → `unknown`.
6. **Adoptiemanifest = eigen documentsoort, nooit installeerbaar.** `GET …/manifest` geeft voor een geadopteerde app
   een document met `kind: adoption`, `installable: false` en de §9-velden die passen (`install.method` volgens wat
   de app **nu** is: `custom_app` of `catalog_app`; `image` of `catalog`; `network.ports`; opslag; afhankelijkheden;
   eigenaar; meetbron). Schema: `schemas/adoption-manifest-v1.schema.json` (kopie in het pakket, byte-gelijk). Een app
   via *Install via YAML* (`custom_compose`) of met onbekende installatiewijze krijgt **geen** manifest (409). De
   validator van manifest v1 weigert een adoptiemanifest met `adoption_manifest_not_installable`, dus ook
   `POST /catalog/v1/preview` (geen herinstallatie). Keuze: een volledig §9-manifest zou velden vragen die niet te
   meten zijn (healthcheck, RPO/RTO, risico) — die invullen zou gokken zijn.
7. **Beslisregister per bekende app** (`decision_record`, APP-01…APP-20): voorgenomen besluit en controle uit
   `APP_REGISTER.md`; controlelijst = de vijf verplichte inventarispunten, A0…A4, het aandachtspunt ("Waarom"), de
   acceptatie en "Gereed wanneer" — letterlijk uit de A-documenten (unit-test vergelijkt). A3 (proefstop) staat
   standaard op n.v.t. als het voorgenomen besluit "behouden" is. Afvinken vraagt **bewijs** (omschrijving ≥ 10
   tekens, optioneel verwijzing en evidence-ID's) door een **gebruiker** (operator+); n.v.t. vraagt een reden. De
   gemeten gegevens staan ernaast als **hint** en vinken nooit iets af. Een **besluit** (`behouden`, `uitschakelen`,
   `container_verwijderen`) legt alleen een beheerder vast, en pas als elke check behalve "gereed" is afgehandeld;
   voor alles behalve `behouden` is een decommission-plan verplicht. Data verwijderen is **geen** besluitcode.
8. **Decommission-plan = voorbereiding (I26 stap 6 en §4).** Stappen met de executor-`action_id`'s uit contract §8
   en hun minimale risicoklasse: `zfs.snapshot.create` (R1, datasets uit de meting), `app.stop` als proefstop (72 u;
   R1/R2 — de policy bepaalt), `app.start` (terugdraaien), `app.uninstall` met `remove_ix_volumes=false` (R2, alleen
   na het besluit). Gates: eigenaar bevestigd, voorbereiding afgevinkt, meting vers, **back-up-gate altijd
   `unknown`** (die meet de Hub/executor vlak vóór uitvoering; nooit groen vanuit de appstore), voorgenomen besluit,
   en bij Readarr/FlareSolverr "7 dagen zonder verzoeken" (`unknown`). `uitgevoerd: false`; `data_verwijderen:
   {aangeboden: false}` (`app.uninstall_data` is R3 en wordt niet aangeboden). Het plan wordt als bewijs
   (`decommission_plan`) opgeslagen met een `plan_hash`; de appstore stuurt **niets** naar de Hub of de NAS.
9. **Worker:** het beslisregister wordt bij de start (idempotent) aangemaakt; de import draait periodiek
   (`APPSTORE_IMPORT_INTERVAL_S`, standaard 900 s, 0 = uit; niet in het formulier) maar alleen als de koppeling
   bevestigd is. Connector: item-types `existing_app` en `decision_record`.

## Contractpunten (gemeld)

- **De Hub geeft zijn appcatalogus nog niet aan diensten.** `GET /api/v1/apps` en `/api/v1/apps/{id}` vereisen in
  `ai-server-manager` (`efb7f3b`) een gebruikerssessie (`Viewer`); met een service-token geeft de echte Hub-code 403
  (lokaal gemeten met de interop-controle). Contract §4.4 noemt "Hub-connector-lezen" maar geen inventaris-route voor
  diensten. Gekozen: de appstore gebruikt deze routes met het service-token en meldt eerlijk
  `capability_unavailable`/`hub_inventaris_niet_voor_diensten` tot de Hub ze (alleen lezen, `ViewerOrService`) voor
  de dienst `appstore-en-fork` openstelt. Voorstel aan de Hub-eigenaar: deze twee GET-routes ook voor ingeschreven
  diensten (of alleen voor `appstore-en-fork`) toestaan, zonder geheimen (de detailroute geeft nu geen geheimen).
- **Beeldnaam MediaGuard.** Contract §2.3 en de release-workflow van media-guard-ultimate noemen
  `ghcr.io/lvos04/mediaguard-core` (alles-in-één `…-aio`) en `mediaguard-{web,encode,sonarr-fork,radarr-fork}`; de
  fake TrueNAS van de Hub gebruikt `ghcr.io/lvos04/mediaguard`. De appstore herkent alleen de gepubliceerde namen;
  met de fake van de Hub blijft MediaGuard daarom `onbekend` (bewust: niet gokken).
- **Data-eigenaar ≠ eigenaar-repo.** Het pakket noemt "eigenaar" zonder definitie. Gekozen: `owner.soort`
  (`derde`, `bestaand`, `eigen_los`, `eigen`) + `repo` (alleen bij eigen repos) uit het register, aangevuld met wie
  bevestigde; de functionele rol staat in het voorgenomen besluit (bv. "bibliotheek- en AV1-eigenaar").

## Gevolgen

- Een niet-ondersteunde app blijft `onbekend`, zonder enige actie en zonder dataverlies (acceptatie B08-002).
- Tot de Hub zijn appcatalogus voor diensten openstelt, werkt de import op een echte installatie niet
  (`capability_unavailable`); alle andere delen (register, beslisregister, plannen) wel.
- De schermen (appoverzicht met *Geïmporteerd*, beslisregister) horen in de Hub-UI (ai-server-manager); de appstore
  levert alleen de API (`/catalog/v1/existing-apps…`, `/catalog/v1/decisions…`).
