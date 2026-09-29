import re
import hashlib
import random
import logging
from collections import Counter

log = logging.getLogger(__name__)

# ── spaCy (required) ──────────────────────────────────────────────────────────
# spaCy + en_core_web_sm are required for full character detection.
# If not yet installed the app degrades to regex-based detection and
# shows a warning in the Settings page.

_nlp = None
_spacy_error: str = ''
_hu_nlp = None
_hu_nlp_checked = False

# HuSpaCy models, best first. Any installed one enables Hungarian NER.
HUNGARIAN_SPACY_MODELS = ('hu_core_news_lg', 'hu_core_news_md', 'hu_core_news_trf')


def _get_hungarian_nlp():
    """Return a loaded HuSpaCy pipeline or None (regex fallback)."""
    global _hu_nlp, _hu_nlp_checked
    if _hu_nlp is not None or _hu_nlp_checked:
        return _hu_nlp
    _hu_nlp_checked = True
    try:
        import spacy
    except ImportError:
        return None
    for model in HUNGARIAN_SPACY_MODELS:
        try:
            _hu_nlp = spacy.load(model)
            log.info('Hungarian character detection uses %s', model)
            return _hu_nlp
        except (OSError, ValueError):
            continue
        except Exception as exc:
            log.warning('Unable to load %s: %s', model, exc)
    return None


def _get_nlp():
    global _nlp, _spacy_error
    if _nlp is not None:
        return _nlp
    try:
        import spacy
        _nlp = spacy.load('en_core_web_sm')
        _spacy_error = ''
        return _nlp
    except ImportError:
        _spacy_error = 'A spaCy nincs telepítve.'
        log.warning(_spacy_error)
        return None
    except OSError:
        _spacy_error = (
            'Az en_core_web_sm angol modell nem található. '
            'Beállítások → Szereplőfelismerés → Angol modell telepítése.'
        )
        log.warning(_spacy_error)
        return None


def reset_nlp_cache() -> None:
    """Forget loaded pipelines after a model install."""
    global _nlp, _spacy_error, _hu_nlp, _hu_nlp_checked
    _nlp = None
    _spacy_error = ''
    _hu_nlp = None
    _hu_nlp_checked = False


def spacy_ready() -> bool:
    return _get_nlp() is not None


def spacy_error() -> str:
    _get_nlp()  # trigger detection
    return _spacy_error


# ── Name → gender lookup (common English names) ──────────────────────────────

MALE_NAMES = {
    'james','john','robert','michael','william','david','richard','joseph','thomas','charles',
    'christopher','daniel','matthew','anthony','mark','donald','steven','paul','andrew','kenneth',
    'joshua','kevin','brian','george','edward','ronald','timothy','jason','jeffrey','ryan',
    'jacob','gary','nicholas','eric','jonathan','stephen','larry','justin','scott','brandon',
    'benjamin','samuel','raymond','gregory','frank','alexander','patrick','jack','dennis','jerry',
    'tyler','aaron','henry','jose','adam','douglas','nathan','peter','zachary','kyle','henry',
    'walter','arthur','carl','albert','clarence','ralph','roy','eugene','wayne','louis',
    'harry','liam','noah','oliver','elijah','lucas','mason','ethan','aiden','logan','caleb',
    'sebastian','julian','ezra','miles','finn','leo','theo','max','felix','hugo','oscar',
    'edgar','ernest','victor','harold','claude','leon','otto','fred','alfred','edgar',
    'gilbert','roland','benedict','gabriel','raphael','dominic','marcus','julius',
    'sherlock','watson','holmes','darcy','heathcliff','rochester','pip','oliver','fagin',
    'tom','huck','atticus','gatsby','dorian','basil','doyle','dickens','austen',
}

