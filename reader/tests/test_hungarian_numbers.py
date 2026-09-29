"""Hungarian numbers as a narrator would say them."""

import unittest
from unittest.mock import patch

from core.hungarian_numbers import (
    NORMALIZER_VERSION,
    cardinal,
    day_of_month,
    looks_hungarian,
    normalize_hungarian,
    ordinal,
    repair_hungarian_glyphs,
)
from core.tts_engine import apply_text_normalization


class CardinalTests(unittest.TestCase):
    def test_the_building_blocks(self):
        cases = {
            0: 'nulla', 1: 'egy', 2: 'kettő', 7: 'hét', 10: 'tíz',
            11: 'tizenegy', 12: 'tizenkettő', 19: 'tizenkilenc', 20: 'húsz',
            22: 'huszonkettő', 30: 'harminc', 32: 'harminckettő',
            99: 'kilencvenkilenc',
        }
        for value, expected in cases.items():
            self.assertEqual(cardinal(value), expected, value)

    def test_hundreds_use_the_short_two(self):
        self.assertEqual(cardinal(100), 'száz')
        self.assertEqual(cardinal(102), 'százkettő')
        self.assertEqual(cardinal(200), 'kétszáz')
        self.assertEqual(cardinal(932), 'kilencszázharminckettő')

    def test_thousands_and_the_hyphen_above_two_thousand(self):
        self.assertEqual(cardinal(1000), 'ezer')
        self.assertEqual(cardinal(1932), 'ezerkilencszázharminckettő')
        self.assertEqual(cardinal(2000), 'kétezer')
        self.assertEqual(cardinal(2024), 'kétezer-huszonnégy')
        self.assertEqual(cardinal(16000), 'tizenhatezer')

    def test_millions(self):
        self.assertEqual(cardinal(1_000_000), 'egymillió')
        self.assertEqual(cardinal(2_000_000), 'kétmillió')
        self.assertEqual(cardinal(3_200_000), 'hárommillió-kétszázezer')

    def test_two_becomes_short_in_front_of_a_noun(self):
        self.assertEqual(cardinal(2, before_noun=True), 'két')
        self.assertEqual(cardinal(12, before_noun=True), 'tizenkét')
        self.assertEqual(cardinal(22, before_noun=True), 'huszonkét')
        self.assertEqual(cardinal(3, before_noun=True), 'három')


class OrdinalTests(unittest.TestCase):
    def test_the_irregular_first_two(self):
        self.assertEqual(ordinal(1), 'első')
        self.assertEqual(ordinal(2), 'második')

    def test_only_the_last_element_takes_the_ending(self):
        self.assertEqual(ordinal(932), 'kilencszázharminckettedik')
        self.assertEqual(ordinal(1932), 'ezerkilencszázharminckettedik')
        self.assertEqual(ordinal(102), 'százkettedik')
        self.assertEqual(ordinal(2024), 'kétezer-huszonnegyedik')

    def test_round_numbers(self):
        self.assertEqual(ordinal(10), 'tizedik')
        self.assertEqual(ordinal(20), 'huszadik')
        self.assertEqual(ordinal(100), 'századik')
        self.assertEqual(ordinal(1000), 'ezredik')
        self.assertEqual(ordinal(1_000_000), 'egymilliomodik')

    def test_days_of_the_month(self):
        self.assertEqual(day_of_month(1), 'elseje')
        self.assertEqual(day_of_month(2), 'másodika')
        self.assertEqual(day_of_month(5), 'ötödike')
        self.assertEqual(day_of_month(10), 'tizedike')
        self.assertEqual(day_of_month(21), 'huszonegyedike')
        self.assertEqual(day_of_month(31), 'harmincegyedike')


