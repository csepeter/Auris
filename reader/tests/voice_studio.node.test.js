const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildInstruct,
  parseInstruct,
  filterCharacters,
  targetPayload,
  saveVoiceProfile,
  previewPayload,
  optionLabel,
} = require('../static/js/voice_studio.js');

test('neutral accent is omitted while saved prompt values remain parseable', () => {
  assert.deepEqual(
    parseInstruct('female, middle-aged, high pitch, british accent'),
    {
      gender: 'female',
      age: 'middle-aged',
      pitch: 'high pitch',
      accent: 'british accent',
    },
  );
  assert.equal(
    buildInstruct('female', 'middle-aged', 'high pitch', ''),
    'female, middle-aged, high pitch',
  );
  assert.equal(optionLabel(''), 'Semleges / nincs akcentus');
  assert.equal(optionLabel('young adult'), 'Fiatal felnőtt');
});

test('unchanged controls keep a legacy free-form prompt byte for byte', () => {
  const original = '  warm Hungarian voice, calm narration  ';
  const parsed = parseInstruct(original);

  assert.equal(
    buildInstruct(parsed.gender, parsed.age, parsed.pitch, parsed.accent, original),
    original,
  );
});

test('changing a structured control retains unknown prompt instructions', () => {
  const original = 'female, young adult, moderate pitch, warm Hungarian voice, calm narration';

  assert.equal(
    buildInstruct('female', 'elderly', 'moderate pitch', '', original),
    'female, elderly, moderate pitch, warm Hungarian voice, calm narration',
  );
});

test('character search is case-insensitive and keeps the original objects', () => {
  const characters = [
    { id: 1, name: 'Árvíztűrő Aladár' },
    { id: 2, name: 'Bori' },
  ];

  assert.deepEqual(filterCharacters(characters, 'ÁRVÍZ'), [characters[0]]);
  assert.deepEqual(filterCharacters(characters, '  '), characters);
});

test('profile target payload distinguishes narrator from character', () => {
  assert.deepEqual(targetPayload(7, null), { book_id: 7 });
  assert.deepEqual(targetPayload(7, 12), { book_id: 7, char_id: 12 });
});

test('profile creation saves current edits before capturing the profile', async () => {
  const events = [];
  const request = async (url, options) => {
    events.push(['request', url, JSON.parse(options.body)]);
    return { id: 4, name: 'Esti narrátor' };
  };

  const result = await saveVoiceProfile({
    bookId: 7,
    charId: null,
    name: ' Esti narrátor ',
    saveCurrent: async () => {
      events.push(['save']);
      return true;
    },
    request,
  });

  assert.deepEqual(events, [
    ['save'],
    ['request', '/api/voice-profiles', { name: 'Esti narrátor', book_id: 7 }],
  ]);
  assert.deepEqual(result, { id: 4, name: 'Esti narrátor' });
});

test('Hungarian preview text is included in preview requests', () => {
  const payload = previewPayload('female, young adult', 'Pontos átirat.');

  assert.equal(payload.instruct, 'female, young adult');
  assert.equal(payload.ref_text, 'Pontos átirat.');
  assert.match(payload.text, /árvíztűrő tükörfúrógép/i);
});

test('A custom Hungarian preview text is preserved', () => {
  const payload = previewPayload('female, young adult', '', 'Tűz és vér.');
  assert.equal(payload.text, 'Tűz és vér.');
});

test("preset voice token is replaced, not duplicated", () => {
  const { presetVoiceOf, withPresetVoice } = require("../static/js/voice_studio.js");
  const instruct = withPresetVoice("female, middle-aged, voice:anna", "berta");
  assert.equal(instruct, "female, middle-aged, voice:berta");
  assert.equal(presetVoiceOf(instruct), "berta");
  assert.equal(withPresetVoice(instruct, ""), "female, middle-aged");
});

test('instruct summary is natural Hungarian and hides the raw English prompt', () => {
  const { summarizeInstruct } = require('../static/js/voice_studio.js');
  assert.equal(summarizeInstruct('male, elderly, low pitch'), 'Férfi · idős · mély hang');
  assert.equal(
    summarizeInstruct('female, young adult, high pitch, british accent'),
    'Női · fiatal felnőtt · magas hang · brit akcentus',
  );
  assert.equal(summarizeInstruct('female, voice:anna'), 'Női · beépített hang: Anna');
  assert.equal(summarizeInstruct('male, middle-aged, warm Hungarian voice'), 'Férfi · középkorú · egyedi kiegészítéssel');
  assert.equal(summarizeInstruct('warm Hungarian voice'), 'Egyedi hangleírás');
  assert.match(summarizeInstruct(''), /^Automatikus hang/);
});

