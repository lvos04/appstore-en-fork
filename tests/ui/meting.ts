// Gedeelde metingen voor de statuspagina in Peil (ONTWERPTAAL §4, §5, §6, §11, §12; COMPONENTEN §4 en §25).
// Gebruikt door statuspagina.spec.ts (CI, vastgelegde antwoorden van de echte app) en statuspagina.live.ts
// (ontwerpreview tegen de draaiende dienst, docs/ontwerp/PEIL_REVIEW.md). Alles wordt in de gerenderde DOM gemeten:
// kleuren worden samengesteld over alle achtergrondlagen (zoals bouw/controle.cjs van Peil), niet uit de tokens gelezen.
import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

export const BREEDTES = [320, 360, 390, 768, 1024, 1440];
export const AFDRUK_BREEDTES = [320, 360, 390, 768, 1440];
export const THEMAS = [
  { naam: 'licht', schema: 'light' as const },
  { naam: 'donker', schema: 'dark' as const },
];
export const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];

/** Woorden die Peil §11.1 vervangt (links) of die de toon verbiedt; nooit op de statuspagina. */
export const VERBODEN_WOORDEN = [
  'In orde', 'Actueel', 'Verminderd', 'Verstoord', 'Let op', 'Offline', 'Probleem', 'Niet gezond', 'Laden…',
  'Doorgaan', 'Weet je het zeker', 'Klaar', 'Gelukt', 'snoozen', 'safe mode', 'blast radius', 'runbook',
  'healthcheck', 'allowlist', 'bundled', 'enrollment-token', 'OK',
];

export type Meting = Awaited<ReturnType<typeof meetInPagina>>;
export type FocusStop = NonNullable<Awaited<ReturnType<typeof focusInPagina>>>;

