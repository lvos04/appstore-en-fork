# Bestaande apps importeren en beslissen (B08-002, APP-01…APP-20)

Keuzes en redenen: [ADR-0010](adr/ADR-0010-bestaande-apps-import.md). De schermen horen in de Hub-UI; dit document
beschrijft de API van de appstore. Alles hieronder is **alleen een register**: de appstore installeert, herstart,
stopt of verwijdert nooit iets en wijzigt geen rechten of data. Echte uitvoering (proefstop, snapshot, verwijderen van
een container) loopt alleen via voorstel → policy → goedkeuring → executor van de Hub (R1/R2/R3).

## Hoe het werkt

1. **Inventaris lezen** — `POST /catalog/v1/existing-apps/import` (operator of de Hub met een dienst-JWT,
   `Idempotency-Key`). De appstore leest met zijn service-token `GET /api/v1/apps` + `GET /api/v1/apps/{id}` van de
   Hub. De worker doet dit ook elke 15 minuten (`APPSTORE_IMPORT_INTERVAL_S`, 0 = uit) zodra de koppeling bevestigd
   is. Uitkomst `status`:
   - `ok` — register bijgewerkt (`counts`: nieuw, bijgewerkt, ongewijzigd, verdwenen);
   - `unknown` — storing, verouderde of onvolledige meting: **niets** gewijzigd. Dat geldt ook als de pagina's van de
     lijst niet uit één meting komen (`inventaris_gewijzigd_tijdens_lezen`) of als het aantal gelezen apps afwijkt van
     het `totaal` van de Hub (`inventaris_onvolledig`): zo wordt een app nooit ten onrechte "verdwenen";
   - `capability_unavailable` — niet gekoppeld, de Hub geeft deze dienst de leesscope `inventaris.lezen` niet
     (403 `scope_ontbreekt` → `scope_inventaris_ontbreekt`; contract 1.3 §4.4, nooit een intrekking), geeft zijn
     inventaris niet aan diensten (`hub_inventaris_niet_voor_diensten`) of kent de route niet.

   Contract 1.3: de lijst wordt gelezen over één **momentopname** (`snapshot_id` op elke pagina; 409
   `snapshot_changed` → opnieuw beginnen, hooguit twee keer), `max_age` volgt de Hub (`inventaris.max_age_s`) maar is
   nooit soepeler dan 900 s, en bij 429/503 wacht de import nooit korter dan `Retry-After` (langer dan 30 s:
   `unknown`/`hub_vraagt_wachten`, de volgende ronde probeert het opnieuw).
2. **Bekijken** — `GET /catalog/v1/existing-apps` (standaard alleen aanwezige apps; `alleen_aanwezig=false` toont ook
   verdwenen apps; filter `status`, `known_app_id`) en `GET /catalog/v1/existing-apps/{id}` (meting, herkenning,
   eigenaar, gedocumenteerde en gemeten afhankelijkheden, historie).
3. **Eigenaar bevestigen** — `POST /catalog/v1/existing-apps/{id}/adopt` (beheerder, `If-Match`, `Idempotency-Key`):
   `{"app_id": "plex", "eigenaar_bevestigd": true, "toelichting_nl": "…"}`. Voor een app die niet (of anders) herkend
   is: `"handmatig": true`. Terugdraaien: `POST …/release` met `{"reden_nl": "…"}`.
4. **Adoptiemanifest** — `GET /catalog/v1/existing-apps/{id}/manifest`: `kind: adoption`, `installable: false`,
   `install.method` zoals de app nu draait. Alleen voor inventaris; de validator en de preview weigeren het
   (`adoption_manifest_not_installable`).
