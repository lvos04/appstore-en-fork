// Statuspagina in Peil (ONTWERPTAAL §12 testmatrix, §13.2): per toestand × thema × breedte
//   axe-core (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa + best-practice) = 0 overtredingen,
//   geen horizontale overloop (ook 320 px), geen CSP-overtreding of extern verzoek, geen JavaScript,
//   aanraakdoelen ≥ 44 px, tekst ≥ 14 px, één h1, zichtbare focusring, eerste signaal nooit groen als niet gereed.
// De pagina's komen uit de echte app (genereer_toestanden.py, Hub-simulator) — uitkomst: Sim-getest.
// Schermafdrukken (SCHERMAFDRUKKEN=1): verplichte toestanden × 360/768/1440 × licht/donker naar
// docs/screenshots/peil/statuspagina/.
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const UIT = process.env.STATUSPAGINA_UIT ?? path.join(__dirname, 'uit');
const AFDRUKKEN = path.resolve(__dirname, '../../docs/screenshots/peil/statuspagina');
const BREEDTES = [320, 360, 390, 768, 1024, 1440];
const AFDRUK_BREEDTES = [360, 768, 1440];
const VERPLICHT = ['niet-gekoppeld', 'koppelen-mislukt', 'gekoppeld', 'ingetrokken', 'niet-gereed'];
const THEMAS = [
  { naam: 'licht', schema: 'light' as const },
  { naam: 'donker', schema: 'dark' as const },
];
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

        // axe-core: WCAG 2.0/2.1/2.2 A+AA en best practices
        const axe = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'])
          .analyze();
        const overtredingen = axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
        expect(overtredingen, plek).toEqual([]);

        const meting = await page.evaluate(() => {
          const zichtbaar = (el: Element) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const doc = document.documentElement;
          const kleineDoelen = [...document.querySelectorAll('button, input:not([type=hidden]), .voet a, .knop')]
            .filter(zichtbaar)
            .filter((el) => el.getBoundingClientRect().height < 44)
            .map((el) => `${el.tagName.toLowerCase()}#${el.id || el.textContent?.trim().slice(0, 30)}`);
          const kleineTekst = [...document.querySelectorAll('body *')]
            .filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim() !== ''))
            .filter(zichtbaar)
            .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 14)
            .map((el) => `${el.tagName.toLowerCase()}: ${el.textContent?.trim().slice(0, 30)}`);
          const totaal = document.querySelector('.totaal .pil');
          const gereed = [...document.querySelectorAll('.feiten dt')].find((dt) => dt.textContent === 'Gereed');
          return {
            overloop: Math.max(doc.scrollWidth, document.body.scrollWidth) - window.innerWidth,
            kleineDoelen,
            kleineTekst,
            h1: document.querySelectorAll('h1').length,
            scripts: document.querySelectorAll('script').length,
            totaalKlasse: totaal?.className ?? '',
            totaalTekst: totaal?.textContent ?? '',
            gereed: gereed?.nextElementSibling?.textContent ?? '',
            dubbeleIds: [...document.querySelectorAll('[id]')]
              .map((el) => el.id)
              .filter((id, i, alle) => alle.indexOf(id) !== i),
          };
        });
        expect(meting.overloop, `${plek}: horizontale overloop`).toBeLessThanOrEqual(0);
        expect(meting.kleineDoelen, `${plek}: doelen < 44 px`).toEqual([]);
        expect(meting.kleineTekst, `${plek}: tekst < 14 px`).toEqual([]);
        expect(meting.h1, plek).toBe(1);
        expect(meting.scripts, plek).toBe(0);
        expect(meting.dubbeleIds, plek).toEqual([]);
        // eerste signaal: nooit groen als de dienst niet gereed is; "Alles gezond" alleen als gereed
        if (meting.gereed.startsWith('Nee')) {
          expect(meting.totaalKlasse, plek).not.toContain('pil-gezond');
          expect(meting.totaalTekst, plek).not.toContain('Alles gezond');
        } else {
          expect(meting.gereed, plek).toBe('Ja');
        }

        // toetsenbord: de eerste Tab gaat naar de skiplink; elk focusbaar element krijgt een ring ≥ 3 px
        if (breedte === 390 || breedte === 1440) {
          const aantal = await page.locator('a[href], button, input:not([type=hidden])').count();
          for (let i = 0; i < aantal; i++) {
            await page.keyboard.press('Tab');
            const ring = await page.evaluate(() => {
              const el = document.activeElement as HTMLElement | null;
              if (!el || el === document.body) return null;
              const s = getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return {
                naam: `${el.tagName.toLowerCase()} ${el.getAttribute('href') ?? el.id}`,
                breedte: parseFloat(s.outlineWidth),
                stijl: s.outlineStyle,
                inBeeld: r.bottom > 0 && r.top < window.innerHeight,
              };
            });
            expect(ring, `${plek}: Tab ${i + 1}`).not.toBeNull();
            if (i === 0) expect(ring!.naam, plek).toMatch(/^a #/);
            expect(ring!.stijl, `${plek}: ${ring!.naam}`).not.toBe('none');
            expect(ring!.breedte, `${plek}: ${ring!.naam}`).toBeGreaterThanOrEqual(3);
            expect(ring!.inBeeld, `${plek}: ${ring!.naam} niet in beeld bij focus`).toBe(true);
          }
        }

        if (process.env.SCHERMAFDRUKKEN && VERPLICHT.includes(naam) && AFDRUK_BREEDTES.includes(breedte)) {
          fs.mkdirSync(AFDRUKKEN, { recursive: true });
          await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
          await page.screenshot({ path: path.join(AFDRUKKEN, `${naam}-${thema.naam}-${breedte}.png`), fullPage: true });
        }
      }
      expect(csp, 'CSP-overtredingen').toEqual([]);
      expect(fouten, 'paginafouten').toEqual([]);
      expect(extern, 'externe verzoeken').toEqual([]);
    });
  }
}
