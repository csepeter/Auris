"""
Text enrichment engine.

Splits chapter text into TTS-ready segments, attributes dialogue to characters,
injects OmniVoice non-verbal tags, adjusts speed for scene tone, and avoids
common sentence-boundary mistakes such as "Mr." or "Dr." being treated as a
full stop.
"""

import re
import unicodedata

from core.hungarian_lexicon import (
    ATTRIBUTION_VERBS as _HU_ATTRIBUTION_VERB_WORDS,
    FAST_WORDS as _HU_FAST_WORDS,
    HUNGARIAN_LOWER as _LOWER,
    HUNGARIAN_UPPER as _UPPER,
    LAUGHTER_WORDS as _HU_LAUGHTER_WORDS,
    NON_TERMINAL_ABBREVIATIONS as _HU_NON_TERMINAL_ABBREVIATIONS,
    PHRASE_ABBREVIATIONS as _HU_PHRASE_ABBREVIATIONS,
    SIGH_WORDS as _HU_SIGH_WORDS,
    SLOW_WORDS as _HU_SLOW_WORDS,
    WHISPER_WORDS as _HU_WHISPER_WORDS,
)
from core.parser.sections import HU_NAMED_SECTIONS, HU_ORDINAL

_DOT = "<prd>"
_ELLIPSIS = "<ell>"
_UNICODE_ELLIPSIS = "<uell>"
_SPLIT = "<split>"
MAX_TTS_SEGMENT_CHARS = 500
_QUOTE_CLASS = r'["\u201c\u201d\u201e\u00ab\u00bb]'
_QUOTE_CONTENT_CLASS = r'"\u201c\u201d\u201e\u00ab\u00bb'
# One capitalised name word.  ASCII names behave exactly as before; accented
# capitals (\u00c9va, \u00d6d\u00f6n, \u0150ze) and accented letters inside names now count too.
_NAME_WORD = rf"[{_UPPER}](?:[^\W\d_]|['.\-])+"
_NAME_PATTERN = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}}"
# Hungarian speaker names after a verb ("\u2013 mondta Gorcsev Iv\u00e1n."): no dots,
# so the sentence-final period is not swallowed into the name.
_HU_NAME_WORD = rf"[{_UPPER}](?:[^\W\d_]|['\-])+"
_HU_NAME_PATTERN = rf"{_HU_NAME_WORD}(?:\s+{_HU_NAME_WORD}){{0,2}}"
_DASH_CHARS = "\\-\u2013\u2014"
# Characters that may close a sentence after its terminator / open the next.
_SENTENCE_CLOSERS = "\"'\u201d\u2019\u00bb\u00ab)\\]"
_SENTENCE_OPENERS = "\"'\u201c\u201e\u2018\u201a\u00ab\u00bb(\\["
# Common Hungarian verbal prefixes ("el|nevetett", "oda|s\u00fagta", "fel|s\u00f3hajtott").
_HU_VERB_PREFIX = (
    r"(?:meg|el|fel|f\u00f6l|ki|be|oda|vissza|r\u00e1|le|\u00e1t|k\u00f6zbe|hozz\u00e1|ut\u00e1na|\u00f6ssze)?"
)


def _word_alternation(words) -> str:
    """Regex alternation for lexicon entries, longest first, spaces flexible.

    An empty lexicon yields a never-matching pattern, so a shared list that is
    emptied can never turn into an "always matches" alternative.
    """
    unique = sorted({str(word).strip() for word in words if str(word).strip()},
                    key=len, reverse=True)
    if not unique:
        return r"(?!)"
    return "|".join(re.escape(word).replace(r"\ ", r"\s+") for word in unique)


def _lexicon_stems(words) -> str:
    """Lexicon words matched as stems: inflected suffixes are allowed."""
    return rf"(?:{_word_alternation(words)})\w*"


_HU_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:\d+|[ivxlcdm]+)\.\s*(?:fejezet|r[e\u00e9]sz)"
    r"|(?:fejezet|r[e\u00e9]sz)\s+(?:\d+|[ivxlcdm]+)\.?"
    rf"|(?:{HU_ORDINAL})\s+(?:fejezet|r[e\u00e9]sz)"
    rf"|(?:{HU_NAMED_SECTIONS})"
    r")(?!\w)"
    # Either nothing else, or a separated title without sentence punctuation:
    # "Els\u0151 fejezet \u2013 A kezdet", "3. fejezet: A titok".  A running sentence
    # such as "Els\u0151 r\u00e9sz volt a legjobb." is not a heading.
    r"(?:\s*[.:]?|\s*[,.:\u2013\u2014-]\s*[^.!?]{1,100})\s*$",
    re.IGNORECASE,
)

# Bump when speaker-unit boundaries change: saved LLM speaker annotations are
# stored by unit index and get re-aligned by text once per version.
SEGMENTER_VERSION = 2

_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:chapter|ch\.?)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty(?:\s*-\s*\w+)?)"
    r"|part\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"|prologue|epilogue|foreword|preface|introduction|afterword|appendix|interlude"
    r")\b.*$",
    re.IGNORECASE,
)

_SHORT_ALL_CAPS_RE = re.compile(
    rf"^[{_UPPER}0-9][{_UPPER}0-9 '&,:;.\u2013\u2014-]{{1,80}}$"
)
_QUESTION_RE = re.compile(r'\?\s*["\u201d\u00bb\u00ab\u2019)]?\s*$')
_SURPRISE_RE = re.compile(r'!\s*["\u201d\u00bb\u00ab\u2019)]?\s*$')
_SHOCKED_QUESTION_END_RE = re.compile(r'\?!\s*["\u201d]?\s*$')

# \u2500\u2500 Question context refiners \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
_QUESTION_HINT_RE = re.compile(
    r"\b(asked|wondered|queried|questioned|inquired|demanded|challenged)\b",
    re.IGNORECASE,
)
# Skeptical / rhetorical: raised eyebrow, disbelief, sarcasm
_SKEPTIC_CONTEXT_RE = re.compile(
    r"\b(scoff|scoffed|sneer|sneered|skeptic|sarcast|disdain|"
    r"smirk|smirked|raised.*eyebrow|narrowed.*eyes|rolled.*eyes|"
    r"doubt|doubted|disbelief|incredulous|dismissive)\b",
    re.IGNORECASE,
)
# Shocked / disbelieving questions
_SHOCK_CONTEXT_RE = re.compile(
    r"\b(shock|shocked|horrified|frozen|stunned|stagger|recoil|"
    r"jaw dropped|speechless|aghast|pale|couldn't believe|"
    r"taken aback|dumbstruck|wide.eyed)\b",
    re.IGNORECASE,
)
# Wondering / curious questions
_WONDER_CONTEXT_RE = re.compile(
    r"\b(wonder|curious|pondered|puzzle|puzzled|contemplat|mused|"
    r"tilted.*head|furrowed.*brow|peered|squinted|speculated|mulled)\b",
    re.IGNORECASE,
)

# \u2500\u2500 Surprise context refiners \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
_SURPRISE_HINT_RE = re.compile(
    r"\b(gasp|gasped|gasping|exclaim|exclaimed|cried out|startled|shouted|yelled|"
    r"yelped|screamed|shrieked|recoiled|flinched|jumped back)\b",
    re.IGNORECASE,
)
# Strong shock \u2014 jaw-dropping, screaming, impossible
_STRONG_SURPRISE_RE = re.compile(
    r"\b(gasp|gasped|shriek|shrieked|scream|screamed|"
    r"jaw dropped|impossible|unbelievable|recoil|recoiled|"
    r"stunned|flinch|flinched|couldn't believe|speechless|aghast|"
    r"mind went blank|froze in place|blood ran cold)\b",
    re.IGNORECASE,
)
# Excited / triumphant surprise
_EXCITED_SURPRISE_RE = re.compile(
    r"\b(finally|at last|triumph|triumphant|victory|succeed|succeeded|"
    r"incredible|amazing|wonderful|brilliant|breakthrough|"
    r"beamed|cheered|lit up|leaped for joy|eyes shone)\b",
    re.IGNORECASE,
)
# Mild realization / dawning understanding
_MILD_REALIZATION_RE = re.compile(
    r"\b(realiz|reali[sz]ed|dawned|suddenly understood|remembered|"
    r"occurred to|it hit|clicked|made sense|recognition|it struck)\b",
    re.IGNORECASE,
)

