// Ontwerpreview van de statuspagina tegen de DRAAIENDE dienst (docs/ontwerp/PEIL_REVIEW.md).
// Wordt per toestand gestart door review_live.py (echte processen `appstore serve` + `worker`, PostgreSQL, de
// Hub-simulator over echte HTTP). Chromium haalt de pagina zelf op of verstuurt het koppelformulier zelf (echte cookie,
// echte Origin-header, echte redirect); daarna wordt hetzelfde document gemeten in licht en donker op 320–1440 px
// (thema en breedte wisselen zonder herladen: een POST wordt nooit opnieuw verstuurd). Uitkomst: Sim-getest.
//
// Omgeving: LIVE_URL, TOESTAND, ACTIE (get | formulier | formulier-zonder-cookie | al-gekoppeld | negatief), PAD,
// VELDEN (JSON), KOPPELCODE (alleen al-gekoppeld), VERWACHT_STATUS, AFDRUKKEN (map), METING_UIT (map).
//
// ACTIE negatief (contract 1.3 §3.7 punt 6): het echte formulier, verstuurd door Chromium, maar vanuit een verkeerde
// herkomst — een document zonder oorsprong (Origin: null), een vreemde site (cross-site), dezelfde host op een andere
// poort (same-site), een publieke naam die naar de dienst wijst (DNS-rebinding, Host-beperking) en zonder cookie. Elke
// poging moet 403 geven met de vaste code, gaat niet naar de Hub en verandert niets (de dienst blijft niet gekoppeld).
import { expect, test, type Browser, type BrowserContext, type Page, type Response } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AFDRUK_BREEDTES, BREEDTES, THEMAS, controleer, controleerBreedLettertype, focusNu, type Samenvatting } from './meting';

const env = (naam: string, standaard?: string): string => {
  const waarde = process.env[naam] ?? standaard;
  if (waarde === undefined) throw new Error(`${naam} ontbreekt (start via tests/ui/review_live.py)`);
  return waarde;
};

test.skip(!process.env.LIVE_URL, 'alleen via review_live.py (draaiende dienst)');

const codeOpPagina = async (p: Page): Promise<string> =>
  ((await p.locator('p.meta code').first().textContent()) ?? '').trim();

/** Een formulier op een vreemde herkomst dat naar de dienst post (zoals een aanvallende pagina het zou doen). */
const vreemdFormulier = (doel: string, velden: Record<string, string>): string =>
  '<!doctype html><html lang="nl"><body><form method="post" action="' + doel + '/koppelen">' +
  Object.entries(velden).map(([k, v]) => `<input name="${k}" value="${v}">`).join('') +
  '<button type="submit">Verstuur</button></form></body></html>';

