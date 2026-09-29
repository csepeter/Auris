"""Shared Hungarian lexicon: abbreviations, units, acronyms and letter names.

Used by the sentence splitter (which must not break after an abbreviation)
and by the speech normalizer (which expands them for TTS). Keys are matched
case-sensitively unless noted; the displayed text never changes.
"""

from __future__ import annotations

# Abbreviations that end with a period and are read as full words.
# Values are the spoken form. Longest keys are matched first.
ABBREVIATIONS: dict[str, str] = {
    'pl.': 'például',
    'Pl.': 'Például',
    'kb.': 'körülbelül',
    'Kb.': 'Körülbelül',
    'stb.': 'és a többi',
    'ill.': 'illetve',
    'ún.': 'úgynevezett',
    'Ún.': 'Úgynevezett',
    'vö.': 'vesd össze',
    'Vö.': 'Vesd össze',
    'ld.': 'lásd',
    'Ld.': 'Lásd',
    'ti.': 'tudniillik',
    'Ti.': 'Tudniillik',
    'uo.': 'ugyanott',
    'u.': 'utca',
    'krt.': 'körút',
    'ker.': 'kerület',
    'sz.': 'szám',
    'hsz.': 'házszám',
    'em.': 'emelet',
    'szül.': 'született',
    'ifj.': 'ifjabb',
    'Ifj.': 'Ifjabb',
    'id.': 'idősebb',
    'Id.': 'Idősebb',
    'özv.': 'özvegy',
    'Özv.': 'Özvegy',
    'dr.': 'doktor',
    'Dr.': 'Doktor',
    'prof.': 'professzor',
    'Prof.': 'Professzor',
    'gr.': 'gróf',
    'br.': 'báró',
    'tel.': 'telefon',
    'Tel.': 'Telefon',
    'ford.': 'fordította',
    'szerk.': 'szerkesztette',
    'kiad.': 'kiadó',
    'évf.': 'évfolyam',
    'old.': 'oldal',
    'max.': 'maximum',
    'min.': 'minimum',
    'Kft.': 'káefté',
    'Zrt.': 'zéerté',
    'Nyrt.': 'nyéerté',
    'Bt.': 'bété',
    'Rt.': 'erté',
    'jan.': 'január',
    'febr.': 'február',
    'márc.': 'március',
    'ápr.': 'április',
    'máj.': 'május',
    'jún.': 'június',
    'júl.': 'július',
    'aug.': 'augusztus',
    'szept.': 'szeptember',
    'okt.': 'október',
    'nov.': 'november',
    'dec.': 'december',
}

# Multi-token abbreviations (checked before single ones).
PHRASE_ABBREVIATIONS: dict[str, str] = {
    'i. e.': 'időszámításunk előtt',
    'i. sz.': 'időszámításunk szerint',
    'Kr. e.': 'Krisztus előtt',
    'Kr. u.': 'Krisztus után',
    's. k.': 'saját kezűleg',
    'a. m.': 'annyi mint',
}

# Abbreviations after which a sentence practically never ends. The splitter
# must keep them together with the next word even before a capital letter.
NON_TERMINAL_ABBREVIATIONS: frozenset[str] = frozenset(
    key.lower() for key in ABBREVIATIONS
) - {'stb.', 'uo.'}

# Units read after a number. Only unambiguous symbols; "ha" (hectare) and
# "t" are skipped because they are also ordinary words.
UNITS: dict[str, str] = {
    'km/h': 'kilométer per óra',
    'km/ó': 'kilométer per óra',
    'm/s': 'méter per szekundum',
    'km²': 'négyzetkilométer',
    'm²': 'négyzetméter',
    'm2': 'négyzetméter',
    'm³': 'köbméter',
    'm3': 'köbméter',
    'cm³': 'köbcentiméter',
    'km': 'kilométer',
    'cm': 'centiméter',
    'mm': 'milliméter',
    'm': 'méter',
    'kg': 'kilogramm',
    'dkg': 'dekagramm',
    'mg': 'milligramm',
    'g': 'gramm',
    'l': 'liter',
    'dl': 'deciliter',
    'cl': 'centiliter',
    'ml': 'milliliter',
    'db': 'darab',
    'mp': 'másodperc',
    'ó': 'óra',
    'p': 'perc',
    'kW': 'kilowatt',
    'W': 'watt',
    'V': 'volt',
    'kWh': 'kilowattóra',
    'MB': 'megabájt',
    'GB': 'gigabájt',
    'Ft': 'forint',
    'HUF': 'forint',
    'EUR': 'euró',
    'USD': 'dollár',
}

# Currency symbols that may precede or follow the amount.
CURRENCY_SYMBOLS: dict[str, str] = {
    '€': 'euró',
    '$': 'dollár',
    '£': 'font',
}