# \u2500\u2500 Emotion word patterns \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# English alternatives keep their original whole-word matching.  Hungarian
# words are inflected, so their stems are matched with a ``\w*`` suffix and an
# optional verbal prefix ("elnevette magát", "odasúgta", "felsóhajtott").
# Python's ``\b``/``\w`` are Unicode-aware for str patterns.
_HU_LAUGHTER = (
    r"nevet(?!ség)\w*|kacag\w*|kuncog\w*|vihog\w*|hahotáz\w*|röhög\w*|"
    r"vigyorog\w*|vigyorg\w*|kacarász\w*|heherész\w*|"
    + _lexicon_stems(_HU_LAUGHTER_WORDS)
)
_HU_SIGH = (
    r"sóhaj\w*|kifújta\s+a\s+levegőt|"
    + _lexicon_stems(_HU_SIGH_WORDS)
)
_HU_DISSATISFACTION = (
    r"morog\w*|morg(?:ott|ta|va|olód)\w*|mordul\w*|dörmög\w*|dohog\w*|"
    r"zsörtölőd\w*|fújtat\w*|sziszeg\w*|vicsorog\w*|vicsorg\w*|dünnyög\w*|"
    r"motyog\w*|förmedt\w*|csattant\s+fel|bosszúsan|mérgesen|dühösen|"
    r"ráncolta\s+a\s+homlokát|homlokát\s+ráncolva"
)
_HU_CONFIRMATION = r"bólint\w*|helyesel\w*|helyeselt\w*|egyetértően"
_HU_WHISPER = (
    r"suttog\w*|súg(?:ta|ja|va|ott|tam|tad|d|ok|sz)\b|halkan|halkabban|"
    r"halk\s+hangon|alig\s+hallhatóan|mormol\w*|pusmog\w*|lehelte|"
    + _lexicon_stems(_HU_WHISPER_WORDS)
)

_LAUGHTER_RE = re.compile(
    r"\b(laugh|laughs|laughed|laughing|chuckl|giggl|"
    r"grinned|grinning|amused|teased|joked|snickered|cackled|"
    r"burst out laughing|couldn't help laughing)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_LAUGHTER})",
    re.IGNORECASE,
)
_SIGH_RE = re.compile(
    r"\b(sigh|sighs|sighed|sighing|exhale|exhaled|"
    r"breathed out|let out a (?:long |weary |heavy |deep )?breath|"
    r"heaved a sigh|resigned(?:ly)?)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_SIGH})",
    re.IGNORECASE,
)
_DISSATISFACTION_RE = re.compile(
    r"\b(grumbl|mutter|growl|snapp|barked|hiss|scowl|gritted|"
    r"glared|glaring|frowned|frowning|stormed|huffed|fumed|"
    r"seethed|snarled|glowered|bristled|sneered at|"
    r"slammed|threw.*down|shook.*head in disgust)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_DISSATISFACTION})",
    re.IGNORECASE,
)
_CONFIRMATION_RE = re.compile(
    r"\b(nod|nodded|nodding|agreed|confirm|confirmed|affirm|affirmed|assented|"
    r"concurred|gave a nod|tilted his head in agreement|tilted her head in agreement)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_CONFIRMATION})",
    re.IGNORECASE,
)
_WHISPER_RE = re.compile(
    r"\b(whisper|whispered|breathed|murmured|under his breath|under her breath|"
    r"barely audible|in a low voice|hissed softly)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_WHISPER})",
    re.IGNORECASE,
)

_TAG_RULES = [
    (_LAUGHTER_RE,       "[laughter]"),
    (_SIGH_RE,           "[sigh]"),
    (_DISSATISFACTION_RE,"[dissatisfaction-hnn]"),
    (_CONFIRMATION_RE,   "[confirmation-en]"),
]
_ENGLISH_ATTRIBUTION_VERBS = (
    "said|replied|asked|whispered|shouted|cried|muttered|exclaimed|called|added|"
    "continued|laughed|sighed|groaned|snapped|retorted|insisted|demanded|pleaded|"
    "began|noted|observed|remarked|growled|yelled|murmured|gasped|stammered|shrieked"
)
# Hungarian attribution verbs from the shared lexicon plus a few frequent
# intransitive forms; an optional verbal prefix covers "megkérdezte",
# "odaszólt", "felkiáltott" and similar.
_HU_ATTRIBUTION_VERBS = (
    rf"{_HU_VERB_PREFIX}(?:"
    + _word_alternation(
        tuple(_HU_ATTRIBUTION_VERB_WORDS)
        + (
            "felelt", "válaszolt", "kérdezett", "kiáltott", "suttogott",
            "motyogott", "morogta", "dörmögte", "sóhajtott", "szólt közbe",
        )
    )
    + ")"
)
_ATTRIBUTION_VERBS = f"{_ENGLISH_ATTRIBUTION_VERBS}|{_HU_ATTRIBUTION_VERBS}"
_ATTRIBUTION_SENTENCE_RE = re.compile(
    rf"^(?:{_NAME_PATTERN}|he|she|they)\s+(?:{_ATTRIBUTION_VERBS})(?:\s+\w+){{0,4}}[.!?]?$",
    re.IGNORECASE,
)

_DIALOGUE_RE = re.compile(
    rf"(?:"
    rf"{_QUOTE_CLASS}(?P<text1>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}\s*[,.]?\s*"
    rf"(?P<name1>{_NAME_PATTERN})\s+"
    rf"(?:{_ATTRIBUTION_VERBS})"
    rf"|"
    rf"(?P<name2>{_NAME_PATTERN})\s+"
    rf"(?:{_ATTRIBUTION_VERBS})\s*[,.:]?\s*"
    rf"{_QUOTE_CLASS}(?P<text2>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}"
    rf")",
    re.DOTALL,
)
# Hungarian verb-first attribution after a quotation:
# „Gyere ide!” – mondta Anna. / „Gyere ide!” mondta halkan Anna.
_HU_QUOTE_ATTRIBUTION_RE = re.compile(
    rf"{_QUOTE_CLASS}(?P<text>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}"
    rf"\s*[,.]?\s*(?:[{_DASH_CHARS}]\s*)?"
    rf"{_HU_ATTRIBUTION_VERBS}(?:\s+[{_LOWER}]\w*){{0,3}}?\s+"
    rf"(?P<name>{_HU_NAME_PATTERN})"
)
# Hungarian name-first attribution before a quotation:
# Ödön azt mondta: „Ez az enyém.”  /  Anna így szólt: »Gyere!«
_HU_NAME_FIRST_ATTRIBUTION_RE = re.compile(
    rf"(?P<name>{_HU_NAME_PATTERN})\s+"
    r"(?:(?:azt|ezt|így|csak|halkan|hangosan|végül|erre|aztán|akkor|újra|"
    r"ismét|még)\s+){0,2}"
    rf"{_HU_ATTRIBUTION_VERBS}\s*[,.:]?\s*(?:[{_DASH_CHARS}]\s*)?"
    rf"{_QUOTE_CLASS}(?P<text>[^{_QUOTE_CONTENT_CLASS}]{{2,}}){_QUOTE_CLASS}"
)
# Hungarian dash dialogue with an interjection (matched at sentence start):
# – Gyere ide – mondta Anna. / – Gyere ide – felelte sóhajtva Gorcsev.
_HU_DASH_ATTRIBUTION_RE = re.compile(
    rf"^\s*[{_DASH_CHARS}]\s+(?P<text>.{{2,}}?)\s+[{_DASH_CHARS}]\s*,?\s*"
    rf"{_HU_ATTRIBUTION_VERBS}(?:\s+[{_LOWER}]\w*){{0,3}}?\s+"
    rf"(?P<name>{_HU_NAME_PATTERN})"
)