test('voice source distinguishes profile, uploaded reference and automatic voice', () => {
  const { voiceSourceOf } = require('../static/js/voice_studio.js');
  const profiles = [
    { id: 1, name: 'Mély mesélő', instruct: 'male, elderly', ref_audio_path: '/p/abc.wav', ref_audio_name: 'a.wav' },
    { id: 2, name: 'Leírt hang', instruct: 'female, teenager', ref_audio_path: null },
  ];
  assert.equal(voiceSourceOf({ instruct: 'x', ref_audio_path: '/p/abc.wav' }, profiles).kind, 'profile');
  assert.equal(voiceSourceOf({ instruct: 'x', ref_audio_path: '/p/abc.wav' }, profiles).label, 'Profil: Mély mesélő');
  assert.equal(voiceSourceOf({ instruct: 'female, teenager', ref_audio_path: null }, profiles).kind, 'profile');
  assert.equal(voiceSourceOf({ instruct: 'male', ref_audio_path: '/u/ref_3.wav' }, profiles).kind, 'reference');
  assert.equal(voiceSourceOf({ instruct: 'male, child', ref_audio_path: null }, profiles).kind, 'auto');
  assert.equal(voiceSourceOf({ instruct: '', ref_audio_path: null }, profiles).kind, 'auto');
  const narrator = { instruct: 'male, elderly', ref_audio_path: 'narrator', ref_audio_name: 'a.wav', match_by_name: true };
  assert.equal(voiceSourceOf(narrator, profiles).kind, 'profile');
});

test('characters sort by line count or Hungarian name order', () => {
  const { sortCharacters } = require('../static/js/voice_studio.js');
  const list = [
    { id: 1, name: 'Zoltán', frequency: 3 },
    { id: 2, name: 'Ádám', frequency: 10 },
    { id: 3, name: 'Béla', frequency: 3 },
  ];
  assert.deepEqual(sortCharacters(list, 'lines').map((c) => c.id), [2, 3, 1]);
  assert.deepEqual(sortCharacters(list, 'name').map((c) => c.name), ['Ádám', 'Béla', 'Zoltán']);
  assert.deepEqual(list.map((c) => c.id), [1, 2, 3], 'input is not mutated');
});

test('compare selection is capped at three profiles', () => {
  const { toggleLimited } = require('../static/js/voice_studio.js');
  let selection = [];
  for (const id of [1, 2, 3, 4]) selection = toggleLimited(selection, id, 3);
  assert.deepEqual(selection, [1, 2, 3]);
  assert.deepEqual(toggleLimited(selection, 2, 3), [1, 3]);
});

test('WAV encoder writes a valid 16-bit mono PCM file', () => {
  const { encodeWav } = require('../static/js/voice_studio.js');
  const buffer = encodeWav(Float32Array.from([0, 0.5, -0.5, 2, -2]), 24000);
  const view = new DataView(buffer);
  const text = (offset, length) => String.fromCharCode(...new Uint8Array(buffer, offset, length));
  assert.equal(buffer.byteLength, 44 + 5 * 2);
  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 4), 'WAVE');
  assert.equal(text(36, 4), 'data');
  assert.equal(view.getUint32(4, true), 36 + 10);
  assert.equal(view.getUint16(20, true), 1, 'PCM');
  assert.equal(view.getUint16(22, true), 1, 'mono');
  assert.equal(view.getUint32(24, true), 24000);
  assert.equal(view.getUint32(28, true), 48000, 'byte rate');
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 10);
  assert.equal(view.getInt16(44, true), 0);
  assert.equal(view.getInt16(46, true), 16383);
  assert.equal(view.getInt16(48, true), -16384);
  assert.equal(view.getInt16(50, true), 32767, 'clipped high');
  assert.equal(view.getInt16(52, true), -32768, 'clipped low');
});

test('silence detection and trimming cut quiet edges of a recording', () => {
  const { detectSilenceBounds, trimSamples, mixToMono } = require('../static/js/voice_studio.js');
  const rate = 1000;
  const samples = new Float32Array(2000);
  for (let i = 500; i < 1500; i += 1) samples[i] = Math.sin(i / 3) * 0.5;
  const bounds = detectSilenceBounds(samples, rate, { padding: 0.1 });
  assert.ok(Math.abs(bounds.start - 0.4) < 0.03, `start ${bounds.start}`);
  assert.ok(Math.abs(bounds.end - 1.6) < 0.03, `end ${bounds.end}`);
  assert.equal(trimSamples(samples, rate, 0.5, 1.5).length, 1000);
  assert.equal(trimSamples(samples, rate, 1.9, 5).length, 100);
  assert.deepEqual(detectSilenceBounds(new Float32Array(100), rate), { start: 0, end: 0.1 });
  assert.deepEqual(Array.from(mixToMono([Float32Array.from([1, 0]), Float32Array.from([0, 0])])), [0.5, 0]);
});

test('microphone errors are explained in Hungarian', () => {
  const { micErrorMessage, formatSeconds, previewSignature } = require('../static/js/voice_studio.js');
  assert.match(micErrorMessage({ name: 'NotAllowedError' }), /nincs engedélyezve/);
  assert.match(micErrorMessage({ name: 'NotFoundError' }), /Nem található mikrofon/);
  assert.equal(formatSeconds(65.4), '1:05');
  assert.notEqual(previewSignature('/a', { text: 'x' }, 0), previewSignature('/a', { text: 'x' }, 1));
});
