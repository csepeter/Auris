// Voice Studio UX check. Requires the isolated ui_fixture_server.py and local Playwright:
//   set AURIS_QA_DATA=<dir> & set AURIS_QA_PORT=17895 & .venv\Scripts\python.exe tests\ui_fixture_server.py
//   set AURIS_QA_URL=http://127.0.0.1:17895 & set AURIS_QA_DATA=<dir> & node tests\voice_studio_ux.playwright.cjs
// Characters are inserted straight into the fixture database (AURIS_QA_DATA\reader.db).
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const base = process.env.AURIS_QA_URL || 'http://127.0.0.1:17895';
const qaData = process.env.AURIS_QA_DATA;
const python = process.env.AURIS_QA_PYTHON || path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
const out = process.env.AURIS_QA_SHOTS || path.join(os.tmpdir(), 'auris-voice-studio-qa');
fs.mkdirSync(out, { recursive: true });

function toneWav(seconds = 2, rate = 24000) {
  const n = Math.round(seconds * rate);
  const buffer = Buffer.alloc(44 + n * 2);
  buffer.write('RIFF', 0); buffer.writeUInt32LE(36 + n * 2, 4); buffer.write('WAVE', 8);
  buffer.write('fmt ', 12); buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36); buffer.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i += 1) buffer.writeInt16LE(Math.round(Math.sin(i / 8) * 8000), 44 + i * 2);
  return buffer;
}

function insertCharacters(bookId) {
  assert.ok(qaData, 'AURIS_QA_DATA must point at the fixture data folder');
  const script = `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
rows = [
  (${bookId}, 'Kovács Aladár', 'male', 42, 'male, elderly, low pitch', '#9ecbff'),
  (${bookId}, 'Bori', 'female', 7, 'female, teenager, high pitch', '#ffc6e0'),
  (${bookId}, 'Éva', 'female', 15, 'female, young adult, moderate pitch, british accent', '#c7f0c2'),
]
db.executemany('INSERT OR IGNORE INTO characters(book_id,name,gender,frequency,instruct,color_hex) VALUES(?,?,?,?,?,?)', rows)
db.commit()
`;
  execFileSync(python, ['-c', script, path.join(qaData, 'reader.db')]);
}

async function noHorizontalScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

