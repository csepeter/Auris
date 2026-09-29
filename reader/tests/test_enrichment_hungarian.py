"""Hungarian sentence segmentation, dialogue and emotion heuristics."""

import unittest

from core import enrichment
from core.enrichment import (
    _build_dialogue_map,
    _has_dialogue,
    _inject_tags,
    _is_heading_paragraph,
    _normalize_source_text,
    _scene_speed,
    _split_paragraph_sentences,
    _split_paragraphs,
    _split_sentence_units,
    _title_key,
    build_speaker_units,
    enrich_chapter,
)

# Short excerpt from Rejtő Jenő: A tizennégy karátos autó
# (test_docs/Rejto_Jeno-14-karatos-auto.pdf, page 4), hard-wrapped as in the PDF.
REJTO_EXCERPT = """A munkavezető később mégis megszólította.
- Halló! Jöjjön az ötös bazenhez, ládákat kell felrakni.
- Nehezek azok a ládák?
A munkavezető szeme megállt, mintha kővé válna. Ilyet még nem kérdezett kikötőmunkás!
- No jó - magyarázta kissé idegesen és türelmetlenül a barna zakós úr -, azt nekem tudni kell,
kérem, mert öt év előtt sérvem volt.
A patron azt mondta, hogy „marha”, és továbbment.
- Finom ember, mondhatom... - dünnyögte utána mély megvetéssel Gorcsev, aki közelről
hallgatta a társalgást, nyomban megérezte, hogy ez az ő embere, és odalépett:
- Mondja! Maga dolgozni akar?
- Nem is vagyok naplopó!
A pincér sápadtan odasiet:
- Hallja! Ez nem matrózkocsma!
"""


def _units(text):
    return [(unit["text"], unit["dialogue_candidate"]) for unit in _split_sentence_units(text)]


class HungarianSentenceSplittingTests(unittest.TestCase):
    def test_splits_before_accented_capitals_and_low_opening_quote(self):
        self.assertEqual(
            _split_paragraph_sentences(
                "Esett az eső most. Éjjel esett. Ő is jött. „Gyere!”"
            ),
            ["Esett az eső most.", "Éjjel esett.", "Ő is jött.", "„Gyere!”"],
        )

    def test_splits_before_every_accented_capital(self):
        for capital in "ÁÉÍÓÖŐÚÜŰ":
            with self.subTest(capital=capital):
                sentences = _split_paragraph_sentences(f"Vége volt. {capital}rra ment.")
                self.assertEqual(len(sentences), 2)

    def test_splits_before_guillemets_dash_dialogue_and_brackets(self):
        cases = {
            "Hallgatott. »Gyere!« Aztán elment.": ["Hallgatott.", "»Gyere!«", "Aztán elment."],
            "Hallgatott. «Gyere!» Aztán elment.": ["Hallgatott.", "«Gyere!»", "Aztán elment."],
            "Anna az ablakhoz lépett. – Gyere ide!": ["Anna az ablakhoz lépett.", "– Gyere ide!"],
            "Anna az ablakhoz lépett. — Gyere ide!": ["Anna az ablakhoz lépett.", "— Gyere ide!"],
            "Hallgattak. (Ez titok volt.) Aztán elment.": [
                "Hallgattak.", "(Ez titok volt.)", "Aztán elment.",
            ],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_split_paragraph_sentences(text), expected)

    def test_question_exclamation_and_ellipsis_terminators(self):
        self.assertEqual(
            _split_paragraph_sentences(
                "Mi az?! Nem tudom... Talán holnap. Várj… Ott van!"
            ),
            ["Mi az?!", "Nem tudom...", "Talán holnap.", "Várj…", "Ott van!"],
        )

    def test_closing_quotes_after_terminator_stay_with_their_sentence(self):
        self.assertEqual(
            _split_paragraph_sentences("„Menj már!” Éva hallgatott. »Jó.« Ödön ment."),
            ["„Menj már!”", "Éva hallgatott.", "»Jó.«", "Ödön ment."],
        )

    def test_colon_before_dash_dialogue_splits(self):
        self.assertEqual(
            _split_paragraph_sentences("A pincér sápadtan odasiet: – Hallja! Ez nem az."),
            ["A pincér sápadtan odasiet:", "– Hallja!", "Ez nem az."],
        )

    def test_dash_interjection_is_not_a_sentence_break(self):
        self.assertEqual(
            _split_paragraph_sentences("– Gyere ide! – kiáltotta Anna. – Most!"),
            ["– Gyere ide! – kiáltotta Anna.", "– Most!"],
        )


