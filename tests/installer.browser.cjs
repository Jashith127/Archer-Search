/* Start ArcherSetup.exe --dry --no-integrate --dev-port 4791 --dir C:\Archer first.
   Companion fixture: python tests/companion_browser_server.py. No real installation occurs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

(async () => {
  fs.mkdirSync('docs/screenshots', { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.ARCHER_TEST_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, reducedMotion: 'reduce' });
  const errors = [];
  let launches = 0;
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.url().endsWith('/api/launch')) launches++; });
  try {
    await page.goto('http://127.0.0.1:4791/');
    await page.locator('[data-view=welcome]').waitFor({ state: 'visible' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.panel')).opacity === '1');
    await page.screenshot({ path: 'docs/screenshots/installer-welcome.png' });
    await page.locator('[data-primary]').click();
    await page.waitForFunction(() => !document.body.classList.contains('is-busy') && ['check', 'search'].some(v => !document.querySelector(`[data-view=${v}]`).hidden));
    await page.waitForTimeout(2200);
    assert.equal(await page.locator('[data-view=check]').isVisible(), true, 'system check waits for a click');
    if (await page.locator('[data-view=check]').isVisible()) await page.locator('[data-primary]').click();
    if (await page.locator('[data-view=search]').isVisible()) await page.locator('[data-secondary]').click();
    await page.locator('[data-view=install]').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.body.classList.contains('is-busy') && !document.querySelector('[data-view=install]').hidden);
    await page.waitForTimeout(2200);
    assert.equal(await page.locator('[data-view=install]').isVisible(), true, 'completed installation waits for a click');
    await page.locator('[data-primary]').click();
    await page.locator('[data-view=configure]').waitFor({ state: 'visible', timeout: 30000 });
    await page.waitForTimeout(2200);
    assert.equal(await page.locator('[data-primary-label]').textContent(), 'Open Archer setup');
    assert.equal(launches, 0, 'entering configuration does not launch another app');
    await page.screenshot({ path: 'docs/screenshots/installer-configure.png' });
    await page.locator('[data-primary]').click();
    await page.waitForTimeout(9500);
    assert.equal(launches, 1);
    assert.equal(await page.locator('[data-view=configure]').isVisible(), true, 'completed companion setup waits for a click');
    await page.goto('http://127.0.0.1:4790/app/welcome');
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator('h1:visible').count(), 1);
    await page.screenshot({ path: 'docs/screenshots/companion-setup.png' });
    await page.goto('http://127.0.0.1:4790/');
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: 'docs/screenshots/search-home.png' });
    await page.goto('http://127.0.0.1:4791/');
    await page.locator('[data-view=welcome]').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 540, height: 760 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log('Installer dry run completed; setup and home loaded without JavaScript errors.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
