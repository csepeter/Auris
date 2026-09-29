"""Read written Hungarian out loud the way a narrator would.

Both TTS engines mangle bare digits in Hungarian: they either spell them in
English or guess a wrong Hungarian form, and ``num2words``' Hungarian tables
are wrong for ordinals above a hundred (``102.`` comes back as *"százkétik"*).
Hungarian audiobooks are full of years, dates, chapter numbers, abbreviations
and units, so the whole mapping lives here instead: written text in, speakable
text out. Only the spoken text changes; the displayed text is never touched.

The hard parts this module gets right:

* ``932.`` is an ordinal — *"kilencszázharminckettedik"*, not *"kilencszázharminckettő"*
* a date reads differently from an ordinal: ``1932. március 5-én`` →
  *"ezerkilencszázharminckettő március ötödikén"*, ``március 15-ig`` →
  *"március tizenötödikéig"*, ``2024-10-05`` → *"kétezer-huszonnégy október ötödike"*
* a number in front of a noun uses the short form: ``12 alma`` →
  *"tizenkét alma"*, while a bare ``12`` stays *"tizenkettő"*
* suffixes glued on with a hyphen keep their own vowels: ``3-at`` → *"hármat"*,
  ``10-et`` → *"tizet"*; after a unit they move onto the unit word:
  ``1500 Ft-ért`` → *"ezerötszáz forintért"*
* Roman numerals before rulers and counted nouns: ``IV. Béla`` →
  *"Negyedik Béla"*, ``a XX. században`` → *"a huszadik században"*
* abbreviations, acronyms, times, phone numbers, ranges, fractions and signs
"""

from __future__ import annotations

import re

from core.hungarian_lexicon import (
    ABBREVIATIONS,
    ACRONYMS,
    CURRENCY_SYMBOLS,
    HUNGARIAN_LOWER,
    HUNGARIAN_UPPER,
    LETTER_NAMES,
    MONTHS,
    NON_TERMINAL_ABBREVIATIONS,
    PHRASE_ABBREVIATIONS,
    UNITS,
)

__all__ = [
    'NORMALIZER_VERSION',
    'cardinal',
    'ordinal',
    'day_of_month',
    'looks_hungarian',
    'normalize_hungarian',
    'repair_hungarian_glyphs',
]

# Bump this whenever ``normalize_hungarian`` can produce different output for
# the same input. It is part of the audio cache key, so a bump makes cached
# Hungarian audio regenerate with the new spoken text.
NORMALIZER_VERSION = 2

_UNITS = ('nulla', 'egy', 'kettő', 'három', 'négy', 'öt', 'hat', 'hét',
          'nyolc', 'kilenc')
_TENS = {2: 'húsz', 3: 'harminc', 4: 'negyven', 5: 'ötven', 6: 'hatvan',
         7: 'hetven', 8: 'nyolcvan', 9: 'kilencven'}
# 11-19 and 21-29 use a bound form of the tens ("tizenhárom", "huszonhárom").
_BOUND_TENS = {1: 'tizen', 2: 'huszon'}
_SCALES = ((10 ** 9, 'milliárd'), (10 ** 6, 'millió'), (1000, 'ezer'))

# Above 2000 Hungarian spelling puts a hyphen on the thousand boundary
# ("kétezer-huszonnégy"), below it the number is one word.
_HYPHEN_ABOVE = 2000

_MONTHS = MONTHS

# Words that can follow a number without being counted by it, so "2 és 3"
# stays "kettő és három" instead of turning into the attributive "két".
_NOT_COUNTED = {
    'és', 'vagy', 'meg', 'de', 'is', 'sem', 'se', 'pedig', 'hogy', 'mint',
    'majd', 'azaz', 'avagy', 'valamint', 'illetve', 'plusz', 'mínusz',
}

# Nouns that make a trailing period an ordinal even when the word is
# capitalised, as in the chapter heading "5. Fejezet".
_ORDINAL_NOUNS = {
    'fejezet', 'rész', 'kötet', 'könyv', 'század', 'évszázad', 'esztendő',
    'esztendejében', 'év', 'évben', 'oldal', 'kiadás', 'világháború',
    'emelet', 'sor', 'pont', 'bekezdés', 'versszak', 'felvonás', 'jelenet',
    'szám', 'osztály', 'kerület', 'alkalom', 'helyezett', 'hadsereg',
    'törvény', 'paragrafus', 'cikkely', 'melléklet', 'függelék', 'táblázat',
    'ábra', 'levél', 'napon', 'nap', 'hét', 'hónap', 'ízben',
    'félév', 'évfolyam', 'évezred', 'zsoltár', 'szimfónia', 'ének',
    'forduló', 'negyedév', 'szakasz', 'zsinat', 'kongresszus',
}

# Parts of the year: after these a year stays a cardinal, because "1932. nyarán"
# is read as "ezerkilencszázharminckettő nyarán", not as an ordinal.
_YEAR_PARTS = {
    'nyarán', 'nyara', 'nyarától', 'nyaráig', 'tavaszán', 'tavasza',
    'őszén', 'ősze', 'őszétől', 'telén', 'tele', 'karácsonyán', 'húsvétján',
    'elején', 'eleje', 'végén', 'vége', 'közepén', 'közepe', 'folyamán',
    'táján', 'januárjában', 'decemberében',
}

# Street words: a number with a period right after them is a house number
# ("Kossuth utca 5. alatt" → "Kossuth utca öt alatt"), not an ordinal.
_STREET_BEFORE = re.compile(
    r'(?:utca|út|útja|tér|tere|körút|köz|sétány|fasor|rakpart)\s+$'
)

_LOWER = HUNGARIAN_LOWER
_UPPER = HUNGARIAN_UPPER
# Suffixes that change the shape of a cardinal stem ("hármat", "tizet").
# "i" is left out on purpose: "3-ig" is "háromig", not "hármig".
_VOWEL_SUFFIX_START = set('aeoóöőuúüű')
_BACK_VOWELS = set('aáoóuú')
_VOWELS = set('aáeéiíoóöőuúüű')


