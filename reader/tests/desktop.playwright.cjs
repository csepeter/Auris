// Uses local Playwright against the real desktop backend; --setup also downloads a real model.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.AURIS_DESKTOP_QA_URL || 'http://127.0.0.1:17902';
const output = process.env.AURIS_DESKTOP_QA_SHOTS || path.resolve(__dirname, '../../build/windows/screenshots');
const errors = [];
const runningSetup = process.argv.includes('--setup');

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const ready = await browser.newContext();
  let available = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    try { available = (await ready.request.get(base + '/api/desktop/status', {timeout: 1000})).ok(); } catch (_) {}
    if (available) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert.ok(available, 'Desktop server did not start: ' + base);
  await ready.close();
  const page = await browser.newPage({viewport: {width: 1280, height: 960}});
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(base + '/desktop/setup');
  await page.locator('h1').waitFor();
  assert.equal(await page.locator('input[value="supertonic"]').isChecked(), true);
  if (await page.locator('#gpu').count()) {
    assert.equal(await page.locator('#gpu').isEnabled(), false);
    await page.locator('input[value="omnivoice"]').check();
    assert.equal(await page.locator('#gpu').isEnabled(), true);
    await page.locator('input[value="supertonic"]').check();
  }
  await page.screenshot({path: path.join(output, 'desktop-first-run.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(output, 'desktop-first-run-mobile.png'), fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'setup mobile overflow');
  await page.setViewportSize({width: 1280, height: 960});
  const invalid = await page.request.post(base + '/api/desktop/setup', {data: {engine: 'bad'}});
  assert.equal(invalid.status(), 400);
  if (runningSetup) {
    await page.locator('#start').click();
    await page.locator('#setup-progress').waitFor();
    await page.screenshot({path: path.join(output, 'desktop-model-download.png'), fullPage: true});
    const finish = Date.now() + 15 * 60 * 1000;
    let state;
    do {
      await page.waitForTimeout(1000);
      state = await (await page.request.get(base + '/api/desktop/status')).json();
      if (state.state === 'error') throw new Error(state.message);
    } while (state.state !== 'done' && Date.now() < finish);
    assert.equal(state.state, 'done', JSON.stringify(state));
    await page.locator('#open-library').waitFor();
    await page.screenshot({path: path.join(output, 'desktop-setup-complete.png'), fullPage: true});
    await page.locator('#open-library').click();
    await page.locator('.brand').waitFor();
    await page.screenshot({path: path.join(output, 'desktop-library.png'), fullPage: true});
  }
  await page.goto(base + '/docs#windows-desktop');
  await page.locator('#windows-desktop').waitFor();
  await page.locator('#windows-desktop').evaluate(element => window.scrollTo({top: element.offsetTop - 110, behavior: 'instant'}));
  await page.waitForTimeout(700);
  const anchors = await page.locator('.docs-toc a').evaluateAll(elements => elements.map(el => el.hash));
  for (const anchor of anchors) assert.equal(await page.locator(anchor).count(), 1, anchor);
  await page.screenshot({path: path.join(output, 'desktop-docs.png'), fullPage: false});
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(base + '/docs#windows-desktop');
  await page.locator('#windows-desktop').evaluate(element => window.scrollTo({top: element.offsetTop - 110, behavior: 'instant'}));
  await page.waitForTimeout(700);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'docs mobile overflow');
  await page.screenshot({path: path.join(output, 'desktop-docs-mobile.png'), fullPage: false});
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, 'console-errors.json'), JSON.stringify(errors));
  await browser.close();
  console.log('Desktop first-run / docs screenshots, mobile layout, input rejection and console: PASS' + (runningSetup ? ' (real model download)' : ''));
})().catch(error => { console.error(error); process.exit(1); });
