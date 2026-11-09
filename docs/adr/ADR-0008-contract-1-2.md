# ADR-0008 — Platformcontract 1.2 toepassen in appstore-en-fork

- **Status:** aangenomen · 2026-11-09 · Sim-getest (Hub-simulator volgens contract 1.2 §3.3) en Docker-getest; NIET
  GEVALIDEERD tegen de echte Hub (die 1.2 nog niet bouwde: H1–H6) en niet op een echte NAS.
- **Context:** `docs/platform-v2/PLATFORM_CONTRACTEN.md` 1.2 en `CONTRACT_1.2_CONFORMITEIT.md` §3 (AS1–AS8, M1–M6) plus
  de gemeenschappelijke punten A4–A7. Deze ADR legt vast hoe dat hier gebouwd is en welke keuzes het contract open liet.

## 1. Manifeststandaard (M1–M6, eerst gebouwd: de manifesten in andere repos volgen pas daarna)

1. Geen nieuw `manifest_version`: `network.joins_platform_network` en `custom_app.ports` zijn uit `required`; aanwezig
   en onschadelijk → `deprecated_field` (waarschuwing), anders de bestaande fouten (`platform_network_not_supported`,
   `ports_inconsistent`). `--strict` maakt van elke waarschuwing een fout, dus ook van `deprecated_field`. De
   overgangsfixture `legacy-1-1-custom.manifest.yaml` bewaakt dat 1.1-manifesten geldig blijven.
2. GPU: nieuw parametertype `gpu`. Een `gpu`-parameter moet `source: wizard`, `decision: D08`, `required: false` hebben
   (`gpu_param_invalid`). Code `gpu_inconsistent` (eiste gelijkheid) is vervangen door `gpu_exceeds_privileges`
   (alleen méér vragen dan `privileges.gpu` is fout); `intel_render` zonder D08 in `decisions_required` →
   `gpu_decision_missing`.
3. Vertaling: het doel (`Target`) kreeg twee gemeten velden: `gpu_render_node` (`measured|absent|null`, D08) en
   `available_versions` (catalogusversies uit `catalog.get_app_details`). Zonder meting nooit een aanname: GPU →
   `gpu_not_measured` (blokkade); versies → `version_availability_unknown` (waarschuwing, de executor controleert vóór
   uitvoering). **Keuze (contract liet open):** bij een Custom App blokkeert `version_unavailable` als de gemeten
   `ix-app`-versie ontbreekt óf als geen enkele geteste `ix-app`-versie (`IX_APP_VERIFIED_VERSIONS`) meer in de lijst
   staat; een nog niet geteste versie naast een geteste blijft de bestaande waarschuwing `ix_app_version_unverified`.
4. De preview geeft altijd een `digest_readback` mee zodra er een digest is (`app.image.query` → `repo_digests`, stop +
   incident bij afwijking) en de terugvaltag zonder digest, omdat niet gevalideerd is dat het formulier `@` accepteert.
5. `requirements` (appdetail) toont `gpu` (het maximum) en nieuw `gpu_request` (letterlijk of de parameter).

## 2. Koppelen met de Hub (AS1, AS2, AS6; §3.2–§3.8)

1. **Verzoek** exact §3.3: `{app_id, enrollment_token | pairing_code, version, base_url, public_key_jwk}`; `app_id` is de
   vaste constante `appstore-en-fork` (§2, R1), `public_key_jwk` alleen `{kty, crv, x, kid}` met `kid` = RFC 7638-
   thumbprint (`appstore.hubkeys.enroll_jwk`). `capabilities` en de JWK-velden `use`/`alg` zijn weg.
2. **Antwoord** volledig gecontroleerd (`hublink.validate_enroll_response`): alle velden met het juiste type, major
   `schema_version` 1, `app_id` gelijk, `service_id` een UUID, `svc_`-patroon, elke JWK geldig Ed25519 met `use: sig`,
   `alg: EdDSA` en unieke `kid`, de **zelf berekende** vingerafdruk gelijk aan het antwoord en aan
   `HUB_KEY_FINGERPRINT`/de statuspagina. Aliassen (`token`, `id`, `policy_public_jwk`) bestaan niet. Daarna
   `GET /api/v1/services/self`.