FEMALE_NAMES = {
    'mary','patricia','jennifer','linda','barbara','elizabeth','susan','jessica','sarah','karen',
    'lisa','nancy','betty','margaret','sandra','ashley','dorothy','kimberly','emily','donna',
    'michelle','carol','amanda','melissa','deborah','stephanie','rebecca','sharon','laura','cynthia',
    'kathleen','amy','angela','shirley','anna','brenda','pamela','emma','nicole','helen','samantha',
    'katherine','christine','debra','rachel','carolyn','janet','catherine','maria','heather',
    'diane','julie','joyce','victoria','kelly','christina','joan','evelyn','lauren','judith',
    'olivia','sophia','isabella','ava','mia','charlotte','amelia','harper','abigail','ella',
    'scarlett','grace','lily','aria','chloe','penelope','layla','riley','zoey','nora','luna',
    'eleanor','violet','aurora','stella','hazel','alice','claire','audrey','ruby','alice',
    'jane','anne','margaret','dorothy','edith','mabel','ethel','florence','beatrice','cecily',
    'esme','clarice','eliza','lydia','catherine','marianne','elinor','emma','harriet','fanny',
    'hester','hattie','nell','daisy','molly','flora','rose','iris','vera','grace','pearl',
    'hermione','ginny','luna','lavender','parvati','tonks','narcissa','bellatrix',
    'katniss','primrose','effie','johanna','clove','glimmer','rue',
}


# Common Hungarian given names and forms of address. Hungarian order is
# family name first ("Kovács Anna"), so every token of a name is checked.
HUNGARIAN_MALE_NAMES = {
    'ádám', 'ákos', 'albert', 'alex', 'alfréd', 'andor', 'andrás', 'antal', 'árpád',
    'attila', 'balázs', 'bálint', 'barnabás', 'béla', 'bence', 'bendegúz', 'benedek',
    'botond', 'csaba', 'dániel', 'dávid', 'dénes', 'dezső', 'domonkos', 'elemér',
    'emil', 'ernő', 'ervin', 'ferenc', 'feri', 'frigyes', 'gábor', 'gáspár', 'gergely',
    'gergő', 'géza', 'gusztáv', 'győző', 'gyula', 'györgy', 'gyuri', 'henrik', 'ignác',
    'imre', 'istván', 'pista', 'iván', 'jakab', 'jános', 'jancsi', 'jenő', 'józsef',
    'jóska', 'kálmán', 'károly', 'kristóf', 'krisztián', 'lajos', 'lőrinc', 'lóránt',
    'lukács', 'márk', 'márton', 'máté', 'mátyás', 'miklós', 'mihály', 'misi', 'milán',
    'norbert', 'nándor', 'olivér', 'ottó', 'pál', 'palkó', 'péter', 'pisti', 'rezső',
    'richárd', 'róbert', 'roland', 'sándor', 'sanyi', 'sebestyén', 'szabolcs', 'szilárd',
    'tamás', 'tibor', 'tivadar', 'tódor', 'vilmos', 'viktor', 'vince', 'zalán', 'zoltán',
    'zsigmond', 'zsolt', 'bácsi', 'úr', 'uraság', 'gróf', 'báró', 'herceg', 'király',
    'atya', 'apó', 'kapitány',
}
HUNGARIAN_FEMALE_NAMES = {
    'ágnes', 'ágota', 'aliz', 'amália', 'andrea', 'anikó', 'anita', 'anna', 'anni',
    'annamária', 'boglárka', 'borbála', 'bori', 'csilla', 'dóra', 'dorina', 'dorottya',
    'edit', 'emese', 'emma', 'enikő', 'erika', 'erzsébet', 'erzsi', 'eszter', 'etelka',
    'éva', 'evelin', 'fanni', 'flóra', 'gabriella', 'gizella', 'hajnalka', 'hanna',
    'ibolya', 'ildikó', 'ilona', 'ilus', 'irén', 'izabella', 'johanna', 'judit', 'julianna',
    'juli', 'júlia', 'kata', 'katalin', 'kati', 'klára', 'kinga', 'krisztina', 'lili',
    'lilla', 'magdolna', 'margit', 'mari', 'mária', 'marianna', 'marika', 'márta',
    'melinda', 'mónika', 'nikolett', 'nóra', 'noémi', 'orsolya', 'panna', 'petra',
    'piroska', 'rebeka', 'réka', 'rita', 'rozália', 'sára', 'sarolta', 'szilvia', 'tímea',
    'terézia', 'teréz', 'valéria', 'vera', 'veronika', 'viktória', 'virág', 'zita',
    'zsófia', 'zsófi', 'zsuzsanna', 'zsuzsa', 'néni', 'asszony', 'kisasszony', 'hölgy',
    'úrnő', 'grófnő', 'hercegnő', 'királynő', 'anyó', 'nővér',
}


