(function exposeListeningHelpers(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AurisListening = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function buildListeningHelpers() {
  const DEFAULT_SECONDS_PER_WORD = 0.4;

  function finiteDuration(segment) {
    const duration = Number(segment?.duration_sec);
    return Number.isFinite(duration) && duration > 0 ? duration : 0;
  }

  function wordCount(text) {
    const words = String(text || '').trim().match(/\S+/g);
    return words ? words.length : 0;
  }

  function wordIndexFromMediaTime(currentTime, startTime, durationSec, words) {
    const count = Math.max(0, Number.parseInt(words, 10) || 0);
    if (!count) return -1;
    const duration = Number(durationSec);
    if (!Number.isFinite(duration) || duration <= 0) return 0;
    const elapsed = Math.max(0, Number(currentTime) - Number(startTime || 0));
    return Math.min(Math.floor((elapsed / duration) * count), count - 1);
  }

  const VOWELS = new Set('aáeéiíoóöőuúüűyAÁEÉIÍOÓÖŐUÚÜŰY');
  const PAUSE_AFTER = { ',': 0.8, ';': 1, ':': 1, '.': 1.6, '!': 1.6, '?': 1.6, '…': 2, '–': 0.6, '—': 0.6 };

  // Same weighting as core/alignment.py: syllables plus punctuation pauses,
  // so long Hungarian compounds and sentence ends do not make the
  // highlight drift ahead.
  function wordWeights(words) {
    return (words || []).map((word) => {
      let count = 0;
      let previous = false;
      let digits = 0;
      for (const ch of String(word)) {
        const vowel = VOWELS.has(ch);
        if (vowel && !previous) count += 1;
        previous = vowel;
        if (ch >= '0' && ch <= '9') digits += 1;
      }
      count += digits * 2;
      if (!count && /[\p{L}\p{N}]/u.test(String(word))) count = 1;
      const tail = String(word).replace(/["'”’»«)]+$/u, '');
      const last = tail.slice(-1);
      return Math.max(count + (PAUSE_AFTER[last] || 0), 0.2);
    });
  }

  function wordIndexFromWeights(currentTime, durationSec, weights) {
    const list = Array.isArray(weights) ? weights : [];
    if (!list.length) return -1;
    const duration = Number(durationSec);
    if (!Number.isFinite(duration) || duration <= 0) return 0;
    const total = list.reduce((sum, w) => sum + w, 0) || 1;
    const target = (Math.max(0, Number(currentTime) || 0) / duration) * total;
    let cursor = 0;
    for (let i = 0; i < list.length; i += 1) {
      cursor += list[i];
      if (target < cursor) return i;
    }
    return list.length - 1;
  }

  // Measured [start, end] pairs per displayed word (from speech recognition).
  function wordIndexFromTimings(currentTime, timings) {
    const list = Array.isArray(timings) ? timings : [];
    if (!list.length) return -1;
    const t = Math.max(0, Number(currentTime) || 0);
    let index = 0;
    for (let i = 0; i < list.length; i += 1) {
      if (Number(list[i][0]) <= t) index = i;
      else break;
    }
    return index;
  }

  function seekTarget(segments, currentIndex, currentTime, deltaSec) {
    const list = Array.isArray(segments) ? segments : [];
    if (!list.length) return { segmentIndex: 0, offsetSec: 0 };

    let index = Math.max(0, Math.min(Number.parseInt(currentIndex, 10) || 0, list.length - 1));
    let offset = Math.max(0, Number(currentTime) || 0);
    let remaining = Math.abs(Number(deltaSec) || 0);
    const direction = Number(deltaSec) < 0 ? -1 : 1;

    if (direction > 0) {
      while (remaining > 0) {
        const duration = finiteDuration(list[index]);
        const available = Math.max(0, duration - offset);
        if (remaining <= available) {
          offset += remaining;
          remaining = 0;
          break;
        }
        remaining -= available;
        const next = index + 1;
        if (next >= list.length || !list[next]?.has_audio || !finiteDuration(list[next])) {
          offset = duration;
          break;
        }
        index = next;
        offset = 0;
      }
    } else {
      while (remaining > 0) {
        if (remaining <= offset) {
          offset -= remaining;
          remaining = 0;
          break;
        }
        remaining -= offset;
        const previous = index - 1;
        if (previous < 0 || !list[previous]?.has_audio || !finiteDuration(list[previous])) {
          offset = 0;
          break;
        }
        index = previous;
        offset = finiteDuration(list[index]);
      }
    }

    return {
      segmentIndex: index,
      offsetSec: Math.max(0, Math.round(offset * 1000) / 1000),
    };
  }

  function estimateRemainingAudio(
    segments,
    currentIndex,
    currentTime,
    playbackRate = 1,
    secondsPerWord = DEFAULT_SECONDS_PER_WORD,
  ) {
    const list = Array.isArray(segments) ? segments : [];
    const start = Math.max(0, Number.parseInt(currentIndex, 10) || 0);
    const rate = Math.max(0.1, Number(playbackRate) || 1);
    let seconds = 0;
    let estimated = false;

    for (let index = start; index < list.length; index += 1) {
      const segment = list[index] || {};
      const duration = finiteDuration(segment);
      let segmentSeconds;
      if (segment.has_audio && duration) {
        segmentSeconds = duration;
      } else {
        estimated = true;
        segmentSeconds = duration || wordCount(segment.text) * secondsPerWord;
      }
      if (index === start) segmentSeconds = Math.max(0, segmentSeconds - (Number(currentTime) || 0));
      seconds += segmentSeconds;
    }

    return {
      seconds: Math.max(0, Math.round(seconds / rate)),
      estimated,
    };
  }

  function playbackResumeTarget(pendingIndex, currentIndex, loadedIndex, currentTime) {
    const pending = Number.parseInt(pendingIndex, 10);
    const current = Math.max(0, Number.parseInt(currentIndex, 10) || 0);
    if (Number.isFinite(pending) && pending >= 0) {
      return { segmentIndex: pending, offsetSec: 0 };
    }
    const offset = Number(loadedIndex) === current
      ? Math.max(0, Number(currentTime) || 0)
      : 0;
    return { segmentIndex: current, offsetSec: offset };
  }

  return {
    wordWeights,
    wordIndexFromWeights,
    wordIndexFromTimings,
    DEFAULT_SECONDS_PER_WORD,
    estimateRemainingAudio,
    playbackResumeTarget,
    seekTarget,
    wordIndexFromMediaTime,
  };
});
