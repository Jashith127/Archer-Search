/* Run the offline fixture server first, then use NODE_PATH to resolve Playwright.
   Optionally set ARCHER_TEST_BROWSER to an installed Chromium executable. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ARCHER_TEST_BROWSER ? { executablePath: process.env.ARCHER_TEST_BROWSER } : {}) });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const calls = [];
  page.on('request', r => { if (r.url().includes('/suggest?')) calls.push(new URL(r.url()).searchParams.get('q')); });
  // Deliberately ignore abort to exercise generation checks, not just fetch cancellation.
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (url, options) => {
      const q = new URL(url, location.href).searchParams.get('q');
      if (String(url).startsWith('/suggest?') && q.startsWith('slow')) {
        return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify([q, [q + ' stale']]), { headers: { 'Content-Type': 'application/json' } })), 550));
      }
      return original(url, options);
    };
  });
  const base = 'http://127.0.0.1:4748';
  const input = page.locator('input[name=q]');
  const list = page.getByRole('listbox');
  const expectOpen = async () => { await page.getByRole('option').first().waitFor(); assert.equal(await input.getAttribute('aria-expanded'), 'true'); };
  const assertClosed = async () => { assert.equal(await list.isVisible(), false); assert.equal(await input.getAttribute('aria-expanded'), 'false'); assert.equal(await input.getAttribute('aria-activedescendant'), null); };
  try {
    await page.goto(base);
    await input.fill('a');
    await page.waitForTimeout(250);
    assert.deepEqual(calls, []);
    await input.fill('archer');
    await expectOpen();
    assert.equal(await page.getByRole('option').count(), 3);
    assert.equal(await page.locator('.suggestion-text b').count(), 0); // provider markup is text
    await input.press('ArrowDown');
    assert.equal(await input.inputValue(), 'archer first');
    assert.ok(await input.getAttribute('aria-activedescendant'));
    await input.press('Escape');
    assert.equal(await input.inputValue(), 'archer');
    await assertClosed();

    await input.fill('tab draft'); await expectOpen();
    await input.press('ArrowDown'); await input.press('Tab');
    assert.equal(await input.inputValue(), 'tab draft'); await assertClosed();

    await input.focus(); await input.fill('slow clear');
    await page.waitForTimeout(220);
    await page.getByRole('button', { name: 'Clear search' }).click();
    await page.waitForTimeout(650); await assertClosed();
    assert.equal(await input.inputValue(), '');

    await input.focus(); await input.fill('slow submit'); await page.waitForTimeout(220);
    await page.locator('form.searchbar').evaluate(form => {
      form.addEventListener('submit', e => e.preventDefault(), { once: true });
      form.requestSubmit();
    });
    await page.waitForTimeout(650); await assertClosed();

    await input.fill('slow race'); await page.waitForTimeout(220);
    await input.fill('new query'); await expectOpen();
    await page.waitForTimeout(600);
    assert.equal(await page.getByRole('option').first().innerText(), 'new query first');

    await input.fill('slow escape'); await page.waitForTimeout(220); await input.press('Escape');
    await page.waitForTimeout(650); await assertClosed();
    await input.fill('slow blur'); await page.waitForTimeout(220);
    await page.getByRole('button', { name: 'Search', exact: true }).focus();
    await page.waitForTimeout(650); await assertClosed();

    await input.focus();
    const beforeIME = calls.length;
    await input.dispatchEvent('compositionstart');
    await input.fill('日本'); await page.waitForTimeout(250);
    assert.equal(calls.length, beforeIME);
    await input.dispatchEvent('compositionend'); await expectOpen();
    assert.equal(calls.at(-1), '日本');

    await input.fill('plain query'); await expectOpen();
    await input.press('Enter'); await page.waitForURL('**/search?q=plain+query');
    assert.equal(await input.inputValue(), 'plain query'); // no automatic selection

    for (const tab of ['all', 'images', 'news', 'videos']) {
      await page.goto(`${base}/search?q=fixture&tab=${tab}`);
      const start = calls.length;
      assert.ok((await page.locator('.related .chip').getAttribute('href')).includes('fixture+related'));
      await page.waitForTimeout(250); assert.equal(calls.length, start); // no fallback when search supplies terms
      await input.fill('selection'); await expectOpen();
      const bounds = await list.boundingBox(), field = await page.locator('[data-searchbox]').boundingBox();
      assert.ok(bounds.y >= field.y + field.height);
      assert.equal(await page.evaluate(({ x, y }) => !!document.elementFromPoint(x, y)?.closest('.suggestion'), { x: bounds.x + 30, y: bounds.y + 25 }), true);
      if (tab === 'images' && process.env.ARCHER_TEST_SCREENSHOTS) await page.screenshot({ path: `${process.env.ARCHER_TEST_SCREENSHOTS}/suggestions-results.png` });
      await input.press('ArrowDown'); await input.press('ArrowDown'); await input.press('Enter');
      await page.waitForURL(url => url.searchParams.get('q') === 'selection second');
      assert.equal(new URL(page.url()).searchParams.get('tab'), tab === 'all' ? null : tab);
      assert.equal(new URL(page.url()).searchParams.has('p'), false);
    }

    await page.goto(`${base}/search?q=fallback&tab=news`);
    await page.locator('.related .chip').first().waitFor();
    assert.ok((await page.locator('.related .chip').first().getAttribute('href')).endsWith('&tab=news'));

    await page.goto(base); await input.fill('click'); await expectOpen();
    await page.getByRole('option').nth(1).click();
    await page.waitForURL(url => url.searchParams.get('q') === 'click second');

    await page.goto(base); await input.fill('failure');
    await page.route('**/suggest?q=failure', route => route.fulfill({ status: 500 }));
    await page.waitForTimeout(350); await assertClosed();
    await input.press('Enter'); await page.waitForURL('**/search?q=failure');

    const touch = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const mobile = await touch.newPage();
    await mobile.goto(base);
    await mobile.locator('input[name=q]').fill('mobile');
    await mobile.getByRole('option').first().waitFor();
    const mobileBounds = await mobile.getByRole('listbox').boundingBox();
    assert.ok(mobileBounds.x >= 0 && mobileBounds.x + mobileBounds.width <= 390);
    if (process.env.ARCHER_TEST_SCREENSHOTS) await mobile.screenshot({ path: `${process.env.ARCHER_TEST_SCREENSHOTS}/suggestions-mobile.png` });
    await mobile.getByRole('option').first().tap();
    await mobile.waitForURL(url => url.searchParams.get('q') === 'mobile first');
    await touch.close();

    await page.goto(base); await input.fill('desktop'); await expectOpen();
    if (process.env.ARCHER_TEST_SCREENSHOTS) await page.screenshot({ path: `${process.env.ARCHER_TEST_SCREENSHOTS}/suggestions-desktop.png` });
    assert.deepEqual(errors, []);
    const noJS = await browser.newContext({ javaScriptEnabled: false });
    const plain = await noJS.newPage(); await plain.goto(base);
    await plain.locator('input[name=q]').fill('no javascript');
    await plain.getByRole('button', { name: 'Search', exact: true }).click();
    await plain.waitForURL('**/search?q=no+javascript');
    await noJS.close();
    console.log('Browser checks passed: keyboard, pointer/touch, IME, races, failures, tab preservation, related searches, layout, and no-JS submission.');
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