class ReportedBugTests(unittest.TestCase):
    """The Monty Python line that started this: "az Úr 932. esztendejében"."""

    def test_a_number_with_a_period_before_a_noun_is_an_ordinal(self):
        self.assertEqual(
            normalize_hungarian('Anglia, az Úr 932. esztendejében'),
            'Anglia, az Úr kilencszázharminckettedik esztendejében',
        )

    def test_a_period_that_ends_a_sentence_is_not_an_ordinal(self):
        self.assertEqual(
            normalize_hungarian('Összesen 932. Ennyi volt.'),
            'Összesen kilencszázharminckettő. Ennyi volt.',
        )

    def test_a_capitalised_counted_noun_still_reads_as_an_ordinal(self):
        self.assertEqual(normalize_hungarian('5. Fejezet'), 'ötödik Fejezet')


class DateTests(unittest.TestCase):
    def test_a_full_date_reads_year_month_day(self):
        self.assertEqual(
            normalize_hungarian('1932. március 5-én történt.'),
            'ezerkilencszázharminckettő március ötödikén történt.',
        )

    def test_a_bare_day_gets_its_dated_form(self):
        self.assertEqual(
            normalize_hungarian('március 5. volt'),
            'március ötödike volt',
        )

    def test_the_first_of_the_month_is_irregular(self):
        self.assertEqual(
            normalize_hungarian('1-jén indultak'), 'elsején indultak'
        )

    def test_a_year_in_front_of_a_month_stays_cardinal(self):
        self.assertEqual(
            normalize_hungarian('1932. március folyamán'),
            'ezerkilencszázharminckettő március folyamán',
        )

    def test_a_year_in_front_of_a_season_stays_cardinal(self):
        self.assertEqual(
            normalize_hungarian('1932. nyarán'),
            'ezerkilencszázharminckettő nyarán',
        )


class SuffixTests(unittest.TestCase):
    def test_a_hyphenated_suffix_keeps_its_own_vowels(self):
        cases = {
            '1932-ben': 'ezerkilencszázharminckettőben',
            '3-at': 'hármat',
            '3-an': 'hárman',
            '3-as': 'hármas',
            '6-os': 'hatos',
            '5-öt': 'ötöt',
            '22-en': 'huszonketten',
            '1000-et': 'ezret',
            '7-et': 'hetet',
        }
        for written, spoken in cases.items():
            self.assertEqual(normalize_hungarian(written), spoken, written)

    def test_an_accented_suffix_marks_a_day_of_the_month(self):
        self.assertEqual(normalize_hungarian('5-ét'), 'ötödikét')
        self.assertEqual(normalize_hungarian('3-án'), 'harmadikán')
        self.assertEqual(normalize_hungarian('10-éig'), 'tizedikéig')

    def test_the_for_suffix_is_not_a_date(self):
        self.assertEqual(normalize_hungarian('5-ért'), 'ötért')


class EverydayNumberTests(unittest.TestCase):
    def test_a_number_in_front_of_a_noun_uses_the_short_two(self):
        self.assertEqual(
            normalize_hungarian('Mindössze 12 alma volt.'),
            'Mindössze tizenkét alma volt.',
        )

    def test_a_number_in_front_of_a_conjunction_stays_long(self):
        self.assertEqual(
            normalize_hungarian('2 és 3 meg 12 ember.'),
            'kettő és három meg tizenkét ember.',
        )

    def test_grouped_thousands_are_one_number(self):
        self.assertEqual(
            normalize_hungarian('10 000 forint'), 'tízezer forint'
        )
        self.assertEqual(
            normalize_hungarian('10.000 forint'), 'tízezer forint'
        )

    def test_decimals(self):
        # Updated in normalizer v2: the decimal place is now read out
        # ("öt tized"), which is the standard Hungarian reading.
        self.assertEqual(
            normalize_hungarian('Ez 3,5 méter.'),
            'Ez három egész öt tized méter.',
        )

    def test_percentages(self):
        self.assertEqual(normalize_hungarian('50%'), 'ötven százalék')
        self.assertEqual(
            normalize_hungarian('50%-os esély'), 'ötven százalékos esély'
        )

    def test_degrees(self):
        self.assertEqual(
            normalize_hungarian('21 °C volt'), 'huszonegy Celsius-fok volt'
        )
        self.assertEqual(
            normalize_hungarian('21 °C-ban'), 'huszonegy Celsius-fokban'
        )
        self.assertEqual(
            normalize_hungarian('-5 °C-ra hűlt'),
            'mínusz öt Celsius-fokra hűlt',
        )

    def test_clock_times(self):
        self.assertEqual(
            normalize_hungarian('10:30-kor'), 'tíz óra harminc perckor'
        )
        self.assertEqual(normalize_hungarian('8:00-kor'), 'nyolc órakor')