3. **Foutgedrag:** 401 → `hub_weigerde`, 404 → `enroll_endpoint_ontbreekt`, 413/422 → `hub_incompatibel` (keuze: een
   aparte code omdat de oplossing anders is dan bij 401), overige 4xx (400/403/409/410) → `hub_weigerde` — alle
   definitief. 429 → opnieuw na `Retry-After`. 3xx (nooit gevolgd), 5xx en netwerkfouten → backoff 5 s → 15 min met
   jitter, stoppen 60 min na `pending.started_at` (`token_verlopen_na_60_min`). Een env-token met verkeerd formaat
   wordt niet verstuurd (`token_ongeldig`, eigen code). Een koppelcodepoging is altijd één verzoek: bij een storing
   wist de dienst `pending` en voert de gebruiker de code opnieuw in (het geheim staat alleen in dat verzoek).
4. **Opslag** in `/appdata/secrets/hub-link.json` (`appstore.hublink_store`): 0600, atomair (`O_EXCL` → `fsync` →
   `rename` → `fsync` map) onder `flock` op `hub-link.lock`, opnieuw lezen bij een andere inode/mtime/grootte.
   Twee **aanvullende** velden (het contract noemt ze niet, de witte lijst laat ze buiten de publieke status):
   `token_rotation_requested_at` (rotatieverzoek overleeft een herstart) en `key_rotation_blocked_sequence` (één
   incident per geweigerd rotatie-statement). Een onleesbaar bestand is fail-closed: niets vertrouwd, géén
   koppelformulier (onbekend of de dienst al gekoppeld was); herstel via een nieuw env-token.
5. **Overzetting** uit de tabel `hub_link`: alleen als het bestand nog niet bestaat (`hublink.ensure_link_file`,
   uitgevoerd door de bewaker vóór API/worker en idempotent door beide processen); daarna wordt de rij verwijderd.
   Bestaat het bestand al, dan wint het bestand en wordt een door een database-herstel teruggekomen rij alleen
   verwijderd. Ontbreekt de policy-sleutel of is een JWK ongeldig, dan `ontkoppeld` + `overzetten_onvolledig`
   (opnieuw koppelen via de Hub). De tabel zelf blijft in deze versie bestaan (leeg) zodat `migrate --down` kan.
6. **Herstart halverwege** (§3.6 stap 5): het API-proces wist bij de start een `pending` koppelcode (die hoort bij het
   vorige API-proces); de worker herhaalt een `pending` env-token met dezelfde hash (toegestane herhaling) en wist een
   ander of verdwenen token met `koppelen_onderbroken`. Een koppelcodepoging ouder dan 2 min wordt ook door de worker
   als onderbroken gezien. `gekoppeld` zonder `confirmed_at` → alleen opnieuw bevestigen.
7. **Opnieuw koppelen** met een env-token waarvan de hash niet in `used_enrollment_token_sha256` staat, ook als de dienst
   gekoppeld, ontkoppeld of ingetrokken is (§3.8); mislukt het, dan blijft de bestaande koppeling ongewijzigd.
8. **Intrekken:** 401 op een aanroep met het service-token → `GET /services/self`; alleen bij opnieuw 401 → `ingetrokken`
   (token en pins weg, pins naar `previous_pins`, incident-event `service.hub_link.revoked`). 5xx/429/netwerk nooit.
9. **Rotatie service-token:** `Hub-Token-Rotation: required` op een antwoord → `token_rotation_requested_at` → de worker
   roteert in de volgende ronde (`POST /services/self/rotate`), schrijft het nieuwe token atomair en bevestigt met
   `services/self`. Een onderbroken rotatie herstelt zich doordat de Hub de header herhaalt.
10. **Ontkoppelen:** `POST /connector/v1/hub-link/unlink` (dienst-JWT of gebruiker `admin`, `Idempotency-Key`).
    `GET /catalog/v1/hub-link` is vervallen (de Hub gebruikte hem nog niet) en vervangen door
    `GET /connector/v1/hub-link` (witte lijst + `pinned_kids` + `rotation_sequence`).

## 3. Inkomende authenticatie (AS3, AS4; §4.3–§4.7)

1. Alleen Hub-JWT's (EdDSA) tegen `pins.jwt`; de oude JWKS-cache met TTL en verversen via HTTP is weg (ADR-0005 punt 4
   vervalt). Onbekende `kid` → 401 `unknown_key` + achtergrond-poll van `key-rotations` (hooguit 1× per 30 s);
   de worker pollt elke 15 min. Een `404` van `key-rotations` (Hub zonder rotatie) is "niets te doen".