# Supported quotation pairs: "…", “…”, „…” and „…“ (OCR / publisher variant),
# «…», »…« (Hungarian inner / alternative style) and ‚…’.
_STANDALONE_QUOTE_RE = re.compile(
    r'(?:"([^"]{2,})"|“([^”]{2,})”|„([^”“]{2,})[”“]|«([^»]{2,})»'
    r'|»([^«]{2,})«|‚([^’]{2,})’)'
)
_PAIRED_DIALOGUE_RE = re.compile(
    r'"[^"]{2,}"|“[^”]{2,}”|„[^”“]{2,}[”“]|«[^»]{2,}»|»[^«]{2,}«|‚[^’]{2,}’'
)
_DASH_SPLIT_RE = re.compile(r'(?=\s+[-–—]\s+)')
_DASH_DIALOGUE_RE = re.compile(
    rf"^\s*[{_DASH_CHARS}]\s+"
    rf"(?:[{_UPPER}0-9\"“„«»‚‘'(]|(?:\.\.\.|…)\s*\w)"
)
# A dash followed by a lowercase word inside a paragraph is the narrator's
# interjection ("– mondta Anna"), not a new line of dialogue.
_DASH_NARRATION_START_RE = re.compile(rf"^\s*[{_DASH_CHARS}]\s+[{_LOWER}]")
_LEADING_DASH_RE = re.compile(rf"^\s*[{_DASH_CHARS}]\s+")
# Dialogue/narration toggles inside one dash-led sentence: " – " or " –,".
_DASH_TOGGLE_RE = re.compile(rf"(?<=\s)[{_DASH_CHARS}](?=\s|[,;:.!?])")
_DASH_ATTRIBUTION_CONTINUATION_RE = re.compile(
    r'^(\s*[-–—]\s+[a-záéíóöőúüű].*?\s+[-–—],)\s*(.+)$'
)
_NARRATION_CONTINUATION_RE = re.compile(
    r"^(?:(?:A|Az|Egy|The|An?)\s+.{0,55}|"
    r"(?:He|She|They|Ő|Azután|Ekkor)\s+)"
    r"\b(?:said|replied|asked|nodded|looked|smiled|laughed|sighed|"
    r"turned|stood|stepped|walked|thought|saw|heard|felt|"
    r"mondta|felelte|kérdezte|bólintott|nézett|elmosolyodott|"
    r"nevetett|sóhajtott|folytatta|fordult|állt|lépett|ment|"
    r"gondolta|látta|hallotta|érezte)\b",
    re.IGNORECASE,
)

_HU_ACTION = (
    r"rohan\w*|szalad\w*|menekül\w*|üldöz\w*|robban\w*|zuhan\w*|csattan\w*|"
    r"sikolt\w*|sikít\w*|vágtat\w*|rontott\w*|ugr(?:ott|ik|anak|ál|va)\w*|"
    r"futott\w*|futva|lőtt\w*|lövés\w*|lövöldöz\w*|ütött\w*|sújtott\w*|"
    r"rúgott\w*|rúgta|rántott\w*|lökött\w*|lökte|ragadta|ragadott\w*|"
    r"összetört\w*|széttört\w*|betört\w*|csapott\w*|csapta|pofon\w*|"
    r"verekedés\w*|verekedt\w*|megtámad\w*|rátámad\w*|nekitámad\w*|támadás\w*|"
    + _lexicon_stems(_HU_FAST_WORDS)
)
_HU_SLOW = (
    r"csendesen|gyengéden|gyöngéden|óvatosan|szelíden|nyugodtan|ünnepélyesen|"
    r"gyászosan|békésen|álmodozva|álmatagon|némán|elgondolkodva|merengve|"
    + _lexicon_stems(_HU_SLOW_WORDS)
)
_ACTION_WORDS = re.compile(
    r"\b(ran|rushed|sprinted|struck|fell|crashed|burst|grabbed|pulled|pushed|"
    r"slammed|exploded|screamed|fired|attacked|fled|chased|leaped|jumped|"
    r"stabbed|shot|hit|smashed|broke|shattered)\b"
    rf"|\b{_HU_VERB_PREFIX}(?:{_HU_ACTION})",
    re.IGNORECASE,
)
_SLOW_WORDS = re.compile(
    r"\b(slowly|gently|quietly|softly|carefully|tenderly|silently|"
    r"solemnly|mournfully|peacefully|dreamily)\b"
    rf"|\b(?:{_HU_SLOW})",
    re.IGNORECASE,
)

_ABBREVIATION_WORDS = (
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "Mt", "Lt", "Capt",
    "Col", "Gen", "Sgt", "Rev", "Hon", "Pres", "Gov", "Sen", "Rep", "Supt",
    "Det", "No", "Nos", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug",
    "Sep", "Sept", "Oct", "Nov", "Dec", "etc", "vs",
)
_ABBREVIATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(item) for item in _ABBREVIATION_WORDS) + r")\.",
    re.IGNORECASE,
)
_MULTI_DOT_TOKEN_RE = re.compile(
    r"\b(?:e\.g\.|i\.e\.|a\.m\.|p\.m\.|u\.s\.a?\.|u\.k\.|u\.n\.|ph\.d\.)",
    re.IGNORECASE,
)
_INITIALISM_RE = re.compile(rf"(?<!\w)(?:[{_UPPER}]\.){{2,}}")
# Single-letter initials, including accented capitals and Hungarian digraph
# initials: "Szabó J. Éva", "Gy. Kovács", "Zs. Nagy", "Dzs. Tóth".
_INITIAL_LETTER = rf"(?:Dzs|Cs|Dz|Gy|Ly|Ny|Sz|Ty|Zs|[{_UPPER}])"
_NAME_INITIAL_RE = re.compile(
    rf"(?<!\w){_INITIAL_LETTER}\."
    # followed by a name ("J. Éva") or by further spaced initials that end in
    # a name ("K. A. Tóth", "J. R. R. Tolkien").
    rf"(?=(?:\s+{_INITIAL_LETTER}\.)*\s+[{_UPPER}][{_LOWER}])"
)

# Hungarian abbreviations that practically never end a sentence (shared
# lexicon, compared case-insensitively).  Two groups need extra care:
# - abbreviations that are also ordinary Hungarian words ("ti." = you,
#   "min." = on what, "Max." = a name) are protected only before a number;
# - abbreviations that are ordinary English words ("old.", "ill.", "Ford.")
#   are protected only inside a paragraph that looks Hungarian.
_HU_ABBREVIATIONS_ALSO_WORDS = frozenset({"ti.", "min.", "max."})
_HU_ABBREVIATIONS_ENGLISH_CLASH = frozenset({"old.", "ill.", "ford."})


def _abbreviation_regex(keys, suffix: str = "") -> "re.Pattern | None":
    stems = [
        str(key).strip()[:-1]
        for key in keys
        if str(key).strip().endswith(".") and len(str(key).strip()) > 1
    ]
    if not stems:
        return None
    return re.compile(
        rf"(?<![\w.])(?:{_word_alternation(stems)})\.{suffix}",
        re.IGNORECASE,
    )


