# ADR-0006 — Catalogusbronnen en ondertekende provenance

- **Status:** aangenomen · 2026-11-09
- **Context:** B08: "Geen willekeurige niet-beoordeelde appstore met root toegang"; B08-001 "signed manifest provenance".
- **Besluit:**
  1. Bronnen: `bundled` (in de image, bij elke start gesynchroniseerd), `git` (https, branch/tag-ref, optioneel
     vastgepinde commit die moet kloppen; dulwich, geen shell), `http` (index-JSON met sha256 per manifest, ETag).
     Alleen https, geen inloggegevens in de URL, geen loopback/link-local-adressen (behalve met
     `APPSTORE_ALLOW_INSECURE_SOURCES` in ontwikkeling/tests); max 500 manifesten van ≤ 256 KiB.
  2. Uitgever-handtekening: Ed25519 over canonieke JSON, bestand `<manifest>.sig`; per bron een vertrouwde sleutel en
     optioneel `require_signature`. Ontbrekend/ongeldig waar vereist → manifest geweigerd.
  3. Elke opgeslagen manifestversie krijgt een **provenance-record** (hash, bron, ref/commit, pad, tijd, schema- en
     validatorversie, uitkomst, status uitgever-handtekening), ondertekend met de eigen identiteit; `GET
     /catalog/v1/versions/{id}` controleert die handtekening (`provenance_verified`).
  4. Een `app_id` hoort bij de bron die hem eerst bracht; een andere bron kan hem niet overschrijven
     (`app_id_claimed_by_other_source`). Geweigerde manifesten worden wél bewaard (versiegeschiedenis + bewijs).
  5. Een ophaalfout wijzigt de catalogus niet (bron `failed`, eerlijk zichtbaar in health en statuspagina).
- **Gevolgen:** verwijderde bestanden in een bron trekken een entry (nog) niet in; dat volgt met B08-002/B08-004.