def detect_gender_by_name(name: str) -> str:
    tokens = [token.lower().strip('.,') for token in name.strip().split() if token]
    if not tokens:
        return 'unknown'
    first = tokens[0]
    if first in MALE_NAMES:
        return 'male'
    if first in FEMALE_NAMES:
        return 'female'
    for token in reversed(tokens):
        if token in HUNGARIAN_MALE_NAMES:
            return 'male'
        if token in HUNGARIAN_FEMALE_NAMES:
            return 'female'
        # Married names: Kovácsné, Szabó Jánosné
        if len(token) > 3 and token.endswith('né'):
            return 'female'
    return 'unknown'


def detect_gender_by_pronouns(name: str, text: str) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    male_score = 0
    female_score = 0
    name_first = name.split()[0]

    for sent in sentences:
        if name_first not in sent:
            continue
        male_score += len(re.findall(r'\b(he|him|his)\b', sent, re.IGNORECASE))
        female_score += len(re.findall(r'\b(she|her|hers)\b', sent, re.IGNORECASE))

    if male_score > female_score:
        return 'male'
    if female_score > male_score:
        return 'female'
    return 'unknown'


def detect_gender(name: str, text: str) -> str:
    gender = detect_gender_by_name(name)
    if gender != 'unknown':
        return gender
    return detect_gender_by_pronouns(name, text)


# ── Voice profile generation ──────────────────────────────────────────────────

CHAR_COLORS = [
    '#FFD700', '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4',
    '#FFEAA7', '#DDA0DD', '#98D8C8', '#F7DC6F', '#BB8FCE',
    '#85C1E9', '#82E0AA', '#F0B27A', '#AED6F1', '#A9DFBF',
]


def _hash_seed(name: str) -> int:
    return int(hashlib.md5(name.lower().encode()).hexdigest(), 16)


def generate_voice_profile(name: str, gender: str) -> dict:
    rng = random.Random(_hash_seed(name))

    ages_m = ['young adult', 'middle-aged', 'elderly']
    ages_f = ['young adult', 'middle-aged']
    ages_n = ['child', 'teenager', 'young adult', 'middle-aged', 'elderly']

    pitches_m = ['very low pitch', 'low pitch', 'moderate pitch']
    pitches_f = ['moderate pitch', 'high pitch', 'very high pitch']
    pitches_n = ['low pitch', 'moderate pitch', 'high pitch']


    if gender == 'male':
        age = rng.choice(ages_m)
        pitch = rng.choice(pitches_m)
        g = 'male'
    elif gender == 'female':
        age = rng.choice(ages_f)
        pitch = rng.choice(pitches_f)
        g = 'female'
    else:
        age = rng.choice(ages_n)
        pitch = rng.choice(pitches_n)
        g = rng.choice(['male', 'female'])

    # No accent: a random foreign accent distorts Hungarian pronunciation.
    # Users can still pick one per character in Voice Studio.
    accent = ''
    instruct = f'{g}, {age}, {pitch}'

    color_idx = _hash_seed(name) % len(CHAR_COLORS)
    color = CHAR_COLORS[color_idx]

    return {
        'instruct': instruct,
        'gender': gender,
        'age': age,
        'pitch': pitch,
        'accent': accent,
        'color_hex': color,
    }


# ── Character extraction ──────────────────────────────────────────────────────

_SAID_RE = re.compile(
    r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+'
    r'(?:said|replied|asked|answered|whispered|shouted|cried|muttered|'
    r'exclaimed|called|added|continued|laughed|sighed|groaned|snapped|'
    r'retorted|insisted|demanded|pleaded|began|noted|observed|remarked)\b'
)
_QUOTE_SAID_RE = re.compile(
    r'["""][^"""]{3,}["""]\s*[,.]?\s*'
    r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+'
    r'(?:said|replied|asked|whispered|shouted|cried|muttered|exclaimed|called)\b'
)


