/* Start ArcherSetup.exe --dry --no-integrate --dev-port 4791 --dir C:\Archer first.
   Companion fixture: python tests/companion_browser_server.py. No real installation occurs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

(async () => {
  fs.mkdirSync('docs/screenshots', { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.ARCHER_TEST_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  // The size of the installer's own window (Frame.PageWidth × PageHeight).
  const page = await browser.newPage({ viewport: { width: 760, height: 660 }, reducedMotion: 'reduce' });
  const errors = [];
  let launches = 0, choice = null;
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => {
    if (r.url().endsWith('/api/launch')) launches++;
    if (r.url().endsWith('/api/choice')) choice = JSON.parse(r.postData());
  });
  try {
    // It opens on the hero; setup waits until Get started.
    await page.goto('http://127.0.0.1:4791/');
    await page.evaluate(() => document.fonts.ready);
    await page.locator('[data-hero-go]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-hero-go]').textContent(), 'Get started');
    assert.equal(await page.locator('[data-view=welcome]').isVisible(), false);
    await page.screenshot({ path: 'docs/screenshots/installer-hero.png' });
    await page.locator('[data-hero-go]').click();
    await page.locator('[data-view=welcome]').waitFor({ state: 'visible' });
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.pane')).opacity === '1');
    assert.equal(await page.locator('[data-logo] svg').count(), 4, 'a logo per provider');
    assert.equal(await page.locator('[data-check]').count(), 0, 'no system checks');
    assert.deepEqual(await page.locator('[data-choice]').evaluateAll(els => els.map(e => e.dataset.choice)),
      ['searxng', 'duckduckgo', 'google', 'brave'], 'SearXNG first, then the others');
    assert.equal(await page.locator('[data-keybox]').evaluate(e => e.classList.contains('is-open')), false);
    await page.screenshot({ path: 'docs/screenshots/installer-welcome.png' });

    // SearXNG and DuckDuckGo make up the Recommended section; Google and Brave, below it, open a key field.
    assert.deepEqual(await page.locator('.list--rec [data-choice]').evaluateAll(els => els.map(e => e.dataset.choice)), ['searxng', 'duckduckgo']);
    assert.equal(await page.locator('.pill').count(), 0, 'no recommended badge');
    await page.locator('[data-choice=brave]').click();
    assert.equal(await page.locator('[data-keybox]').evaluate(e => e.classList.contains('is-open')), true);
    assert.equal(await page.locator('[data-key-label]').textContent(), 'Brave Search API key');
    await page.locator('[data-choice=searxng]').click();
    assert.equal(await page.locator('[data-keybox]').evaluate(e => e.classList.contains('is-open')), false);

    // Options sit at the top and open as a sheet; with a key field open nothing scrolls.
    await page.locator('[data-choice=google]').click();
    assert.equal(await page.locator('[data-view=welcome]').evaluate(v => v.scrollHeight <= v.clientHeight), true, 'setup fits without scrolling');
    await page.locator('[data-options-open]').click();
    await page.locator('[data-sheet=options]').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await page.locator('[data-sheet=options]').waitFor({ state: 'hidden' });
    await page.locator('[data-choice=searxng]').click();
    await page.locator('[data-primary]').click();
    await page.locator('[data-view=install]').waitFor({ state: 'visible' });
    assert.deepEqual(choice, { provider: 'searxng', key: '' });
    assert.equal(await page.locator('[data-task=searxng]').count(), 1, 'SearXNG is installed with Archer');
    await page.waitForFunction(() => !document.body.classList.contains('is-busy') && document.querySelector('[data-primary-label]').textContent === 'Continue', null, { timeout: 60000 });
    await page.waitForTimeout(1500);
    assert.equal(await page.locator('[data-view=install]').isVisible(), true, 'completed installation waits for a click');
    await page.locator('[data-primary]').click();
    await page.locator('[data-view=done]').waitFor({ state: 'visible' });
    await page.waitForTimeout(1500);
    assert.equal(launches, 0, 'finishing does not launch the companion by itself');
    await page.screenshot({ path: 'docs/screenshots/installer-done.png' });
    await page.locator('[data-personalise]').click();
    await page.waitForTimeout(9500);
    assert.equal(launches, 1);
    assert.equal(await page.locator('[data-personalise-end]').evaluate(e => e.classList.contains('is-ok')), true, 'companion setup shows as done');
    await page.locator('[data-browser]').click();
    await page.locator('[data-sheet=browser]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.copy-row').count() >= 3, true);
    await page.keyboard.press('Escape');
    await page.locator('[data-sheet=browser]').waitFor({ state: 'hidden' });

    await page.goto('http://127.0.0.1:4790/app/welcome');
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator('h1:visible').count(), 1);
    await page.screenshot({ path: 'docs/screenshots/companion-setup.png' });
    await page.goto('http://127.0.0.1:4790/');
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: 'docs/screenshots/search-home.png' });
    await page.goto('http://127.0.0.1:4791/');
    await page.locator('[data-hero-go]').click();
    await page.locator('[data-view=welcome]').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 540, height: 760 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log('Installer dry run completed; setup and home loaded without JavaScript errors.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