_HU_NON_TERMINAL_KEYS = frozenset(
    str(key).lower() for key in _HU_NON_TERMINAL_ABBREVIATIONS
)
_HU_ABBREVIATION_RE = _abbreviation_regex(
    _HU_NON_TERMINAL_KEYS
    - _HU_ABBREVIATIONS_ALSO_WORDS
    - _HU_ABBREVIATIONS_ENGLISH_CLASH
)
_HU_WORD_ABBREVIATION_RE = _abbreviation_regex(
    _HU_NON_TERMINAL_KEYS & _HU_ABBREVIATIONS_ALSO_WORDS,
    suffix=r"(?=\s*\d)",
)
_HU_ENGLISH_CLASH_ABBREVIATION_RE = _abbreviation_regex(
    _HU_NON_TERMINAL_KEYS & _HU_ABBREVIATIONS_ENGLISH_CLASH
)
# Multi-token abbreviations: "Kr. e. 44-ben", "i. sz. 200", "s. k.".
_HU_PHRASE_ABBREVIATION_RE = (
    re.compile(
        rf"(?<![\w.])(?:{_word_alternation(_HU_PHRASE_ABBREVIATIONS)})",
        re.IGNORECASE,
    )
    if _HU_PHRASE_ABBREVIATIONS else None
)
# Roman numeral or number + period before a capitalised word.  In Hungarian
# this is an ordinal ("IV. Béla", "II. Rákóczi Ferenc"), not a sentence end.
_ORDINAL_CANDIDATE_RE = re.compile(
    rf"(?<![\w.])(?P<num>\d{{1,4}}|[IVXLCDM]{{1,7}})\."
    rf"(?=\s+[{_SENTENCE_OPENERS}]*[{_UPPER}])"
)
_ROMAN_NUMERAL_RE = re.compile(
    r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
)
_PREVIOUS_WORD_RE = re.compile(r"(\S+)\s+$")
# Cheap per-paragraph language hint used only where English and Hungarian
# punctuation conventions genuinely conflict (e.g. "born in 1990. Then" vs.
# "1848. Március idusán").
_HU_FUNCTION_WORD_RE = re.compile(
    r"\b(?:az|és|hogy|nem|egy|meg|de|volt|van|már|csak|még|ez|azt|mint|mert|"
    r"aki|ami|sem|vagy|pedig|majd|akkor|úgy|így|lett|kell|itt|ott|nagyon|"
    r"király|úr|ő|én|te|mi|ők)\b",
    re.IGNORECASE,
)
_EN_FUNCTION_WORD_RE = re.compile(
    r"\b(?:the|and|of|to|was|were|he|she|it|is|in|that|you|his|her|with|for|"
    r"had|on|at|as|but|this|they|not|have)\b",
    re.IGNORECASE,
)
_HU_ACCENT_RE = re.compile(r"[áéíóöőúüű]", re.IGNORECASE)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[,;:…、，：；])\s+")
# Invisible import artefacts: soft hyphen, zero-width space/joiners, word
# joiner and BOM.  A soft hyphen right before a line break joins the word.
_INVISIBLE_CHARACTER_RE = re.compile("[­​‌‍⁠﻿]")
_SOFT_HYPHEN_LINE_BREAK_RE = re.compile("­[ \t]*\n[ \t]*(?=\\S)")


def _looks_hungarian(text: str) -> bool:
    text = str(text or "")
    hungarian = (
        2 * len(_HU_FUNCTION_WORD_RE.findall(text))
        + len(_HU_ACCENT_RE.findall(text))
    )
    english = 2 * len(_EN_FUNCTION_WORD_RE.findall(text))
    return hungarian > english


def _strip_invisible_characters(text: str) -> str:
    """Drop zero-width characters; join words split by a soft hyphen + break."""
    text = _SOFT_HYPHEN_LINE_BREAK_RE.sub("", str(text or ""))
    return _INVISIBLE_CHARACTER_RE.sub("", text)