class HungarianAbbreviationTests(unittest.TestCase):
    def assertOneSentence(self, text):
        self.assertEqual(_split_paragraph_sentences(text), [text])

    def test_non_terminal_abbreviations_do_not_end_sentences(self):
        for text in (
            "Aztán pl. Kovács jött.",
            "Kb. 5 perc múlva megérkezett.",
            "Ott volt dr. Kiss is.",
            "Ott volt Dr. Kiss is.",
            "Ott volt id. Szabó is.",
            "Megjött özv. Nagyné is.",
            "Megjött Özv. Nagyné is.",
            "A levelet ld. Melléklet szerint küldte.",
            "Caesar Kr. e. 44-ben halt meg.",
            "A város i. sz. 200 körül virágzott.",
            "A Kossuth u. 5. szám alatt lakott.",
        ):
            with self.subTest(text=text):
                self.assertOneSentence(text)

    def test_stb_can_end_a_sentence(self):
        self.assertEqual(
            _split_paragraph_sentences("Hoztunk almát, körtét stb. Aztán hazamentünk."),
            ["Hoztunk almát, körtét stb.", "Aztán hazamentünk."],
        )

    def test_abbreviations_that_are_also_words_still_end_sentences(self):
        self.assertEqual(
            _split_paragraph_sentences("Nem mi voltunk, hanem ti. Ők nem tudták."),
            ["Nem mi voltunk, hanem ti.", "Ők nem tudták."],
        )
        self.assertOneSentence("Legyen min. 5 fő a csoportban.")

    def test_english_words_are_not_treated_as_hungarian_abbreviations(self):
        self.assertEqual(
            _split_paragraph_sentences("She was ill. The doctor came."),
            ["She was ill.", "The doctor came."],
        )
        self.assertEqual(
            _split_paragraph_sentences("He was old. Then he died."),
            ["He was old.", "Then he died."],
        )


class HungarianOrdinalTests(unittest.TestCase):
    def test_roman_and_arabic_ordinals_do_not_end_sentences(self):
        for text in (
            "IV. Béla király volt.",
            "Akkor II. Rákóczi Ferenc vezette a felkelést.",
            "1848. március 15-én kitört a forradalom.",
            "1848. Március idusán kitört a forradalom.",
            "Ez a 3. fejezetben áll.",
            "A XX. század nehéz volt.",
            "Majd XIV. Lajos következett a trónon.",
        ):
            with self.subTest(text=text):
                self.assertEqual(_split_paragraph_sentences(text), [text])

    def test_numeral_at_paragraph_end_is_kept(self):
        self.assertEqual(
            _split_paragraph_sentences("Ez volt az év: 1848."),
            ["Ez volt az év: 1848."],
        )

    def test_english_sentence_final_numbers_still_split(self):
        self.assertEqual(
            _split_paragraph_sentences("He was born in 1990. Then he moved."),
            ["He was born in 1990.", "Then he moved."],
        )
        self.assertEqual(
            _split_paragraph_sentences("The king was Henry VIII. He had six wives."),
            ["The king was Henry VIII.", "He had six wives."],
        )