class RomanNumeralTests(unittest.TestCase):
    def test_a_century_is_an_ordinal(self):
        self.assertEqual(
            normalize_hungarian('A XX. században élt.'),
            'A huszadik században élt.',
        )

    def test_a_rulers_name_keeps_its_capital(self):
        self.assertEqual(
            normalize_hungarian('II. Erzsébet királynő'),
            'Második Erzsébet királynő',
        )

    def test_letters_that_only_look_roman_are_left_alone(self):
        # Updated in normalizer v2: these are now read as acronyms, but they
        # must still never become ordinals.
        self.assertEqual(
            normalize_hungarian('Megvettem a DVD. Aztán hazamentem.'),
            'Megvettem a dévédé. Aztán hazamentem.',
        )
        self.assertEqual(
            normalize_hungarian('Az MTA. tagja lett.'), 'Az emtéá. tagja lett.'
        )


class RoutingTests(unittest.TestCase):
    def test_hungarian_text_never_reaches_the_english_normalizer(self):
        with patch('core.tts_engine._num2words_fallback') as fallback:
            out = apply_text_normalization('Az Úr 932. esztendejében', 'hu')
        fallback.assert_not_called()
        self.assertIn('kilencszázharminckettedik', out)

    def test_hungarian_is_recognised_without_a_language_tag(self):
        self.assertTrue(looks_hungarian(None, 'Az idő későre jár, 12 óra.'))
        self.assertTrue(looks_hungarian('hu', 'anything'))
        self.assertFalse(looks_hungarian('en', 'I have 12 apples.'))

    def test_control_tags_survive_normalization(self):
        out = apply_text_normalization('[laughter] 3 alma és 2 körte', 'hu')
        self.assertIn('[laughter]', out)
        self.assertIn('három alma', out)

    def test_text_without_numbers_is_returned_unchanged(self):
        text = 'Nincs ebben a mondatban egyetlen szám sem.'
        self.assertEqual(normalize_hungarian(text), text)


class Helper(unittest.TestCase):
    def check(self, cases):
        for written, spoken in cases.items():
            with self.subTest(written=written):
                self.assertEqual(normalize_hungarian(written), spoken)