(async () => {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  });
  const errors = [];
  const watch = (page) => {
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  };
  try {
    const api = await browser.newContext();
    assert.equal((await (await api.request.get(`${base}/api/qa-fixture`)).json()).fixture, true);
    const preview = await (await api.request.post(`${base}/api/import/preview`, { multipart: {
      file: { name: 'hangstudio.txt', mimeType: 'text/plain', buffer: Buffer.from(`Hangstúdió próba ${Date.now()}\n\nElső fejezet\n\n– Jó reggelt! – mondta Kovács Aladár.\n– Szia! – felelte Bori.`) },
    } })).json();
    const imported = await (await api.request.post(`${base}/api/import/confirm`, { data: { token: preview.token, title: 'Hangstúdió próba', narration_mode: 'single' } })).json();
    const bookId = imported.book_id || imported.id;
    assert.ok(bookId, JSON.stringify(imported));
    insertCharacters(bookId);
    const characters = await (await api.request.get(`${base}/api/books/${bookId}/characters`)).json();
    const byName = Object.fromEntries(characters.map((c) => [c.name, c]));
    const aladar = byName['Kovács Aladár'];
    const bori = byName.Bori;
    const eva = byName['Éva'];
    const narratorProfile = await (await api.request.post(`${base}/api/voice-profiles`, { data: { name: `Esti mesélő ${bookId}`, book_id: bookId } })).json();
    const oldProfile = await (await api.request.post(`${base}/api/voice-profiles`, { data: { name: `Öreg hang ${bookId}`, book_id: bookId, char_id: aladar.id } })).json();
    assert.ok(narratorProfile.id && oldProfile.id);

    // ── Desktop ────────────────────────────────────────────────────────────
    const context = await browser.newContext({ viewport: { width: 1365, height: 1000 }, permissions: ['microphone'], acceptDownloads: true });
    const page = await context.newPage();
    watch(page);
    const previewPosts = [];
    await page.route('**/preview', async (route) => {
      previewPosts.push(route.request().url());
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });
    await page.goto(`${base}/voice-studio/${bookId}`);
    const card = page.locator(`#card-${aladar.id}`);
    await card.waitFor();

    // Human-readable summary, raw English prompt hidden behind a disclosure.
    assert.equal(await page.locator(`#sum-${aladar.id}`).innerText(), 'Férfi · idős · mély hang');
    assert.equal(await page.locator(`#ins-${aladar.id}`).isVisible(), false);
    assert.match(await card.locator('.voice-source-badge').innerText(), /Profil: Öreg hang/);
    assert.match(await page.locator(`#card-${bori.id} .voice-source-badge`).innerText(), /Automatikus hang/);
    assert.match(await card.locator('.line-count').innerText(), /42\s*megszólalás/);
    assert.match(await page.locator('#narrator-voice-summary').innerText(), /^Férfi/);

    // Sorting.
    const order = () => page.locator('#char-list .char-title').allInnerTexts();
    assert.deepEqual(await order(), ['Kovács Aladár', 'Éva', 'Bori']);
    await page.selectOption('#character-sort', 'name');
    assert.deepEqual(await order(), ['Bori', 'Éva', 'Kovács Aladár']);
    await page.selectOption('#character-sort', 'lines');

    // Visible primary preview with busy state and inline player.
    const previewButton = card.locator('[data-action=preview]');
    await previewButton.click();
    await page.waitForFunction((id) => document.querySelector(`#card-${id} [data-action=preview]`).getAttribute('aria-busy') === 'true', aladar.id);
    assert.match(await previewButton.innerText(), /Készül/);
    const player = page.locator(`[data-player="char-${aladar.id}"]`);
    await player.waitFor({ state: 'visible' });
    await page.waitForFunction((id) => !document.querySelector(`#card-${id} [data-action=preview]`).hasAttribute('aria-busy'), aladar.id);
    assert.match(await previewButton.innerText(), /Meghallgatás/);
    assert.equal(previewPosts.length, 1);
    await page.screenshot({ path: path.join(out, '01-desktop-preview-player.png'), fullPage: false });

    // "Próba mentése" reuses the audio that was just generated.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      player.locator('[data-player-action=download]').click(),
    ]);
    assert.match(download.suggestedFilename(), /\.wav$/);
    assert.equal(previewPosts.length, 1, 'download must not synthesize again');

    // Sample text play button is a real button that speaks with the narrator.
    await page.locator('#sample-play-btn').click();
    await page.locator('[data-player="sample"]').waitFor({ state: 'visible' });
    assert.ok(previewPosts.some((url) => url.includes('/characters/narrator/preview')));

    // Settings toggle with aria-expanded; technical description collapsed.
    const toggle = card.locator('[data-action=toggle]');
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    await page.locator(`#body-${aladar.id}`).waitFor({ state: 'visible' });

    // Reference upload + playback of the uploaded reference.
    await card.locator(`#ref-text-${aladar.id}`).fill('Jó reggelt, kedves olvasó.');
    await card.locator('[data-file-action=ref-audio]').setInputFiles({ name: 'aladar.wav', mimeType: 'audio/wav', buffer: toneWav() });
    await page.locator(`#ref-status-${aladar.id}:not(.hidden)`).waitFor();
    assert.match(await card.locator('.voice-source-badge').innerText(), /Saját referenciahang/);
    await card.locator('[data-action=play-ref]').click();
    await page.locator(`[data-player="ref-${aladar.id}"]`).waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.getElementById('preview-audio').readyState >= 1);
    assert.equal(await page.locator(`[data-player="ref-${aladar.id}"] [data-player-action=download]`).isVisible(), false);
    await page.screenshot({ path: path.join(out, '02-desktop-character-settings.png'), fullPage: false });

    // Microphone recording (fake device) → trim → upload as narrator reference.
    await page.locator('#narrator-record-btn').click();
    const recorder = page.locator('#rec-narrator');
    await recorder.waitFor({ state: 'visible' });
    assert.equal(await recorder.locator('[data-rec-action=stop]').isVisible(), false, 'stop is hidden before recording');
    await recorder.locator('[data-rec-action=insert-sample]').click();
    assert.match(await page.locator('#narrator-ref-text').inputValue(), /árvíztűrő/);
    await recorder.locator('[data-rec-action=start]').click();
    await recorder.locator('[data-rec-action=stop]').waitFor({ state: 'visible' });
    await page.waitForTimeout(2200);
    await recorder.locator('[data-rec-action=stop]').click();
    await recorder.locator('.recorder-review').waitFor({ state: 'visible', timeout: 10000 });
    const endMax = Number(await recorder.locator('[data-trim=end]').getAttribute('max'));
    assert.ok(endMax > 1.5 && endMax <= 30, `recording length ${endMax}`);
    await recorder.locator('[data-trim=start]').fill('0.2');
    await recorder.locator('[data-trim=start]').dispatchEvent('change');
    assert.match(await recorder.locator('[data-trim-out=start]').innerText(), /0,2 s/);
    await page.screenshot({ path: path.join(out, '03-desktop-recorder.png'), fullPage: false });
    await recorder.locator('[data-rec-action=upload]').click();
    await page.locator('#narrator-ref-status:not(.hidden)').waitFor();
    assert.match(await page.locator('#narrator-ref-name').innerText(), /^mikrofon-felvetel-\d+\.wav$/);
    const refResponse = await page.request.get(`${base}/api/books/${bookId}/narrator-ref-audio`);
    const wav = Buffer.from(await refResponse.body());
    assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
    assert.equal(wav.readUInt16LE(22), 1, 'mono');
    assert.equal(wav.readUInt16LE(34), 16, '16-bit');
    const seconds = wav.readUInt32LE(40) / (wav.readUInt32LE(24) * 2);
    assert.ok(seconds > 1 && seconds < endMax, `trimmed length ${seconds}`);
    assert.equal(await recorder.isVisible(), false);

    // Compare drawer: current voice + two profiles, play each, choose one.
    await page.locator('#narrator-compare-btn').click();
    const drawer = page.locator('#compare-drawer');
    await drawer.waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.getElementById('compare-drawer').contains(document.activeElement)), true);
    // Start from a clean selection (earlier runs may have left more profiles).
    const preselected = await drawer.locator('[data-compare-profile]:checked')
      .evaluateAll((inputs) => inputs.map((input) => input.dataset.compareProfile));
    for (const id of preselected) await drawer.locator(`[data-compare-profile="${id}"]`).uncheck();
    await drawer.locator(`[data-compare-profile="${narratorProfile.id}"]`).check();
    await drawer.locator(`[data-compare-profile="${oldProfile.id}"]`).check();
    const cards = drawer.locator('.compare-card');
    assert.equal(await cards.count(), 3);
    const before = previewPosts.length;
    for (let i = 0; i < 3; i += 1) {
      await cards.nth(i).locator('[data-compare-action=play]').click();
      await cards.nth(i).locator('[data-player]').waitFor({ state: 'visible' });
    }
    assert.equal(previewPosts.filter((url) => url.includes('/voice-profiles/')).length, 2);
    assert.ok(previewPosts.length >= before + 3);
    await page.screenshot({ path: path.join(out, '04-desktop-compare.png'), fullPage: false });
    await drawer.locator(`[data-compare-id="${narratorProfile.id}"] [data-compare-action=choose]`).click();
    await drawer.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'narrator-compare-btn');
    assert.match(await page.locator('#narrator-source-badge').innerText(), /Profil: Esti mesélő/);

    // Bulk apply to selected characters with an accessible confirmation.
    await page.locator(`#card-${bori.id} [data-select-char]`).check();
    await page.locator(`#card-${eva.id} [data-select-char]`).check();
    assert.match(await page.locator('#bulk-count').innerText(), /2 szereplő kijelölve/);
    await page.selectOption('#bulk-profile', String(oldProfile.id));
    await page.locator('#bulk-apply').click();
    await page.locator('#vs-confirm').waitFor({ state: 'visible' });
    await page.screenshot({ path: path.join(out, '05-desktop-bulk-confirm.png'), fullPage: false });
    await page.locator('#vs-confirm [data-confirm=ok]').click();
    await page.waitForFunction((id) => /Profil: Öreg hang/.test(document.querySelector(`#card-${id} .voice-source-badge`)?.textContent || ''), bori.id);
    assert.match(await page.locator(`#card-${eva.id} .voice-source-badge`).innerText(), /Profil: Öreg hang/);

    // Profile delete confirmation can be cancelled with Escape.
    await page.selectOption('#profile-narrator', String(narratorProfile.id));
    await page.locator('#narrator-section button', { hasText: 'Törlés' }).first().click();
    await page.locator('#vs-confirm').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await page.locator('#vs-confirm').waitFor({ state: 'hidden' });
    const profiles = await (await page.request.get(`${base}/api/voice-profiles`)).json();
    assert.ok(profiles.some((p) => p.id === narratorProfile.id), 'cancelled delete must keep the profile');

    // Every interactive control has an accessible name.
    const unnamed = await page.evaluate(() => [...document.querySelectorAll('main button, main select, main input, main textarea')]
      .filter((el) => el.offsetParent !== null && el.type !== 'file')
      .filter((el) => !(el.getAttribute('aria-label') || el.labels?.length || el.textContent.trim() || el.getAttribute('title')))
      .map((el) => el.outerHTML.slice(0, 120)));
    assert.deepEqual(unnamed, []);
    assert.ok(await noHorizontalScroll(page));
    await page.screenshot({ path: path.join(out, '06-desktop-full.png'), fullPage: true });
    await context.close();

    // ── Mobile 390px ───────────────────────────────────────────────────────
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, permissions: ['microphone'] });
    const phone = await mobile.newPage();
    watch(phone);
    await phone.goto(`${base}/voice-studio/${bookId}`);
    await phone.locator(`#card-${aladar.id}`).waitFor();
    assert.ok(await noHorizontalScroll(phone), 'no horizontal scroll at 390px');
    await phone.locator(`#card-${aladar.id} [data-action=preview]`).click();
    await phone.locator(`[data-player="char-${aladar.id}"]`).waitFor({ state: 'visible' });
    await phone.locator(`#card-${aladar.id}`).scrollIntoViewIfNeeded();
    await phone.screenshot({ path: path.join(out, '07-mobile-card.png'), fullPage: false });
    await phone.locator(`#card-${aladar.id} [data-action=toggle]`).click();
    await phone.locator(`#card-${aladar.id} [data-action=record]`).click();
    await phone.locator(`#rec-${aladar.id}`).waitFor({ state: 'visible' });
    assert.ok(await noHorizontalScroll(phone), 'no horizontal scroll with open settings and recorder');
    await phone.screenshot({ path: path.join(out, '08-mobile-settings.png'), fullPage: true });
    await phone.locator(`#card-${aladar.id} [data-action=compare]`).click();
    await phone.locator('#compare-drawer').waitFor({ state: 'visible' });
    assert.ok(await noHorizontalScroll(phone));
    await phone.screenshot({ path: path.join(out, '09-mobile-compare.png'), fullPage: false });
    await phone.keyboard.press('Escape');
    await phone.locator('#compare-drawer').waitFor({ state: 'hidden' });
    assert.equal(await phone.evaluate(() => document.activeElement?.dataset?.action), 'compare');
    await mobile.close();

    // ── Microphone permission denied ───────────────────────────────────────
    const denied = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    await denied.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
    });
    const deniedPage = await denied.newPage();
    watch(deniedPage);
    await deniedPage.goto(`${base}/voice-studio/${bookId}`);
    await deniedPage.locator('#narrator-record-btn').click();
    await deniedPage.locator('#rec-narrator [data-rec-action=start]').click();
    await deniedPage.locator('#rec-narrator .recorder-status.is-error').waitFor();
    assert.match(await deniedPage.locator('#rec-narrator .recorder-status').innerText(), /nincs engedélyezve/);
    await deniedPage.screenshot({ path: path.join(out, '10-mic-denied.png'), fullPage: false });
    await denied.close();

    assert.deepEqual(errors, []);
    console.log(`PASS: voice studio UX (screens in ${out}), console errors: 0`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