5. **Beslisregister** — `GET /catalog/v1/decisions` (APP-01…APP-20) en `GET /catalog/v1/decisions/{app_id}`
   (controlelijst, bewijs, gemeten hints, instanties, historie).
   - Check afvinken: `POST /catalog/v1/decisions/{app_id}/checks/{check_id}` (gebruiker operator+, `If-Match`,
     `Idempotency-Key`) met `{"status": "afgevinkt", "bewijs": {"omschrijving_nl": "…", "verwijzing": "…",
     "evidence_ids": ["…"]}}`, of `{"status": "nvt", "reden_nl": "…"}`, of `{"status": "open"}`.
   - Decommission-plan voorbereiden: `POST /catalog/v1/decisions/{app_id}/decommission-plan` (operator,
     `Idempotency-Key`) → plan met stappen, gates, `plan_hash` en `plan_id` (ook via `/connector/v1/evidence/{id}`).
   - Besluit vastleggen: `POST /catalog/v1/decisions/{app_id}/decision` (beheerder, `If-Match`, `Idempotency-Key`)
     met `{"code": "behouden" | "uitschakelen" | "container_verwijderen", "motivatie_nl": "…",
     "decommission_plan_id": "…"}`.

## Statussen

| Status | Betekenis | Wat het platform doet |
|---|---|---|
| `onbekend` | Niet of niet eenduidig herkend (op naam wordt nooit herkend) | Niets; de app blijft ongemoeid ("unmanaged") |
| `kandidaat` | Herkend aan catalogusapp of exacte image-repository | Niets; wacht op bevestiging van de eigenaar |
| `adopted` (UI: *Geïmporteerd*) | Een beheerder bevestigde de eigenaar | Register, beslisregister en adoptiemanifest; geen acties |

Een verdwenen app blijft staan met `present: false` en `gone_since`.

## Controlelijst per app

Letterlijk uit het A-document: `inventaris-1` … `inventaris-5` (verplichte inventaris), `a0-meten`,
`a1-integreren`, `a2-verbeteren`, `a3-proefstop` (n.v.t. bij "behouden"), `a4-besluit`, `aandachtspunt` ("Waarom"),
`acceptatie` (belangrijkste controle uit APP_REGISTER) en `gereed` ("Gereed wanneer": alleen na een proef op de echte
TrueNAS). Een besluit kan pas als alle checks behalve `gereed` zijn afgehandeld.

## Emby (APP-02): alleen lezen, nooit wijzigen

Emby is de bestaande mediaserver van de gebruiker met de **kijkgeschiedenis** (contract 1.3 §2.3, HARDE EIS van de
gebruiker). In de appstore is Emby een gewoon adoptie-item met `owner.soort: derde`, herkend alleen op de exacte
catalogusappnaam `emby` of de exacte image-repository (`emby/embyserver`, `lscr.io/linuxserver/emby`), nooit op de
appnaam. Het register markeert Emby met `platform_wijzigt_nooit: true`:

- het decommission-plan heeft **geen enkele** stap voor de executor (`action_id` overal `null`, status `handmatig`):
  het platform stopt, updatet of verwijdert Emby nooit en schrijft er nooit in (§2.3 regel 3);
- extra gates, allemaal `unknown` (de appstore meet ze niet): `kijkgeschiedenis_rapport` (vóór/na-rapport per
  gebruiker en akkoord op elk verschil, §2.3 regel 7), `exportarchief_b12` (blijvend JSON-exportarchief gedekt in de
  back-up, §2.3 regel 8) en `controlelijst_eigenaar` ("Emby verwijderen", B03 §10.9.2);
- Emby blijft de terugval tot de gebruiker hem zelf verwijdert; stoppen en data verwijderen blijven aparte stappen.

## Wat nooit gebeurt

- Geen schrijvende aanroep naar de Hub of de NAS vanuit de import, adoptie, controlelijst, het plan of het besluit.
- Geen versie, tag, digest, poort, dataset of eigenaar verzinnen: alleen gemeten waarden of het register.
- Geen data verwijderen: dat is geen stap en geen besluitcode (`app.uninstall_data` is R3 en wordt niet aangeboden).
- De Hub of de AI (dienst-JWT) kan geen eigenaar bevestigen, geen check afvinken en geen besluit vastleggen.

## Validatie

Sim-getest (Hub-simulator, PostgreSQL, echte processen) en één lokale interop-controle tegen de echte Hub-code met zijn
eigen fake TrueNAS. **NIET GEVALIDEERD (echte NAS):** de inventaris van de echte NAS, een echte Hub-installatie en de
gebruikerstest per app (APP-01…APP-20) zijn hier niet mogelijk.