/** Alles wat zonder interactie te meten is (in de pagina uitgevoerd). */
function meetInPagina(verboden: string[]) {
  const cvs = document.createElement('canvas');
  cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d', { willReadFrequently: true })!;
  type K = [number, number, number, number];
  const rgba = (s: string): K => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = s;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (t: K, b: K): K => {
    const a = t[3] + b[3] * (1 - t[3]);
    if (a === 0) return [0, 0, 0, 0];
    const c = [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / a);
    return [c[0], c[1], c[2], a];
  };
  const lum = (c: K) => {
    const f = (v: number) => ((v /= 255) <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a: K, b: K) => {
    const x = lum(a);
    const y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const hex = (c: K) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  /** tint (0–360) en verzadiging (0–1) volgens HSL */
  const tint = (c: K) => {
    const [r, g, b] = c.slice(0, 3).map((v) => v / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    const d = max - min;
    if (d === 0) return { h: 0, s: 0 };
    const s = d / (1 - Math.abs(2 * l - 1));
    let h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
    return { h: h < 0 ? h + 360 : h, s };
  };
  const groen = (c: K) => {
    const t = tint(c);
    return t.s > 0.2 && t.h >= 90 && t.h <= 170;
  };
  /** samengestelde achtergrond van een element (alle lagen tot en met het canvas, wit onderaan) */
  const vlak = (el: Element | null): K => {
    const lagen: K[] = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) lagen.push(rgba(getComputedStyle(e).backgroundColor));
    let c: K = [255, 255, 255, 1];
    for (const l of lagen.reverse()) c = over(l, c);
    return c;
  };
  const isSr = (el: Element) => {
    for (let e: Element | null = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.clip === 'rect(0px, 0px, 0px, 0px)' || cs.clipPath === 'inset(50%)') return true;
      if (cs.position === 'absolute' && parseFloat(cs.width) <= 1 && parseFloat(cs.height) <= 1 && cs.overflow === 'hidden')
        return true;
    }
    return false;
  };
  const zichtbaar = (el: Element) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    // de skiplink staat buiten beeld tot hij focus krijgt
    if (r.bottom <= 0 && el.closest('.spring')) return false;
    return !isSr(el);
  };
  const naam = (el: Element) => {
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 3).join('.');
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : '');
  };
  const eigenTekst = (el: Element) =>
    [...el.childNodes]
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent)
      .join('')
      .replace(/\s+/g, ' ')
      .trim();

  // 1. tekstcontrast (WCAG 1.4.3; Peil-eis ≥ 7:1 op tokenniveau) en lettergrootte (Peil: ≥ 14 px)
  const tekst: { el: string; t: string; fs: number; eis: number; r: number; fg: string; bg: string }[] = [];
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE', 'TITLE'].includes(el.tagName)) continue;
    const t = eigenTekst(el);
    if (!t || !zichtbaar(el)) continue;
    const cs = getComputedStyle(el);
    const fg = rgba(cs.color);
    const bg = vlak(el);
    const fs = parseFloat(cs.fontSize);
    const groot = fs >= 24 || (fs >= 18.66 && parseInt(cs.fontWeight) >= 700);
    tekst.push({ el: naam(el), t: t.slice(0, 50), fs, eis: groot ? 3 : 4.5, r: +ratio(over(fg, bg), bg).toFixed(2), fg: hex(over(fg, bg)), bg: hex(bg) });
  }
  // 2. grenzen van componenten en statusvormen (WCAG 1.4.11): rand tegen het vlak eromheen, iconen tegen hun vlak
  const grenzen: { el: string; soort: string; r: number; kleur: string; bg: string }[] = [];
  for (const el of document.querySelectorAll('.pil, .veld input, .knop, .selecteer, .melding, .voorbeeld')) {
    if (!zichtbaar(el)) continue;
    const cs = getComputedStyle(el);
    const rand = rgba(cs.borderTopColor);
    if (parseFloat(cs.borderTopWidth) === 0 || rand[3] === 0) {
      // geen rand (Gezond heeft bewust geen vlak en geen rand): dan moet het eigen vlak gelijk zijn aan de omgeving
      const eigen = vlak(el);
      const buiten = vlak(el.parentElement);
      if (ratio(eigen, buiten) > 1.05 && ratio(eigen, buiten) < 3)
        grenzen.push({ el: naam(el), soort: 'vlak', r: +ratio(eigen, buiten).toFixed(2), kleur: hex(eigen), bg: hex(buiten) });
      continue;
    }
    const buiten = vlak(el.parentElement);
    const k = over(rand, buiten);
    grenzen.push({ el: naam(el), soort: `rand ${cs.borderTopStyle} ${cs.borderTopWidth}`, r: +ratio(k, buiten).toFixed(2), kleur: hex(k), bg: hex(buiten) });
  }
  for (const svg of document.querySelectorAll('body svg:not([style*="display:none"])')) {
    if (!zichtbaar(svg) || svg.closest('[style*="display:none"]')) continue;
    const k = rgba(getComputedStyle(svg).color);
    const bg = vlak(svg.parentElement);
    grenzen.push({ el: 'svg in ' + naam(svg.parentElement!), soort: 'icoon', r: +ratio(over(k, bg), bg).toFixed(2), kleur: hex(over(k, bg)), bg: hex(bg) });
  }
  // 3. aanraakdoelen (Peil §6: 44 × 44 voor elk bedienbaar element; alleen inline links in lopende tekst uitgezonderd)
  const doelen: { el: string; t: string; w: number; h: number }[] = [];
  for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary')) {
    if (!zichtbaar(el)) continue;
    const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && !!el.parentElement && eigenTekst(el.parentElement).length > 0;
    if (inline) continue;
    if (r.width < 44 || r.height < 44)
      doelen.push({ el: naam(el), t: (el.textContent || '').trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) });
  }
  // 4. overloop: pagina én tekst die buiten zijn kaart/melding steekt (ook als de pagina zelf niet scrolt)
  const se = document.scrollingElement!;
  const overloopPagina = Math.max(se.scrollWidth, document.body.scrollWidth) - window.innerWidth;
  const buitenKader: string[] = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!eigenTekst(el) || !zichtbaar(el)) continue;
    const kader = el.closest('.melding, .blok, .kop, .voet, .wrap');
    if (!kader) continue;
    const kr = kader.getBoundingClientRect();
    const kcs = getComputedStyle(kader);
    const rechts = kr.right - parseFloat(kcs.borderRightWidth) - parseFloat(kcs.paddingRight) + 1;
    const links = kr.left + parseFloat(kcs.borderLeftWidth) + parseFloat(kcs.paddingLeft) - 1;
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.textContent!.trim()) continue;
      const bereik = document.createRange();
      bereik.selectNodeContents(n);
      for (const r of bereik.getClientRects())
        if (r.width > 0 && (r.right > rechts || r.left < links)) {
          buitenKader.push(`${naam(el)} "${n.textContent!.trim().slice(0, 30)}" ${Math.round(r.right - rechts)} px`);
          break;
        }
    }
  }
  const afgekapt = [...document.querySelectorAll('body *')]
    .filter((el) => getComputedStyle(el).textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1)
    .map(naam);
  // 5. statusvormen (Peil §4.2/§4.3): icoon + woord + vorm; groen alleen voor Gezond; onbekend/verouderd nooit groen
  const ICOON: Record<string, string[]> = {
    'pil-gezond': ['i-gezond'],
    'pil-beperkt': ['i-beperkt'],
    'pil-storing': ['i-storing'],
    'pil-onbekend': ['i-onbekend', 'i-niet-gekoppeld'],
    'pil-gate': ['i-gate'],
    'pil-neutraal': ['i-gekoppeld'],
  };
  const pillen: { klasse: string; tekst: string; icoon: string; fout: string[] }[] = [];
  for (const pil of document.querySelectorAll('.pil')) {
    const klasse = [...pil.classList].find((c) => c.startsWith('pil-')) ?? '';
    const tekst = (pil.textContent || '').replace(/\s+/g, ' ').trim();
    const use = pil.querySelector('svg use');
    const icoon = (use?.getAttribute('href') || '').replace('#', '');
    const fout: string[] = [];
    if (!tekst) fout.push('geen woord');
    if (!icoon || !document.getElementById(icoon) || document.getElementById(icoon)!.tagName.toLowerCase() !== 'symbol')
      fout.push(`icoon ${icoon || '(geen)'} bestaat niet`);
    if (!(ICOON[klasse] ?? []).includes(icoon)) fout.push(`icoon ${icoon} hoort niet bij ${klasse}`);
    if (pil.closest('[aria-hidden="true"]')) fout.push('verborgen voor hulpmiddelen');
    const cs = getComputedStyle(pil);
    const kleuren = [rgba(cs.color), rgba(getComputedStyle(pil.querySelector('svg') ?? pil).color), rgba(cs.borderTopColor), vlak(pil)];
    const isGroen = kleuren.some((k) => k[3] > 0 && groen(k));
    if (klasse === 'pil-gezond' && !/^(Alles gezond|Gezond)$/.test(tekst)) fout.push('groene pil zonder het woord Gezond');
    if (klasse !== 'pil-gezond' && isGroen) fout.push('groen buiten Gezond');
    if (/Onbekend|Verouderd|Niet gekoppeld|Ingetrokken|Ontkoppeld|Niet gereed|Niet bevestigd|onbekend/.test(tekst) && (klasse === 'pil-gezond' || isGroen))
      fout.push('onbekend/verouderd in groen');
    pillen.push({ klasse, tekst, icoon, fout });
  }
  // 6. structuur
  const koppen = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => +h.tagName[1]);
  const sprongen: string[] = [];
  for (let i = 1; i < koppen.length; i++) if (koppen[i] > koppen[i - 1] + 1) sprongen.push(`h${koppen[i - 1]}→h${koppen[i]}`);
  const ids = [...document.querySelectorAll('[id]')].map((e) => e.id);
  const kapot: string[] = [];
  for (const e of document.querySelectorAll('[aria-describedby],[aria-labelledby],[aria-controls]'))
    for (const a of ['aria-describedby', 'aria-labelledby', 'aria-controls'])
      for (const id of (e.getAttribute(a) || '').split(/\s+/).filter(Boolean)) if (!document.getElementById(id)) kapot.push(`${a}=${id}`);
  for (const a of document.querySelectorAll('a[href^="#"]')) {
    const doel = a.getAttribute('href')!.slice(1);
    if (doel && !document.getElementById(doel)) kapot.push(`href=#${doel}`);
  }
  // 7. microcopy (Peil §11): verboden woorden en uitroeptekens in zichtbare tekst en de titel
  const zichtbareTekst = document.title + '\n' + document.body.innerText;
  const woorden = verboden.filter((w) => new RegExp(`(^|[^\\p{L}])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}($|[^\\p{L}])`, 'u').test(zichtbareTekst));
  if (zichtbareTekst.includes('!')) woorden.push('uitroepteken');

  return {
    tekst,
    tekstFout: tekst.filter((t) => t.r < t.eis),
    minTekst: tekst.reduce((m, t) => Math.min(m, t.r), 99),
    kleineTekst: tekst.filter((t) => t.fs < 14).map((t) => `${t.el} ${t.fs}px "${t.t}"`),
    grenzen,
    grensFout: grenzen.filter((g) => g.r < 3),
    minGrens: grenzen.reduce((m, g) => Math.min(m, g.r), 99),
    doelen,
    overloopPagina,
    buitenKader,
    afgekapt,
    pillen,
    pilFout: pillen.filter((p) => p.fout.length).map((p) => `${p.klasse} "${p.tekst}": ${p.fout.join(', ')}`),
    h1: document.querySelectorAll('h1').length,
    main: document.querySelectorAll('main').length,
    sprongen,
    dubbeleIds: ids.filter((id, i) => ids.indexOf(id) !== i),
    kapot,
    scripts: document.querySelectorAll('script').length,
    woorden,
    totaalKlasse: document.querySelector('.totaal .pil')?.className ?? '',
    totaalTekst: (document.querySelector('.totaal .pil')?.textContent ?? '').trim(),
    gereed:
      [...document.querySelectorAll('.feiten dt')].find((dt) => dt.textContent === 'Gereed')?.nextElementSibling?.textContent ?? '',
  };
}

