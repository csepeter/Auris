const test = require('node:test');
const assert = require('node:assert/strict');

const {
  hashString,
  coverSpec,
  llmConfigured,
  bookStats,
  activeJobsByBook,
} = require('../static/js/library.js');
const {
  jobState,
  jobStateLabel,
  jobTypeLabel,
  jobMessage,
  jobFilterMatches,
  parseServerTime,
  relativeTime,
  formatDuration,
  jobDuration,
  jobPercent,
  jobDownloads,
  jobActions,
} = require('../static/js/jobs.js');

test('typographic cover is deterministic per title and spreads hues', () => {
  assert.equal(hashString('A kis herceg'), hashString('A kis herceg'));
  assert.deepEqual(coverSpec('Egri csillagok'), coverSpec('Egri csillagok'));
  assert.deepEqual(coverSpec('Egri csillagok'), coverSpec('  egri CSILLAGOK '));
  const hues = new Set(['Egri csillagok', 'A Pál utcai fiúk', 'Az ember tragédiája', 'Légy jó mindhalálig', 'Abigél']
    .map((t) => coverSpec(t).hue));
  assert.ok(hues.size >= 4, 'different titles get different colours');
  for (const t of ['', 'X', 'Nagyon hosszú cím, amely több sorba tördelődik a borítón is, mert ilyen is van']) {
    const spec = coverSpec(t);
    assert.ok(spec.hue >= 0 && spec.hue < 360);
    assert.ok([0, 1, 2, 3].includes(spec.variant));
  }
  assert.equal(coverSpec('Abigél').size, 'l');
  assert.equal(coverSpec('A kőszívű ember fiai és unokái').size, 'm');
  assert.equal(coverSpec('x'.repeat(61)).size, 'xs');
});

test('language model check mirrors the import confirmation rule', () => {
  assert.equal(llmConfigured(null), null);
  assert.equal(llmConfigured({ llm_provider: 'local', llm_base_url: 'http://127.0.0.1:1234/v1', llm_model: '' }), false);
  assert.equal(llmConfigured({ llm_provider: 'local', llm_base_url: 'http://127.0.0.1:1234/v1', llm_model: 'qwen' }), true);
  assert.equal(llmConfigured({ llm_provider: 'local', llm_base_url: '', llm_model: 'qwen' }), false);
  assert.equal(llmConfigured({ llm_provider: 'openai', openai_api_key: '', openai_model: 'gpt' }), false);
  assert.equal(llmConfigured({ llm_provider: 'openai', openai_api_key: '••••••••', openai_model: '' }), false);
  assert.equal(llmConfigured({ llm_provider: 'openai', openai_api_key: '••••••••', openai_model: 'gpt' }), true);
});

test('book stats combine reading position and chapter audio readiness', () => {
  const chapters = [
    { id: 10, audio_total: 4, audio_ready: 4 },
    { id: 11, audio_total: 4, audio_ready: 2 },
    { id: 12, audio_total: 0, audio_ready: 0 },
    { id: 13, audio_total: 0, audio_ready: 0 },
  ];
  const s = bookStats({ progress_chapter_id: 11, total_chapters: 4 }, chapters);
  assert.equal(s.chapters, 4);
  assert.equal(s.readIndex, 2);
  assert.equal(s.readPct, 50);
  assert.equal(s.audioDone, 1);
  assert.equal(s.audioPartial, 1);
  assert.equal(s.audioPct, 38);
  assert.equal(bookStats({ reading_state: 'finished' }, chapters).readPct, 100);
  assert.equal(bookStats({ total_chapters: 3 }, null).chapters, 3);
});

test('active jobs are keyed by book, newest first', () => {
  const map = activeJobsByBook([
    { id: 'a', book_id: 1, state: 'running' },
    { id: 'b', book_id: 1, state: 'pending' },
    { id: 'c', book_id: 2, state: 'complete' },
    { id: 'd', book_id: null, state: 'running' },
  ]);
  assert.deepEqual([...map.keys()], [1]);
  assert.equal(map.get(1).id, 'a');
});