class MonthDateTests(Helper):
    def test_day_suffixes_after_a_month(self):
        self.check({
            'március 15-e': 'március tizenötödike',
            'augusztus 20-a': 'augusztus huszadika',
            'március 15-ig': 'március tizenötödikéig',
            'március 15-től': 'március tizenötödikétől',
            'március 15-én': 'március tizenötödikén',
            'augusztus 20-án': 'augusztus huszadikán',
            'március 15-i ünnepség': 'március tizenötödikei ünnepség',
            'május 1-je': 'május elseje',
            'május 1-jén': 'május elsején',
            'május 1-ig': 'május elsejéig',
        })

    def test_a_day_with_a_period_is_a_date(self):
        self.check({
            'március 15. volt': 'március tizenötödike volt',
            # The period that also ends the sentence is kept as a full stop.
            'Ez történt március 15. Aztán': 'Ez történt március tizenötödike. Aztán',
            'március 15.': 'március tizenötödike.',
            '1848. március 15.': 'ezernyolcszáznegyvennyolc március tizenötödike.',
            '1848. március 15-e': 'ezernyolcszáznegyvennyolc március tizenötödike',
        })

    def test_numeric_and_iso_dates(self):
        self.check({
            '2024. 10. 05.': 'kétezer-huszonnégy október ötödike.',
            '2024.10.05. volt': 'kétezer-huszonnégy október ötödike volt',
            '2024-10-05': 'kétezer-huszonnégy október ötödike',
            '2024-10-05-én': 'kétezer-huszonnégy október ötödikén',
            '2024. 10. 05-én': 'kétezer-huszonnégy október ötödikén',
        })

    def test_invalid_numeric_dates_are_not_dates(self):
        spoken = normalize_hungarian('2024-13-45')
        self.assertFalse(any(month in spoken for month in ('január', 'december')))
        self.assertNotIn('dike', spoken)

    def test_month_abbreviations_feed_the_date_rules(self):
        self.assertEqual(normalize_hungarian('márc. 15-én'), 'március tizenötödikén')

    def test_a_year_before_an_inflected_month_stays_cardinal(self):
        self.assertEqual(
            normalize_hungarian('1848. márciusában'),
            'ezernyolcszáznegyvennyolc márciusában',
        )

    def test_day_endings_without_a_month(self):
        self.check({
            '15-e': 'tizenötödike',
            '15-ig': 'tizenötig',  # without a month it is just a count
            '1848-i forradalom': 'ezernyolcszáznegyvennyolci forradalom',
        })


class RomanRulerTests(Helper):
    def test_single_letter_numerals_before_names_and_nouns(self):
        self.check({
            'I. István': 'Első István',
            'V. László': 'Ötödik László',
            'IV. Béla': 'Negyedik Béla',
            'III. Napóleon': 'Harmadik Napóleon',
            'I. világháború': 'első világháború',
            'Az I. világháború után': 'Az első világháború után',
            'a XX. században': 'a huszadik században',
            'a XIII. kerületben': 'a tizenharmadik kerületben',
            'a II. félévben': 'a második félévben',
            'X. fejezet': 'tizedik fejezet',
        })

    def test_capitalised_at_the_start_of_a_sentence(self):
        self.assertEqual(
            normalize_hungarian('Vége volt. II. világháború jött.'),
            'Vége volt. Második világháború jött.',
        )

    def test_a_chapter_heading_in_capitals(self):
        self.assertEqual(normalize_hungarian('II. RÉSZ'), 'második RÉSZ')

    def test_false_positives_are_left_alone(self):
        for text in ('MI. Aztán jött.', 'DC. Washington', 'M. Kovács',
                     'C. Aztán', 'L. Nagy', 'D. Tóth', 'ELSŐ FEJEZET',
                     'XL. Aztán'):
            with self.subTest(text=text):
                self.assertEqual(normalize_hungarian(text), text)

    def test_a_known_acronym_is_never_a_numeral(self):
        self.assertEqual(normalize_hungarian('CD. Aztán'), 'cédé. Aztán')


class TimeTests(Helper):
    """Clock times read "óra … perc" consistently: "hét óra harminc perc".

    The suffix moves onto the last spoken word, so "7.30-kor" is "… perckor".
    """

    def test_times(self):
        self.check({
            '7:30': 'hét óra harminc perc',
            '22:05-kor': 'huszonkét óra öt perckor',
            '7.30-kor': 'hét óra harminc perckor',
            '7.30-tól': 'hét óra harminc perctől',
            '7.30-ig': 'hét óra harminc percig',
            '7:30-as vonat': 'hét óra harminc perces vonat',
            '8.00-tól': 'nyolc órától',
            '7.30 órakor': 'hét óra harminc perckor',
            '7:30 órától': 'hét óra harminc perctől',
            '8.00 órakor': 'nyolc órakor',
        })

    def test_impossible_times_are_not_times(self):
        self.assertNotIn('óra', normalize_hungarian('25:30'))
        self.assertNotIn('óra', normalize_hungarian('12:75'))