/** De focus van het actieve element: ring (WCAG 2.4.7/2.4.13-achtig: ≥ 3 px, ≥ 3:1) en niet bedekt (2.4.11). */
function focusInPagina() {
  const el = document.activeElement as HTMLElement | null;
  if (!el || el === document.body) return null;
  const cvs = document.createElement('canvas');
  cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d')!;
  type K = [number, number, number, number];
  const rgba = (s: string): K => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = s;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (t: K, b: K): K => {
    const a = t[3] + b[3] * (1 - t[3]);
    const c = [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / (a || 1));
    return [c[0], c[1], c[2], a];
  };
  const lum = (c: K) => {
    const f = (v: number) => ((v /= 255) <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a: K, b: K) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  const vlak = (e: Element | null): K => {
    const lagen: K[] = [];
    for (let p = e; p && p.nodeType === 1; p = p.parentElement) lagen.push(rgba(getComputedStyle(p).backgroundColor));
    let c: K = [255, 255, 255, 1];
    for (const l of lagen.reverse()) c = over(l, c);
    return c;
  };
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const omgeving = vlak(el.parentElement);
  const ring = over(rgba(cs.outlineColor), omgeving);
  const punten = [
    [r.left + r.width / 2, r.top + r.height / 2],
    [r.left + 2, r.top + 2],
    [r.right - 2, r.bottom - 2],
  ];
  let inBeeld = 0;
  let onbedekt = 0;
  for (const [x, y] of punten) {
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
    inBeeld++;
    const boven = document.elementFromPoint(x, y);
    if (boven && (boven === el || el.contains(boven) || boven.contains(el))) onbedekt++;
  }
  return {
    naam: `${el.tagName.toLowerCase()} ${el.getAttribute('href') ?? el.id ?? ''}`.trim(),
    breedte: parseFloat(cs.outlineWidth),
    stijl: cs.outlineStyle,
    ringContrast: +ratio(ring, omgeving).toFixed(2),
    halo: cs.boxShadow !== 'none',
    inBeeld: inBeeld > 0 && r.bottom > 0 && r.top < innerHeight,
    bedekt: inBeeld > 0 && onbedekt === 0,
  };
}

export async function meet(page: Page): Promise<Meting> {
  return page.evaluate(meetInPagina, VERBODEN_WOORDEN);
}

export async function focusNu(page: Page): Promise<FocusStop | null> {
  return page.evaluate(focusInPagina);
}

/** Tab-doorloop over alle focusbare elementen. Begint bij het laden (eerste Tab = skiplink) of bij de skiplink. */
export async function tabDoorloop(page: Page, vanafSkiplink: boolean): Promise<FocusStop[]> {
  const aantal = await page.locator('a[href], button, input:not([type=hidden])').count();
  const stops: FocusStop[] = [];
  if (vanafSkiplink) {
    await page.locator('a.spring').focus();
    stops.push((await focusNu(page))!);
  }
  while (stops.length < aantal) {
    await page.keyboard.press('Tab');
    const f = await focusNu(page);
    if (!f) break;
    stops.push(f);
  }
  return stops;
}

export type Samenvatting = {
  toestand: string;
  thema: string;
  breedte: number;
  axe: string[];
  minTekst: number;
  minGrens: number;
  minRing: number | null;
  focusStops: number;
  overloop: number;
};

/** Alle eisen voor één toestand × thema × breedte (de pagina staat al op de juiste breedte en in het juiste thema). */
export async function controleer(
  page: Page,
  plek: string,
  opties: { focus: boolean; eersteTabIsSkiplink: boolean; afdruk?: string },
): Promise<Omit<Samenvatting, 'toestand' | 'thema' | 'breedte'>> {
  const axe = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const overtredingen = axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect.soft(overtredingen, `${plek}: axe`).toEqual([]);

  const m = await meet(page);
  expect.soft(m.overloopPagina, `${plek}: horizontale overloop`).toBeLessThanOrEqual(0);
  expect.soft(m.buitenKader, `${plek}: tekst buiten zijn kaart`).toEqual([]);
  expect.soft(m.afgekapt, `${plek}: afgekapte inhoud`).toEqual([]);
  expect.soft(m.tekstFout, `${plek}: tekstcontrast < 4,5:1`).toEqual([]);
  expect.soft(m.grensFout, `${plek}: rand/icoon < 3:1`).toEqual([]);
  expect.soft(m.kleineTekst, `${plek}: tekst < 14 px`).toEqual([]);
  expect.soft(m.doelen, `${plek}: doelen < 44 × 44 px`).toEqual([]);
  expect.soft(m.pilFout, `${plek}: statusvorm`).toEqual([]);
  expect.soft(m.h1, plek).toBe(1);
  expect.soft(m.main, plek).toBe(1);
  expect.soft(m.sprongen, `${plek}: kopsprongen`).toEqual([]);
  expect.soft(m.dubbeleIds, `${plek}: dubbele id's`).toEqual([]);
  expect.soft(m.kapot, `${plek}: kapotte verwijzingen`).toEqual([]);
  expect.soft(m.scripts, plek).toBe(0);
  expect.soft(m.woorden, `${plek}: woorden buiten de Peil-woordenlijst`).toEqual([]);
  // eerste signaal (ONTWERPTAAL §13.2): nooit groen als de dienst niet gereed is; "Alles gezond" alleen als gereed
  if (m.gereed.startsWith('Nee')) {
    expect.soft(m.totaalKlasse, plek).not.toContain('pil-gezond');
    expect.soft(m.totaalTekst, plek).not.toContain('gezond');
  } else {
    expect.soft(m.gereed, plek).toBe('Ja');
  }

  let minRing: number | null = null;
  let focusStops = 0;
  if (opties.focus) {
    const stops = await tabDoorloop(page, !opties.eersteTabIsSkiplink);
    const aantal = await page.locator('a[href], button, input:not([type=hidden])').count();
    expect.soft(stops.length, `${plek}: elk focusbaar element bereikbaar met Tab`).toBe(aantal);
    expect.soft(stops[0]?.naam ?? '', `${plek}: eerste tabstop is de skiplink`).toMatch(/^a #/);
    for (const s of stops) {
      expect.soft(s.stijl, `${plek}: ${s.naam} zonder ring`).not.toBe('none');
      expect.soft(s.breedte, `${plek}: ${s.naam} ring < 3 px`).toBeGreaterThanOrEqual(3);
      expect.soft(s.ringContrast, `${plek}: ${s.naam} ring < 3:1`).toBeGreaterThanOrEqual(3);
      expect.soft(s.halo, `${plek}: ${s.naam} zonder halo`).toBe(true);
      expect.soft(s.inBeeld, `${plek}: ${s.naam} niet in beeld`).toBe(true);
      expect.soft(s.bedekt, `${plek}: ${s.naam} bedekt`).toBe(false);
    }
    minRing = stops.reduce((min, s) => Math.min(min, s.ringContrast), 99);
    focusStops = stops.length;
    await page.evaluate(() => {
      (document.activeElement as HTMLElement | null)?.blur();
      window.scrollTo(0, 0);
    });
  }
  if (opties.afdruk) {
    fs.mkdirSync(path.dirname(opties.afdruk), { recursive: true });
    await page.screenshot({ path: opties.afdruk, fullPage: true });
  }
  const uit = { axe: overtredingen, minTekst: m.minTekst, minGrens: m.minGrens, minRing, focusStops, overloop: m.overloopPagina };
  if (process.env.METING_UIT) {
    // volledige meting per plek (ontwerpreview): alle contrasten, randen, pillen en tabstops
    fs.mkdirSync(process.env.METING_UIT, { recursive: true });
    const bestand = path.join(process.env.METING_UIT, plek.replace(/[^\w-]+/g, '_') + '.json');
    fs.writeFileSync(bestand, JSON.stringify({ plek, ...uit, meting: m }, null, 1));
  }
  return uit;
}

/** Breed lettertype (DejaVu Sans, zoals op een kale Linux-machine of de CI-runner): het systeemlettertype verschilt
 *  per apparaat, en een lange naam zonder afbreekpunt kan dan op 320 px buiten zijn kaart steken (gevonden in CI).
 *  Alleen de reflow-eisen; de pagina zelf verandert niet (stijl alleen in deze browser toegevoegd). */
export async function controleerBreedLettertype(page: Page, plek: string): Promise<void> {
  await page.addStyleTag({
    content:
      'body, body *:not(code):not(.mono) { font-family: "DejaVu Sans", sans-serif !important } ' +
      'code, .mono { font-family: "DejaVu Sans Mono", monospace !important }',
  });
  const m = await meet(page);
  expect.soft(m.overloopPagina, `${plek} (breed lettertype): horizontale overloop`).toBeLessThanOrEqual(0);
  expect.soft(m.buitenKader, `${plek} (breed lettertype): tekst buiten zijn kaart`).toEqual([]);
  expect.soft(m.doelen, `${plek} (breed lettertype): doelen < 44 × 44 px`).toEqual([]);
}
