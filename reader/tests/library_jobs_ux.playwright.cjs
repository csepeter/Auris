// Library and Jobs UX checks. Requires the isolated fixture server:
//   set AURIS_QA_DATA=<scratch dir> & set AURIS_QA_PORT=17896
//   .venv\Scripts\python.exe tests\ui_fixture_server.py
// Then: node tests/library_jobs_ux.playwright.cjs  (AURIS_QA_URL overrides the base URL,
// AURIS_QA_SHOTS the screenshot folder).
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.AURIS_QA_URL || 'http://127.0.0.1:17896';
const out = process.env.AURIS_QA_SHOTS || path.join(require('node:os').tmpdir(), 'auris-library-jobs');
fs.mkdirSync(out, { recursive: true });

const BOOK_TEXT = `Csendes éjszaka ${Date.now()}\n\nElső fejezet\n\nA falu fölött lassan feljött a hold, és a kutyák elhallgattak. Anna az ablakban állt, és a hegyek felé nézett.\n\nMásodik fejezet\n\n– Holnap indulunk – mondta az apja halkan. Anna bólintott, de nem szólt semmit.`;

async function importBook(request, title, text) {
  const preview = await (await request.post(base + '/api/import/preview', {
    multipart: { file: { name: 'konyv.txt', mimeType: 'text/plain', buffer: Buffer.from(text) } },
  })).json();
  assert.ok(preview.token, JSON.stringify(preview));
  const done = await (await request.post(base + '/api/import/confirm', {
    data: { token: preview.token, title, author: 'Próba Szerző', narration_mode: 'single', allow_duplicate: true },
  })).json();
  assert.ok(done.book_id, JSON.stringify(done));
  return done.book_id;
}

function watchErrors(page, errors) {
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
}

async function noHorizontalScroll(page, label) {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, width: window.innerWidth,
  }));
  assert.ok(scroll <= width, `${label}: horizontal scroll (${scroll} > ${width})`);
}