2. `token_use` verplicht: `user` (rol `service` verboden) of `hub_service` (`sub: hub`, `roles: ["service"]`, eigen
   `service_id`). Principal `service:hub` heeft dezelfde rechten als voorheen het service-token (operator-niveau:
   lezen, valideren, voorbeeld, synchroniseren; nooit bronbeheer of admin), plus ontkoppelen. Rol `kind` wint altijd
   (§4.2; was hier nog niet zo — nu wel, dit is een aanscherping).
3. Verplichte claims `iss, aud, sub, iat, nbf, exp` (+30 s speling, `exp − iat ≤ 120 s`), daarna `token_use`, daarna
   `jti` (1–128 tekens). Een ontbrekend of onleesbaar token telt als `invalid_token` (de vaste lijst kent geen
   `missing_token`).
4. Replaycache: tabel `jwt_replay` (`iss`, `jti`, `expires_at` = `exp` + 30 s, methode, pad, `Idempotency-Key`),
   `INSERT … ON CONFLICT DO NOTHING` per mutatie. Exacte herhaling (zelfde `jti`, methode, pad én aanwezige
   `Idempotency-Key`) mag door naar de idempotency-laag; zonder `Idempotency-Key` is hergebruik altijd
   `token_replayed` (veiligste lezing). Database weg → 503 `replay_check_unavailable`. De worker ruimt verlopen rijen op.
5. Sleutelrotatie-statements (§4.5) worden gecontroleerd tegen de gepinde sleutel van **hetzelfde doel**, strikt
   oplopend zonder gaten; de laatste sleutel van een doel intrekken, een onbekende `kid` intrekken of een bestaande
   `kid` met andere sleutel → geweigerd, verdere statements niet toegepast, één incident per volgnummer. **Keuze:** met
   tijdelijk twee policy-sleutels telt de vingerafdruk ze net als de JWT-sleutels gesorteerd met komma's (het contract
   definieert alleen één policy-sleutel).

## 4. Statuspagina (AS5) en image (AS8)

1. Formulier alleen als `first_linked_at` leeg is (ook niet meer na `ingetrokken`), met verplichte vingerafdruk; CSRF +
   `Origin` (zonder of met afwijkende `Origin` → geweigerd); 5 pogingen/15 min per bron-IP (TCP-adres, uvicorn zonder
   proxy-headers) én 20/15 min voor de hele dienst, in het geheugen (één API-proces). Het Hub-adres volgt dezelfde
   regels als `HUB_URL` (geen pad, loopback, link-local, `0.0.0.0`/`::`; loopback alleen met
   `APPSTORE_ALLOW_LOOPBACK_HUB=1`, alleen voor ontwikkeling/tests). AS7 (CSS uit de ontwerptaaltokens) is
   **geblokkeerd**: `ONTWERPTAAL.md` en `design/ontwerptaal/` zijn nog niet geleverd (§13: zolang ze ontbreken, start
   geen UI-ombouw).
2. Geen `dumb-init` meer: de bewaker is PID 1, laat de bounding set vallen, wordt 568 en zet `PR_SET_NO_NEW_PRIVS`
   (weigert te starten als dat niet lukt), ruimt zombies op (`/proc/<pid>/stat` met PPID 1) en stelt signalen direct
   in. Rooktest/image-E2E: elk proces, ook PID 1, `Uid 568`, `CapEff 0`, `NoNewPrivs 1`.

## Gevolgen en open punten

- Werkt pas tegen de echte Hub als die H1–H4 bouwt (enroll-schema 1.2, `services/self`, `self/rotate`, dienst-JWT met
  `token_use`, `key-rotations`). Tot dan: Sim-getest tegen `apps/api/tests/fakehub.py` (volgt §3.3 strikt).
- NIET GEVALIDEERD (echte NAS): het bron-IP dat de dienst achter de Docker-portpublicatie van TrueNAS ziet (de
  rate-limit per bron-IP kan dan voor alle cliënten één adres zien — de dienstbrede grens van 20 blijft gelden).
- Een latere migratie verwijdert de lege tabel `hub_link`.
