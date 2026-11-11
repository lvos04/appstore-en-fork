# ADR-0015 — Geheimen in de vertaling: alleen een verwijzing naar de vault van de Hub (C1.3-ASF-14)

- **Status:** aangenomen · 2026-11-11 · vervangt de tijdelijke weigering uit [ADR-0013](ADR-0013-contract-1-3.md)
  punt 14 · vult [ADR-0004](ADR-0004-truenas-vertaling.md) aan
- **Bron:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`), kop "3.1 Uitgangspunten" punt (a) en kop
  "9. Manifeststandaard (B08-001, eigenaar `appstore-en-fork`)"; `docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md`, rijen
  C1.3-ASF-14 en C1.3-ASM-18; de policy van de Hub (`ai-server-manager:apps/policy/src/asm_policy/params.py`,
  `VAULT_REF_RE` en `vault_refs`).
- **Validatie:** Unit-getest (vertaling, CLI) en via de API met PostgreSQL 16 (`POST /catalog/v1/preview`).
  **NIET GEVALIDEERD (echte NAS):** het invullen van de waarde door de executor en een `app.create` op een echte TrueNAS
  zijn niet gedaan; de executor van de Hub weigert een installatie met vault-verwijzingen nog met
  `501 capability_unavailable` (`ai-server-manager:apps/executor/src/executor/uitvoeren.py`).

## Context

Contract 1.3 §3.1 (a): verplichte geheime waarden van catalogusapps maakt de Hub (CSPRNG, minstens 256 bit), bewaart
hij versleuteld in zijn vault als `vault://app/<app_id>/<parameter>` en geeft de executor alleen in de `values` van
`app.create`/`app.update` mee. Ze staan nooit in een manifest, preview-diff, auditdetail, log of evidence. Tot deze
stap weigerde de vertaling elke geheime parameter (`parameter_value_not_allowed`), zodat er nooit een geheim in een
payload kon komen (ADR-0013 punt 14).

De Hub-kant (C1.3-ASM-18) verwacht de verwijzing als gewone tekstwaarde **op de plek van de waarde**: de policy van de
Hub vindt elke tekst die precies `^vault://app/[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9_.-]{1,128}$` is, en weigert elke andere
tekst die met `vault://` begint (`vault_ref_ongeldig`).