class HungarianInitialTests(unittest.TestCase):
    def test_accented_and_digraph_initials(self):
        for text in (
            "Szabó J. Éva érkezett.",
            "Kovács É. Péter érkezett.",
            "Gy. Kovács is ott volt.",
            "Sz. Nagy Ödön is ott volt.",
            "Zs. Tóth is ott volt.",
            "Cs. Kiss is ott volt.",
            "Ny. Horváth is ott volt.",
            "Ty. Varga is ott volt.",
            "Ly. Pál is ott volt.",
            "Dzs. Takács is ott volt.",
        ):
            with self.subTest(text=text):
                self.assertEqual(_split_paragraph_sentences(text), [text])


class HungarianHeadingTests(unittest.TestCase):
    def test_hungarian_headings_are_recognised(self):
        for heading in (
            "ELSŐ FEJEZET",
            "Első fejezet",
            "II. RÉSZ",
            "II. rész",
            "Harmadik fejezet",
            "Tizenegyedik fejezet",
            "3. fejezet: A titok",
            "Első fejezet – A kezdet",
            "Fejezet 4",
            "Előszó",
            "Utószó",
            "Epilógus",
            "Prológus",
            "Függelék",
            "ÖTÖDIK RÉSZ",
        ):
            with self.subTest(heading=heading):
                self.assertTrue(_is_heading_paragraph(heading))

    def test_running_sentences_are_not_headings(self):
        for text in (
            "Első rész volt a legjobb.",
            "Mi rész volt ez az egészben",
            "Bevezetés nélkül kezdte a beszédet.",
            "Előszó helyett inkább mesélt.",
        ):
            with self.subTest(text=text):
                self.assertFalse(_is_heading_paragraph(text))

    def test_heading_line_becomes_its_own_paragraph(self):
        self.assertEqual(
            _split_paragraphs("ELSŐ FEJEZET\nGorcsev Iván matróz volt.\nHuszonegy éves."),
            ["ELSŐ FEJEZET", "Gorcsev Iván matróz volt.", "Huszonegy éves."],
        )

    def test_title_key_keeps_accented_letters(self):
        self.assertEqual(_title_key("Előszó!"), "előszó")
        self.assertNotEqual(_title_key("Ő"), _title_key("É"))
        self.assertTrue(_is_heading_paragraph("A hős útja", chapter_title="A HŐS ÚTJA"))


class InvisibleCharacterTests(unittest.TestCase):
    def test_soft_hyphen_and_zero_width_characters_are_removed(self):
        normalized = _normalize_source_text(
            "meg­külön­böztetett szó és‌‍⁠﻿​x"
        )
        self.assertEqual(normalized, "megkülönböztetett szó ésx")

    def test_soft_hyphen_before_line_break_joins_the_word(self):
        self.assertEqual(
            _normalize_source_text("megkülönböz­\ntetetten szép"),
            "megkülönböztetetten szép",
        )
        segments = enrich_chapter("Ez megkülönböz­\ntetetten szép volt.", character_map={})
        self.assertEqual([s["text"] for s in segments], ["Ez megkülönböztetetten szép volt."])

    def test_paragraph_structure_is_identical_for_raw_and_normalized_text(self):
        # Text editor blocks split the raw chapter text; speaker units split
        # the normalised text.  Their unit counts must line up exactly.
        raw = (
            "​ELSŐ FEJEZET\n"
            "Gorcsev meg­állt. – Gye­re ide – mondta.\n\n"
            "Vanek úr hallgatott."
        )
        blocks = _split_paragraphs(raw)
        self.assertEqual(blocks[0], "ELSŐ FEJEZET")
        self.assertEqual(
            sum(len(build_speaker_units(block)) for block in blocks),
            len(build_speaker_units(raw)),
        )


