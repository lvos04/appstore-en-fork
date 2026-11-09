# Schermafdrukken — statuspagina in Peil 1.1.0

Gemaakt met `make schermafdrukken` (Playwright 1.65, Chromium, `tests/ui/statuspagina.spec.ts`) uit de **echte** app
(FastAPI + PostgreSQL) met de **Hub-simulator** (`apps/api/tests/fakehub.py`). Validatie: **Sim-getest** — adressen als
`http://hub.test:30400`, vingerafdrukken, trace-ID's en tijden komen uit de simulatie, niet van een echte Hub of NAS.

Bestandsnaam: `<toestand>-<licht|donker>-<360|768|1440>.png` (volledige pagina).

| Toestand | Wat je ziet |
|---|---|
| `niet-gekoppeld` | Eerste start: **Niet gereed** (deels onbekend, wacht op koppeling), koppelformulier met Hub-adres, koppelcode en vingerafdruk. |
| `koppelen-mislukt` | Antwoord op een koppelpoging die de Hub weigerde: melding met code `hub_weigerde` en selecteerbare trace-ID, veldfout bij de koppelcode; ingevulde waarden worden niet teruggetoond. |
| `gekoppeld` | Gereed en bevestigd: **Alles gezond**, neutrale pil **Gekoppeld**, Hub-adres en vingerafdruk; geen formulier, geen ontkoppelen. |
| `ingetrokken` | De Hub heeft de koppeling ingetrokken: **Niet gereed**, pil **Ingetrokken**, "opnieuw koppelen gaat via de Hub". |
| `niet-gereed` | Gekoppeld, maar de eigen database is weg: **Storing** — "Niet gereed — de database is niet bereikbaar"; aantallen onbekend in plaats van 0. |

De Playwright-test draait daarnaast ook `koppelen-invoerfouten`, `laatste-poging-mislukt`, `koppelen-loopt`,
`gekoppeld-melding`, `ontkoppeld` en `koppelstatus-onleesbaar`, en alle toestanden ook op 320, 390 en 1024 px
(axe = 0, geen overloop). Geen pixel-baselines (ONTWERPTAAL §12).

NIET GEVALIDEERD: echte schermlezer, echte Android-telefoon in fel licht, echte Hub en NAS.
