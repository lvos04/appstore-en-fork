# ADR-0013 — Platformcontract 1.3 en Peil 1.1.1 toepassen

- **Status:** aangenomen · 2026-11-10 · aangevuld door ADR-0014 (review: healthcheck zonder proxy, verplaatsen van
  een onleesbaar koppelbestand van een andere eigenaar, `confirmed_at` buiten de witte lijst, routetest; de Hub heeft
  C1.3-ASM-07/08 inmiddels gebouwd — gelezen, niet tegen een draaiende Hub getest)
- **Bron:** `ai-server-manager:docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`, bron-commit `02b119c`) en
  `ai-server-manager:docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md` (hoofdstuk 3, C1.3-ASF-01…14); Peil 1.1.1
  (`ai-server-manager:docs/platform-v2/ONTWERPTAAL.md` §13.2/§14/§16, bron-commit `0a7143b`).
- **Validatie:** Unit-getest en Sim-getest (Hub-simulator volgens 1.3, PostgreSQL 16, echte processen, Chromium) en
  de alles-in-één image in Docker. **NIET GEVALIDEERD (echte NAS):** geen echte TrueNAS, geen echte Hub-installatie,
  geen echte Hub 1.3 (die heeft C1.3-ASM-07/08 nog niet), geen echte schermlezer of telefoon.

## Context

Contract 1.3 verandert voor deze dienst vooral het koppelen (statuspagina, koppelbestand, adressen, JWT), de API-regels
(foutcatalogus, `Idempotency-Key`, paginering over een momentopname), de connector (health, versheid), de events en —
als eigenaar van de manifeststandaard — de validator. De conformiteitslijst markeert C1.3-ASF-01…13 als **NU** en
C1.3-ASF-14 als **P2**. Hieronder alleen de keuzes die het contract open laat; wat het contract letterlijk zegt, staat
in de code en de tests.

## Besluiten

1. **Kopieën en drift (C1.3-ALG-01).** `PLATFORM_CONTRACTEN.md` en `CONTRACT_1.3_CONFORMITEIT.md` zijn byte-gelijke
   kopieën op bron-commit `02b119c`, gepind in `docs/platform-v2/CONTRACT_HERKOMST.json`. Drie controles: de unit-test
   (sha256 hard in de test én gelijk aan de pin), `tools/contract_kopie.py controleer` (offline) en
   `tools/contract_kopie.py vergelijk --online` in CI (de bron op de gepinde commit heeft exact die sha256; een nieuwere
   bron is een waarschuwing). `CONTRACT_1.2_CONFORMITEIT.md` blijft als historie staan.
2. **Peil 1.1.1.** Nieuwe gepinde kopie (`tools/peil_kopie.py bijwerken`, herkomstkop + `HERKOMST.json`, pinregel E08:
   afwijking of zelfde versie met andere inhoud = fout, nieuwere versie = waarschuwing). De renderer leest het sjabloon
   in een boom en vult in één doorgang in, byte-gelijk met `bouw/sjabloon.py` op de vier prototypes van 1.1.1. Keuzes:
   het aantal apps in de catalogus is een `feit` (geen gezondheid); koppelrijen gebruiken `niet-gekoppeld`; een
   bestaande toestand (opgeslagen `last_error`) heeft `melding_rol` leeg en geen trace-ID; de fout van dít verzoek
   `alert` met trace-ID; de melding na een geslaagde koppeling `status`. Bij een onleesbaar koppelbestand en een
   lopende poging zegt het koppelblok het al; daar staat geen dubbele melding.
3. **Statuspagina (C1.3-ASF-02/03).** Volgorde van een formulier-POST: (1) herkomst — `Host`-beperking, exacte
   `Origin`, `Sec-Fetch-Site` — geweigerd = 403 zonder te tellen (`host_niet_toegestaan` of `herkomst_onjuist`); (2)
   poging tellen (5/IP, 20/dienst per 15 min); (3) formuliertoken (`sessie_verlopen`); (4) toestand
   (`koppelstatus_onleesbaar`, `al_gekoppeld`, `koppelen_bezig`, allemaal 409); (5) invoer als geheel; (6) de Hub.
   Een POST met een verkeerd formuliertoken telt dus wél als poging (de herkomst was in orde) — de strengste lezing van
   "elke formulier-POST die de herkomstcontrole doorstaat". De melding na een geslaagde koppeling hoort bij een sleutel
   in het geheugen van het API-proces, gebonden aan de sha256 van het formuliercookie en 15 minuten geldig
   (`/?poging=<sleutel>`); een andere browser, een verzonnen sleutel of een herstart geeft geen melding. Op `GET /` via
   een niet toegestane `Host` zegt de pagina vooraf dat koppelen via het IP-adres moet (K1).