def _normalize_source_text(text: str) -> str:
    """Remove invisible/import artefacts without changing readable content."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _strip_invisible_characters(normalized)
    normalized = normalized.replace(" ", " ").replace(" ", " ")
    normalized = normalized.replace("�", "")
    return _CONTROL_CHARACTER_RE.sub(" ", normalized)


def _split_by_words(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        if len(word) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                word[index:index + max_chars]
                for index in range(0, len(word), max_chars)
            )
            continue
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def _split_oversized_segment(
    text: str,
    max_chars: int = MAX_TTS_SEGMENT_CHARS,
) -> list[str]:
    """Keep generative TTS requests bounded, preferring natural clause breaks."""
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return [cleaned] if cleaned else []

    clauses = [
        clause.strip()
        for clause in _CLAUSE_BOUNDARY_RE.split(cleaned)
        if clause.strip()
    ]
    if len(clauses) <= 1:
        return _split_by_words(cleaned, max_chars)

    pieces: list[str] = []
    current = ""
    for clause in clauses:
        if len(clause) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_split_by_words(clause, max_chars))
            continue
        candidate = f"{current} {clause}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = clause
    if current:
        pieces.append(current)
    return pieces


def _scene_speed(text: str) -> float:
    action = len(_ACTION_WORDS.findall(text))
    slow = len(_SLOW_WORDS.findall(text))
    if action >= 3:
        return 1.15
    if slow >= 2:
        return 0.9
    return 1.0


def _title_key(text: str) -> str:
    # Unicode-aware so accented titles ("Előszó", "Ő") keep their letters.
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", str(text or "").lower())).strip()


def _is_short_all_caps_heading(text: str) -> bool:
    words = text.split()
    return len(words) <= 10 and bool(_SHORT_ALL_CAPS_RE.match(text))


def _is_heading_paragraph(text: str, chapter_title: str | None = None) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return False
    if chapter_title and _title_key(cleaned) == _title_key(chapter_title):
        return True
    if _SECTION_HEADING_RE.match(cleaned):
        return True
    if len(cleaned) <= 150 and _HU_SECTION_HEADING_RE.match(cleaned):
        return True
    if _is_short_all_caps_heading(cleaned) and not re.search(r"[.!?]", cleaned):
        return True
    return False


def _line_starts_new_paragraph(previous_line: str, current_line: str) -> bool:
    prev = previous_line.rstrip()
    curr = current_line.lstrip()
    if not prev:
        return True
    if _is_heading_paragraph(curr):
        return True
    # Hungarian dialogue: every dash-led line of speech is its own paragraph,
    # even after "odasietett:" or another line without a full stop.  A dash
    # followed by a lowercase word continues the previous line ("– mondta").
    if _DASH_DIALOGUE_RE.match(curr):
        return True
    if _DASH_NARRATION_START_RE.match(curr):
        return False
    return bool(re.search(r'[.!?]["”]?\s*$', prev))


def _split_paragraphs(text: str, chapter_title: str | None = None) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Invisible characters must not influence paragraph/heading decisions, so
    # raw chapter text (text editor blocks) and normalised text (speaker
    # units) always produce the same paragraph structure.
    raw = _strip_invisible_characters(raw)
    blocks = re.split(r"\n\s*\n+", raw)
    paragraphs: list[str] = []

    for block in blocks:
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in block.splitlines()
            if re.sub(r"\s+", " ", line).strip()
        ]
        if not lines:
            continue

        buffer: list[str] = []
        for line in lines:
            if _is_heading_paragraph(line, chapter_title):
                if buffer:
                    paragraphs.append(" ".join(buffer).strip())
                    buffer = []
                paragraphs.append(line)
                continue
            if buffer and _line_starts_new_paragraph(buffer[-1], line):
                paragraphs.append(" ".join(buffer).strip())
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            paragraphs.append(" ".join(buffer).strip())

    return paragraphs


def _protect_dots(match: re.Match) -> str:
    return match.group(0).replace(".", _DOT)


def _protect_ordinals(text: str, hungarian: bool) -> str:
    """Protect "IV. Béla" / "1848. Március"-style ordinals before capitals."""

    def replace(match: re.Match) -> str:
        number = match.group("num")
        if number.isdigit():
            # "born in 1990. Then" is a real sentence end in English.
            return number + _DOT if hungarian else match.group(0)
        if not _ROMAN_NUMERAL_RE.fullmatch(number):
            return match.group(0)
        if not hungarian:
            # English regnal suffix after a name: "Henry VIII. He ruled."
            previous = _PREVIOUS_WORD_RE.search(text[:match.start()])
            word = previous.group(1) if previous else ""
            if word[:1].isupper() and word.isalpha():
                return match.group(0)
        return number + _DOT

    return _ORDINAL_CANDIDATE_RE.sub(replace, text)


def _protect_phrase_abbreviations(text: str) -> str:
    if _HU_PHRASE_ABBREVIATION_RE is None:
        return text
    capital_follows = re.compile(rf"\s+[{_SENTENCE_OPENERS}]*[{_UPPER}]")

    def replace(match: re.Match) -> str:
        token = match.group(0)
        last = token.rfind(".")
        if last < 0:
            return token
        head = token[:last].replace(".", _DOT)
        # "Kr. e. 44-ben" keeps going; "… Kr. u. Aztán" may end a sentence.
        if capital_follows.match(match.string, match.end()):
            return head + token[last:]
        return head + _DOT + token[last + 1:]

    return _HU_PHRASE_ABBREVIATION_RE.sub(replace, text)


def _protect_sentence_boundaries(text: str) -> str:
    hungarian = _looks_hungarian(text)
    protected = text.replace("...", _ELLIPSIS).replace("…", _UNICODE_ELLIPSIS)
    protected = re.sub(r"(?<=\d)\.(?=\d)", _DOT, protected)
    protected = _MULTI_DOT_TOKEN_RE.sub(_protect_dots, protected)
    protected = _protect_phrase_abbreviations(protected)
    protected = _INITIALISM_RE.sub(_protect_dots, protected)
    protected = _NAME_INITIAL_RE.sub(_protect_dots, protected)
    protected = _ABBREVIATION_RE.sub(_protect_dots, protected)
    if _HU_ABBREVIATION_RE is not None:
        protected = _HU_ABBREVIATION_RE.sub(_protect_dots, protected)
    if _HU_WORD_ABBREVIATION_RE is not None:
        protected = _HU_WORD_ABBREVIATION_RE.sub(_protect_dots, protected)
    if hungarian and _HU_ENGLISH_CLASH_ABBREVIATION_RE is not None:
        protected = _HU_ENGLISH_CLASH_ABBREVIATION_RE.sub(_protect_dots, protected)
    protected = _protect_ordinals(protected, hungarian)
    return protected


def _restore_sentence_boundaries(text: str) -> str:
    return (
        text.replace(_DOT, ".")
        .replace(_ELLIPSIS, "...")
        .replace(_UNICODE_ELLIPSIS, "…")
    )


_SENTENCE_TERMINATOR = (
    rf"(?:[.!?]|{re.escape(_ELLIPSIS)}|{re.escape(_UNICODE_ELLIPSIS)})+"
)
_SENTENCE_START = (
    rf"(?:[{_DASH_CHARS}]\s+[{_SENTENCE_OPENERS}]*[{_UPPER}0-9]"
    rf"|[{_SENTENCE_OPENERS}]*[{_UPPER}0-9])"
)
# Terminator (?, !, ?!, ., ..., …) + optional closing quote/bracket, then the
# next sentence: a capital (accented ones included), a digit, an opening quote
# („ » « “ ‘ ‚ ") or bracket, or a dash-led line of dialogue.
_SENTENCE_BOUNDARY_RE = re.compile(
    rf"({_SENTENCE_TERMINATOR}[{_SENTENCE_CLOSERS}]*)\s+(?={_SENTENCE_START})"
)
# "A pincér odasiet: – Hallja!" — dash dialogue introduced by a colon.
_COLON_DASH_BOUNDARY_RE = re.compile(
    rf"(:)\s+(?=[{_DASH_CHARS}]\s+[{_SENTENCE_OPENERS}]*[{_UPPER}0-9])"
)


def _split_paragraph_sentences(paragraph: str) -> list[str]:
    protected = _protect_sentence_boundaries(paragraph)
    protected = _SENTENCE_BOUNDARY_RE.sub(rf"\1{_SPLIT}", protected)
    protected = _COLON_DASH_BOUNDARY_RE.sub(rf"\1{_SPLIT}", protected)
    parts = [_restore_sentence_boundaries(part).strip() for part in protected.split(_SPLIT)]
    return [part for part in parts if part]


def _dash_turn_stays_open(sentence: str) -> bool:
    """True when a dash-led sentence ends inside the spoken line.

    Each inner dash toggles between speech and the narrator's interjection:
    "– Gyere ide – mondta Anna." ends in narration (turn closed), while
    "– Jó – mondta –, gyere!" ends in speech again (turn still open).
    """
    body = _LEADING_DASH_RE.sub("", sentence, count=1)
    return len(_DASH_TOGGLE_RE.findall(body)) % 2 == 0


def _quoted_sentence_flags(paragraph: str, sentences: list[str]) -> list[bool]:
    """True for sentences overlapping a complete quotation in the paragraph.

    A quotation may span several sentences (“Hello. How are you?”); every
    sentence inside it is dialogue even though none of them contains both
    quotation marks on its own.
    """
    spans = [match.span() for match in _STANDALONE_QUOTE_RE.finditer(paragraph)]
    flags: list[bool] = []
    cursor = 0
    for sentence in sentences:
        start = paragraph.find(sentence, cursor)
        if start < 0:
            flags.append(bool(_STANDALONE_QUOTE_RE.search(sentence)))
            continue
        end = start + len(sentence)
        cursor = end
        flags.append(any(start < span_end and span_start < end
                         for span_start, span_end in spans))
    return flags


def _dialogue_sentence_flags(
    sentences: list[str],
    paragraph: str | None = None,
) -> list[tuple[bool, bool]]:
    """Per sentence: (inside a quotation, inside a dash-led dialogue turn).

    A dash-led line starts a dialogue turn; plain sentences following it stay
    in that turn until the narrator interjects ("– mondta Anna.") or a
    narration sentence is recognised.  This is the same turn logic
    ``build_speaker_units`` uses for LLM attribution.
    """
    quoted_flags = (
        _quoted_sentence_flags(paragraph, sentences)
        if paragraph is not None
        else [bool(_STANDALONE_QUOTE_RE.search(sentence)) for sentence in sentences]
    )
    flags: list[tuple[bool, bool]] = []
    active_dash_turn = False
    for sentence, quoted in zip(sentences, quoted_flags):
        if quoted:
            dashed = bool(_DASH_DIALOGUE_RE.match(sentence))
            active_dash_turn = False
        elif _DASH_DIALOGUE_RE.match(sentence):
            dashed = True
            active_dash_turn = _dash_turn_stays_open(sentence)
        elif _DASH_NARRATION_START_RE.match(sentence):
            dashed = False
            active_dash_turn = False
        elif active_dash_turn and not _NARRATION_CONTINUATION_RE.search(sentence):
            dashed = True
            active_dash_turn = _dash_turn_stays_open(sentence)
        else:
            dashed = False
            active_dash_turn = False
        flags.append((quoted, dashed))
    return flags


def _classify_dialogue_sentences(
    sentences: list[str],
    paragraph: str | None = None,
) -> list[bool]:
    """Mark sentences of one paragraph as dialogue (quoted or dash turn)."""
    return [
        quoted or dashed
        for quoted, dashed in _dialogue_sentence_flags(sentences, paragraph)
    ]


def _quote_inner(match: re.Match | None) -> str:
    if not match:
        return ""
    return next((group for group in match.groups() if group is not None), "")


def _quote_inner_span(match: re.Match) -> tuple[int, int]:
    for group_index, group in enumerate(match.groups(), start=1):
        if group is not None:
            return match.start(group_index), match.end(group_index)
    return match.start(), match.end()


def build_speaker_units(text: str) -> list[dict]:
    """Split prose into stable, numbered units suitable for LLM attribution.

    Quoted passages and dash-led Hungarian dialogue become isolated candidates;
    narration remains available as context.  The same units are later consumed
    by ``enrich_chapter`` so stored unit indexes stay deterministic.
    """
    text = _normalize_source_text(text)
    units: list[dict] = []
    next_turn_index = 0
    paragraphs = _split_paragraphs(text)
    for paragraph in paragraphs:
        paragraph_start = len(units)
        active_dash_turn: int | None = None
        protected = _protect_sentence_boundaries(paragraph)
        protected = re.sub(
            r'([.!?\u2026]["\u201d\u00bb]?)\s+'
            r'(?=(?:[-\u2013\u2014]\s+|["\u201c\u201e\u00ab]?[A-ZÁÉÍÓÖŐÚÜŰ0-9]))',
            rf"\1{_SPLIT}",
            protected,
        )
        sentences = [
            _restore_sentence_boundaries(part).strip()
            for part in protected.split(_SPLIT)
            if _restore_sentence_boundaries(part).strip()
        ]
        for sentence in sentences:
            quote_parts: list[tuple[str, bool]] = []
            cursor = 0
            for match in _PAIRED_DIALOGUE_RE.finditer(sentence):
                prefix = sentence[cursor:match.start()].strip()
                if prefix:
                    quote_parts.append((prefix, False))
                quote_parts.append((match.group(0).strip(), True))
                cursor = match.end()
            suffix = sentence[cursor:].strip()
            if suffix:
                quote_parts.append((suffix, False))
            if not quote_parts:
                quote_parts = [(sentence, False)]

            for part, quoted in quote_parts:
                dash_parts = [
                    fragment.strip()
                    for fragment in _DASH_SPLIT_RE.split(part)
                    if fragment.strip()
                ]
                for fragment in dash_parts:
                    previous_turn = active_dash_turn
                    attribution = (
                        _DASH_ATTRIBUTION_CONTINUATION_RE.match(fragment)
                        if not quoted else None
                    )
                    pieces = (
                        [
                            (attribution.group(1), False, None, False),
                            (
                                f"- {attribution.group(2)}",
                                True,
                                previous_turn,
                                True,
                            ),
                        ]
                        if attribution else
                        [(fragment, None, None, False)]
                    )
                    for piece_text, forced_candidate, forced_turn, continuation in pieces:
                        starts_dialogue = bool(
                            quoted or _DASH_DIALOGUE_RE.match(piece_text)
                        )
                        starts_narration = bool(
                            re.match(
                                r"^\s*[-\u2013\u2014]\s+"
                                r"[a-záéíóöőúüű]",
                                piece_text,
                            )
                        )

                        if forced_candidate is not None:
                            candidate = forced_candidate
                            turn_index = forced_turn
                            if candidate and turn_index is None:
                                turn_index = next_turn_index
                                next_turn_index += 1
                            active_dash_turn = turn_index if candidate else None
                        elif starts_dialogue:
                            candidate = True
                            turn_index = next_turn_index
                            next_turn_index += 1
                            active_dash_turn = None if quoted else turn_index
                            continuation = False
                        elif starts_narration:
                            candidate = False
                            turn_index = None
                            active_dash_turn = None
                            continuation = False
                        elif (
                            active_dash_turn is not None
                            and not _NARRATION_CONTINUATION_RE.search(piece_text)
                        ):
                            candidate = True
                            turn_index = active_dash_turn
                            continuation = True
                        else:
                            candidate = False
                            turn_index = None
                            if active_dash_turn is not None:
                                active_dash_turn = None
                            continuation = False

                        units.append(
                            {
                                "index": len(units),
                                "text": piece_text.strip(),
                                "dialogue_candidate": candidate,
                                "turn_index": turn_index,
                                "continuation": continuation,
                                "ends_paragraph": False,
                            }
                        )
        if len(units) > paragraph_start:
            units[-1]["ends_paragraph"] = True
    return units


def expand_speaker_annotations(
    text: str,
    speaker_annotations: dict[int, str],
) -> dict[int, str]:
    """Fill unassigned continuation sentences inside an unambiguous turn.

    Explicit annotations, including a manual empty-string narration override,
    always win.  A turn is inherited only when its existing non-empty
    assignments all name the same speaker.
    """
    expanded = dict(speaker_annotations)
    units = build_speaker_units(text)
    turn_units: dict[int, list[dict]] = {}
    for unit in units:
        turn_index = unit.get("turn_index")
        if unit.get("dialogue_candidate") and turn_index is not None:
            turn_units.setdefault(int(turn_index), []).append(unit)

    for members in turn_units.values():
        assigned = {
            str(expanded[unit["index"]]).strip()
            for unit in members
            if unit["index"] in expanded and str(expanded[unit["index"]]).strip()
        }
        if len(assigned) != 1:
            continue
        speaker = next(iter(assigned))
        for unit in members:
            if unit["index"] not in expanded:
                expanded[unit["index"]] = speaker

    return expanded


def _has_dialogue(text: str) -> bool:
    return bool(
        _STANDALONE_QUOTE_RE.search(text) or _DASH_DIALOGUE_RE.match(text)
    )


def _should_merge_sentences(
    buffer: str,
    sentence: str,
    buffer_dialogue: bool | None = None,
    sentence_dialogue: bool | None = None,
) -> bool:
    if not buffer or not sentence:
        return False
    if buffer_dialogue is None:
        buffer_dialogue = _has_dialogue(buffer)
    if sentence_dialogue is None:
        sentence_dialogue = _has_dialogue(sentence)
    if buffer_dialogue and _ATTRIBUTION_SENTENCE_RE.match(sentence):
        return True
    if _ATTRIBUTION_SENTENCE_RE.match(buffer) and sentence_dialogue:
        return True
    if _QUESTION_RE.search(buffer) or _SURPRISE_RE.search(buffer):
        return False
    if buffer_dialogue != sentence_dialogue:
        return False
    combined_words = len((buffer + " " + sentence).split())
    if combined_words > 30:
        return False
    if len(buffer.split()) < 8 or len(sentence.split()) < 4:
        return True
    return not buffer_dialogue and not sentence_dialogue and combined_words <= 22


def _merge_paragraph_sentence_units(paragraph: str) -> list[tuple[str, bool]]:
    """Short-sentence merge within one paragraph, keeping dialogue flags.

    Merge decisions look at each text exactly as before (plus the dash-turn
    context), so English segmentation is unchanged.  The returned flag is
    richer: a segment is dialogue when it lies inside a quotation (even one
    spanning several sentences) or inside a dash-led dialogue turn.
    """
    sentences = _split_paragraph_sentences(paragraph)
    if not sentences:
        return []
    flags = _dialogue_sentence_flags(sentences, paragraph)

    segments: list[tuple[str, bool]] = []
    buffer = ""
    buffer_quoted = buffer_dashed = False
    for sentence, (quoted, dashed) in zip(sentences, flags):
        if not buffer:
            buffer, buffer_quoted, buffer_dashed = sentence, quoted, dashed
        elif _should_merge_sentences(
            buffer,
            sentence,
            _has_dialogue(buffer) or buffer_dashed,
            _has_dialogue(sentence) or dashed,
        ):
            buffer = f"{buffer} {sentence}".strip()
            buffer_quoted = buffer_quoted or quoted
            buffer_dashed = buffer_dashed or dashed
        else:
            segments.append(
                (buffer, buffer_quoted or buffer_dashed or _has_dialogue(buffer))
            )
            buffer, buffer_quoted, buffer_dashed = sentence, quoted, dashed
    if buffer:
        segments.append(
            (buffer, buffer_quoted or buffer_dashed or _has_dialogue(buffer))
        )
    return [
        (piece, is_dialogue)
        for segment, is_dialogue in segments
        for piece in _split_oversized_segment(segment)
    ]


def _merge_paragraph_sentences(paragraph: str) -> list[str]:
    """Apply the normal short-sentence merge within one paragraph."""
    return [text for text, _ in _merge_paragraph_sentence_units(paragraph)]


def _split_sentences(text: str, chapter_title: str | None = None) -> list[str]:
    return [
        unit["text"]
        for unit in _split_sentence_units(text, chapter_title)
    ]


def _split_sentence_units(
    text: str,
    chapter_title: str | None = None,
) -> list[dict]:
    units: list[dict] = []
    for paragraph in _split_paragraphs(text, chapter_title):
        segments = _merge_paragraph_sentence_units(paragraph)
        for index, (segment, is_dialogue) in enumerate(segments):
            units.append({
                "index": None,
                "text": segment,
                "dialogue_candidate": is_dialogue,
                "ends_paragraph": index == len(segments) - 1,
            })
    return units


def _single_narrator_paragraph_units(
    paragraph: str,
    max_words: int,
) -> list[tuple[str, bool]]:
    output: list[tuple[str, bool]] = []
    buffer = ""
    buffer_kind: tuple[bool, bool] | None = None

    for segment, is_dialogue in _merge_paragraph_sentence_units(paragraph):
        kind = (is_dialogue, bool(_WHISPER_RE.search(segment)))
        combined_words = len((buffer + " " + segment).split())
        closes_emphatically = bool(
            buffer and (_QUESTION_RE.search(buffer) or _SURPRISE_RE.search(buffer))
        )
        if (
            buffer
            and kind == buffer_kind
            and combined_words <= max_words
            and not closes_emphatically
        ):
            buffer = f"{buffer} {segment}".strip()
        else:
            if buffer:
                output.append((buffer, bool(buffer_kind and buffer_kind[0])))
            buffer = segment
            buffer_kind = kind

    if buffer:
        output.append((buffer, bool(buffer_kind and buffer_kind[0])))
    return output


def _split_single_narrator_segments(
    text: str,
    chapter_title: str | None = None,
    max_words: int = 60,
) -> list[str]:
    """Build longer paragraph-local blocks when every line uses one voice.

    Speaker turns do not need separate model conditioning in this mode.  Keep
    dialogue/narration and whisper boundaries for prosody, while combining
    adjacent compatible text up to OmniVoice's useful long-form range.
    """
    max_words = max(30, int(max_words))
    return [
        segment
        for paragraph in _split_paragraphs(text, chapter_title)
        for segment, _ in _single_narrator_paragraph_units(paragraph, max_words)
    ]


def _split_single_narrator_units(
    text: str,
    chapter_title: str | None = None,
    max_words: int = 60,
) -> list[dict]:
    units: list[dict] = []
    max_words = max(30, int(max_words))
    for paragraph in _split_paragraphs(text, chapter_title):
        paragraph_segments = _single_narrator_paragraph_units(paragraph, max_words)
        for index, (segment, is_dialogue) in enumerate(paragraph_segments):
            units.append({
                "index": None,
                "text": segment,
                "dialogue_candidate": is_dialogue,
                "ends_paragraph": index == len(paragraph_segments) - 1,
            })
    return units


def _dash_dialogue_text(sentence: str) -> str:
    """Spoken part of a dash-led line, up to the narrator's interjection."""
    if not _DASH_DIALOGUE_RE.match(sentence):
        return ""
    body = _LEADING_DASH_RE.sub("", sentence, count=1)
    toggle = _DASH_TOGGLE_RE.search(body)
    if toggle:
        body = body[:toggle.start()]
    return body.strip()