# Acronyms with an established spoken form.
ACRONYMS: dict[str, str] = {
    'EU': 'e-u',
    'USA': 'u-es-á',
    'BKV': 'békávé',
    'OTP': 'ótépé',
    'MTA': 'emtéá',
    'BME': 'béemé',
    'KSH': 'káesshá',
    'SZJA': 'eszjéá',
    'TV': 'tévé',
    'PC': 'pécé',
    'CD': 'cédé',
    'DVD': 'dévédé',
    'SMS': 'esemes',
    'PDF': 'pédéef',
    'GPS': 'gépées',
    'ÁFA': 'áfa',
    'MÁV': 'máv',
    'ELTE': 'elte',
    'NATO': 'nátó',
    'ENSZ': 'ensz',
    'MOL': 'mol',
}

# Hungarian letter names for spelling out unknown consonant-only acronyms.
LETTER_NAMES: dict[str, str] = {
    'A': 'a', 'Á': 'á', 'B': 'bé', 'C': 'cé', 'CS': 'csé', 'D': 'dé', 'DZ': 'dzé',
    'E': 'e', 'É': 'é', 'F': 'ef', 'G': 'gé', 'GY': 'gyé', 'H': 'há', 'I': 'i',
    'Í': 'í', 'J': 'jé', 'K': 'ká', 'L': 'el', 'LY': 'elipszilon', 'M': 'em',
    'N': 'en', 'NY': 'eny', 'O': 'ó', 'Ó': 'ó', 'Ö': 'ö', 'Ő': 'ő', 'P': 'pé',
    'Q': 'kú', 'R': 'er', 'S': 'es', 'SZ': 'esz', 'T': 'té', 'TY': 'tyé', 'U': 'u',
    'Ú': 'ú', 'Ü': 'ü', 'Ű': 'ű', 'V': 'vé', 'W': 'duplavé', 'X': 'iksz',
    'Y': 'ipszilon', 'Z': 'zé', 'ZS': 'zsé',
}

HUNGARIAN_VOWELS = 'aáeéiíoóöőuúüűAÁEÉIÍOÓÖŐUÚÜŰ'
HUNGARIAN_UPPER = 'A-ZÁÉÍÓÖŐÚÜŰ'
HUNGARIAN_LOWER = 'a-záéíóöőúüű'

MONTHS: tuple[str, ...] = (
    'január', 'február', 'március', 'április', 'május', 'június',
    'július', 'augusztus', 'szeptember', 'október', 'november', 'december',
)

# Verbs that attribute a line of dialogue to its speaker ("– mondta Anna").
ATTRIBUTION_VERBS: tuple[str, ...] = (
    'mondta', 'mondja', 'kérdezte', 'kérdezi', 'felelte', 'feleli', 'válaszolta',
    'válaszolja', 'szólt', 'szól', 'kiáltotta', 'kiáltja', 'kiabálta', 'suttogta',
    'suttogja', 'súgta', 'súgja', 'motyogta', 'dünnyögte', 'morogta', 'mormolta',
    'sóhajtotta', 'nevetett', 'nevette', 'kacagta', 'vágta rá', 'jegyezte meg',
    'tette hozzá', 'folytatta', 'magyarázta', 'erősködött', 'ismételte',
    'kiabált', 'üvöltötte', 'ordította', 'sikította', 'dadogta', 'hebegte',
    'szipogta', 'zokogta', 'nyögte', 'vetette közbe', 'szólalt meg', 'kezdte',
    'bólintott', 'mosolygott', 'intett', 'tiltakozott', 'könyörgött',
)

WHISPER_WORDS: tuple[str, ...] = (
    'suttogta', 'suttogja', 'suttogva', 'súgta', 'súgja', 'halkan', 'suttogó',
    'fojtott hangon', 'lehelte',
)
LAUGHTER_WORDS: tuple[str, ...] = (
    'nevetett', 'nevetve', 'nevette', 'kacagott', 'kacagta', 'kuncogott',
    'vihogott', 'hahotázott', 'felnevetett',
)
SIGH_WORDS: tuple[str, ...] = ('sóhajtott', 'sóhajtotta', 'sóhajtva', 'felsóhajtott')
SHOUT_WORDS: tuple[str, ...] = (
    'kiáltotta', 'kiabálta', 'üvöltötte', 'ordította', 'rikoltotta', 'kiáltott',
    'kiabált', 'üvöltött', 'ordított',
)
SLOW_WORDS: tuple[str, ...] = (
    'lassan', 'megfontoltan', 'vontatottan', 'elnyújtva', 'tagoltan', 'komótosan',
)
FAST_WORDS: tuple[str, ...] = ('hadarta', 'gyorsan', 'sietve', 'kapkodva', 'hadarva')