4. **Koppelbestand (C1.3-ASF-04).** Onleesbaar = geen geldige JSON, geen object, `schema_version` zonder major 1,
   ontbrekende `schema_version`/`state`/`app_id`, een andere `app_id`, `gekoppeld` zonder adres, `service_id`, token,
   pins of tijden, of een eigenaar ≠ dit proces / rechten ruimer dan 0600. Er wordt geen `first_linked_at` meer verzonnen
   (1.2-lezing); de eigenschap `unreadable` (bestand onleesbaar, of vervangen en sindsdien nooit gekoppeld) sluit het
   formulier. Het origineel wordt bewaard met een harde link (exacte bytes, ook als dit proces het niet mag lezen) vóór
   het atomair vervangen; dat gebeurt alleen bij de eerste schrijfactie van een poging met een env-token (ook bij de
   vaste codes `hub_adres_ongeldig`/`vingerafdruk_ongeldig`, die het token onafgehandeld laten). In de witte lijst is
   `state` dan `null` (onbekend; de lijst is een maximum) met `last_error.code = koppelstatus_onleesbaar`; `/readyz`
   blijft 200 en meldt dat onder `hub_link`. Extra velden blijven bij herschrijven staan.
5. **Adressen (C1.3-ASF-06).** Een host die op een getal eindigt (zoals een browser hem als IPv4 leest) moet exact
   `a.b.c.d` zijn. De verboden bereiken gelden ook na DNS-resolutie bij **elke** verbinding: `appstore.netguard` lost de
   naam zelf op en verbindt met het gecontroleerde IP (TLS houdt de naam). Dat geldt voor alle uitgaande
   HTTP-verbindingen van de dienst (de Hub én HTTP-catalogusbronnen; strenger dan nodig, maar een bron hoort nooit op
   loopback of link-local te staan; loopback alleen met de ontwikkelvlag). Git-bronnen gaan via dulwich en houden hun
   bestaande controle vooraf (`check_remote_url`, alleen https); dat is geen Hub-adres en valt buiten §3.2.
   `base_url` alleen als hij geldig is, anders `null`.
6. **JWT en foutcatalogus (C1.3-ASF-05, §5).** `aud` als lijst → `invalid_token`. FastAPI leest een JSON-body vóór de
   dependencies; bij onleesbare JSON doet de foutafhandeling daarom eerst de aanmelding van de route (gemarkeerde
   dependency) en geeft zonder geldige aanmelding 401 (RV9). Foutcodes gelijk aan de Hub: `validation_error` +
   `details.fouten[]` (ook voor een ongeldige cursor of limit, met `details.reason`), `idempotency_key_reused` (422),
   `idempotency_in_progress` (409), header `Idempotent-Replay`, 404 altijd `not_found` (eigen situatie in
   `details.reason`). De HTML-antwoorden van de statuspagina houden hun eigen statuscodes (bv. 502 als de Hub op het
   ingevulde adres niet antwoordt); dat is geen API-route.
7. **Events (C1.3-ASF-07).** `service.hub_link.linked` met payload precies `{via}` (de auditregel houdt de details);
   `catalog.source_sync.failed/succeeded` en `catalog.import.failed/succeeded` op een stabiel onderwerp (de bron, de
   inventaris); `catalog.manifest.rejected` blijft een probleem. `payload.ernst` alleen waar de bron het weet (middel bij
   een mislukte synchronisatie of import, laag bij een geweigerd manifest). Elk event wordt bij opslaan en vóór
   verzending tegen de grenzen van de inbox gecontroleerd; een ongeldig event gaat meteen naar dead-letter
   (`envelope_ongeldig: …`) en nooit naar de Hub. Eén release beide namen sturen mag; wij sturen alleen de nieuwe.
8. **Connector-health (C1.3-ASF-10).** De worker schrijft hooguit elke 10 s `/appdata/state/worker-hartslag.json`; de
   check `worker` heeft die tijd als `observed_at` en is na 120 s `unknown`/`meting_verouderd` (`limits.max_age_s.worker`
   = 120). `jobs` is `degraded` als een wachtende taak > 15 min niet is opgepakt of een taak > 30 min loopt. `detail` is
   voortaan een object met `tekst` (het contract noemt `detail.wachtrij` e.d.; één type voor alle checks).