def _multiplier(n: int) -> str:
    """The form used in front of száz/ezer/millió: 2 is "két", never "kettő"."""
    return cardinal(n, before_noun=True)


def _below_hundred(n: int) -> str:
    if n < 10:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    if unit == 0:
        return 'tíz' if tens == 1 else _TENS[tens]
    if tens in _BOUND_TENS:
        return _BOUND_TENS[tens] + _UNITS[unit]
    return _TENS[tens] + _UNITS[unit]


def _below_thousand(n: int) -> str:
    if n < 100:
        return _below_hundred(n)
    hundreds, rest = divmod(n, 100)
    head = 'száz' if hundreds == 1 else _multiplier(hundreds) + 'száz'
    return head + (_below_thousand(rest) if rest else '')


def cardinal(n: int, before_noun: bool = False) -> str:
    """``932`` → "kilencszázharminckettő"; before a noun 2 becomes "két"."""
    n = int(n)
    if n < 0:
        return 'mínusz ' + cardinal(-n, before_noun)

    if n < 1000:
        word = _below_thousand(n)
    else:
        word = ''
        for value, name in _SCALES:
            if n < value:
                continue
            count, rest = divmod(n, value)
            if value == 1000 and count == 1:
                head = name  # 1000 is "ezer", never "egyezer"
            else:
                head = _multiplier(count) + name
            if rest:
                separator = '-' if n > _HYPHEN_ABOVE else ''
                word = head + separator + cardinal(rest)
            else:
                word = head
            break

    if before_noun and word.endswith('kettő'):
        word = word[:-len('kettő')] + 'két'
    return word