class HungarianDialogueDetectionTests(unittest.TestCase):
    def test_quote_variants_are_dialogue(self):
        for text in (
            "„Gyere ide!”",
            "„Gyere ide!“",
            "»Gyere ide!«",
            "«Gyere ide!»",
            "‚Gyere ide!’",
            "– Gyere ide!",
            "— Gyere ide!",
            "- Gyere ide!",
            "– »Na még mit!« – mondta.",
            "– ...és akkor elment.",
        ):
            with self.subTest(text=text):
                self.assertTrue(_has_dialogue(text))

    def test_low_high_german_style_quote_does_not_swallow_narration(self):
        units = build_speaker_units("„Gyere“ – mondta. „Most”")
        quoted = [unit["text"] for unit in units if unit["dialogue_candidate"]]
        self.assertEqual(quoted, ["„Gyere“", "„Most”"])

    def test_guillemet_quotes_are_isolated_speaker_units(self):
        units = build_speaker_units("Anna felnézett. »Hová mész?« Péter nem felelt.")
        self.assertEqual(
            [(unit["text"], unit["dialogue_candidate"]) for unit in units],
            [
                ("Anna felnézett.", False),
                ("»Hová mész?«", True),
                ("Péter nem felelt.", False),
            ],
        )

    def test_dash_turn_interjection_and_resumed_dialogue(self):
        self.assertEqual(
            _units("– Gyere ide – mondta Anna. Aztán leült."),
            [("– Gyere ide – mondta Anna.", True), ("Aztán leült.", False)],
        )
        self.assertEqual(
            _units("– Halló! Jöjjön az ötös bazenhez, ládákat kell felrakni."),
            [
                ("– Halló!", True),
                ("Jöjjön az ötös bazenhez, ládákat kell felrakni.", True),
            ],
        )
        # Narration interjected with "–," returns to speech: the turn stays open.
        self.assertEqual(
            _units("– No jó – magyarázta Vanek –, azt tudni kell! Sérvem volt."),
            [("– No jó – magyarázta Vanek –, azt tudni kell!", True), ("Sérvem volt.", True)],
        )

    def test_non_llm_and_llm_paths_agree_on_dash_dialogue(self):
        text = (
            "A tanár feltette a szemüvegét. – Ki maga? – Gorcsev Iván vagyok. "
            "A tanár bólintott."
        )
        self.assertEqual(
            _units(text),
            [
                ("A tanár feltette a szemüvegét.", False),
                ("– Ki maga?", True),
                ("– Gorcsev Iván vagyok.", True),
                ("A tanár bólintott.", False),
            ],
        )
        self.assertEqual(
            [(unit["text"], unit["dialogue_candidate"]) for unit in build_speaker_units(text)],
            _units(text),
        )

    def test_dash_lines_start_paragraphs_and_lowercase_dash_continues(self):
        self.assertEqual(
            _split_paragraphs("A pincér sápadtan odasiet:\n- Hallja!\n- Mit akar?"),
            ["A pincér sápadtan odasiet:", "- Hallja!", "- Mit akar?"],
        )
        self.assertEqual(
            _split_paragraphs("– Gyere ide!\n– mondta Anna."),
            ["– Gyere ide! – mondta Anna."],
        )

    def test_hungarian_attribution_builds_speaker_map(self):
        mapping = _build_dialogue_map(
            "„Állj meg!” – kiáltotta Anna.\n\n"
            "– Nem megyek – felelte sóhajtva Gorcsev Iván.\n\n"
            "„Jó” mondta halkan Éva.\n\n"
            "Ödön azt mondta: „Ez az enyém.”"
        )
        self.assertEqual(mapping.get("Állj meg!"), "Anna")
        self.assertEqual(mapping.get("Nem megyek"), "Gorcsev Iván")
        self.assertEqual(mapping.get("Jó"), "Éva")
        self.assertEqual(mapping.get("Ez az enyém."), "Ödön")

    def test_dash_dialogue_speaker_is_assigned_without_llm(self):
        characters = {"Anna": {"instruct": "female, young adult"}}
        segments = enrich_chapter("– Gyere ide – mondta Anna.", characters)
        self.assertEqual(segments[0]["character_name"], "Anna")
        self.assertEqual(segments[0]["instruct"], "female, young adult")
        self.assertTrue(segments[0]["is_dialogue"])


