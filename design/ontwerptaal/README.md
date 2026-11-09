# Ontwerptaal Peil — versie-gepinde kopie (O-14)

Deze map bevat **kopieën**, geen eigen ontwerp. Bron van waarheid: `lvos04/ai-server-manager`
(`docs/platform-v2/ONTWERPTAAL.md` + `design/ontwerptaal/`). Versie, bron-commit en sha256 staan in `HERKOMST.json`
en in de herkomstkop (eerste regel) van elk bestand.

| Bestand | Gebruik |
|---|---|
| `statuspagina.html` | Sjabloon van `GET /` (geen JS, CSS en iconen inline); gerenderd door `apps/api/src/appstore/peil.py`. |
| `statuspagina.css` | Dezelfde CSS als in het sjabloon; alleen voor de driftcontrole (ingebedde `<style>` = dit bestand). |

**Niet met de hand wijzigen.** Controleren en bijwerken:

```sh
python3 tools/peil_kopie.py controleer                      # offline (ook in CI en in de unit-tests)
python3 tools/peil_kopie.py vergelijk --online              # tegen GitHub (CI): drift faalt, nieuwe versie = waarschuwing
python3 tools/peil_kopie.py vergelijk --bron ../ai-server-manager
python3 tools/peil_kopie.py bijwerken --bron ../ai-server-manager --commit <40 hex> --ref <branch>
```

Na bijwerken: `make test-unit test-integration test-ui schermafdrukken` en de afdrukken in
`docs/screenshots/peil/statuspagina/` vervangen. Zie `docs/adr/ADR-0009-statuspagina-peil.md`.