class PhoneNumberTests(Helper):
    def test_phone_numbers_are_read_in_groups(self):
        self.check({
            '06-1-234-5678': 'nulla hat, egy, kétszázharmincnégy, ötvenhat hetvennyolc',
            '+36 30 123 4567': 'plusz harminchat, harminc, százhuszonhárom, negyvenöt hatvanhét',
            '06 30 123 4567': 'nulla hat, harminc, százhuszonhárom, negyvenöt hatvanhét',
            '+36301234567': 'plusz harminchat, harminc, százhuszonhárom, negyvenöt hatvanhét',
            '06 1 234 0506': 'nulla hat, egy, kétszázharmincnégy, nulla öt nulla hat',
            '06 62 123 456': 'nulla hat, hatvankettő, százhuszonhárom, négyszázötvenhat',
        })

    def test_phone_numbers_after_an_abbreviation(self):
        self.assertEqual(
            normalize_hungarian('Tel.: 06 1 234 5678'),
            'Telefon: nulla hat, egy, kétszázharmincnégy, ötvenhat hetvennyolc',
        )


class RangeTests(Helper):
    def test_ranges(self):
        self.check({
            '10–12 oldal': 'tíz-tizenkét oldal',
            '10-12 oldal': 'tíz-tizenkét oldal',
            '10–12': 'tíztől tizenkettőig',
            '1848–49-es': 'ezernyolcszáznegyvennyolc–negyvenkilences',
            '1914–1918': 'ezerkilencszáztizennégytől ezerkilencszáztizennyolcig',
            '1914–1918 között': 'ezerkilencszáztizennégy és ezerkilencszáztizennyolc között',
            '1914–1918 volt a háború': 'ezerkilencszáztizennégytől ezerkilencszáztizennyolcig volt a háború',
            '10–12 km-re': 'tíz-tizenkét kilométerre',
            '1 000–2 000 Ft': 'ezer-kétezer forint',
            '2-3 napos': 'két-három napos',
        })

    def test_a_score_is_not_a_range(self):
        self.assertNotIn('tól', normalize_hungarian('3-2'))


class SuffixStemTests(Helper):
    def test_ten_and_twenty_shorten_before_a_vowel(self):
        self.check({
            '10-et': 'tizet',
            '10-es': 'tizes',
            '20-at': 'huszat',
            '20-as': 'huszas',
            '110-et': 'száztizet',
            # "tízen" and "húszan" (ten/twenty people) keep the long vowel.
            '10-en': 'tízen',
            '20-an': 'húszan',
        })

    def test_the_terminative_never_shortens_the_stem(self):
        self.check({'3-ig': 'háromig', '7-ig': 'hétig', '2-ig': 'kettőig'})

    def test_compounds_use_the_short_two(self):
        self.check({'2-szer': 'kétszer', '3-szor': 'háromszor'})

    def test_written_ordinal_endings(self):
        self.check({
            '3-ik': 'harmadik', '5-ödik': 'ötödik', '1-ső': 'első',
            '1-sején': 'elsején', '10-edikén': 'tizedikén',
        })

    def test_zero_lengthens(self):
        self.assertEqual(normalize_hungarian('0-t'), 'nullát')


class FractionTests(Helper):
    def test_fractions(self):
        self.check({
            '½': 'fél',
            '¼': 'negyed',
            '¾': 'háromnegyed',
            '1½ kiló': 'másfél kiló',
            '2½ óra': 'két és fél óra',
            '1/2': 'fél',
            '1/3': 'egyharmad',
            '2/3': 'kétharmad',
            '3/4-es': 'háromnegyedes',
            '1/10': 'egytized',
        })

    def test_school_years_are_not_fractions(self):
        self.assertEqual(
            normalize_hungarian('2024/25-ös tanév'),
            'kétezer-huszonnégy–huszonötös tanév',
        )

    def test_other_slashes_are_left_as_numbers(self):
        self.assertEqual(normalize_hungarian('24/7'), 'huszonnégy/hét')
        self.assertEqual(normalize_hungarian('5/3'), 'öt/három')


