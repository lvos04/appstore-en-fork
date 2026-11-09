# ADR-0003 — Alles-in-één image en hardening ondanks het standaard Custom App-formulier

- **Status:** aangenomen · 2026-11-09
- **Context:** contract §1/§10: één image met ingebouwde PostgreSQL (unix-socket), start als root alleen voor `/appdata`,
  daarna 568, `no-new-privileges`, geen extra capabilities. Het standaard Custom App-formulier (ix-app 1.4.8) roept
  echter `clear_caps()` en `remove_security_opt("no-new-privileges")` aan: Docker-standaardcapabilities blijven en
  `no-new-privileges` kan in het formulier niet aan.
- **Besluit:** de procesbewaker (`deploy/allinone/supervisor.py`) doet als root alleen `chown` van de eigen submappen
  (`postgres`, `secrets`, `backups`), leegt daarna de capability-bounding-set (`PR_CAPBSET_DROP`), zet
  `PR_SET_NO_NEW_PRIVS` en wordt 568; alle kinderen (PostgreSQL, API, worker) erven dat (E2E controleert `CapEff=0`,
  `CapBnd=0`, `NoNewPrivs=1`, uid 568). *(Bijgewerkt voor contract 1.2 §2.2, ADR-0008:)* PID 1 is de procesbewaker
  zelf — geen `dumb-init` meer, dat als root bleef draaien —; hij ruimt zombies op, handelt SIGTERM/SIGINT af en
  draait na de eigenaarschapsstap ook als 568 zonder capabilities; de image-E2E controleert elk proces, ook PID 1.
  PostgreSQL 16 komt uit de officiële image (op digest), luistert alleen op `/tmp/appstore-pg` (0700, eigenaar 568,
  `trust` alleen lokaal, `host` geweigerd). Geheimen: `/appdata/secrets/{vault.key,identity.ed25519}` (0600, nooit
  overschreven). Vóór openstaande migraties van een bestaande database: `pg_dump -Fc` naar `/appdata/backups`; **mislukt
  die, dan wordt er niet gemigreerd** (strenger dan MSO, dat doorstart) — de oude versie blijft met intacte data bruikbaar.
- **Gevolgen:** werkt zowel zonder als met "Custom User 568". Base-images moeten periodiek op nieuwe digests worden gezet.
  Niet gevalideerd op een echte TrueNAS-host (alleen Docker met dezelfde instellingen).