def _build_dialogue_map(text: str) -> dict[str, str]:
    """Map dialogue snippets to detected speaker names."""
    mapping = {}
    for match in _DIALOGUE_RE.finditer(text):
        dialogue = match.group("text1") or match.group("text2") or ""
        speaker = match.group("name1") or match.group("name2") or ""
        if dialogue and speaker:
            mapping[dialogue.strip()[:60]] = speaker.strip()

    # Hungarian verb-first attributions, matched paragraph by paragraph:
    # „Gyere ide!” – mondta Anna.   /   – Gyere ide – mondta Anna.
    for paragraph in _split_paragraphs(text):
        for pattern in (_HU_NAME_FIRST_ATTRIBUTION_RE, _HU_QUOTE_ATTRIBUTION_RE):
            for match in pattern.finditer(paragraph):
                dialogue = match.group("text").strip()
                if len(dialogue) >= 2:
                    mapping[dialogue[:60]] = match.group("name").strip()
        for sentence in _split_paragraph_sentences(paragraph):
            match = _HU_DASH_ATTRIBUTION_RE.match(sentence)
            if not match:
                continue
            dialogue = _dash_dialogue_text(sentence) or match.group("text").strip()
            if len(dialogue) >= 2:
                mapping[dialogue[:60]] = match.group("name").strip()
    return mapping