# The last element of a compound carries the ordinal ending; everything in
# front of it stays a cardinal ("ezerkilencszáz" + "harminc" + "kettedik").
_ORDINAL_ENDINGS = sorted(
    (
        ('kettő', 'kettedik'), ('három', 'harmadik'), ('négy', 'negyedik'),
        ('öt', 'ötödik'), ('hat', 'hatodik'), ('hét', 'hetedik'),
        ('nyolc', 'nyolcadik'), ('kilenc', 'kilencedik'), ('egy', 'egyedik'),
        ('tíz', 'tizedik'), ('húsz', 'huszadik'), ('harminc', 'harmincadik'),
        ('negyven', 'negyvenedik'), ('ötven', 'ötvenedik'),
        ('hatvan', 'hatvanadik'), ('hetven', 'hetvenedik'),
        ('nyolcvan', 'nyolcvanadik'), ('kilencven', 'kilencvenedik'),
        ('száz', 'századik'), ('ezer', 'ezredik'), ('millió', 'milliomodik'),
        ('milliárd', 'milliárdodik'), ('nulla', 'nulladik'),
    ),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def ordinal(n: int) -> str:
    """``932`` → "kilencszázharminckettedik"."""
    n = int(n)
    if n == 1:
        return 'első'
    if n == 2:
        return 'második'
    word = cardinal(n)
    for ending, replacement in _ORDINAL_ENDINGS:
        if word.endswith(ending):
            return word[:-len(ending)] + replacement
    return word + 'dik'


def _day_stem(n: int) -> str:
    """The stem a date suffix attaches to: "elsej-én", "ötödik-én"."""
    return 'elsej' if int(n) == 1 else ordinal(n)


def _harmony_vowel(word: str) -> str:
    """Back or front linking vowel, ignoring the neutral i of "-dik"."""
    for char in reversed(word):
        if char in _BACK_VOWELS:
            return 'a'
        if char in 'eéöőüű':
            return 'e'
    return 'e'


def day_of_month(n: int) -> str:
    """``5`` → "ötödike" — how a date is read when nothing follows it."""
    if int(n) == 1:
        return 'elseje'
    word = ordinal(n)
    return word + _harmony_vowel(word)


def _attach(stem: str, suffix: str) -> str:
    """Glue a written suffix onto a stem without doubling the linking letter."""
    if stem.endswith('j') and suffix.startswith('j'):
        suffix = suffix[1:]
    return stem + suffix


def _lengthen(word: str) -> str:
    """Final a/e lengthens in front of a suffix: "nulla" → "nullá-", "-ike" → "-iké"."""
    if word.endswith('a'):
        return word[:-1] + 'á'
    if word.endswith('e'):
        return word[:-1] + 'é'
    return word


# ---------------------------------------------------------------------------
# Vowel harmony for suffixes that move onto a different word
# ---------------------------------------------------------------------------

def _harmony(word: str) -> str:
    """'back', 'front' or 'round' — which suffix variant ``word`` takes.

    i, í and é are neutral, so "százalék" is back ("százalékos") and "tíz" with
    only neutral vowels is front ("tíztől").
    """
    for char in reversed(word.lower()):
        if char in _BACK_VOWELS:
            return 'back'
        if char in 'öőüű':
            return 'round'
        if char == 'e':
            return 'front'
    return 'front'


_SUFFIX_FAMILIES = (
    ('ban', 'ben', 'ben'), ('ba', 'be', 'be'), ('ra', 're', 're'),
    ('ról', 'ről', 'ről'), ('tól', 'től', 'től'), ('ból', 'ből', 'ből'),
    ('nak', 'nek', 'nek'), ('val', 'vel', 'vel'), ('nál', 'nél', 'nél'),
    ('hoz', 'hez', 'höz'), ('szor', 'szer', 'ször'),
    ('os', 'es', 'ös'), ('ot', 'et', 'öt'), ('on', 'en', 'ön'), ('ok', 'ek', 'ök'),
    ('as', 'es', 'ös'), ('at', 'et', 'öt'), ('an', 'en', 'ön'), ('ak', 'ek', 'ök'),
)
# Longest member first; for a member in two families the first family wins.
_FAMILY_LOOKUP = sorted(
    ((member, family) for family in _SUFFIX_FAMILIES for member in family),
    key=lambda pair: -len(pair[0]),
)
_HARMONY_INDEX = {'back': 0, 'front': 1, 'round': 2}
_LINKING = {'as', 'es', 'os', 'ös', 'at', 'et', 'ot', 'öt', 'an', 'en', 'on',
            'ön', 'ak', 'ek', 'ok', 'ök'}
_NO_LENGTHENING = ('kor', 'ként', 'nként')
_TRIPLE = re.compile(r'([^\W\d_])\1\1')


def _reharmonize(stem: str, suffix: str) -> str:
    """Rewrite a suffix written for another word so it fits ``stem``.

    "30-tól" said as "harminc perc" needs "perctől": the suffix was written for
    "harminc" but lands on "perc".
    """
    index = _HARMONY_INDEX[_harmony(stem)]
    out = []
    rest = suffix
    while rest:
        for member, family in _FAMILY_LOOKUP:
            if rest.startswith(member):
                out.append(family[index])
                rest = rest[len(member):]
                break
        else:
            out.append(rest)
            break
    return ''.join(out)


def _glue(stem: str, suffix: str | None, harmonize: bool = True) -> str:
    """Attach ``suffix`` to a spoken word: harmony, linking vowel, lengthening."""
    if not suffix:
        return stem
    if harmonize:
        suffix = _reharmonize(stem, suffix)
    last = stem[-1:]
    if last in _VOWELS:
        if suffix[:2] in _LINKING:
            # "óra" + "-as" is "órás": the linking vowel drops after a vowel.
            suffix = suffix[1:]
            stem = _lengthen(stem)
        elif (suffix[:1] not in _VOWELS and not suffix.startswith(_NO_LENGTHENING)) \
                or suffix.startswith('ig'):
            stem = _lengthen(stem)
    return _TRIPLE.sub(r'\1\1', stem + suffix)


# ---------------------------------------------------------------------------
# Numbers with suffixes and dates
# ---------------------------------------------------------------------------

# Stems that change shape in front of a suffix that starts with a vowel:
# "három" + "-at" is "hármat", "ezer" + "-et" is "ezret", "tíz" + "-et" is
# "tizet". The third element lists suffix starts that keep the long stem:
# "tízen" and "húszan" (ten/twenty people) keep their long vowel.
_SUFFIX_STEMS = (
    ('három', 'hárm', ()), ('kettő', 'kett', ()), ('ezer', 'ezr', ()),
    ('hét', 'het', ()), ('tíz', 'tiz', ('en', 'an')),
    ('húsz', 'husz', ('en', 'an')),
)
# Suffixes that make a compound with the short form: "2-szer" → "kétszer".
_SHORT_FORM_SUFFIX = re.compile(r'^(?:sz[oeö]r|féle|fős)')


def _cardinal_with_suffix(n: int, suffix: str) -> str:
    if _SHORT_FORM_SUFFIX.match(suffix):
        return cardinal(n, before_noun=True) + suffix
    word = cardinal(n)
    if suffix[:1] in _VOWEL_SUFFIX_START:
        for ending, stem, keep_long in _SUFFIX_STEMS:
            if word.endswith(ending):
                if not suffix.startswith(keep_long):
                    word = word[:-len(ending)] + stem
                break
    elif word.endswith('a') and suffix[:1] not in _VOWELS:
        word = _lengthen(word)  # "0-t" → "nullát"
    return word + suffix


def _day_with_suffix(day: int, suffix: str | None) -> str:
    """A day of the month in a date: "15-e", "15-én", "15-ig", "15-i"."""
    if not suffix:
        return day_of_month(day)
    if suffix in ('e', 'a', 'je', 'ja'):
        return day_of_month(day)  # "március 15-e" → "tizenötödike"
    if suffix[:1] in ('á', 'é', 'j'):
        return _attach(_day_stem(day), suffix)  # "5-én" → "ötödikén"
    if suffix in ('i', 'ei', 'ai'):
        return day_of_month(day) + 'i'  # "15-i" → "tizenötödikei"
    # "15-ig" → "tizenötödikéig", "15-től" → "tizenötödikétől"
    return _lengthen(day_of_month(day)) + suffix


# Written ordinal endings: "3-ik", "5-ödik", "10-edik", "3-ikán".
_ORDINAL_SUFFIX = re.compile(r'^(?:[aeoö]?d)?ik(.*)$')
# Endings that only ever sit on a day of the month.
_DAY_ONLY_SUFFIX = re.compile(r'^(?:j?[ea]i?|i)$')


def _number_suffix(n: int, suffix: str) -> str:
    """A hyphenated suffix decides whether this is a date or a plain number.

    ``-án``/``-én``/``-jén`` only ever appear on a day of the month, so
    ``5-én`` is "ötödikén" while ``1932-ben`` stays "ezerkilencszázharminckettőben".
    """
    ordinal_match = _ORDINAL_SUFFIX.match(suffix)
    if ordinal_match:
        return ordinal(n) + ordinal_match.group(1)
    if n == 1 and suffix[:1] == 's' and suffix[1:2] in ('ő', 'e'):
        return 'el' + suffix  # "1-ső" → "első", "1-sején" → "elsején"
    if suffix.startswith('ér'):  # "5-ért" is a plain number, not a date
        return _cardinal_with_suffix(n, suffix)
    if 1 <= n <= 31 and _DAY_ONLY_SUFFIX.match(suffix):
        return _day_with_suffix(n, suffix)
    if suffix[:1] in ('á', 'é', 'j'):
        return _attach(_day_stem(n), suffix)
    return _cardinal_with_suffix(n, suffix)


# ---------------------------------------------------------------------------
# Roman numerals
# ---------------------------------------------------------------------------

_ROMAN_PARTS = (
    (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
    (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'),
)
# Rulers and popes never go past this, and above it the letters (L, C, D, M)
# are far more likely to be initials or acronyms ("DC.", "MI.").
_MAX_RULER_NUMERAL = 39


def _roman_value(text: str) -> int | None:
    """Value of a canonically written Roman numeral, else ``None``.

    The round-trip check keeps letter salad out: ``DVD`` parses to a number
    with a naive reader, but it is not how anyone writes 995.
    """
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = 0
    previous = 0
    for char in reversed(text):
        value = values.get(char)
        if value is None:
            return None
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    if total <= 0 or _to_roman(total) != text:
        return None
    return total


def _to_roman(value: int) -> str:
    out = []
    for amount, letters in _ROMAN_PARTS:
        while value >= amount:
            out.append(letters)
            value -= amount
    return ''.join(out)


# ---------------------------------------------------------------------------
# Regexes (compiled once; normalize_hungarian runs per TTS segment)
# ---------------------------------------------------------------------------

_MONTH_RE = '|'.join(_MONTHS)
_SUFFIX_RE = f'[{_LOWER}]+'

_SPACE_LIKE = re.compile('[    ]')
_NEEDS_WORK = re.compile(
    rf'[\d.½¼¾⅐-⅞€$£%°+−õûÕÛ]|[{_UPPER}]{{2}}'
)
_SENTENCE_END_AHEAD = re.compile(rf'\s*$|\s+[{_UPPER}]')
_CAPITALISED_WORD_BEFORE = re.compile(rf'[{_UPPER}][{_LOWER}]+\s+$')
_ROMAN_BEFORE = re.compile(r'(?<![\w.])[IVXLCDM]+\.\s+$')

_PHRASE_LOOKUP = {re.sub(r'\s+', '', key): value
                  for key, value in PHRASE_ABBREVIATIONS.items()}
_PHRASE_ABBREV = re.compile(
    r'(?<![\w.])('
    + '|'.join(
        r'\s*'.join(re.escape(part) for part in key.split())
        for key in sorted(PHRASE_ABBREVIATIONS, key=len, reverse=True)
    )
    + r')(?!\w)'
)
_ABBREV = re.compile(
    r'(?<![\w.])('
    + '|'.join(re.escape(key)
               for key in sorted(ABBREVIATIONS, key=len, reverse=True))
    + rf')(?:-({_SUFFIX_RE}))?(?!\w)'
)

_PHONE = re.compile(
    rf'(?<![\w+])\(?(\+36|06)[ \-/]?\(?(1|[2-9]\d)\)?[ \-/](\d{{3}})[ \-]?(\d{{3,4}})'
    rf'(?!\d)(?:-({_SUFFIX_RE}))?'
)
_PHONE_COMPACT = re.compile(
    rf'(?<![\w+])(\+36)(1|[2-9]\d)(\d{{3}})(\d{{4}})(?!\d)(?:-({_SUFFIX_RE}))?'
)

_THOUSAND_GROUPED = re.compile(r'(?<![\d.,])(\d{1,3})(?:[.  ](\d{3}))+(?![\d.,])')

_ISO_DATE = re.compile(
    rf'(?<![\d.\-])(\d{{4}})-(\d{{2}})-(\d{{2}})(?:-({_SUFFIX_RE}))?(?![\d\-])'
)
_NUMERIC_DATE = re.compile(
    rf'(?<![\d.,])(\d{{4}})\.\s?(\d{{1,2}})\.\s?(\d{{1,2}})'
    rf'(?:(\.)(?:-({_SUFFIX_RE}))?|-({_SUFFIX_RE}))?(?![\d])'
)
_DATE = re.compile(
    rf'(?:(?<![\d.])(\d{{1,4}})\.\s+)?(?<!\w)({_MONTH_RE})\s+(\d{{1,2}})'
    rf'(?:(\.)(?:-({_SUFFIX_RE}))?|-({_SUFFIX_RE}))(?![\d\w])'
)
# "1932. március folyamán", "1848. márciusában": the year stays a cardinal.
_DATE_YEAR_MONTH = re.compile(rf'(?<![\d.])(\d{{3,4}})\.\s+(?=(?:{_MONTH_RE}))')

_ROMAN = re.compile(r'(?<![\w.])([IVXLCDM]+)\.(?=\s+([^\W\d_][\w-]*))')

_TIME_WITH_HOUR_WORD = re.compile(
    r'(?<![\d.,])(\d{1,2})[.:](\d{2})\s+ór([aá])([' + _LOWER + r']*)'
)
_TIME_DOTTED = re.compile(
    r'(?<![\d.,])(\d{1,2})\.(\d{2})-(kor|kori|ig|tól|től|ra|re|as|es|os|ös)(?![\w])'
)
_TIME = re.compile(rf'(?<![\d.,])(\d{{1,2}}):(\d{{2}})(?:-({_SUFFIX_RE}))?(?![\d:])')

_SIGN = re.compile(r'(?<![^\s(\[])([\-−+])(?=\d)')
_ENDASH_MINUS = re.compile(r'(?<![^\s(\[])–(?=\d+(?:,\d+)?\s?(?:°|fok))')

_CURRENCY_PREFIX = re.compile(
    rf'(?<!\w)([€$£])\s?(\d+(?:,\d+)?)(\s+(?:ezer|millió|milliárd)(?!\w))?'
    rf'(?:-({_SUFFIX_RE}))?'
)
_LETTER_UNITS = {key: value for key, value in UNITS.items()}
_UNIT_WORDS = re.compile(
    r'(?<=\d)(\s?)('
    + '|'.join(re.escape(key)
               for key in sorted(_LETTER_UNITS, key=len, reverse=True))
    + rf')(?:-({_SUFFIX_RE}))?(?![\w²³])'
)
_SYMBOL_UNITS = {**CURRENCY_SYMBOLS, '%': 'százalék', '°C': 'Celsius-fok',
                 '°F': 'Fahrenheit-fok', '°': 'fok'}
_UNIT_SYMBOLS = re.compile(
    rf'(?<=\d)(\s?)(%|°\s?C|°\s?F|°|€|\$|£)(?:-?({_SUFFIX_RE}))?'
)

_VULGAR_FRACTIONS = {
    '½': 'fél', '¼': 'negyed', '¾': 'háromnegyed', '⅓': 'egyharmad',
    '⅔': 'kétharmad', '⅕': 'egyötöd', '⅖': 'kétötöd', '⅗': 'háromötöd',
    '⅘': 'négyötöd', '⅙': 'egyhatod', '⅚': 'öthatod', '⅛': 'egynyolcad',
    '⅜': 'háromnyolcad', '⅝': 'ötnyolcad', '⅞': 'hétnyolcad', '⅒': 'egytized',
    '⅐': 'egyheted', '⅑': 'egykilenced',
}
_VULGAR = re.compile(
    rf'(?:(?<![\d,])(\d+)\s?)?([{"".join(_VULGAR_FRACTIONS)}])(?:-({_SUFFIX_RE}))?'
)
_FRACTION_DENOMINATORS = {2: 'ketted', 3: 'harmad', 4: 'negyed', 5: 'ötöd',
                          8: 'nyolcad', 10: 'tized'}
_SLASH_FRACTION = re.compile(
    rf'(?<![\d/.,])([1-9])/(10|[23458])(?![\d/])(?:-({_SUFFIX_RE}))?'
)
_SCHOOL_YEAR = re.compile(
    rf'(?<![\d/.,])(\d{{4}})/(\d{{4}}|\d{{2}})(?![\d/])(?:-({_SUFFIX_RE}))?'
)

_DECIMAL = re.compile(rf'(?<![\d,.])(\d+),(\d+)(?:-({_SUFFIX_RE}))?(?![\d,])')
_DECIMAL_PLACES = {1: 'tized', 2: 'század', 3: 'ezred', 4: 'tízezred',
                   5: 'százezred', 6: 'milliomod'}

_RANGE = re.compile(
    rf'(?<![\d.,/\-–])(\d+)[–-](\d+)(?:-({_SUFFIX_RE}))?(?![\d,]|[.–\-/]\d)'
)
_NEXT_WORD = re.compile(rf'\s+([{_LOWER}]+)')
# First words of spoken units: a range in front of them counts that unit.
# "volt" is also the verb "was" ("1914–1918 volt a háború"), so it is left out.
_UNIT_NOUNS = tuple(
    ({value.split()[0].lower() for value in
      (*UNITS.values(), *CURRENCY_SYMBOLS.values())} | {'százalék', 'fok'})
    - {'volt'}
)

_SUFFIXED = re.compile(rf'\b(\d+)-({_SUFFIX_RE})')
_ORDINAL = re.compile(r'\b(\d+)\.(?=\s+(\w+))')
_INTEGER = re.compile(r'\b\d+\b')
_COUNTED_NEXT = re.compile(rf'\s+(?:([{_LOWER}]+)|Celsius-|Fahrenheit-)')

_CAPS_TOKEN = re.compile(
    rf'(?<![\w\-])([{_UPPER}]{{2,5}})(?:-({_SUFFIX_RE}))?(?!\w)'
)
_CONSONANT_LETTERS = set('BCDFGHJKLMNPQRSTVWXYZ')
_INTERJECTIONS = {'HM', 'HMM', 'MM', 'SH', 'SHH', 'PST', 'PSZT', 'PSSZT',
                  'BRR', 'GRR', 'PFF', 'SSZ', 'CSSZ', 'ZZ', 'ZZZ', 'NY'}
_LETTER_KEYS = sorted(LETTER_NAMES, key=len, reverse=True)

_WORD = re.compile(r'[^\W\d_]{2,}')


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _ends_sentence(text: str, pos: int) -> bool:
    """Nothing but a new capitalised sentence (or the end) follows ``pos``."""
    return bool(_SENTENCE_END_AHEAD.match(text, pos))


def _starts_sentence(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    return bool(before) and before[-1] in '.!?…'


def _is_shouting(segment: str) -> bool:
    """An all-caps heading: at least two words and not one lowercase letter."""
    return (not any(char.islower() for char in segment)
            and len(_WORD.findall(segment)) >= 2)


def _line_is_all_caps(text: str, pos: int) -> bool:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    return _is_shouting(text[start:end if end >= 0 else len(text)])


def _sentence_is_all_caps(text: str, start: int, end: int) -> bool:
    left = max(text.rfind(mark, 0, start) for mark in '.!?\n') + 1
    rights = [i for i in (text.find(mark, end) for mark in '.!?\n') if i >= 0]
    right = min(rights) if rights else len(text)
    return _is_shouting(text[left:right])


def _is_ordinal_context(word: str) -> bool:
    """``század``, ``fejezet``, ``esztendejében`` — nouns you count with."""
    plain = word.lower()
    return plain in _ORDINAL_NOUNS or any(
        plain.startswith(noun) for noun in _ORDINAL_NOUNS
    )


def _counts_the_next_word(text: str, end: int) -> bool:
    """True when the number modifies a following noun ("12 alma")."""
    match = _COUNTED_NEXT.match(text, end)
    if not match:
        return False
    return match.group(1) not in _NOT_COUNTED


def _read_digits(group: str) -> str:
    """Leading zeros are read one by one: "05" → "nulla öt"."""
    stripped = group.lstrip('0')
    words = ['nulla'] * (len(group) - len(stripped))
    if stripped:
        words.append(cardinal(int(stripped)))
    return ' '.join(words)


# ---------------------------------------------------------------------------
# Expanders
# ---------------------------------------------------------------------------

def _expand_phrase_abbreviation(match: re.Match) -> str:
    key = re.sub(r'\s+', '', match.group(1))
    spoken = _PHRASE_LOOKUP[key]
    if _ends_sentence(match.string, match.end()):
        spoken += '.'
    return spoken


def _expand_abbreviation(match: re.Match) -> str:
    key, suffix = match.group(1), match.group(2)
    text = match.string
    start, end = match.start(), match.end()
    spoken = ABBREVIATIONS[key]
    before = text[max(0, start - 40):start]
    if key == 'u.':
        # "Kossuth u. 5." is a street; a lone "u." is anything but.
        if not _CAPITALISED_WORD_BEFORE.search(before):
            return match.group()
    elif key in ('ti.', 'Ti.'):
        # "Ti." is also the pronoun "you" closing a sentence.
        if not re.match(rf'\s+[{_LOWER}]', text[end:end + 2]):
            return match.group()
    elif key == 'sz.' and _ROMAN_BEFORE.search(before):
        spoken = 'század'  # "XX. sz." is a century
    elif key == 'min.' and re.search(r'\d\s*$', before):
        spoken = 'perc'  # "5 min." is minutes
    if suffix:
        return spoken + suffix
    rest = text[end:]
    if not rest.strip() or (
        key.lower() not in NON_TERMINAL_ABBREVIATIONS
        and _ends_sentence(text, end)
    ):
        # The abbreviation's period also closed the sentence: keep it.
        spoken += '.'
    return spoken


def _expand_phone(match: re.Match) -> str:
    prefix, area, first, last, suffix = match.groups()
    head = 'plusz harminchat' if prefix == '+36' else 'nulla hat'
    if len(last) == 4:
        tail_groups = [last[:2], last[2:]]
    else:
        tail_groups = [last]
    tail = [_read_digits(group) for group in tail_groups]
    if suffix:
        final = tail_groups[-1]
        stripped = final.lstrip('0')
        zeros = ['nulla'] * (len(final) - len(stripped))
        tail[-1] = ' '.join(
            zeros + [_cardinal_with_suffix(int(stripped or '0'), suffix)]
        )
    return ', '.join([head, cardinal(int(area)), _read_digits(first), ' '.join(tail)])


def _spoken_date(year: int | None, month: int, day: int, suffix: str | None,
                 keep_period: bool) -> str:
    spoken = f'{_MONTHS[month - 1]} {_day_with_suffix(day, suffix)}'
    if year is not None:
        spoken = f'{cardinal(year)} {spoken}'
    return spoken + ('.' if keep_period else '')


def _expand_iso_date(match: re.Match) -> str:
    year, month, day, suffix = match.groups()
    month_n, day_n = int(month), int(day)
    if not (1 <= month_n <= 12 and 1 <= day_n <= 31):
        return match.group()
    return _spoken_date(int(year), month_n, day_n, suffix, False)


def _expand_numeric_date(match: re.Match) -> str:
    year, month, day, dot, dot_suffix, suffix = match.groups()
    month_n, day_n = int(month), int(day)
    if not (1 <= month_n <= 12 and 1 <= day_n <= 31):
        return match.group()
    suffix = dot_suffix or suffix
    keep = bool(dot) and not suffix and _ends_sentence(match.string, match.end())
    return _spoken_date(int(year), month_n, day_n, suffix, keep)


def _expand_date(match: re.Match) -> str:
    year, month, day, dot, dot_suffix, suffix = match.groups()
    day_n = int(day)
    if not 1 <= day_n <= 31:
        return match.group()
    suffix = dot_suffix or suffix
    spoken = f'{month} {_day_with_suffix(day_n, suffix)}'
    if year:
        spoken = f'{cardinal(int(year))} {spoken}'
    if dot and not suffix and _ends_sentence(match.string, match.end()):
        spoken += '.'
    return spoken


def _expand_roman(match: re.Match) -> str:
    """``XX. század`` and ``II. Erzsébet`` are ordinals; other letters are not."""
    token, following = match.group(1), match.group(2)
    value = _roman_value(token)
    if value is None or token in ACRONYMS:
        return match.group()
    if len(token) == 1 and token not in 'IVX':
        # A lone C., D., L. or M. is almost always somebody's initial.
        return match.group()
    text = match.string
    is_name = (following[:1].isupper() and len(following) >= 2
               and any(char.islower() for char in following))
    if is_name and following.lower() not in _ORDINAL_NOUNS:
        # A ruler's name: "II. Erzsébet" reads as "Második Erzsébet".
        if value <= _MAX_RULER_NUMERAL:
            return ordinal(value).capitalize()
        return match.group()
    if _is_ordinal_context(following):
        word = ordinal(value)
        return word.capitalize() if _starts_sentence(text, match.start()) else word
    return match.group()


def _time_words(hour: int, minute: int) -> str:
    if minute:
        return f'{cardinal(hour, before_noun=True)} óra {cardinal(minute)} perc'
    return f'{cardinal(hour, before_noun=True)} óra'


def _valid_time(hour: int, minute: int) -> bool:
    return 0 <= hour <= 24 and 0 <= minute <= 59


def _glue_last(spoken: str, suffix: str | None) -> str:
    """Attach a suffix to the last word of an already spoken phrase."""
    if not suffix:
        return spoken
    head, _, last = spoken.rpartition(' ')
    glued = _glue(last, suffix)
    return f'{head} {glued}' if head else glued


def _expand_time_with_hour_word(match: re.Match) -> str:
    """``7.30 órakor`` → "hét óra harminc perckor": the suffix moves to "perc"."""
    hour, minute = int(match.group(1)), int(match.group(2))
    if not _valid_time(hour, minute):
        return match.group()
    long_vowel, rest = match.group(3), match.group(4)
    if not minute:
        return f'{cardinal(hour, before_noun=True)} ór{long_vowel}{rest}'
    if rest and rest[0] not in _VOWELS and rest[1:2] not in _VOWELS:
        rest = 'a' + rest  # "órás" / "órát" were "óra" + "-as" / "-at"
    return _glue_last(_time_words(hour, minute), rest or None)


def _expand_time(match: re.Match) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if not _valid_time(hour, minute):
        return match.group()
    return _glue_last(_time_words(hour, minute), match.group(3))


def _expand_sign(match: re.Match) -> str:
    return 'plusz ' if match.group(1) == '+' else 'mínusz '


def _expand_currency_prefix(match: re.Match) -> str:
    symbol, amount, scale, suffix = match.groups()
    word = CURRENCY_SYMBOLS[symbol]
    return f'{amount}{scale or ""} {_glue(word, suffix)}'


def _expand_unit_word(match: re.Match) -> str:
    return ' ' + _glue(_LETTER_UNITS[match.group(2)], match.group(3))


def _expand_unit_symbol(match: re.Match) -> str:
    key = re.sub(r'\s+', '', match.group(2))
    return ' ' + _glue(_SYMBOL_UNITS[key], match.group(3))


def _expand_vulgar(match: re.Match) -> str:
    whole, char, suffix = match.groups()
    word = _VULGAR_FRACTIONS[char]
    if whole is not None:
        n = int(whole)
        word = 'másfél' if (n == 1 and char == '½') else (
            f'{cardinal(n, before_noun=True)} és {word}'
        )
    return word + (suffix or '')


def _expand_slash_fraction(match: re.Match) -> str:
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if numerator >= denominator:
        return match.group()
    if (numerator, denominator) == (1, 2):
        word = 'fél'
    else:
        word = (cardinal(numerator, before_noun=True)
                + _FRACTION_DENOMINATORS[denominator])
    return word + (match.group(3) or '')


def _expand_school_year(match: re.Match) -> str:
    first, second, suffix = match.groups()
    a, b = int(first), int(second)
    expected = (a + 1) % 100 if len(second) == 2 else a + 1
    if b != expected:
        return match.group()
    tail = _number_suffix(b, suffix) if suffix else cardinal(b)
    return f'{cardinal(a)}–{tail}'


def _expand_decimal(match: re.Match) -> str:
    whole, fraction, suffix = match.groups()
    spoken = f'{cardinal(int(whole))} egész {cardinal(int(fraction))}'
    place = _DECIMAL_PLACES.get(len(fraction))
    if place:
        spoken += f' {place}'
    return _glue_last(spoken, suffix)


def _expand_range(match: re.Match) -> str:
    first, second, suffix = match.groups()
    a, b = int(first), int(second)
    abbreviated_year = a >= 1000 and len(second) <= 2 and b > a % 100
    if suffix:
        if a < b or abbreviated_year:
            return f'{cardinal(a)}–{_number_suffix(b, suffix)}'
        return match.group()
    if not (a < b or abbreviated_year):
        return match.group()  # "3-2" is a score, not a range
    text = match.string
    following = _NEXT_WORD.match(text, match.end())
    if following and following.group(1) == 'között':
        return f'{cardinal(a)} és {cardinal(b)}'
    start = _glue(cardinal(a), 'tól')
    next_word = following.group(1) if following else ''
    looks_like_years = (len(first) == 4 and 1000 <= a <= 2999
                        and (abbreviated_year or 1000 <= b <= 2999))
    if looks_like_years and next_word.startswith(_UNIT_NOUNS):
        looks_like_years = False  # "1000–2000 forint" counts money, not years
    if not looks_like_years and _counts_the_next_word(text, match.end()):
        # An approximate count before a noun: "két-három napos", "tíz-tizenkét oldal".
        return f'{cardinal(a, before_noun=True)}-{cardinal(b, before_noun=True)}'
    return f'{start} {_glue(cardinal(b), "ig")}'  # "tíztől tizenkettőig"


def _expand_ordinal_or_sentence_end(text: str) -> str:
    """``932. esztendejében`` is an ordinal; ``Volt 932. Aztán…`` is not."""
    def repl(match: re.Match) -> str:
        following = match.group(2)
        value = int(match.group(1))
        if _STREET_BEFORE.search(text[max(0, match.start() - 20):match.start()]):
            # A house number: "Kossuth utca 5. alatt" → "Kossuth utca öt alatt".
            if following[:1].islower():
                return cardinal(value)
            return f'{cardinal(value)}.'
        if 1000 <= value <= 2999 and following.lower() in _YEAR_PARTS:
            # A year in front of a season or a part of the year keeps its
            # cardinal form: "1932. nyarán" is "ezerkilencszázharminckettő nyarán".
            return cardinal(value)
        if following[:1].islower() or following.lower() in _ORDINAL_NOUNS:
            return ordinal(value)
        # A capitalised word after the period reads as a new sentence, so the
        # number itself is a plain cardinal and the period stays a full stop.
        return f'{cardinal(value)}.'
    return _ORDINAL.sub(repl, text)


def _spell_letters(token: str) -> str:
    out = []
    i = 0
    while i < len(token):
        for key in _LETTER_KEYS:
            if token.startswith(key, i):
                out.append(LETTER_NAMES[key])
                i += len(key)
                break
        else:
            out.append(token[i].lower())
            i += 1
    return ''.join(out)


def _expand_acronym(match: re.Match) -> str:
    token, suffix = match.group(1), match.group(2)
    if _sentence_is_all_caps(match.string, match.start(), match.end()):
        return match.group()  # an all-caps heading, not an acronym
    spoken = ACRONYMS.get(token)
    if spoken is None:
        if (not set(token) <= _CONSONANT_LETTERS
                or token in _INTERJECTIONS
                or _roman_value(token) is not None
                or _TRIPLE.search(token)):
            return match.group()
        spoken = _spell_letters(token)
    return f'{spoken}-{suffix}' if suffix else spoken


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_MOJIBAKE = str.maketrans({'õ': 'ő', 'Õ': 'Ő', 'û': 'ű', 'Û': 'Ű'})
_MOJIBAKE_CHARS = re.compile('[õÕûÛ]')
_HUNGARIAN_WORDS = re.compile(
    r'(?<!\w)(?:és|hogy|nem|volt|van|egy|meg|már|csak|még|mint|vagy|azt|ezt|'
    r'amikor|mert|aki|ami|neki|lesz|lett|nagyon|itt|ott|igen|sem|pedig|'
    r'akkor|után|között|alatt|felé|miatt|nélkül)(?!\w)',
    re.IGNORECASE,
)
_NOT_HUNGARIAN = re.compile(
    r'[äÄãÃçÇœ]|ões\b|ão\b|(?<!\w)(?:não|être|très|même|sûr|coût|goût|'
    r'août|dû|mûr|flûte|piqûre|jeûne)(?!\w)',
    re.IGNORECASE,
)


def repair_hungarian_glyphs(text: str, assume_hungarian: bool = False) -> str:
    """Fix Latin-1 look-alikes of ő/ű that legacy PDFs and fonts produce.

    ``hõs`` → ``hős``, ``fûz`` → ``fűz``. Portuguese, Estonian and French use
    õ/û for real, so unless ``assume_hungarian`` is set the text must look
    Hungarian (Hungarian function words or ö/ü) and nothing else.
    """
    if not text or not _MOJIBAKE_CHARS.search(text):
        return text
    if not assume_hungarian:
        if _NOT_HUNGARIAN.search(text):
            return text
        if not (_HUNGARIAN_WORDS.search(text) or re.search('[öüÖÜ]', text)):
            return text
    return text.translate(_MOJIBAKE)


def _clean_input(text: str) -> str:
    if '­' in text:
        text = text.replace('­', '')  # soft hyphen: invisible, unspoken
    text = _SPACE_LIKE.sub(' ', text)
    return repair_hungarian_glyphs(text, assume_hungarian=True)


def normalize_hungarian(text: str) -> str:
    """Turn written Hungarian into what a narrator says.

    Numbers, dates, times, units, abbreviations and acronyms become words;
    everything else is left as it is.
    """
    if not text:
        return text
    out = _clean_input(text)
    if not _NEEDS_WORK.search(out):
        return out

    # Abbreviations first: "márc. 15." must reach the date rules as "március 15.".
    out = _PHRASE_ABBREV.sub(_expand_phrase_abbreviation, out)
    out = _ABBREV.sub(_expand_abbreviation, out)

    # Phone numbers before anything reads their digit groups as thousands.
    out = _PHONE.sub(_expand_phone, out)
    out = _PHONE_COMPACT.sub(_expand_phone, out)

    out = _THOUSAND_GROUPED.sub(
        lambda m: re.sub(r'[.  ]', '', m.group()), out
    )

    out = _ISO_DATE.sub(_expand_iso_date, out)
    out = _NUMERIC_DATE.sub(_expand_numeric_date, out)
    out = _DATE.sub(_expand_date, out)
    out = _DATE_YEAR_MONTH.sub(lambda m: f'{cardinal(int(m.group(1)))} ', out)

    out = _ROMAN.sub(_expand_roman, out)

    out = _TIME_WITH_HOUR_WORD.sub(_expand_time_with_hour_word, out)
    out = _TIME_DOTTED.sub(_expand_time, out)
    out = _TIME.sub(_expand_time, out)

    out = _ENDASH_MINUS.sub('mínusz ', out)
    out = _SIGN.sub(_expand_sign, out)

    out = _CURRENCY_PREFIX.sub(_expand_currency_prefix, out)
    out = _UNIT_SYMBOLS.sub(_expand_unit_symbol, out)
    out = _UNIT_WORDS.sub(_expand_unit_word, out)

    out = _VULGAR.sub(_expand_vulgar, out)
    out = _SCHOOL_YEAR.sub(_expand_school_year, out)
    out = _SLASH_FRACTION.sub(_expand_slash_fraction, out)
    out = _DECIMAL.sub(_expand_decimal, out)
    out = _RANGE.sub(_expand_range, out)

    out = _SUFFIXED.sub(lambda m: _number_suffix(int(m.group(1)), m.group(2)), out)
    out = _expand_ordinal_or_sentence_end(out)
    out = _INTEGER.sub(
        lambda m: cardinal(int(m.group()), _counts_the_next_word(out, m.end())),
        out,
    )

    out = _CAPS_TOKEN.sub(_expand_acronym, out)
    return out


_HUNGARIAN_CODES = {'hu', 'hun', 'hu-hu', 'hungarian', 'magyar'}
_UNDECLARED = {'', 'none', 'auto', 'und', 'unknown'}


def looks_hungarian(language: str | None, text: str = '') -> bool:
    """Hungarian by declared language, or by what the text looks like.

    Without a declared language the text counts as Hungarian when it has ő/ű
    (letters only Hungarian uses), or legacy õ/û with Hungarian context, or at
    least two distinct Hungarian function words plus a Hungarian accent.
    """
    code = str(language or '').strip().lower().replace('_', '-')
    if code in _HUNGARIAN_CODES or code.startswith('hu-'):
        return True
    if code not in _UNDECLARED:
        return False
    text = text or ''
    if re.search(r'[őűŐŰ]', text):
        return True
    if _NOT_HUNGARIAN.search(text):
        return False
    words = {word.lower() for word in _HUNGARIAN_WORDS.findall(text)}
    if _MOJIBAKE_CHARS.search(text) and words:
        return True
    return len(words) >= 2 and bool(re.search('[áéíóöúüÁÉÍÓÖÚÜ]', text))
