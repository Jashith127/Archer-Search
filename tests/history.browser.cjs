/* History and personal results in a real browser. Start tests/history_browser_server.py first (:4752),
   then run with NODE_PATH pointing at Playwright. ARCHER_SHOTS=<dir> saves screenshots. */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:4752';
const shots = process.env.ARCHER_SHOTS;
const shot = async (page, name) => { if (shots) await page.screenshot({ path: path.join(shots, name), fullPage: true }); };

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ARCHER_TEST_BROWSER ? { executablePath: process.env.ARCHER_TEST_BROWSER } : {}) });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) errors.push(m.text()); });

  // Results: blocked site hidden, pages found before recalled, earlier searches by meaning, tags.
  await page.goto(`${BASE}/search?q=${encodeURIComponent('1440p gaming monitor for rtx laptop')}`);
  assert.equal(await page.locator('.result-host', { hasText: 'Pinterest' }).count(), 0);
  assert.match(await page.locator('.status').innerText(), /blocked sites hidden/);
  assert.ok(await page.locator('.recall-item').count() >= 1, 'recall shows pages found before');
  const mine = await page.locator('.related--mine .chip').allInnerTexts();
  assert.ok(mine.some(t => /RTX 4050 laptop/.test(t)), `earlier searches: ${mine}`);
  assert.equal(await page.locator('.related--mine .pxmeter').count(), mine.length, 'each earlier search shows how close it is');
  assert.ok(!mine.some(t => /carbonara/.test(t)), 'unrelated history stays out');
  // Site habit is a light nudge since the history evaluation; the tag says why a site is favoured.
  assert.equal(await page.locator('[data-result]', { hasText: 'Rtings' }).locator('.result-tag', { hasText: 'You often open this site' }).count(), 1);
  await shot(page, 'results.png');

  // "Not related" removes an earlier search at once and remembers it.
  const chips = await page.locator('.related--mine .chip-pair').count();
  await page.locator('.related--mine .chip-pair').first().hover();
  const unrelated = page.waitForResponse(r => r.url().endsWith('/api/history/unrelated') && r.status() === 200);
  await page.locator('.related--mine .chip-dismiss').first().click();
  await unrelated;
  assert.equal(await page.locator('.related--mine .chip-pair').count(), chips - 1);

  // Opening a result records it (keepalive POST), without leaving this test's page.
  const recorded = page.waitForRequest(r => r.url().endsWith('/api/history/click') && r.method() === 'POST');
  await page.locator('[data-result] .result-title a').first().click({ modifiers: ['Control'] });
  const body = JSON.parse((await recorded).postData());
  assert.ok(body.search > 0 && /^https:/.test(body.url) && body.title);

  // The site menu raises a site and reloads with it marked.
  const menu = page.locator('[data-site-menu="example-shop.com"]');
  await menu.locator('summary').click();
  await shot(page, 'menu.png');
  await Promise.all([page.waitForNavigation(), menu.locator('[data-site-pref="raise"]').click()]);
  assert.equal(await page.locator('.result-tag', { hasText: 'Raised site' }).count(), 1);

  // An empty search box offers recent searches, marked and removable.
  await page.locator('.searchbar input[name=q]').fill('');
  await page.locator('.searchbar input[name=q]').click();
  await page.locator('.suggestion--past').first().waitFor();
  const before = await page.locator('.suggestion--past').count();
  await shot(page, 'recent.png');
  await page.locator('.suggestion--past').first().hover();
  await page.locator('.suggestion--past .suggestion-remove').first().click();
  await page.waitForFunction(n => document.querySelectorAll('.suggestion--past').length === n - 1, before);

  // History page: days, topics, search by meaning, remove, site preferences.
  await page.goto(`${BASE}/history`);
  assert.ok(await page.locator('.h-entry').count() >= 4);
  assert.ok(await page.locator('.h-topic').count() >= 1, 'topics');
  await shot(page, 'history.png');
  const entries = await page.locator('.h-entry').count();
  await page.locator('.h-entry').first().hover();
  await page.locator('[data-remove-search]').first().click();
  await page.waitForFunction(n => document.querySelectorAll('.h-entry').length === n - 1, entries);
  await page.locator('[data-site="rtings.com"] [data-site-set="lower"]').click();
  await page.waitForFunction(() => document.querySelector('[data-site="rtings.com"] [data-site-set="lower"]').getAttribute('aria-pressed') === 'true');
  await page.goto(`${BASE}/history?q=${encodeURIComponent('screen sharpness')}`);
  assert.match(await page.locator('h1').innerText(), /Searches like/);
  await page.goto(`${BASE}/history?q=${encodeURIComponent('gaming monitor resolution')}`);
  assert.ok(await page.locator('.h-list--matches .pxmeter').count() >= 2, 'matches carry a closeness meter');
  assert.equal(await page.locator('.h-via').count(), 1, 'a match through an opened page says so');
  await shot(page, 'history-search.png');

  // Home shows recent searches.
  await page.goto(`${BASE}/`);
  assert.ok(await page.locator('.home-recent .home-chip').count() >= 2);
  await shot(page, 'home.png');

  // Mobile width: no horizontal scroll on the history page.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/history`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal scroll');
  await shot(page, 'history-mobile.png');

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('history browser checks passed');
})().catch(e => { console.error(e); process.exit(1); });