test('job labels are Hungarian and messages never repeat the state', () => {
  assert.equal(jobTypeLabel('generate_chapter'), 'Fejezethang generálása');
  assert.equal(jobTypeLabel('qa_chapter'), 'Minőségellenőrzés');
  assert.equal(jobTypeLabel('voice_suggestions'), 'Hangjavaslatok');
  assert.equal(jobTypeLabel('speaker_review'), 'Beszélők ellenőrzése');
  assert.equal(jobTypeLabel('ismeretlen'), 'Feladat');
  assert.equal(jobStateLabel('interrupted'), 'Megszakadt');
  assert.equal(jobMessage({ state: 'complete', message: 'Elkészült' }), '');
  assert.equal(jobMessage({ state: 'complete', message: 'elkészült.' }), '');
  assert.equal(jobMessage({ state: 'pending', message: 'Starting...' }), 'Indítás…');
  assert.equal(jobMessage({ state: 'running', message: '3. fejezet felolvasása' }), '3. fejezet felolvasása');
  assert.equal(jobState({ state: 'running', cancel_requested: true }), 'cancelling');
});

test('filters group jobs into active, done and problem', () => {
  assert.ok(jobFilterMatches({ state: 'running' }, 'active'));
  assert.ok(jobFilterMatches({ state: 'pending' }, 'active'));
  assert.ok(jobFilterMatches({ state: 'complete' }, 'done'));
  for (const state of ['failed', 'interrupted', 'cancelled']) assert.ok(jobFilterMatches({ state }, 'problem'));
  assert.ok(!jobFilterMatches({ state: 'complete' }, 'active'));
  assert.ok(jobFilterMatches({ state: 'failed' }, 'all'));
});

test('server times are UTC and render as Hungarian relative times', () => {
  const t = parseServerTime('2026-09-29 10:00:00');
  assert.equal(t.toISOString(), '2026-09-29T10:00:00.000Z');
  assert.equal(parseServerTime(''), null);
  assert.equal(parseServerTime('nem dátum'), null);
  const now = new Date('2026-09-29T12:30:00Z');
  assert.equal(relativeTime(new Date('2026-09-29T12:29:50Z'), now), 'az imént');
  assert.equal(relativeTime(new Date('2026-09-29T12:25:00Z'), now), '5 perce');
  assert.equal(relativeTime(t, now), '3 órája');
  assert.equal(relativeTime(new Date('2026-09-28T10:00:00Z'), now), 'tegnap');
  assert.equal(formatDuration(42_000), '42 mp');
  assert.equal(formatDuration(125_000), '2 p 5 mp');
  assert.equal(formatDuration(3_900_000), '1 ó 5 p');
  assert.equal(jobDuration({ state: 'complete', started_at: '2026-09-29 10:00:00', finished_at: '2026-09-29 10:01:30' }), '1 p 30 mp');
  assert.equal(jobDuration({ state: 'running', started_at: '2026-09-29 12:29:00' }, now), '1 p 0 mp');
  assert.equal(jobDuration({ state: 'pending' }), '');
});

test('progress, downloads and actions follow the job state', () => {
  assert.equal(jobPercent({ done: 3, total: 4 }), 75);
  assert.equal(jobPercent({ done: 9, total: 4 }), 100);
  assert.equal(jobPercent({ done: 0, total: 0 }), 0);
  const links = jobDownloads({
    download: '/api/jobs/x/download/export',
    audio_download: '/api/jobs/x/download/audio',
    subtitle_download: null,
    zip_download: 'https://evil.example/api/x',
    other: '/api/nope',
  }, 'http://127.0.0.1:5000');
  assert.deepEqual(links.map((l) => l.key), ['download', 'audio_download']);
  assert.equal(links[1].label, 'Hang letöltése');
  assert.deepEqual(jobDownloads(null), []);
  assert.deepEqual(jobActions({ state: 'running' }), { cancel: true, resume: false });
  assert.deepEqual(jobActions({ state: 'running', cancel_requested: true }), { cancel: false, resume: false });
  assert.deepEqual(jobActions({ state: 'interrupted' }), { cancel: false, resume: true });
  assert.deepEqual(jobActions({ state: 'complete' }), { cancel: false, resume: false });
});