class HungarianEmotionTests(unittest.TestCase):
    def test_suttogta_matches_english_whisper_handling(self):
        characters = {"Anna": {"instruct": "female, young adult"}}
        english = enrich_chapter('"Come here," Anna whispered.', characters)[0]
        hungarian = enrich_chapter("– Gyere ide – suttogta Anna.", characters)[0]
        for segment in (english, hungarian):
            self.assertTrue(segment["is_whisper"])
            self.assertEqual(segment["character_name"], "Anna")
            self.assertEqual(segment["instruct"], "female, young adult, whisper")
            self.assertLessEqual(segment["speed"], 0.94)

        narrator = enrich_chapter(
            "– Gyere ide – súgta oda Anna.", {}, single_narrator_mode=True,
            narrator_instruct="male",
        )[0]
        self.assertEqual(narrator["instruct"], "male, whisper")

    def test_whisper_inflections_and_prefixes(self):
        for text in ("elsuttogta", "suttogva", "odasúgta", "halkan", "fojtott hangon",
                     "megsúgta", "suttogott"):
            with self.subTest(text=text):
                self.assertTrue(enrichment._WHISPER_RE.search(f"– Jó – {text} Anna."))

    def test_laughter_sigh_and_dissatisfaction_tags(self):
        cases = {
            "– Ez jó vicc volt – nevetett Anna.": "[laughter]",
            "– Ez jó vicc volt – elnevette magát Anna.": "[laughter]",
            "„Ez jó vicc volt” – kacagott Anna.": "[laughter]",
            "– Hát jó – sóhajtott Gorcsev.": "[sigh]",
            "– Hát jó – felsóhajtott Gorcsev.": "[sigh]",
            "– Finom ember – dünnyögte Gorcsev.": "[dissatisfaction-hnn]",
            "– Menjen innen – morogta Vanek.": "[dissatisfaction-hnn]",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                _, tag = _inject_tags(text, text, True)
                self.assertEqual(tag, expected)

    def test_laughter_tag_goes_inside_the_spoken_line(self):
        enriched, tag = _inject_tags("– Ha-ha! – nevetett Gorcsev.", "", True)
        self.assertEqual(tag, "[laughter]")
        self.assertEqual(enriched, "– [laughter] Ha-ha! – nevetett Gorcsev.")
        enriched, _ = _inject_tags("„Ha-ha!” – nevetett Gorcsev.", "", True)
        self.assertEqual(enriched, "„[laughter] Ha-ha!” – nevetett Gorcsev.")

    def test_ridiculous_is_not_laughter(self):
        _, tag = _inject_tags("– Ez nevetséges! – mondta Anna.", "", True)
        self.assertIsNone(tag)

    def test_sigh_slows_the_segment_like_english(self):
        english = enrich_chapter('"Fine," he sighed.', {})[0]
        hungarian = enrich_chapter("– Hát jó – sóhajtott Gorcsev.", {})[0]
        self.assertEqual(hungarian["enriched_text"], "– [sigh] Hát jó – sóhajtott Gorcsev.")
        self.assertEqual(english["speed"], hungarian["speed"])
        self.assertLessEqual(hungarian["speed"], 0.94)

    def test_slow_and_action_scene_speed(self):
        self.assertEqual(_scene_speed("He walked slowly. Gently he sat."), 0.9)
        self.assertEqual(_scene_speed("Lassan ment. Óvatosan leült."), 0.9)
        self.assertEqual(_scene_speed("Megfontoltan, komótosan lépett be."), 0.9)
        self.assertEqual(
            _scene_speed("Rohant, felugrott, és pofon csapta a pincért. Elmenekült."),
            1.15,
        )
        self.assertEqual(_scene_speed("Gorcsev a kikötőben állt."), 1.0)

    def test_confirmation_phrases_are_unicode_aware(self):
        self.assertTrue(enrichment._should_allow_confirmation_tag("Értem.", True))
        self.assertTrue(enrichment._should_allow_confirmation_tag("Úgy van!", True))
        self.assertFalse(enrichment._should_allow_confirmation_tag("Értem.", False))


class RejtoExcerptTests(unittest.TestCase):
    def test_realistic_passage_units(self):
        self.assertEqual(
            _units(REJTO_EXCERPT),
            [
                ("A munkavezető később mégis megszólította.", False),
                ("- Halló!", True),
                ("Jöjjön az ötös bazenhez, ládákat kell felrakni.", True),
                ("- Nehezek azok a ládák?", True),
                (
                    "A munkavezető szeme megállt, mintha kővé válna. "
                    "Ilyet még nem kérdezett kikötőmunkás!",
                    False,
                ),
                (
                    "- No jó - magyarázta kissé idegesen és türelmetlenül a barna "
                    "zakós úr -, azt nekem tudni kell, kérem, mert öt év előtt sérvem volt.",
                    True,
                ),
                ("A patron azt mondta, hogy „marha”, és továbbment.", True),
                (
                    "- Finom ember, mondhatom... - dünnyögte utána mély megvetéssel "
                    "Gorcsev, aki közelről hallgatta a társalgást, nyomban megérezte, "
                    "hogy ez az ő embere, és odalépett:",
                    True,
                ),
                ("- Mondja!", True),
                ("Maga dolgozni akar?", True),
                ("- Nem is vagyok naplopó!", True),
                ("A pincér sápadtan odasiet:", False),
                ("- Hallja!", True),
                ("Ez nem matrózkocsma!", True),
            ],
        )

    def test_realistic_passage_speaker_and_tags(self):
        characters = {"Gorcsev": {"instruct": "male, young adult"}}
        segments = enrich_chapter(REJTO_EXCERPT, characters)
        insult = next(s for s in segments if "Finom ember" in s["text"])
        self.assertEqual(insult["character_name"], "Gorcsev")
        self.assertTrue(insult["enriched_text"].startswith("- [dissatisfaction-hnn] Finom ember"))
        self.assertEqual(
            [s["ends_paragraph"] for s in segments].count(True),
            len(_split_paragraphs(REJTO_EXCERPT)),
        )

    def test_llm_units_keep_dash_turns(self):
        units = build_speaker_units(REJTO_EXCERPT)
        halloo = next(u for u in units if u["text"] == "- Halló!")
        follow = units[halloo["index"] + 1]
        self.assertEqual(follow["text"], "Jöjjön az ötös bazenhez, ládákat kell felrakni.")
        self.assertEqual(follow["turn_index"], halloo["turn_index"])
        self.assertTrue(follow["continuation"])
        interjection = next(u for u in units if u["text"].startswith("- magyarázta"))
        self.assertFalse(interjection["dialogue_candidate"])


class EnglishBehaviourTests(unittest.TestCase):
    def test_english_abbreviations_and_spaced_initials(self):
        self.assertEqual(
            _split_paragraph_sentences(
                'Mr. Smith arrived at 5 p.m. with Dr. Watson. "Hello!" He sat down. '
                "J. R. R. Tolkien wrote it, e.g. The Hobbit."
            ),
            [
                "Mr. Smith arrived at 5 p.m. with Dr. Watson.",
                '"Hello!"',
                "He sat down.",
                "J. R. R. Tolkien wrote it, e.g. The Hobbit.",
            ],
        )

    def test_english_multi_sentence_quote_is_dialogue(self):
        self.assertEqual(
            _units("“Hullo there! Come to the fifth basin, crates have to be loaded.”"),
            [
                ("“Hullo there!", True),
                ("Come to the fifth basin, crates have to be loaded.”", True),
            ],
        )

    def test_english_attribution_and_whisper_still_work(self):
        characters = {"John": {"instruct": "male"}}
        segment = enrich_chapter('"Come here," John whispered.', characters)[0]
        self.assertEqual(segment["character_name"], "John")
        self.assertEqual(segment["instruct"], "male, whisper")


if __name__ == "__main__":
    unittest.main()
