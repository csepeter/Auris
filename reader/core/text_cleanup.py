"""Import-time text cleanup shared by every parser.

Runs once on imported chapter blocks, so the reader, the speaker analysis and
every TTS engine see the same repaired text.
"""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE_RE = re.compile('[­​‌‍⁠﻿]')
_SPACE_RE = re.compile('[   ]')
# Legacy Latin-1 fonts render ő/ű as õ/û. Real Hungarian text never uses õ/û.
_GLYPH_REPAIRS = str.maketrans({'õ': 'ő', 'û': 'ű', 'Õ': 'Ő', 'Û': 'Ű'})
_HU_EVIDENCE_RE = re.compile(r'\b(?:és|hogy|nem|egy|volt|csak|már|még|az)\b', re.IGNORECASE)

_pyphen_dic = None
_pyphen_loaded = False


def _hyphenator():
    global _pyphen_dic, _pyphen_loaded
    if not _pyphen_loaded:
        _pyphen_loaded = True
        try:
            import pyphen

            _pyphen_dic = pyphen.Pyphen(lang='hu_HU')
        except Exception:
            _pyphen_dic = None
    return _pyphen_dic


def looks_hungarian_text(text: str) -> bool:
    sample = str(text or '')[:20000]
    return len(_HU_EVIDENCE_RE.findall(sample)) >= 3 or any(ch in sample for ch in 'őűŐŰ')


def repair_glyphs(text: str, language: str | None = None) -> str:
    """Repair õ/û mojibake in Hungarian text; leave other languages alone."""
    if not text or not any(ch in text for ch in 'õûÕÛ'):
        return text
    if language not in (None, '', 'hu') and not looks_hungarian_text(text):
        return text
    if language != 'hu' and not looks_hungarian_text(text.translate(_GLYPH_REPAIRS)):
        return text
    return text.translate(_GLYPH_REPAIRS)


def clean_text(text: str, language: str | None = None) -> str:
    text = unicodedata.normalize('NFC', str(text or ''))
    text = _INVISIBLE_RE.sub('', text)
    text = _SPACE_RE.sub(' ', text)
    return repair_glyphs(text, language)


def should_join_hyphenated(left: str, right: str) -> bool:
    """Decide whether ``left-`` + ``right`` at a line break is one word.

    A hyphen after a capitalized stem is a Hungarian compound hyphen
    (Kossuth-díj, Nobel-díj, Duna-part) and must stay. Otherwise the break is
    a typesetting hyphen when Hungarian hyphenation rules allow a break there.
    """
    stem = re.search(r'([^\W\d_]+)$', left)
    tail = re.match(r'([^\W\d_]+)', right)
    if not stem or not tail:
        return False
    stem_word, tail_word = stem.group(1), tail.group(1)
    if stem_word[:1].isupper() and not stem_word.isupper():
        return False
    if not tail_word[:1].islower():
        return False
    dic = _hyphenator()
    if dic is None:
        return True
    joined = (stem_word + tail_word).lower()
    return len(stem_word) in dic.positions(joined)


def clean_parsed_book(result: dict) -> dict:
    """Clean titles and blocks of a parsed book in place and return it."""
    language = str(result.get('language') or '')[:2].lower() or None
    for key in ('title', 'author', 'description', 'publisher', 'series'):
        if isinstance(result.get(key), str):
            result[key] = clean_text(result[key], language).strip()
    for chapter in result.get('chapters') or []:
        if isinstance(chapter.get('title'), str):
            chapter['title'] = clean_text(chapter['title'], language).strip()
        blocks = chapter.get('blocks')
        if blocks:
            for block in blocks:
                block['text'] = clean_text(block.get('text', ''), language).strip()
            chapter['blocks'] = [block for block in blocks if block['text']]
            chapter['content'] = '\n\n'.join(block['text'] for block in chapter['blocks'])
        elif isinstance(chapter.get('content'), str):
            chapter['content'] = clean_text(chapter['content'], language)
        chapter['word_count'] = len(str(chapter.get('content') or '').split())
    return result
