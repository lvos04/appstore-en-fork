# Schermafdrukken — statuspagina in Peil 1.1.1 (contract 1.3)

Gemaakt tijdens de ontwerpreview ([`docs/ontwerp/PEIL_REVIEW.md`](../../../ontwerp/PEIL_REVIEW.md)) met
`tests/ui/review_live.py`: de **draaiende** dienst (`python -m appstore serve` + `worker`, eigen `/appdata`, PostgreSQL
16) en de **Hub-simulator** over echte HTTP (`apps/api/tests/fakehub.py`). Chromium (Playwright 1.65) haalt de pagina
zelf op of verstuurt het koppelformulier zelf. Validatie: **Sim-getest** — adressen (`http://127.0.0.1:<poort>`),
vingerafdrukken, trace-ID's en tijden komen uit de simulatie, niet van een echte Hub of NAS. De bestanden zijn
verkleind met `pngquant` (alleen kleurenpalet; de metingen gaan over de pagina zelf, niet over deze afdrukken).

Bestandsnaam: `<toestand>-<licht|donker>-<320|360|390|768|1440>.png` (volledige pagina).

| Toestand | Hoe bereikt | Wat je ziet |
|---|---|---|
| `niet-gekoppeld` | eerste start zonder `HUB_URL` | **Niet gereed** (deels onbekend, wacht op koppeling), koppelformulier |
| `koppelen-invoerfouten` | formulier met een adres met pad, code "123", vingerafdruk "ABCD" | HTTP 422, drie veldfouten, skiplink en melding naar het eerste foute veld, niets naar de Hub |
| `vingerafdruk-onjuist` | geldige code, vingerafdruk van een andere Hub | HTTP 400, `hub_vingerafdruk_onjuist`, veldfout bij de vingerafdruk |
| `hub-onbereikbaar` | adres waar niets luistert | HTTP 502, `hub_onbereikbaar`, veldfout bij het adres |
| `koppelen-mislukt` | onbekende koppelcode | HTTP 400, `hub_weigerde`, veldfout bij de koppelcode |
| `laatste-poging-mislukt` | pagina opnieuw laden na de mislukte poging | opgeslagen fout met tijdstip, vervolgstap en trace-ID van dit paginaverzoek |
| `te-veel-pogingen` | zesde poging binnen 15 minuten | HTTP 429 met `Retry-After: 900` |
| `sessie-verlopen` | formulier zonder cookie verstuurd (langer dan een uur open), na het legen van de pogingenteller (contract 1.3: een POST met geldige herkomst telt eerst als poging) | HTTP 403 "Het formulier is verlopen" |
| `al-gekoppeld` | tab 1 staat open, in tab 2 wordt gekoppeld, daarna verstuurt tab 1 | HTTP 409 "Koppelen kan hier niet meer", melding als fout van dít verzoek (`role="alert"`), geen formulier meer |
| `gekoppeld-niet-bevestigd` | gekoppeld terwijl de Hub `services/self` nog niet bevestigt | **Niet gereed**, controle "Niet bevestigd" |
| `gekoppeld-melding` | tab 2 van `al-gekoppeld` direct na de geslaagde koppeling (`/?poging=<sleutel>` van die browser; een losse query-parameter geeft geen melding meer, contract 1.3 §3.7) | info-melding "Gekoppeld met de Hub" met `role="status"`; de Hub bevestigt nog niet, dus **Niet gereed** |
| `gekoppeld` | gekoppeld en bevestigd | **Alles gezond**, neutrale pil **Gekoppeld**, geen formulier |
| `catalogus-bronnen` | drie bronnen via de catalogus-API: onbereikbaar, uitgezet, nog nooit | **Beperkt** (gereed; één bron mislukt), gate "Uit", Onbekend "Nog nooit gesynchroniseerd" |
| `niet-gereed` | `serve` herstart met een onbereikbare database | **Storing** — "Niet gereed — de database is niet bereikbaar"; aantallen onbekend, niet 0 |
| `ingetrokken` | de Hub trekt de koppeling in; de worker krijgt 401 op `services/self` | **Niet gereed**, pil **Ingetrokken**, "De dienst weigert elke opdracht" |
| `koppelstatus-onleesbaar` | foutinjectie: `hub-link.json` kapot | **Storing**, koppelblok **Onleesbaar** (Peil 1.1.1) met de herstelweg via een nieuw koppeltoken, geen formulier, reden-code `koppelstatus_onleesbaar` in de controle; geen dubbele melding |
| `koppelen-loopt` | tweede installatie met `HUB_URL` + koppeltoken terwijl de Hub onbereikbaar is; lege ingebouwde catalogus | blok **Koppelen bezig** (Peil 1.1.1, formulier en stappen weg), melding zonder live-rol met de laatste fout, gate "Nog leeg" |
| `ontkoppeld` | de Hub ontkoppelt via `POST /connector/v1/hub-link/unlink` | **Niet gereed**, pil **Ontkoppeld**, "opnieuw koppelen gaat via de Hub" |

Daarnaast (zonder afdrukken) `herkomst-geweigerd`: het echte formulier vanuit een verkeerde herkomst in Chromium —
`Origin: null` (data:-document), een vreemde site (`Sec-Fetch-Site: cross-site`), dezelfde host op een andere poort
(`same-site`), een publieke naam die naar de dienst wijst (DNS-rebinding, `host_niet_toegestaan`) en zonder cookie —
telkens HTTP 403 met de vaste code; de dienst blijft ongekoppeld (contract 1.3 §3.7 punt 6).

Elke toestand is gemeten op 320, 360, 390, 768, 1024 en 1440 px in licht en donker (axe-core, contrast, randen,
focusring, overloop, doelen, statusvorm, woordenlijst); afdrukken alleen op 320/360/390/768/1440. Geen
pixel-baselines (ONTWERPTAAL §12).

NIET GEVALIDEERD: echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel licht, echte Hub en NAS.
