"""Word timings for read-along highlighting.

The reader highlights the word being spoken. Without timings it spreads time
evenly over the words, which drifts on long Hungarian compounds and at
punctuation. This module produces per-word ``[start, end]`` pairs for the
displayed words of a segment (``text.split()`` order):

* ``align_words`` maps Whisper word timestamps onto the displayed words.
  Numbers the TTS read as words ("1848" → "ezernyolcszáznegyvennyolc") are
  compared in spoken form; unmatched stretches are interpolated between their
  matched neighbours, weighted by syllables.
* ``estimate_words`` is the fallback: syllable- and punctuation-weighted.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher

from core.database import get_conn

_VOWELS = set("aáeéiíoóöőuúüűyAÁEÉIÍOÓÖŐUÚÜŰY")
_PAUSE_AFTER = {",": 0.8, ";": 1.0, ":": 1.0, ".": 1.6, "!": 1.6, "?": 1.6, "…": 2.0, "–": 0.6, "—": 0.6}


def display_words(text: str) -> list[str]:
    return str(text or "").split()


def syllables(word: str) -> int:
    count = 0
    previous_vowel = False
    for ch in word:
        vowel = ch in _VOWELS
        if vowel and not previous_vowel:
            count += 1
        previous_vowel = vowel
    digits = sum(ch.isdigit() for ch in word)
    # A written number is read as several syllables ("1848": ~9).
    count += digits * 2
    return max(1 if any(ch.isalnum() for ch in word) else 0, count)


def word_weights(words: list[str]) -> list[float]:
    weights = []
    for word in words:
        weight = float(syllables(word))
        tail = word.rstrip("\"'”’»«)")
        if tail and tail[-1] in _PAUSE_AFTER:
            weight += _PAUSE_AFTER[tail[-1]]
        weights.append(max(weight, 0.2))
    return weights


def estimate_words(text: str, duration: float) -> list[list[float]]:
    words = display_words(text)
    if not words or not duration:
        return []
    weights = word_weights(words)
    total = sum(weights)
    timings, cursor = [], 0.0
    for weight in weights:
        span = duration * weight / total
        timings.append([round(cursor, 3), round(cursor + span, 3)])
        cursor += span
    return timings


def _key(token: str, language: str | None) -> str:
    from core.qa import comparable_text

    return comparable_text(token, language).replace(" ", "")


def align_words(text: str, asr_words: list[dict], duration: float,
                language: str | None = None) -> list[list[float]]:
    words = display_words(text)
    if not words:
        return []
    heard = [w for w in asr_words if w.get("start") is not None]
    if not heard:
        return estimate_words(text, duration)
    shown_keys = [_key(w, language) for w in words]
    heard_keys = [_key(w.get("word", ""), language) for w in heard]
    timings: list[list[float] | None] = [None] * len(words)
    matcher = SequenceMatcher(a=shown_keys, b=heard_keys, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            h = heard[block.b + offset]
            end = h.get("end") if h.get("end") is not None else h["start"]
            timings[block.a + offset] = [float(h["start"]), float(end)]
    # Partial matches ("tíz-tizenkét" ↔ "tíz", "-tizenkét"): prefix overlap.
    for i, key in enumerate(shown_keys):
        if timings[i] is not None or not key:
            continue
        joined = ""
        start = None
        for h, hk in zip(heard, heard_keys):
            if start is None and hk and key.startswith(hk):
                start, joined = h, hk
            elif start is not None and joined + hk == key[: len(joined + hk)]:
                joined += hk
            else:
                if start is not None and joined == key:
                    break
                start, joined = None, ""
            if start is not None and joined == key:
                timings[i] = [float(start["start"]), float(h.get("end") or h["start"])]
                break
    return _fill_gaps(words, timings, duration)


def _fill_gaps(words, timings, duration) -> list[list[float]]:
    weights = word_weights(words)
    result = [list(t) if t else None for t in timings]
    i = 0
    while i < len(result):
        if result[i] is not None:
            i += 1
            continue
        j = i
        while j < len(result) and result[j] is None:
            j += 1
        left = result[i - 1][1] if i > 0 else 0.0
        right = result[j][0] if j < len(result) else float(duration or left)
        right = max(right, left)
        span_weights = weights[i:j]
        total = sum(span_weights) or 1.0
        cursor = left
        for k, weight in zip(range(i, j), span_weights):
            step = (right - left) * weight / total
            result[k] = [round(cursor, 3), round(cursor + step, 3)]
            cursor += step
        i = j
    # Monotonic, non-overlapping, inside the clip.
    last = 0.0
    for t in result:
        t[0] = round(max(t[0], last), 3)
        t[1] = round(max(t[1], t[0]), 3)
        if duration:
            t[0], t[1] = min(t[0], duration), min(t[1], duration)
        last = t[1]
    return result


# ── Storage ─────────────────────────────────────────────────────────────────

def ensure_table() -> None:
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS segment_words ("
            " cache_key TEXT PRIMARY KEY, words_json TEXT NOT NULL,"
            " source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )


def store(cache_key: str, timings: list[list[float]], source: str) -> None:
    if not cache_key or not timings:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO segment_words (cache_key, words_json, source) VALUES (?,?,?)",
            (cache_key, json.dumps(timings), source),
        )


def load_many(cache_keys: list[str]) -> dict[str, list]:
    keys = [k for k in cache_keys if k]
    if not keys:
        return {}
    result = {}
    with get_conn() as conn:
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            rows = conn.execute(
                "SELECT cache_key, words_json FROM segment_words WHERE cache_key IN (%s)"
                % ",".join("?" * len(chunk)), chunk,
            ).fetchall()
            result.update({row["cache_key"]: json.loads(row["words_json"]) for row in rows})
    return result
