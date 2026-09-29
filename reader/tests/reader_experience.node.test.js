const test = require('node:test');
const assert = require('node:assert/strict');

const {
  estimateRemainingAudio,
  playbackResumeTarget,
  seekTarget,
  wordIndexFromMediaTime,
} = require('../static/js/listening.js');

test('word highlighting follows media currentTime without applying playback speed twice', () => {
  assert.equal(wordIndexFromMediaTime(4, 1, 12, 6), 1);
  assert.equal(wordIndexFromMediaTime(13, 1, 12, 6), 5);
});

test('15 second seek crosses cached segments and keeps the offset inside the target', () => {
  const cached = [
    { has_audio: true, duration_sec: 10 },
    { has_audio: true, duration_sec: 20 },
    { has_audio: true, duration_sec: 8 },
  ];

  assert.deepEqual(seekTarget(cached, 0, 8, 15), {
    segmentIndex: 1,
    offsetSec: 13,
  });
  assert.deepEqual(seekTarget(cached, 1, 12, -15), {
    segmentIndex: 0,
    offsetSec: 7,
  });
});

test('seek stops at the edge of available cached audio', () => {
  const partlyCached = [
    { has_audio: true, duration_sec: 10 },
    { has_audio: false, duration_sec: null },
  ];

  assert.deepEqual(seekTarget(partlyCached, 0, 8, 15), {
    segmentIndex: 0,
    offsetSec: 10,
  });
});

test('remaining time estimates uncached text and accounts for playback speed', () => {
  const segments = [
    { has_audio: true, duration_sec: 10, text: 'Egy ketto harom.' },
    { has_audio: false, duration_sec: null, text: 'egy ketto harom negy ot hat het nyolc kilenc tiz' },
    { has_audio: true, duration_sec: 20, text: 'Vege.' },
  ];

  assert.deepEqual(estimateRemainingAudio(segments, 0, 2, 2), {
    seconds: 16,
    estimated: true,
  });
});

test('resume advances to a pending segment before replaying an ended segment', () => {
  assert.deepEqual(playbackResumeTarget(3, 2, 2, 9.9), {
    segmentIndex: 3,
    offsetSec: 0,
  });
  assert.deepEqual(playbackResumeTarget(-1, 2, 2, 4.25), {
    segmentIndex: 2,
    offsetSec: 4.25,
  });
});

test('syllable weights keep long compounds and sentence ends longer', () => {
  const { wordWeights, wordIndexFromWeights, wordIndexFromTimings } = require('../static/js/listening.js');
  const weights = wordWeights(['A', 'tükörfúrógép', 'működik.']);
  assert.ok(weights[1] > weights[0] * 3);
  assert.ok(weights[2] > 3);
  assert.equal(wordIndexFromWeights(0.1, 10, weights), 0);
  assert.equal(wordIndexFromWeights(9.9, 10, weights), 2);
  assert.equal(wordIndexFromTimings(1.2, [[0, 0.5], [0.5, 1.1], [1.1, 2]]), 2);
  assert.equal(wordIndexFromTimings(0.2, [[0, 0.5], [0.5, 1.1]]), 0);
});
