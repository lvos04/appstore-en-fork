// Statuspagina in Peil (ONTWERPTAAL §12 testmatrix, §13.2): per toestand × thema × breedte (320–1440 px)
//   axe-core (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa + best-practice) = 0 overtredingen,
//   gemeten contrast in de DOM (tekst ≥ 4,5:1, randen/iconen/focusring ≥ 3:1), geen overloop (ook geen tekst buiten
//   zijn kaart), doelen ≥ 44 × 44 px, tekst ≥ 14 px, statusvorm (icoon + woord; groen alleen voor Gezond), één h1,
//   Peil-woordenlijst, geen CSP-overtreding of extern verzoek, geen JavaScript, Tab-doorloop met zichtbare, onbedekte
//   ring, en het eerste signaal nooit groen als de dienst niet gereed is. Gedeelde metingen: meting.ts.
// De pagina's komen uit de echte app (genereer_toestanden.py, Hub-simulator) — uitkomst: Sim-getest. De ontwerpreview
// tegen de draaiende dienst staat in statuspagina.live.ts (docs/ontwerp/PEIL_REVIEW.md).
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AFDRUK_BREEDTES, BREEDTES, THEMAS, controleer, controleerBreedLettertype } from './meting';

const UIT = process.env.STATUSPAGINA_UIT ?? path.join(__dirname, 'uit');
const AFDRUKKEN = path.resolve(__dirname, '../../docs/screenshots/peil/statuspagina');
const VERPLICHT = ['niet-gekoppeld', 'koppelen-mislukt', 'gekoppeld', 'ingetrokken', 'niet-gereed'];
const ADRES = 'http://appstore.test/';

type Respons = { toestand: string; status: number; headers: Record<string, string> };

function toestanden(): { naam: string; body: string; meta: Respons }[] {
  if (!fs.existsSync(UIT)) throw new Error(`Geen toestanden in ${UIT}: draai eerst python tests/ui/genereer_toestanden.py`);
  const namen = fs.readdirSync(UIT).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5)).sort();
  for (const v of VERPLICHT) if (!namen.includes(v)) throw new Error(`verplichte toestand ${v} ontbreekt in ${UIT}`);
  return namen.map((naam) => ({
    naam,
    body: fs.readFileSync(path.join(UIT, `${naam}.html`), 'utf8'),
    meta: JSON.parse(fs.readFileSync(path.join(UIT, `${naam}.json`), 'utf8')) as Respons,
  }));
}

async function serveer(page: Page, body: string, meta: Respons, extern: string[]): Promise<void> {
  await page.route('**/*', async (route) => {
    if (route.request().url() !== ADRES) {
      extern.push(route.request().url()); // de pagina mag niets anders laden (CSP default-src 'none')
      await route.abort();
      return;
    }
    const headers = { ...meta.headers };
    delete headers['content-length'];
    await route.fulfill({ status: meta.status, headers, body });
  });
}

for (const { naam, body, meta } of toestanden()) {
  for (const thema of THEMAS) {
    test(`${naam} · ${thema.naam}`, async ({ page }) => {
      const csp: string[] = [];
      const fouten: string[] = [];
      const extern: string[] = [];
      page.on('console', (m) => {
        if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text());
      });
      page.on('pageerror', (e) => fouten.push(String(e)));
      await page.emulateMedia({ colorScheme: thema.schema, reducedMotion: 'reduce' });
      await serveer(page, body, meta, extern);
      expect(meta.headers['content-security-policy']).toContain("default-src 'none'");
      expect(body.startsWith('<!doctype html>')).toBe(true);
      expect(body).not.toMatch(/<script/i);

      for (const breedte of BREEDTES) {
        await page.setViewportSize({ width: breedte, height: 900 });
        await page.goto(ADRES);
        const plek = `${naam} · ${thema.naam} · ${breedte}px`;
        // thema werkelijk actief (prefers-color-scheme in de CSS van het sjabloon)
        const achtergrond = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
        expect(achtergrond, plek).toBe(thema.schema === 'dark' ? 'rgb(10, 13, 18)' : 'rgb(238, 241, 245)');
        const afdruk =
          process.env.SCHERMAFDRUKKEN && VERPLICHT.includes(naam) && AFDRUK_BREEDTES.includes(breedte)
            ? path.join(AFDRUKKEN, `${naam}-${thema.naam}-${breedte}.png`)
            : undefined;
        await controleer(page, plek, { focus: breedte === 390 || breedte === 1440, eersteTabIsSkiplink: true, afdruk });
      }
      await page.setViewportSize({ width: 320, height: 900 });
      await page.goto(ADRES);
      await controleerBreedLettertype(page, `${naam} · ${thema.naam} · 320px`);
      expect(csp, 'CSP-overtredingen').toEqual([]);
      expect(fouten, 'paginafouten').toEqual([]);
      expect(extern, 'externe verzoeken').toEqual([]);
    });
  }
}