const now = Date.now();
const sqlite = (offsetSec) => new Date(now - offsetSec * 1000).toISOString().slice(0, 19).replace('T', ' ');
function mockJobs(bookId, runningState = 'running') {
  return [
    { id: 'job-run', type: 'generate_chapter', state: runningState, message: runningState === 'running' ? '2. fejezet felolvasása' : 'Elkészült', done: runningState === 'running' ? 3 : 8, total: 8, book_id: bookId, chapter_id: 1, book_title: 'Csendes éjszaka', chapter_title: 'Első fejezet', created_at: sqlite(120), started_at: sqlite(100), finished_at: runningState === 'running' ? null : sqlite(1), result: null, cancel_requested: false },
    { id: 'job-done', type: 'export_book', state: 'complete', message: 'Elkészült', done: 2, total: 2, book_id: bookId, book_title: 'Csendes éjszaka', created_at: sqlite(4000), started_at: sqlite(3990), finished_at: sqlite(3900), result: { download: '/api/jobs/job-done/download/export', subtitle_download: '/api/jobs/job-done/download/subtitle', zip_download: 'https://example.com/api/x' } },
    { id: 'job-fail', type: 'qa_chapter', state: 'failed', message: 'A minőségellenőrzés nem sikerült', error: 'A beszédfelismerő modell nem tölthető be.', done: 1, total: 5, book_id: bookId, book_title: 'Csendes éjszaka', chapter_title: 'Második fejezet', created_at: sqlite(90000), started_at: sqlite(89990), finished_at: sqlite(89900), result: null },
    { id: 'job-int', type: 'initial_analysis', state: 'interrupted', message: 'Az alkalmazás újraindításakor megszakadt', done: 0, total: 0, book_id: bookId, book_title: 'Csendes éjszaka', created_at: sqlite(200000), started_at: null, finished_at: sqlite(199000), result: null },
    { id: 'job-wait', type: 'voice_suggestions', state: 'pending', message: 'Starting...', done: 0, total: 0, book_id: 999, book_title: null, created_at: sqlite(5), started_at: null, finished_at: null, result: null },
  ];
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, serviceWorkers: 'block' });
    const request = context.request;
    assert.equal((await (await request.get(base + '/api/qa-fixture')).json()).fixture, true);
    const books = await (await request.get(base + '/api/books')).json();
    // Always import this run's text so the duplicate check below has a match.
    const bookId = await importBook(request, 'Csendes éjszaka', BOOK_TEXT);
    if (!books.some((b) => b.title.startsWith('Az ember tragédiája')))
      await importBook(request, 'Az ember tragédiája — drámai költemény tizenöt színben', BOOK_TEXT + ' második');
    await request.post(base + '/api/settings', { data: { llm_provider: 'local', llm_model: '' } });

    /* ── Library, desktop ─────────────────────────────────────────────── */
    const page = await context.newPage();
    watchErrors(page, errors);
    await page.goto(base + '/');
    const card = page.locator(`.book-card[data-id="${bookId}"]`);
    await card.waitFor();
    await card.locator('.book-meter').first().waitFor();
    // 1. typographic cover with a deterministic hue
    const hue = await card.locator('.typo-cover').evaluate((e) => e.style.getPropertyValue('--cover-h'));
    assert.match(hue, /^\d+$/);
    const coverBox = await card.locator('.book-cover').boundingBox();
    assert.ok(Math.abs(coverBox.height / coverBox.width - 1.5) < 0.02, 'cover keeps the 2:3 aspect ratio');
    const bg = await card.locator('.typo-cover').evaluate((e) => getComputedStyle(e).backgroundImage);
    assert.match(bg, /linear-gradient/);
    // 3. welcome card hidden once books exist
    assert.equal(await page.locator('#welcome-card').isVisible(), false);
    // 6. actions: production, reader, voices, QA — all keyboard reachable links
    for (const [href, text] of [[`/production/${bookId}`, 'Produkció'], [`/reader/${bookId}`, 'Olvasás'], [`/voice-studio/${bookId}`, 'Hangok'], [`/qa/${bookId}`, 'Ellenőrzés']]) {
      const link = card.locator(`.book-actions a[href="${href}"]`);
      assert.equal(await link.count(), 1, href);
      assert.ok((await link.innerText()).includes(text), text);
      assert.equal(await link.locator('svg.icon').count(), 1, `${text} icon`);
    }
    const meters = await card.locator('.book-meters').textContent();
    assert.ok(meters.includes('Olvasás') && meters.includes('Hang'), meters);
    // Keyboard order follows the visual order: title, Olvasás, Hangok, Ellenőrzés, Produkció.
    await card.locator('.book-title a').focus();
    const order = [];
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press('Tab');
      order.push(await page.evaluate(() => document.activeElement.textContent.trim()));
    }
    assert.deepEqual(order, ['Olvasás', 'Hangok', 'Ellenőrzés', 'Produkció']);
    await page.screenshot({ path: path.join(out, 'library-1365.png') });

    // 4 + 5. import dialog: duplicate alert and language-model requirement
    await page.locator('#file-input').setInputFiles({ name: 'ujra.txt', mimeType: 'text/plain', buffer: Buffer.from(BOOK_TEXT) });
    await page.locator('#import-dialog').waitFor({ state: 'visible' });
    const books2 = await (await request.get(base + '/api/books')).json();
    const dupAlert = page.locator('#import-duplicate');
    await dupAlert.waitFor({ state: 'visible' });
    assert.ok((await dupAlert.innerText()).includes('már szerepel a könyvtárban'));
    const openHref = await dupAlert.locator('a.duplicate-open').getAttribute('href');
    assert.ok(books2.some((b) => `/reader/${b.id}` === openHref), openHref);
    assert.equal(await page.locator('#confirm-import-btn').isDisabled(), true);
    await page.locator('#import-allow-duplicate').check();
    assert.equal(await page.locator('#confirm-import-btn').isDisabled(), false);
    assert.equal(await page.locator('[name="narration-mode"][value="multi"]').isDisabled(), true);
    await page.locator('#multi-unavailable').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#multi-unavailable a').getAttribute('href'), '/settings#characters');
    assert.equal(await page.locator('[name="narration-mode"][value="single"]').isChecked(), true);
    await page.screenshot({ path: path.join(out, 'library-import-dialog-1365.png') });
    await page.keyboard.press('Escape');

    // Configured model: the option becomes available again.
    await request.post(base + '/api/settings', { data: { llm_provider: 'local', llm_base_url: 'http://127.0.0.1:1234/v1', llm_model: 'proba-modell' } });
    await page.locator('#file-input').setInputFiles({ name: 'ujra.txt', mimeType: 'text/plain', buffer: Buffer.from(BOOK_TEXT) });
    await page.locator('#import-dialog').waitFor({ state: 'visible' });
    await page.waitForFunction(() => !document.querySelector('[name="narration-mode"][value="multi"]').disabled);
    assert.equal(await page.locator('#multi-unavailable').isVisible(), false);
    await page.keyboard.press('Escape');
    await request.post(base + '/api/settings', { data: { llm_model: '' } });

    // Dark theme covers
    await page.evaluate(() => { localStorage.setItem('theme', 'night'); });
    await page.reload();
    await card.locator('.book-meter').first().waitFor();
    await page.screenshot({ path: path.join(out, 'library-1365-night.png') });
    await page.evaluate(() => { localStorage.removeItem('theme'); });

    // 2 + 3. Welcome card: only for an empty library with setup pending; dismissible.
    await page.route('**/api/books', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/setup/status', (route) => route.fulfill({ json: { completed: false } }));
    await page.reload();
    await page.locator('#welcome-card').waitFor({ state: 'visible' });
    await page.locator('#welcome-dismiss').click();
    assert.equal(await page.locator('#welcome-card').isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'add-book-btn');
    await page.reload();
    await page.locator('.empty-library').waitFor();
    assert.equal(await page.locator('#welcome-card').isVisible(), false, 'dismissal is remembered');
    await page.evaluate(() => localStorage.removeItem('auris.welcomeDismissed'));
    await page.unroute('**/api/books');
    await page.unroute('**/api/setup/status');

    /* ── Library, phone ───────────────────────────────────────────────── */
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await card.locator('.book-meter').first().waitFor();
    await noHorizontalScroll(page, 'library 390');
    const firstCardTop = (await page.locator('.book-card').first().boundingBox()).y;
    assert.ok(firstCardTop < 600, `first book visible above the fold (top ${firstCardTop})`);
    assert.equal(await page.locator('#library-state').isVisible(), false, 'filters collapsed on phone');
    await page.locator('#library-filter-toggle').click();
    assert.equal(await page.locator('#library-filter-toggle').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('#library-state').isVisible(), true);
    await page.screenshot({ path: path.join(out, 'library-390-filters.png') });
    await page.locator('#library-filter-toggle').click();
    await page.screenshot({ path: path.join(out, 'library-390.png') });
    await page.screenshot({ path: path.join(out, 'library-390-full.png'), fullPage: true });

    /* ── Jobs with a real export job ──────────────────────────────────── */
    await page.setViewportSize({ width: 1365, height: 900 });
    const started = await request.post(base + `/api/books/${bookId}/export/chapterwise`, {
      data: { audio_fmt: 'mp3', sub_fmt: 'none', chapters: 'all' },
    });
    assert.ok(started.ok() || started.status() === 409, `export start: ${started.status()} ${await started.text()}`);
    await page.goto(base + '/jobs');
    await page.locator('.job-item').first().waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.job-item')].some((n) => /state-(complete|failed)/.test(n.className)), null, { timeout: 60000 });
    const realText = await page.locator('.job-item').first().innerText();
    assert.ok(/Könyv exportálása|Fejezet exportálása/.test(realText), realText);
    assert.ok(!/Elkészült\s*·\s*Elkészült/.test(await page.locator('#jobs-list').innerText()));
    await page.screenshot({ path: path.join(out, 'jobs-real-1365.png'), fullPage: true });

    /* ── Jobs with mocked states: rendering, filters, incremental updates ── */
    let runningState = 'running';
    await page.route('**/api/jobs', (route) => route.fulfill({ json: mockJobs(bookId, runningState) }));
    await page.route('**/api/jobs/job-run/cancel', (route) => route.fulfill({ json: { ok: true } }));
    await page.reload();
    await page.locator('[data-job-id="job-fail"]').waitFor();
    await page.locator('[data-filter="all"]').click();
    const run = page.locator('[data-job-id="job-run"]');
    assert.ok((await run.innerText()).includes('Fejezethang generálása'));
    assert.equal(await run.locator('.job-title a').getAttribute('href'), `/reader/${bookId}`);
    assert.ok((await run.locator('.job-progress-text').innerText()).includes('3 / 8'));
    assert.equal(await run.locator('time[datetime]').first().getAttribute('title') !== '', true);
    assert.ok((await run.locator('.job-times').innerText()).includes('Időtartam'));
    const done = page.locator('[data-job-id="job-done"]');
    assert.equal(await done.locator('.job-message').isVisible(), false, 'message equal to the state is skipped');
    assert.deepEqual(await done.locator('.job-actions a').evaluateAll((a) => a.map((x) => x.getAttribute('href'))),
      ['/api/jobs/job-done/download/export', '/api/jobs/job-done/download/subtitle']);
    assert.ok((await page.locator('[data-job-id="job-fail"] .job-error').innerText()).includes('beszédfelismerő'));
    assert.equal(await page.locator('[data-job-id="job-fail"] button[data-action="resume"]').count(), 1);
    assert.equal(await page.locator('[data-job-id="job-int"] button[data-action="resume"]').count(), 1);
    assert.equal(await page.locator('[data-job-id="job-wait"] .job-message').innerText(), 'Indítás…');
    assert.ok((await page.locator('[data-job-id="job-wait"] .job-title').innerText()).includes('Törölt könyv'));
    await page.screenshot({ path: path.join(out, 'jobs-1365.png'), fullPage: true });

    // Incremental: same DOM node and focus survive a poll; state change is announced.
    await run.evaluate((n) => { n.__marker = 'kept'; });
    await run.locator('button[data-action="cancel"]').focus();
    await page.evaluate(() => loadJobs());
    assert.equal(await run.evaluate((n) => n.__marker), 'kept');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.action), 'cancel');
    runningState = 'complete';
    await page.evaluate(() => loadJobs());
    await page.waitForFunction(() => document.getElementById('jobs-live').textContent.includes('elkészült'));
    assert.equal(await run.evaluate((n) => n.__marker), 'kept');
    assert.equal(await run.locator('.job-message').isVisible(), false);

    // Filters
    await page.locator('[data-filter="problem"]').click();
    assert.equal(await page.locator('[data-filter="problem"]').getAttribute('aria-pressed'), 'true');
    const visible = await page.locator('.job-item:visible').evaluateAll((n) => n.map((x) => x.dataset.jobId));
    assert.deepEqual(visible.sort(), ['job-fail', 'job-int']);
    await page.locator('[data-filter="active"]').click();
    assert.deepEqual(await page.locator('.job-item:visible').evaluateAll((n) => n.map((x) => x.dataset.jobId)), ['job-wait']);
    await page.locator('[data-filter="all"]').click();

    /* ── Jobs, phone ──────────────────────────────────────────────────── */
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(200);
    await noHorizontalScroll(page, 'jobs 390');
    await page.locator('[data-filter="active"]').focus();
    await page.screenshot({ path: path.join(out, 'jobs-390.png') });
    await page.screenshot({ path: path.join(out, 'jobs-390-full.png'), fullPage: true });

    assert.deepEqual(errors, []);
    console.log(`Library and Jobs UX checks passed; screenshots in ${out}; console errors 0`);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
