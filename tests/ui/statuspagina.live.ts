// Ontwerpreview van de statuspagina tegen de DRAAIENDE dienst (docs/ontwerp/PEIL_REVIEW.md).
// Wordt per toestand gestart door review_live.py (echte processen `appstore serve` + `worker`, PostgreSQL, de
// Hub-simulator over echte HTTP). Chromium haalt de pagina zelf op of verstuurt het koppelformulier zelf (echte cookie,
// echte Origin-header, echte redirect); daarna wordt hetzelfde document gemeten in licht en donker op 320–1440 px
// (thema en breedte wisselen zonder herladen: een POST wordt nooit opnieuw verstuurd). Uitkomst: Sim-getest.
//
// Omgeving: LIVE_URL, TOESTAND, ACTIE (get | formulier | formulier-zonder-cookie | al-gekoppeld), PAD, VELDEN (JSON),
// KOPPELCODE (alleen al-gekoppeld), VERWACHT_STATUS, AFDRUKKEN (map), METING_UIT (map).
import { expect, test, type Response } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AFDRUK_BREEDTES, BREEDTES, THEMAS, controleer, controleerBreedLettertype, focusNu, type Samenvatting } from './meting';

const env = (naam: string, standaard?: string): string => {
  const waarde = process.env[naam] ?? standaard;
  if (waarde === undefined) throw new Error(`${naam} ontbreekt (start via tests/ui/review_live.py)`);
  return waarde;
};

test.skip(!process.env.LIVE_URL, 'alleen via review_live.py (draaiende dienst)');

test(`${process.env.TOESTAND ?? 'live'} (draaiende dienst)`, async ({ browser }) => {
  const basis = env('LIVE_URL');
  const toestand = env('TOESTAND');
  const actie = env('ACTIE', 'get');
  const velden = JSON.parse(env('VELDEN', '{}')) as Record<string, string>;
  const context = await browser.newContext({ locale: 'nl-NL', timezoneId: 'UTC', reducedMotion: 'reduce' });
  const page = await context.newPage();
  const csp: string[] = [];
  const fouten: string[] = [];
  const extern: string[] = [];
  page.on('console', (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text());
  });
  page.on('pageerror', (e) => fouten.push(String(e)));
  page.on('request', (r) => {
    if (!r.url().startsWith(basis)) extern.push(r.url());
  });

  const invullen = async (p: typeof page, waarden: Record<string, string>): Promise<Response | null> => {
    await p.locator('#hub').fill(waarden.hub_adres ?? '');
    await p.locator('#koppelcode').fill(waarden.koppelcode ?? '');
    await p.locator('#vingerafdruk').fill(waarden.hub_vingerafdruk ?? '');
    const [antwoord] = await Promise.all([p.waitForNavigation(), p.locator('button.knop[type=submit]').click()]);
    return antwoord;
  };

  let antwoord: Response | null;
  if (actie === 'get') {
    antwoord = await page.goto(basis + env('PAD', '/'));
  } else if (actie === 'formulier' || actie === 'formulier-zonder-cookie') {
    await page.goto(basis + '/');
    if (actie === 'formulier-zonder-cookie') await context.clearCookies(); // formulier langer dan een uur open
    antwoord = await invullen(page, velden);
  } else if (actie === 'al-gekoppeld') {
    // tab 1 staat nog open op het formulier; in tab 2 koppelt iemand de dienst; daarna verstuurt tab 1 alsnog
    await page.goto(basis + '/');
    const tweede = await context.newPage();
    await tweede.goto(basis + '/');
    const gekoppeld = await invullen(tweede, { ...velden, koppelcode: env('KOPPELCODE') });
    expect(gekoppeld?.url(), 'tab 2 koppelt (303 → /?melding=gekoppeld)').toContain('melding=gekoppeld');
    await tweede.close();
    antwoord = await invullen(page, velden);
  } else {
    throw new Error(`onbekende ACTIE ${actie}`);
  }
  expect(antwoord, 'antwoord van de dienst').not.toBeNull();
  const status = antwoord!.status();
  const headers = await antwoord!.allHeaders();
  expect(status, `${toestand}: HTTP-status`).toBe(Number(env('VERWACHT_STATUS', '200')));
  expect(headers['content-security-policy'], 'CSP').toContain("default-src 'none'");
  expect(headers['cache-control'], 'Cache-Control').toBe('no-store');
  expect(await page.locator('script').count(), 'geen JavaScript').toBe(0);
  // een getoonde trace-ID is die van dit verzoek (X-Trace-Id), niet van een ander
  const trace = page.locator('code.selecteer');
  if ((await trace.count()) > 0) expect((await trace.textContent())?.trim()).toBe(headers['x-trace-id']);
  // eerste Tab na het laden: de skiplink, en die verschijnt in beeld
  await page.keyboard.press('Tab');
  const eerste = await focusNu(page);
  expect(eerste?.naam ?? '', `${toestand}: eerste Tab`).toMatch(/^a #/);
  expect(eerste?.inBeeld, `${toestand}: skiplink in beeld bij focus`).toBe(true);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  const samenvatting: Samenvatting[] = [];
  for (const thema of THEMAS) {
    await page.emulateMedia({ colorScheme: thema.schema, reducedMotion: 'reduce' });
    for (const breedte of BREEDTES) {
      await page.setViewportSize({ width: breedte, height: 900 });
      await page.evaluate(() => new Promise((klaar) => requestAnimationFrame(() => requestAnimationFrame(klaar))));
      const achtergrond = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(achtergrond).toBe(thema.schema === 'dark' ? 'rgb(10, 13, 18)' : 'rgb(238, 241, 245)');
      const plek = `${toestand} · ${thema.naam} · ${breedte}px`;
      const afdruk =
        process.env.AFDRUKKEN && AFDRUK_BREEDTES.includes(breedte)
          ? path.join(process.env.AFDRUKKEN, `${toestand}-${thema.naam}-${breedte}.png`)
          : undefined;
      const uit = await controleer(page, plek, { focus: breedte === 390 || breedte === 1440, eersteTabIsSkiplink: false, afdruk });
      samenvatting.push({ toestand, thema: thema.naam, breedte, ...uit });
    }
  }
  // als laatste (de toegevoegde stijl blijft in dit document): breed lettertype op 320 px
  await page.setViewportSize({ width: 320, height: 900 });
  await controleerBreedLettertype(page, `${toestand} · donker · 320px`);
  expect(csp, 'CSP-overtredingen').toEqual([]);
  expect(fouten, 'paginafouten').toEqual([]);
  expect(extern, 'verzoeken buiten de dienst').toEqual([]);
  if (process.env.METING_UIT) {
    fs.mkdirSync(process.env.METING_UIT, { recursive: true });
    fs.writeFileSync(
      path.join(process.env.METING_UIT, `samenvatting-${toestand}.json`),
      JSON.stringify(
        {
          toestand,
          actie,
          url: page.url(),
          status,
          titel: await page.title(),
          headers: { csp: headers['content-security-policy'], 'x-trace-id': headers['x-trace-id'], 'retry-after': headers['retry-after'] },
          runs: samenvatting,
        },
        null,
        1,
      ),
    );
  }
  await context.close();
});