class SignTests(Helper):
    def test_signs(self):
        self.check({
            '-5 fok': 'mínusz öt fok',
            '−5 °C': 'mínusz öt Celsius-fok',
            '–5 °C': 'mínusz öt Celsius-fok',
            'Ez +3 fok': 'Ez plusz három fok',
            '(-3)': '(mínusz három)',
        })

    def test_a_hyphen_between_numbers_is_not_a_sign(self):
        self.assertNotIn('mínusz', normalize_hungarian('10-12 oldal'))
        self.assertNotIn('plusz', normalize_hungarian('3+4'))


class DecimalTests(Helper):
    def test_decimals_name_the_place(self):
        self.check({
            '3,5': 'három egész öt tized',
            '2,75': 'kettő egész hetvenöt század',
            '0,05': 'nulla egész öt század',
            '1,125': 'egy egész százhuszonöt ezred',
            '3,5%': 'három egész öt tized százalék',
            '36,6 °C': 'harminchat egész hat tized Celsius-fok',
            '3,5-es': 'három egész öt tizedes',
        })

    def test_a_list_is_not_a_decimal(self):
        self.assertEqual(normalize_hungarian('1,2,3'), 'egy,kettő,három')


class UnitAndCurrencyTests(Helper):
    def test_units_after_numbers(self):
        self.check({
            '500 Ft': 'ötszáz forint',
            '1500 Ft-ért': 'ezerötszáz forintért',
            '10 000 Ft': 'tízezer forint',
            '12 km': 'tizenkét kilométer',
            '2 km': 'két kilométer',
            '5 kg-os': 'öt kilogrammos',
            '5 kg-mal': 'öt kilogrammal',
            '10 km-re': 'tíz kilométerre',
            '3 db': 'három darab',
            '3 m²': 'három négyzetméter',
            '1 m2-es': 'egy négyzetméteres',
            '90 km/h': 'kilencven kilométer per óra',
            '2 °C': 'két Celsius-fok',
        })

    def test_currency_symbols(self):
        self.check({
            '€10': 'tíz euró',
            '10 €': 'tíz euró',
            '10€-t': 'tíz eurót',
            '$5': 'öt dollár',
            '$5 millió': 'öt millió dollár',
            '£3': 'három font',
        })

    def test_unit_letters_inside_words_are_left_alone(self):
        self.check({
            '5 macska': 'öt macska',
            '3 gól': 'három gól',
            'Ft': 'Ft',
        })


class AbbreviationTests(Helper):
    def test_abbreviations(self):
        self.check({
            'pl. alma': 'például alma',
            'kb. 5 km': 'körülbelül öt kilométer',
            'Dr. Kovács': 'Doktor Kovács',
            'a Kovács Kft.-nél': 'a Kovács káefténél',
            'Kr. e. 44-ben': 'Krisztus előtt negyvennégyben',
            'i. sz. 1000': 'időszámításunk szerint ezer',
            'a XX. sz. elején': 'a huszadik század elején',
            'XIII. ker.': 'tizenharmadik kerület.',
            'a 2. em. 3.': 'a második emelet három.',
            'ti. ő': 'tudniillik ő',
        })

    def test_a_sentence_ending_abbreviation_keeps_its_period(self):
        self.assertEqual(
            normalize_hungarian('Vettem almát, körtét stb. Aztán hazamentem.'),
            'Vettem almát, körtét és a többi. Aztán hazamentem.',
        )
        self.assertEqual(
            normalize_hungarian('Vettem almát stb.'), 'Vettem almát és a többi.'
        )

    def test_street_abbreviation_needs_a_street_name(self):
        self.assertEqual(
            normalize_hungarian('Kossuth u. 5. alatt'), 'Kossuth utca öt alatt'
        )
        self.assertEqual(normalize_hungarian('u. i.'), 'u. i.')

    def test_the_pronoun_ti_is_not_an_abbreviation(self):
        self.assertEqual(normalize_hungarian('Ki jön? Ti.'), 'Ki jön? Ti.')

    def test_only_whole_tokens_expand(self):
        self.assertEqual(normalize_hungarian('www.pl.hu'), 'www.pl.hu')