def extract_characters_regex(text: str, top_n: int = 20) -> list[dict]:
    counter = Counter()
    for m in _SAID_RE.finditer(text):
        counter[m.group(1)] += 1
    for m in _QUOTE_SAID_RE.finditer(text):
        counter[m.group(1)] += 1

    # Filter out common false positives
    stop_words = {
        'The', 'A', 'An', 'He', 'She', 'It', 'They', 'We', 'You', 'I',
        'But', 'And', 'Or', 'So', 'As', 'In', 'On', 'At', 'By', 'For',
        'His', 'Her', 'Their', 'Its', 'Our', 'Your',
    }
    characters = []
    for name, freq in counter.most_common(top_n * 2):
        if name in stop_words or len(name) < 2:
            continue
        characters.append({'name': name, 'frequency': freq})
        if len(characters) >= top_n:
            break
    return characters


def extract_characters_spacy(text: str, top_n: int = 20) -> list[dict]:
    nlp = _get_nlp()
    if nlp is None:
        log.warning('spaCy unavailable — falling back to regex character detection.')
        return extract_characters_regex(text, top_n)

    chunk_size = 100_000
    counter = Counter()
    for i in range(0, len(text), chunk_size):
        doc = nlp(text[i:i + chunk_size])
        for ent in doc.ents:
            if ent.label_ == 'PERSON' and len(ent.text.split()) <= 3:
                name = ent.text.strip().title()
                if len(name) > 1:
                    counter[name] += 1

    # Merge with regex results for better recall
    for m in _SAID_RE.finditer(text):
        counter[m.group(1)] += 1

    stop_words = {'He', 'She', 'It', 'They', 'The', 'A', 'His', 'Her'}
    characters = []
    for name, freq in counter.most_common(top_n * 2):
        if name in stop_words:
            continue
        characters.append({'name': name, 'frequency': freq})
        if len(characters) >= top_n:
            break
    return characters


_HU_CASE_SUFFIXES = tuple(sorted({
    'nak', 'nek', 'val', 'vel', 'ért', 'ról', 'ről', 'tól', 'től', 'hoz', 'hez', 'höz',
    'ban', 'ben', 'ba', 'be', 'ból', 'ből', 'ra', 're', 'on', 'en', 'ön', 'nál', 'nél',
    'ig', 'ként', 'kor', 'ék', 'é', 't', 'at', 'et', 'ot', 'öt', 'n',
    'tal', 'tel', 'ral', 'rel', 'lal', 'lel', 'nal', 'nel', 'sal', 'sel', 'dal', 'del',
    'mal', 'mel', 'kal', 'kel', 'gal', 'gel', 'jal', 'jel',
}, key=len, reverse=True))
_HU_LENGTHENED = {'á': 'a', 'é': 'e'}
_HU_ATTRIBUTION = (
    'mondta', 'kérdezte', 'felelte', 'válaszolta', 'szólt', 'kiáltotta',
    'kiabálta', 'suttogta', 'súgta', 'motyogta', 'morogta', 'sóhajtotta',
    'nevetett', 'folytatta', 'tette hozzá', 'jegyezte meg', 'ismételte',
    'ordította', 'üvöltötte', 'dadogta', 'hebegte', 'nyögte', 'vetette közbe',
    'szólalt meg', 'kezdte', 'magyarázta', 'erősködött',
)
_HU_NAME_RE = re.compile(
    r'(?:' + '|'.join(re.escape(v) for v in sorted(_HU_ATTRIBUTION, key=len, reverse=True))
    + r')\s+((?:[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+)(?:\s[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+)?)'
)
_HU_STOP = {
    'Az', 'Egy', 'Ő', 'Én', 'Te', 'Mi', 'Ti', 'Ők', 'Isten', 'Uram', 'De', 'És',
    'Hogy', 'Aztán', 'Majd', 'Most', 'Igen', 'Nem', 'Hát', 'Na', 'Ön', 'Önök',
}


