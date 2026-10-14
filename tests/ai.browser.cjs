/* Start ai_browser_server.py first. All sources, images and model outputs are offline fixtures. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.ARCHER_TEST_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let paid = 0;
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/api/ai/runs')) paid++; });
  const url = 'http://127.0.0.1:4749/search?q=compare+loan+repayment+options';
  const turn = page.locator('.ai-turn').last();
  try {
    // Classic shows one quiet prompt; nothing is generated until a click and consent.
    await page.goto(url);
    assert.equal(paid, 0);
    await page.locator('[data-ai-prompt]').waitFor();
    await page.locator('[data-ai-mode=balanced]').click();
    await page.locator('[data-ai-settings-panel]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-ai-mode=balanced]').getAttribute('aria-pressed'), 'true');
    assert.equal(paid, 0);
    await page.locator('[data-ai-save]').click();  // "Turn on and continue" (consent is pre-ticked)

    // Balanced: a small card with an image row by default, expandable in place.
    await page.locator('#ai-search[data-stage=complete]').waitFor();
    assert.equal(paid, 1);
    assert.ok(await page.locator('.ai-visual').count() >= 3);
    assert.equal(await page.locator('#ai-search').evaluate(n => n.classList.contains('is-small')), true);
    await page.locator('[data-turn-more]').click();
    assert.equal(await page.locator('[data-turn-size]').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('[data-turn-followups] button').count(), 2);
    await page.locator('.ai-citation-chip').first().click();
    assert.equal(await page.locator('[data-ai-sources]').getAttribute('open'), '');

    // Go deeper replaces the quick answer with research in AI Mode; tool cards follow the lead paragraph.
    await page.locator('[data-turn-expand]').click();
    await page.locator('body.ai-fullview').waitFor();
    await page.locator('#ai-search[data-stage=complete]').waitFor();
    assert.equal(paid, 2);
    assert.equal(await page.locator('.ai-turn').count(), 1);
    assert.equal(await page.locator('.ai-tool-card').count(), 2);
    assert.equal(await page.locator('.ai-table-scroll table').count(), 1);
    const editor = page.locator('.ai-loan-edit').first();
    await editor.locator('[name=months]').fill('36');
    await editor.getByRole('button', { name: 'Recalculate' }).click();
    assert.ok((await page.locator('.ai-tool-card.is-edited').count()) === 1);
    assert.equal(paid, 2);
    await page.locator('#ai-search').screenshot({ path: path.resolve('screenshots/ai-research-desktop.png') });

    // Follow-up becomes a new turn and keeps the earlier one.
    await page.locator('#ai-question').fill('What changes with a shorter term?');
    await page.keyboard.press('Enter');
    await page.locator('#ai-search[data-stage=complete]').waitFor();
    assert.equal(paid, 3);
    assert.equal(await page.locator('.ai-turn').count(), 2);

    // Compare from the source rail.
    await page.locator('[data-ai-select]').click();
    await page.locator('.ai-source input').nth(0).check();
    await page.locator('.ai-source input').nth(1).check();
    await page.locator('[data-ai-compare]').click();
    await page.locator('#ai-search[data-stage=complete]').waitFor();
    assert.equal(paid, 4);
    assert.equal(await turn.locator('[data-turn-label]').innerText(), 'Comparison');

    // Reload restores from the session without a new AI call.
    await page.reload();
    await page.locator('.ai-turn').first().waitFor();
    assert.equal(paid, 4);
    assert.ok((await turn.locator('[data-turn-meta]').innerText()).includes('no new AI call'));

    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.resolve('screenshots/ai-research-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({ width: 1440, height: 1050 });

    // All results keeps the conversation one click away; Classic hides it.
    await page.locator('[data-ai-web]').click();
    assert.ok((await page.locator('[data-ai-prompt-text]').innerText()).includes('still here'));
    await page.getByRole('button', { name: 'Classic', exact: true }).click();
    assert.equal(await page.locator('[data-ai-workspace]').isVisible(), false);

    // Stop leaves an honest, retryable state and the results stay usable.
    await page.goto(url);
    await page.locator('[data-ai-ask]').click();
    await page.locator('[data-turn-stop]').click();
    assert.equal(await page.locator('#ai-search').getAttribute('data-stage'), 'error');
    assert.ok(await page.locator('.result').count());

    // Utility answers never trigger automatic AI.
    const beforeUtility = paid;
    await page.goto('http://127.0.0.1:4749/search?q=15%25+of+2400');
    await page.waitForTimeout(300);
    assert.equal(paid, beforeUtility);

    // Settings app: edits save to the fixture's temporary .env and apply immediately.
    await page.goto('http://127.0.0.1:4749/settings#ai');
    await page.locator('[data-field=ai_daily_usd] [data-input]').fill('2');
    await page.keyboard.press('Control+s');
    await page.locator('[data-toast]').getByText('Saved and applied.').waitFor();
    assert.ok((await page.locator('[data-usage-usd]').innerText()).includes('$2.00'));
    await page.locator('[data-field=ai_daily_usd] [data-input]').fill('-1');
    await page.locator('[data-save]').click();
    await page.locator('[data-field=ai_daily_usd] [data-error]').waitFor();
    assert.deepEqual(errors, []);
    console.log('AI and Settings browser checks passed; screenshots saved in screenshots/.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