class AcronymTests(Helper):
    def test_known_acronyms(self):
        self.check({
            'Az EU tagja': 'Az e-u tagja',
            'EU-ban': 'e-u-ban',
            'A BKV busza': 'A békávé busza',
            'A BKV.': 'A békávé.',
        })

    def test_unknown_consonant_acronyms_are_spelled(self):
        self.check({
            'Az MSZP szerint': 'Az emeszpé szerint',
            'a HVG-ben': 'a hávégé-ben',
        })

    def test_words_and_headings_are_left_alone(self):
        for text in ('Azt mondta: NEM!', 'AZ EU JÖVŐJE', 'Hmm, HMM.',
                     'a XX században', 'ELSŐ FEJEZET'):
            with self.subTest(text=text):
                self.assertEqual(normalize_hungarian(text), text)


class GlyphRepairTests(unittest.TestCase):
    def test_legacy_glyphs_are_repaired_in_hungarian(self):
        self.assertEqual(repair_hungarian_glyphs('A hõs nem félt.'), 'A hős nem félt.')
        self.assertEqual(repair_hungarian_glyphs('ÕSZI SZÉL és fûz'), 'ŐSZI SZÉL és fűz')

    def test_other_languages_keep_their_letters(self):
        for text in ('Je suis sûr.', 'Os corações', 'Õhtu on ilus ja päike paistab.'):
            with self.subTest(text=text):
                self.assertEqual(repair_hungarian_glyphs(text), text)

    def test_the_caller_can_vouch_for_the_language(self):
        self.assertEqual(repair_hungarian_glyphs('hõs', assume_hungarian=True), 'hős')

    def test_normalize_cleans_invisible_characters(self):
        self.assertEqual(normalize_hungarian('a hõs'), 'a hős')
        self.assertEqual(normalize_hungarian('meg­lepő'), 'meglepő')
        self.assertEqual(normalize_hungarian('10 000 Ft'), 'tízezer forint')
        self.assertEqual(normalize_hungarian('5 kg'), 'öt kilogramm')


class LanguageDetectionTests(unittest.TestCase):
    def test_declared_codes(self):
        self.assertTrue(looks_hungarian('hu'))
        self.assertTrue(looks_hungarian('hu_HU'))
        self.assertTrue(looks_hungarian('HU-hu'))
        self.assertFalse(looks_hungarian('de', 'Ez nem volt jó, és hogy'))

    def test_undeclared_text(self):
        self.assertTrue(looks_hungarian(None, 'Ez nem volt jó.'))
        self.assertTrue(looks_hungarian('auto', 'A hõs nem félt.'))
        self.assertFalse(looks_hungarian(None, 'I have 12 apples.'))
        self.assertFalse(looks_hungarian(None, 'Van Gogh met van der Berg.'))
        self.assertFalse(looks_hungarian(None, ''))
        self.assertFalse(looks_hungarian(None, None))


class VersionTests(unittest.TestCase):
    def test_version_is_an_int_that_was_bumped(self):
        self.assertIsInstance(NORMALIZER_VERSION, int)
        self.assertGreaterEqual(NORMALIZER_VERSION, 2)


if __name__ == '__main__':
    unittest.main()