**Besluit van de gebruiker (2026-11-11, keuzemenu na de verkenning van stap S-P2-500):** vault-verwijzingen
(`vault://app/<app_id>/<parameter>`) gelden **ook voor Custom Apps** (`custom_app.env[].value_from` naar een geheim
dat de Hub maakt), niet alleen voor de `values` van een catalogusapp. Dit botst met contract 1.3 §3.1 ("Nooit geheimen
in het Custom App-formulier"): na het invullen door de executor staat het geheim als omgevingsvariabele in het formulier
van de Custom App. Die uitzondering moet in het contract komen; dat is werk voor de eigenaar van het contract
(ai-server-manager), niet voor deze repo. Deze repo bouwt volgens het besluit en meldt de botsing.

## Besluit

1. **Geheime parameter = nooit een waarde.** Een parameter met `source: generated`, `source: secret`,
   `type: secret_ref` of `secret: true` wordt in de vertaling nooit ingevuld. Op zijn plek komt alleen de tekst
   `vault://app/<app_id>/<parameter>`, met de vaste `app_id` uit het manifest (contract 1.2 correctie R1), **nooit** de
   door de gebruiker gekozen TrueNAS-appnaam (`app_name`). De executor vult de waarde in.
   **Alleen als de `app_id` vaststaat** (herstel na de review, R-VEILIG-1): de `app_id` staat in het manifest zelf, en
   een los manifest kan zich de `app_id` van een andere app geven. `translate()` maakt daarom alleen een verwijzing met
   `trusted_app_id=True` (standaard niet: dan `parameter_value_not_allowed`). Dat zet alleen:
   - `POST /catalog/v1/preview` met `app_id` of `version_id` van een **geldige** catalogusversie die bij een
     catalogus-entry met dezelfde `app_id` hoort (één bron per `app_id`: `app_id_claimed_by_other_source`);
   - `jl-manifest translate` (het eigen bestand van wie de vertaling bekijkt; de uitvoer installeert niets).
   `POST /catalog/v1/preview` met `manifest_yaml`, of met een geweigerde of losse versie, geeft geen verwijzing.
2. **Waar een verwijzing mag staan** (alleen tekstplekken):
   - in de `values` van een catalogusapp, op elke plek waar het manifest `{param: <geheim>}` zet;
   - in een omgevingsvariabele van een Custom App (`custom_app.env[].value_from`), en dan **alleen** naar een geheim dat
     de Hub maakt (`source: generated` + `secret: true`, besluit hierboven). De validator weigert elk ander geheim in
     het formulier al (`secret_in_form`, fout); de vertaling controleert het opnieuw.
   Een geheim op een plek die een getypte waarde vraagt (hostpoort, hostpad, tijdzone, GPU) of van een ander type dan
   `string` of `secret_ref` krijgt geen verwijzing en blijft geweigerd (`parameter_value_not_allowed`). Ook geweigerd
   (herstel na de review): een verwijzing onder een sleutel met `.`, `[` of `]` (het pad zou dubbelzinnig zijn,
   R-VEILIG-2) en een verwijzing onder een sleutel `image`, `images`, `TZ`, `notes` of `labels` (hoofdletterongevoelig;
   daar is de waarde openbaar of komt zij in een log, R-VEILIG-3).
3. **Opgegeven waarden.** Voor een geheime parameter mag hooguit precies de eigen verwijzing worden opgegeven (dat
   geeft dezelfde vertaling). Elke andere waarde wordt geweigerd met `parameter_value_not_allowed`: een geheime waarde,
   een verwijzing naar een **andere app** (`vault://app/<andere app>/…`), naar een andere parameter, de oude vorm met
   twee delen, hoofdletters, spaties of een regeleinde, een getal, een lijst of een object. Dat geldt ook voor een
   geheime parameter die het manifest nergens gebruikt (pad `parameters.<naam>`, R-TECH-2). De melding noemt de
   parameter en de reden, **nooit de opgegeven waarde**; die staat ook niet in het antwoord, het bewijs
   (`blocking_codes`) of de log.
4. **Markering in de uitvoer.** `TranslationResult` krijgt het veld `vault_refs`: een lijst met per verwijzing precies
   `{path, parameter, ref}`:
   - `path`: de plek in de payload, bijvoorbeeld `values.voorbeeld.db_password` (catalogusapp) of
     `values.envs[2].value` (Custom App). Eenduidig: elk deel tussen de punten is een sleutel zonder `.`, `[` of `]`
     en `[i]` is een lijstpositie (een verwijzing onder een andere sleutel wordt geweigerd, punt 2);
   - `parameter`: de naam van de geheime parameter in het manifest;
   - `ref`: de verwijzing die daar staat (`vault://app/<app_id>/<parameter>`).
   Zonder payload is de lijst leeg. Het veld staat in `jl-manifest translate` en in `translation` van
   `POST /catalog/v1/preview`. De `preview_hash` gaat over de payload mét verwijzingen: een goedkeuring bindt de
   verwijzing, nooit de waarde. `ready_for_install` verandert niet van betekenis: de vertaling is volledig; of het
   invullen al kan, beslissen de Hub en de executor.
5. **Alleen de vertaling maakt een verwijzing.** Elke tekst in de payload die `vault://` bevat (ook met hoofdletters
   of midden in een tekst, en ook een sleutel) en niet precies een gemarkeerde verwijzing op zijn gemarkeerde plek is,
   geeft de fout `vault_ref_not_allowed` (met het pad, zonder de waarde; bij een sleutel het pad van het object) en dan
   is er geen payload. De vergelijking gaat over de echte plek (sleutels en lijstposities), niet over het pad als tekst:
   een sleutel `a.b` is een andere plek dan `a` → `b`. Zo kan een letterlijke verwijzing in
   de `values` of in `custom_app.env[].value` van een manifest de Hub niet het geheim van een andere app laten invullen.
   Een gewone parameter (bijvoorbeeld uit de wizard) met `vault://` in de waarde geeft al eerder
   `parameter_value_invalid`.
6. **Geen schemawijziging.** `manifest-v1.schema.json`, `model.py` en `rules.py` blijven gelijk; alleen de vertaling
   (`translate.py`), de route `POST /catalog/v1/preview` (`trusted_app_id`, punt 1), `jl-manifest translate` en de
   codelijst (`findings.py`, nieuwe code `vault_ref_not_allowed`) veranderen. De kopieën van de validator in andere
   repos hoeven daarom niet mee; de repos die een commit van deze repo pinnen houden het oude gedrag (weigeren) tot zij
   hun pin bewust verzetten. **Uitzondering (herstel na de review, R-TECH-1):** de CI van jelly-leon-platform pint
   niet, maar haalt `jl-manifest` van de werkbranch van deze repo. Daar verwacht
   `tests/repo/test_installeerbare_manifesten.py::test_authentik_translation_is_blocked_until_the_executor_fills_secrets`
   nog de oude melding met `capability_unavailable`; de codes en paden van de weigering zijn gelijk gebleven, alleen de
   tekst van de melding is anders. Die test wordt bijgewerkt in jelly-leon-platform (stap S-P2-395). Deze stap is pas
   klaar als de CI van jelly-leon-platform op zijn laatste commit weer groen is.
7. **Vaste weigeringen blijven.** `jl-manifest` weigert compose en de vaste EX47-gevallen zoals voorheen; de fixtures
   `compose-forbidden.manifest.yaml` en `ex47-privileged-hostmount.manifest.yaml` zijn niet gewijzigd.

## Gevolgen

- Een catalogusapp met verplichte geheime waarden (bijvoorbeeld Authentik) en een Custom App met een geheim dat de Hub
  maakt, krijgen nu een volledige vertaling in plaats van een weigering. Installeren lukt pas als de Hub de waarde
  maakt en bewaart (C1.3-ASM-18) en de executor haar invult; tot dan weigert de executor eerlijk met
  `capability_unavailable`.
- Voor de Hub en de executor (ai-server-manager): de verwijzing staat op de plek van de waarde en `vault_refs` noemt elke
  plek; de vorm is gelijk aan `VAULT_REF_RE` van de policy. **`vault_refs` is geen autorisatie.** De `app_id` in de
  verwijzing komt uit het manifest; deze repo weigert alleen een verwijzing die niet bij die `app_id` hoort, en maakt
  er alleen een bij een vaste `app_id` (punt 1). De Hub vult een verwijzing alleen in als de `app_id` die van de
  catalogusversie (`version_id`, uit een vertrouwde bron) én van het doel van de actie is, en alleen waar de waarde
  precies de verwachte verwijzing op een gemarkeerde plek is.
- Contract: §3.1 heeft een uitzondering nodig voor geheimen die de Hub maakt in een omgevingsvariabele van een Custom
  App (besluit van de gebruiker van 2026-11-11). Dat is een contractwijziging in ai-server-manager (P-CONTRACT); tot die
  er is, wijkt deze repo op dat punt bewust af, op besluit van de gebruiker.

## Wat dit niet bewijst

Niets is op een echte TrueNAS uitgevoerd. Hoe het formulier van de Custom App een door de executor ingevulde
omgevingsvariabele toont of opslaat, en of TrueNAS zo'n waarde ergens logt, is NIET GEVALIDEERD (echte NAS).
De lijst met openbare plekken (punt 2) is vast en kort: een catalogusveld met een andere naam dat ook openbaar is (een
adres of een hostnaam, bijvoorbeeld) herkent de vertaling niet; daar beslist de maker van het catalogusmanifest.
