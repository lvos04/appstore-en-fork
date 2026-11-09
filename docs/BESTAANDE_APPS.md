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
   - `unknown` — storing, verouderde of onvolledige meting: **niets** gewijzigd;
   - `capability_unavailable` — niet gekoppeld, of de Hub geeft zijn inventaris niet aan diensten
     (`hub_inventaris_niet_voor_diensten`, zo is het nu bij de Hub) of kent de route niet.
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

## Wat nooit gebeurt

- Geen schrijvende aanroep naar de Hub of de NAS vanuit de import, adoptie, controlelijst, het plan of het besluit.
- Geen versie, tag, digest, poort, dataset of eigenaar verzinnen: alleen gemeten waarden of het register.
- Geen data verwijderen: dat is geen stap en geen besluitcode (`app.uninstall_data` is R3 en wordt niet aangeboden).
- De Hub of de AI (dienst-JWT) kan geen eigenaar bevestigen, geen check afvinken en geen besluit vastleggen.

## Validatie

Sim-getest (Hub-simulator, PostgreSQL, echte processen) en één lokale interop-controle tegen de echte Hub-code met zijn
eigen fake TrueNAS. **NIET GEVALIDEERD (echte NAS):** de inventaris van de echte NAS, een echte Hub-installatie en de
gebruikerstest per app (APP-01…APP-20) zijn hier niet mogelijk.
