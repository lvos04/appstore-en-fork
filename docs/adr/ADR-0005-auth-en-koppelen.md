# ADR-0005 — Authenticatie en koppelen met de Hub

- **Status:** **vervangen** door [ADR-0008](ADR-0008-contract-1-2.md) (platformcontract 1.2, 2026-11-09). Punten 1–4
  hieronder gelden niet meer: geen `capabilities` in het verzoek, opslag in `/appdata/secrets/hub-link.json` in plaats
  van de database, de Hub meldt zich met een dienst-JWT (het service-token wordt inkomend geweigerd) en sleutels zijn
  gepind (geen JWKS-cache via HTTP). Punt 5 (rollen) en 6 (statuspagina, nu strenger) blijven in aangescherpte vorm.
  Oorspronkelijk: aangenomen · 2026-11-09 · Sim-getest tegen een Hub-simulator, niet tegen de echte Hub
- **Context:** contract §3/§4 noemt enrollment, service-token, Hub-JWT via JWKS, maar niet het exacte
  verzoek/antwoord van `POST /api/v1/services/enroll`, de naam van de omgevingsvariabele voor het token, en welk token
  de Hub gebruikt als hij een dienst aanroept.
- **Besluit:**
  1. Omgevingsvariabelen `HUB_URL` en `HUB_ENROLLMENT_TOKEN` (ook `ENROLLMENT_TOKEN` geaccepteerd), gelijk aan de
     concepten in `home-control-hub`/`jelly-leon-platform`.
  2. Verzoek: `{app_id, version, base_url|null, public_key_jwk (Ed25519), capabilities, enrollment_token | pairing_code}`.
     Antwoord: `{service_token (≥ 20 tekens), jwks {keys}, service_id?, policy_public_key | policy_public_jwk?}`.
     4xx → niet opnieuw proberen met hetzelfde token; netwerk/5xx → max 3 pogingen met backoff + jitter; automatische
     pogingen alleen binnen 60 min.
  3. Het service-token wordt AES-256-GCM-versleuteld (AAD `hub_link/service_token`) opgeslagen; voor inkomende verificatie
     alleen de SHA-256 (constante-tijdvergelijking). **Aangenomen:** de Hub gebruikt hetzelfde token als hij deze dienst
     aanroept. Een 401 van de Hub-inbox → status `revoked`, statuspagina toont het koppelformulier.
  4. JWT: `alg=EdDSA`, `iss=hub`, `aud=appstore-en-fork`, `exp-iat ≤ 120 s` (+30 s klokspeling); JWKS-cache 300 s,
     verversen bij onbekende `kid` (max 1× per 10 s); kan de JWKS niet ververst worden dan hoogstens 900 s oud, daarna
     alles weigeren (`jwks_unavailable`, fail-closed). Niet gekoppeld → `hub_not_linked`.
  5. Rollen: `kind < viewer < operator < admin`. Lezen = viewer, valideren = viewer, voorbeeld/synchroniseren = operator,
     bronnen toevoegen/wijzigen = admin **met** gebruikers-JWT (een dienst-token telt als operator, nooit als admin).
  6. Koppelcode via de statuspagina: Hub-adres + code, CSRF-cookie (SameSite=Strict), max 5 pogingen per 15 min per IP.
- **Gevolgen:** zodra de Hub zijn enroll-API vastlegt, moet dit ADR (en `hublink.py`) daarop worden gecontroleerd.