# A sentence-initial word HuSpaCy glues onto a name ("Délre Péter", "Reggel
# Anna"): an inflected common word or a time adverb is never a surname.
_HU_LEADING_SUFFIXES = (
    'ban', 'ben', 'ból', 'ből', 'nál', 'nél', 'tól', 'től', 'hoz', 'hez', 'höz',
    'val', 'vel', 'kor', 'ért', 'ról', 'ről', 'nak', 'nek', 're', 'ra', 'ba', 'be',
)
_HU_LEADING_ADVERBS = {
    'Reggel', 'Este', 'Tegnap', 'Ma', 'Holnap', 'Akkor', 'Aztán', 'Majd', 'Közben',
    'Ekkor', 'Később', 'Délután', 'Éjjel', 'Hirtelen', 'Végül', 'Csak', 'Még', 'Már',
    'Most', 'Pedig', 'Talán', 'Persze', 'Nos',
}


def _strip_leading_non_name(name: str) -> str:
    parts = name.split()
    while len(parts) > 1:
        head = parts[0]
        lower = head.lower()
        inflected = any(lower.endswith(sfx) and len(lower) - len(sfx) >= 3
                        for sfx in _HU_LEADING_SUFFIXES)
        if head in _HU_LEADING_ADVERBS or inflected:
            parts = parts[1:]
        else:
            break
    return ' '.join(parts)


def _hungarian_base_name(name: str, known: set) -> str:
    """Map an inflected name ("Annának", "Péterrel") to a known base form."""
    head, _, last = name.rpartition(' ')
    prefix = head + ' ' if head else ''
    lower = last.lower()
    for suffix in _HU_CASE_SUFFIXES:
        if len(lower) - len(suffix) < 2 or not lower.endswith(suffix):
            continue
        stem = last[: len(last) - len(suffix)]
        candidates = [stem]
        if stem and stem[-1] in _HU_LENGTHENED:
            candidates.append(stem[:-1] + _HU_LENGTHENED[stem[-1]])
        if len(stem) > 2 and stem[-1] == stem[-2]:
            candidates.append(stem[:-1])  # Péterrel -> Péterr -> Péter
        for candidate in candidates:
            full = prefix + candidate
            if full != name and full in known:
                return full
    return name


def extract_characters_hungarian(text: str, top_n: int = 20) -> list[dict]:
    """Hungarian people via HuSpaCy NER, or dialogue-attribution regex."""
    counter: Counter = Counter()
    nlp = _get_hungarian_nlp()
    if nlp is not None:
        chunk_size = 100_000
        for i in range(0, len(text), chunk_size):
            doc = nlp(text[i:i + chunk_size])
            for ent in doc.ents:
                if ent.label_ in ('PER', 'PERSON') and len(ent.text.split()) <= 3:
                    name = _strip_leading_non_name(ent.text.strip(' .,;:!?–—-"„”»«'))
                    if len(name) > 1 and name[0].isupper():
                        counter[name] += 1
    for match in _HU_NAME_RE.finditer(text):
        counter[match.group(1)] += 2
    # Fold inflected forms into the base name that also occurs on its own.
    known = set(counter)
    merged: Counter = Counter()
    for name, freq in counter.items():
        merged[_hungarian_base_name(name, known)] += freq
    # "Anna" alone refers to "Kovács Anna" when she is the only full-name match.
    full_by_given: dict[str, list[str]] = {}
    for name in merged:
        parts = name.split()
        if len(parts) >= 2:
            full_by_given.setdefault(parts[-1], []).append(name)
    for name in list(merged):
        owners = full_by_given.get(name) if ' ' not in name else None
        if owners and len(owners) == 1:
            merged[owners[0]] += merged.pop(name)
    characters = []
    for name, freq in merged.most_common(top_n * 2):
        if name in _HU_STOP or len(name) < 2:
            continue
        characters.append({'name': name, 'frequency': freq})
        if len(characters) >= top_n:
            break
    return characters


def extract_characters(text: str, top_n: int = 20, language: str | None = None) -> list[dict]:
    if language is None:
        from core.parser.language import detect_language

        language = detect_language(text)
    if str(language or '').lower().startswith('hu'):
        chars = extract_characters_hungarian(text, top_n)
    else:
        # Always attempt spaCy (preferred); regex is only the degraded fallback
        chars = extract_characters_spacy(text, top_n)

    # Enrich with gender + voice profile
    for ch in chars:
        gender = detect_gender(ch['name'], text)
        profile = generate_voice_profile(ch['name'], gender)
        ch.update(profile)

    return chars