async function negatief(browser: Browser, basis: string, velden: Record<string, string>): Promise<void> {
  const poort = new URL(basis).port;
  const gevallen: { naam: string; code: string; doe: (ctx: BrowserContext) => Promise<Response | null> }[] = [
    {
      naam: 'Origin: null (document zonder oorsprong)',
      code: 'herkomst_onjuist',
      doe: async (ctx) => {
        const p = await ctx.newPage();
        await p.goto(basis + '/'); // echte cookie en echt formuliertoken van de dienst
        const csrf = await p.locator('input[name=csrf]').getAttribute('value');
        // een data:-document heeft een ondoorzichtige oorsprong: de browser stuurt dan Origin: null (setContent zou de
        // oorsprong van de dienst houden en dus een geldige same-origin-POST geven)
        const html = vreemdFormulier(basis, { ...velden, csrf: csrf ?? '' });
        await p.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html));
        const [a] = await Promise.all([p.waitForNavigation(), p.locator('button').click()]);
        return a;
      },
    },
    {
      naam: 'vreemde site (Sec-Fetch-Site: cross-site)',
      code: 'herkomst_onjuist',
      doe: async (ctx) => {
        const p = await ctx.newPage();
        await p.goto(basis + '/');
        const csrf = await p.locator('input[name=csrf]').getAttribute('value');
        await p.route('http://aanvaller.test/', (r) =>
          r.fulfill({ contentType: 'text/html', body: vreemdFormulier(basis, { ...velden, csrf: csrf ?? '' }) }),
        );
        await p.goto('http://aanvaller.test/');
        const [a] = await Promise.all([p.waitForNavigation(), p.locator('button').click()]);
        return a;
      },
    },
    {
      naam: 'zelfde host, andere poort (Sec-Fetch-Site: same-site)',
      code: 'herkomst_onjuist',
      doe: async (ctx) => {
        const p = await ctx.newPage();
        await p.goto(basis + '/');
        const csrf = await p.locator('input[name=csrf]').getAttribute('value');
        const andere = `http://127.0.0.1:${Number(poort) === 9 ? 10 : 9}/`;
        await p.route(andere, (r) =>
          r.fulfill({ contentType: 'text/html', body: vreemdFormulier(basis, { ...velden, csrf: csrf ?? '' }) }),
        );
        await p.goto(andere);
        const [a] = await Promise.all([p.waitForNavigation(), p.locator('button').click()]);
        return a;
      },
    },
    {
      naam: 'publieke naam die naar de dienst wijst (Host-beperking, DNS-rebinding)',
      code: 'host_niet_toegestaan',
      doe: async (ctx) => {
        const p = await ctx.newPage();
        const rebind = `http://nas.fritz.box:${poort}`;
        await p.goto(rebind + '/'); // Origin = Host = nas.fritz.box: de herkomstcontrole alleen houdt dit niet tegen
        expect(await p.content(), 'GET via een publieke naam waarschuwt vooraf').toContain('host_niet_toegestaan');
        await p.locator('#hub').fill(velden.hub_adres);
        await p.locator('#koppelcode').fill(velden.koppelcode);
        await p.locator('#vingerafdruk').fill(velden.hub_vingerafdruk);
        const [a] = await Promise.all([p.waitForNavigation(), p.locator('button.knop[type=submit]').click()]);
        return a;
      },
    },
    {
      naam: 'geen cookie',
      code: 'sessie_verlopen',
      doe: async (ctx) => {
        const p = await ctx.newPage();
        await p.goto(basis + '/');
        await ctx.clearCookies();
        await p.locator('#hub').fill(velden.hub_adres);
        await p.locator('#koppelcode').fill(velden.koppelcode);
        await p.locator('#vingerafdruk').fill(velden.hub_vingerafdruk);
        const [a] = await Promise.all([p.waitForNavigation(), p.locator('button.knop[type=submit]').click()]);
        return a;
      },
    },
  ];
  for (const geval of gevallen) {
    const ctx = await browser.newContext({ locale: 'nl-NL', timezoneId: 'UTC' });
    const antwoord = await geval.doe(ctx);
    expect(antwoord, geval.naam).not.toBeNull();
    expect(antwoord!.status(), `${geval.naam}: HTTP-status`).toBe(403);
    const pagina = ctx.pages()[ctx.pages().length - 1];
    expect(await codeOpPagina(pagina), `${geval.naam}: vaste code`).toBe(geval.code);
    await ctx.close();
  }
  // niets gewijzigd: de dienst is nog steeds niet gekoppeld en biedt het formulier nog
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto(basis + '/');
  expect(await p.locator('form[action="/koppelen"]').count(), 'formulier staat er nog (niets gekoppeld)').toBe(1);
  await ctx.close();
}

type Logboek = { csp: string[]; fouten: string[]; extern: string[] };

/** Alle eisen op één al geladen document: status, headers, skiplink, en per thema × breedte de metingen. */
async function meetPagina(
  page: Page,
  toestand: string,
  actie: string,
  antwoord: Response | null,
  verwacht: number,
  log: Logboek,
): Promise<void> {
  expect(antwoord, 'antwoord van de dienst').not.toBeNull();
  const status = antwoord!.status();
  const headers = await antwoord!.allHeaders();
  expect(status, `${toestand}: HTTP-status`).toBe(verwacht);
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
  expect(log.csp, 'CSP-overtredingen').toEqual([]);
  expect(log.fouten, 'paginafouten').toEqual([]);
  expect(log.extern, 'verzoeken buiten de dienst').toEqual([]);
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
}

test(`${process.env.TOESTAND ?? 'live'} (draaiende dienst)`, async ({ browser }) => {
  const basis = env('LIVE_URL');
  const toestand = env('TOESTAND');
  const actie = env('ACTIE', 'get');
  const velden = JSON.parse(env('VELDEN', '{}')) as Record<string, string>;
  if (actie === 'negatief') {
    await negatief(browser, basis, velden);
    return;
  }
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
  let tweedeTab: { page: Page; antwoord: Response | null } | null = null;
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
    // contract 1.3 §3.7: de melding hoort bij deze poging van deze browser (sleutel, geen vaste query-parameter)
    expect(gekoppeld?.url(), 'tab 2 koppelt (303 → /?poging=<sleutel>)').toMatch(/\/\?poging=[A-Za-z0-9_-]{16,64}$/);
    expect(await tweede.locator('.melding[role="status"]').count(), 'melding na dít verzoek: role=status').toBe(1);
    tweedeTab = { page: tweede, antwoord: gekoppeld };
    antwoord = await invullen(page, velden);
  } else {
    throw new Error(`onbekende ACTIE ${actie}`);
  }
  await meetPagina(page, toestand, actie, antwoord, Number(env('VERWACHT_STATUS', '200')), { csp, fouten, extern });
  if (tweedeTab) {
    // tab 2 na de geslaagde koppeling: de melding "Gekoppeld met de Hub" van díe poging (Peil 1.1.1, rol status)
    await meetPagina(tweedeTab.page, 'gekoppeld-melding', actie, tweedeTab.antwoord, 200, { csp, fouten, extern });
  }
  await context.close();
});