def _find_speaker(sentence: str, dialogue_map: dict, last_speaker: str | None) -> str | None:
    if not sentence:
        return None

    inner = _STANDALONE_QUOTE_RE.search(sentence)
    snippet = (
        _quote_inner(inner).strip()[:60]
        if inner
        else _dash_dialogue_text(sentence)[:60]
    )
    if len(snippet) >= 2:
        for key, name in dialogue_map.items():
            if key in snippet or snippet in key:
                return name

    return None


def _explicit_tag_text(sentence: str) -> str:
    sentence = str(sentence or "").strip()
    inner = _STANDALONE_QUOTE_RE.search(sentence)
    if not inner:
        return sentence

    quoted = _quote_inner(inner).strip()
    prefix = sentence[:inner.start()].strip(" ,.-")
    suffix = sentence[inner.end():].strip(" ,.-")
    parts = [part for part in (quoted, prefix, suffix) if part]
    return " ".join(parts).strip()


_CONFIRMATION_PHRASES = frozenset({
    "yes", "yeah", "yep", "yup", "okay", "ok", "sure", "right",
    "indeed", "exactly", "correct", "of course", "certainly",
    "i know", "i see", "all right",
    "igen", "persze", "rendben", "jó", "jól van", "értem", "úgy van",
    "így van", "pontosan", "hogyne", "természetesen", "igaz", "valóban",
    "helyes", "igenis", "na jó",
})