9. **Statische `Idempotency-Key`-test (C1.3-ASF-11).** Uitgezonderd met reden: de formulier-POST van de statuspagina
   (§5, RV10), `propose`/`apply`/`rollback` (501) en de rekenroutes `POST /catalog/v1/validate`, `/preview` en
   `/import/preview`: ze wijzigen niets behalve een bewijsrecord, net als `…/evaluate` en `…/verify` in §5. Omdat ze
   niet letterlijk in de lijst van §5 staan, is dit een contractmelding voor de volgende versie.
10. **Inventaris-import (C1.3-ASF-09).** De Hub 1.3 heeft de scope en de momentopname nog niet (C1.3-ASM-07/08); gebouwd
    tegen de simulator: 403 `scope_ontbreekt` → `capability_unavailable` (`scope_inventaris_ontbreekt`, nooit een
    intrekking); `snapshot_id` moet op elke pagina gelijk zijn; 409 `snapshot_changed` → opnieuw beginnen, hooguit
    twee keer; `max_age` = min(900 s, `inventaris.max_age_s` van de Hub). Bij 429/503 nooit eerder dan `Retry-After`;
    langer dan 30 s wachten doet de import niet in één ronde (`unknown`/`hub_vraagt_wachten`, volgende ronde).
11. **Emby (C1.3-ASF-12).** Het register markeert Emby met `platform_wijzigt_nooit`; het decommission-plan heeft dan
    geen enkele `action_id` (alles handmatig door de gebruiker) en extra gates voor de HARDE EIS (vóór/na-rapport
    kijkgeschiedenis), het exportarchief (B12) en de controlelijst van de eigenaar, alle `unknown`. Herkenning blijft
    exact (catalogusappnaam of image-repository).
12. **Validator en schema (C1.3-ASF-08).** `backup.db_export` blijft een object; de nieuwe waarde staat in
    `db_export.method: stopped_snapshot` (het contract schrijft `backup.db_export: stopped_snapshot`). `secret: true` op
    een parameter; met `source: generated` geeft gebruik in formulier of values de **acceptabele** waarschuwing
    `secret_in_form`, anders blijft het een fout (formulier) of een niet-acceptabele waarschuwing (catalogus-values).
    Nieuwe eisen die bestaande manifesten raken zijn tijdens de overgang waarschuwingen (`install_reason_ontbreekt`,
    `restrisico_nnp_ontbreekt`, `healthcheck_monitor_missing`), fout in `--strict` (`strict_valid` in het rapport).
    `kind: checklist` heeft een eigen schema, afgeleid van het formaat dat jelly-leon-platform al gebruikte (zelfde
    veldnamen, plus `kind` en `install: {method: none}`; `installatie: geen` mag nog tijdens de overgang), met
    `jl-manifest checklist`; de manifestvalidator weigert hem met `checklist_not_installable`.
13. **Image (C1.3-ASF-01).** De healthcheck staat in exec-vorm als `python -I -S healthcheck.py` (geen shell, geen
    Python-omgevingsvariabelen, geen site-packages vóór de wissel), wisselt als eerste handeling (bij root eerst de
    bounding set leeg) en heeft de controlevlag `--controle`. De bewaker stopt met een Nederlandse melding als de
    bounding set na het legen niet 0 is (K3). De image-E2E kijkt alleen naar de procesboom van PID 1 en controleert de
    healthcheck apart, ook bij "Custom User 568".
14. **Niet in deze ronde:** C1.3-ASF-14 (vault-verwijzing voor geheime waarden van catalogusapps) is P2; tot dan weigert
    de vertaling een geheime waarde eerlijk (`parameter_value_not_allowed`) en staat er nooit een geheim in een payload.
    Uitgewerkt in P2: [ADR-0015](ADR-0015-vault-verwijzing-geheimen-in-de-vertaling.md) (alleen een vault-verwijzing).

## Gevolgen

- Een Hub of executor die `detail` van een connector-check als tekst leest, moet `detail.tekst` lezen (de Hub doet dat
  nu niet; gemeten in `ai-server-manager:apps/api/src/hub/monitoring/sources.py`).
- Andere repos met manifesten (jelly-leon-platform en de eigen manifesten) kunnen nu naar 1.3: hun losse
  uitzonderingenlijst vervalt (`accepted_findings`), hun controlelijsten krijgen `kind: checklist` en `install`.
- De Hub moet voor C1.3-ASM-07/08 de vorm van de momentopname vastleggen (`snapshot_id` op elke pagina, 409
  `snapshot_changed`, `inventaris.max_age_s`); deze dienst volgt die vorm al tegen de simulator.