def _should_allow_confirmation_tag(text: str, is_dialogue: bool) -> bool:
    if not is_dialogue:
        return False

    normalized = re.sub(r"[^\w\s']|_", " ", str(text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False

    words = normalized.split()
    if len(words) > 4:
        return False

    return normalized in _CONFIRMATION_PHRASES


def _select_expression_tag(sentence: str, context: str, is_dialogue: bool) -> str | None:
    tag_text = _explicit_tag_text(sentence)

    # OmniVoice question/surprise tags are literal non-verbal vocalizations
    # ("oh", "ah", and similar), not silent prosody controls.  Punctuation must
    # therefore never add them automatically.  Keep only sound tags supported
    # by explicit sentence-local wording, such as laughter or a sigh.
    if is_dialogue:
        for pattern, tag in _TAG_RULES:
            if not pattern.search(tag_text):
                continue
            if tag == "[confirmation-en]" and not _should_allow_confirmation_tag(tag_text, is_dialogue):
                continue
            return tag

    return None


def _inject_tags(sentence: str, context: str, is_dialogue: bool) -> tuple[str, str | None]:
    tag = _select_expression_tag(sentence, context, is_dialogue)
    if not tag:
        return sentence, None

    inner = _STANDALONE_QUOTE_RE.search(sentence)
    leading_dash = _LEADING_DASH_RE.match(sentence)
    if inner:
        quoted = _quote_inner(inner).strip()
        enriched_quoted = f"{tag} {quoted}".strip()
        start, end = _quote_inner_span(inner)
        sentence = sentence[:start] + enriched_quoted + sentence[end:]
    elif leading_dash:
        # Hungarian dash dialogue: the tag belongs to the spoken line, just as
        # it goes inside the quotation marks above ("– [laughter] Ez jó!").
        sentence = (
            sentence[:leading_dash.end()] + f"{tag} " + sentence[leading_dash.end():]
        )
    else:
        sentence = f"{tag} {sentence}".strip()

    return sentence, tag


def _segment_speed(sentence: str, is_dialogue: bool, scene_speed: float, tag: str | None, is_whisper: bool) -> float:
    speed = scene_speed if is_dialogue else min(scene_speed, 1.05)

    if is_whisper or tag == "[sigh]":
        speed = min(speed, 0.94)
    elif tag == "[dissatisfaction-hnn]":
        speed = min(speed, 0.97)          # muttering is deliberate and slow
    elif tag == "[question-oh]":
        speed = min(speed, 0.96)          # shocked questions land harder when slower
    elif tag in {"[surprise-wa]", "[laughter]"} and is_dialogue:
        speed = max(speed, 1.03)          # shock and laughter burst out faster
    elif tag == "[surprise-yo]":
        speed = max(speed, 1.05)          # excited triumph is animated

    if "..." in sentence or " -- " in sentence or ";" in sentence or ":" in sentence:
        speed = min(speed, 0.98)

    return round(speed, 2)


_HONORIFICS = ("bácsi", "néni", "úr", "asszony", "kisasszony", "doktor", "tanár úr")


def _resolve_character_name(name: str | None, character_map: dict) -> str | None:
    """Match an attributed name ("Anna", "Imre bácsi") to a detected character
    ("Kovács Anna", "Nagy Imre"). Ambiguous short names stay unassigned."""
    if not name:
        return None
    name = name.strip()
    if name in character_map:
        return name
    lowered = name.lower()
    for honorific in _HONORIFICS:
        if lowered.endswith(" " + honorific):
            return _resolve_character_name(name[: -len(honorific)].strip(), character_map)
    parts = name.split()
    if len(parts) != 1:
        return None
    owners = [full for full in character_map
              if len(full.split()) >= 2 and name in (full.split()[0], full.split()[-1])]
    return owners[0] if len(owners) == 1 else None


def enrich_chapter(
    chapter_text: str,
    character_map: dict,
    narrator_instruct: str = "male, middle-aged, low pitch",
    single_narrator_mode: bool = False,
    chapter_title: str | None = None,
    speaker_annotations: dict[int, str] | None = None,
) -> list[dict]:
    """
    Return segment dicts used by playback and export.
    """
    cleaned_text = _normalize_source_text(chapter_text).strip()
    dialogue_map = _build_dialogue_map(cleaned_text)
    # Speaker annotations deliberately use fine-grained units so dialogue can
    # switch voices at exact boundaries. In single-narrator mode those
    # boundaries carry no routing information and would turn a chapter into
    # hundreds of tiny model jobs.
    if single_narrator_mode:
        speaker_annotations = None
        sentence_units = _split_single_narrator_units(
            cleaned_text,
            chapter_title=chapter_title,
        )
    elif speaker_annotations is None:
        sentence_units = _split_sentence_units(
            cleaned_text,
            chapter_title=chapter_title,
        )
    else:
        sentence_units = build_speaker_units(cleaned_text)

    bounded_units: list[dict] = []
    for unit in sentence_units:
        pieces = _split_oversized_segment(unit["text"])
        for piece_index, piece in enumerate(pieces):
            bounded_units.append({
                **unit,
                "text": piece,
                "ends_paragraph": bool(unit.get("ends_paragraph"))
                and piece_index == len(pieces) - 1,
            })
    sentence_units = bounded_units
    scene_speed = _scene_speed(cleaned_text)

    segments = []
    last_speaker = None
    # A paragraph is one speaker's turn: an unattributed dialogue sentence
    # continues the speaker already found earlier in the same paragraph.
    paragraph_speaker = None
    for sequence_index, unit in enumerate(sentence_units):
        unit_index = unit["index"]
        sentence = unit["text"]
        sentence = sentence.strip()
        if not sentence:
            if unit.get("ends_paragraph"):
                paragraph_speaker = None
            continue

        if speaker_annotations is None:
            # The splitter already classified the unit with paragraph context
            # (e.g. a sentence continuing a dash-led Hungarian dialogue turn).
            is_dialogue = bool(unit.get("dialogue_candidate")) or _has_dialogue(sentence)
            speaker = _find_speaker(sentence, dialogue_map, last_speaker) if is_dialogue else None
        else:
            speaker = speaker_annotations.get(unit_index)
            is_dialogue = bool(speaker)
        speaker = speaker or None
        if speaker and speaker not in character_map:
            speaker = _resolve_character_name(speaker, character_map)
        if speaker_annotations is None:
            if speaker:
                paragraph_speaker = speaker
            elif is_dialogue and paragraph_speaker:
                speaker = paragraph_speaker
            if unit.get("ends_paragraph"):
                paragraph_speaker = None
        if speaker:
            last_speaker = speaker

        is_whisper = bool(_WHISPER_RE.search(sentence))
        character_name = None if single_narrator_mode else speaker
        if single_narrator_mode:
            instruct = narrator_instruct
        else:
            instruct = (
                character_map.get(speaker, {}).get("instruct", narrator_instruct)
                if speaker
                else narrator_instruct
            )

        if is_whisper and single_narrator_mode:
            if "whisper" not in narrator_instruct:
                instruct = narrator_instruct + ", whisper"
        elif is_whisper and speaker and speaker in character_map:
            base = character_map[speaker].get("instruct", narrator_instruct)
            if "whisper" not in base:
                instruct = base + ", whisper"

        # Use the last 3 sentences as context so multi-sentence scene build-up
        # (e.g. shock/surprise described two sentences before the dialogue) is
        # captured and the correct emotion tag is selected.
        context = sentence.strip()
        enriched, tag = _inject_tags(sentence, context, is_dialogue)
        speed = _segment_speed(sentence, is_dialogue, scene_speed, tag, is_whisper)
        segments.append(
            {
                "text": sentence,
                "enriched_text": enriched,
                "character_name": character_name,
                "instruct": instruct,
                "speed": speed,
                "is_dialogue": is_dialogue,
                "is_whisper": is_whisper,
                "unit_index": unit_index,
                "speaker_candidate": bool(unit["dialogue_candidate"]),
                "ends_paragraph": bool(unit.get("ends_paragraph")),
            }
        )

    return segments
