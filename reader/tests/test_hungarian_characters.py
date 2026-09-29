import unittest
from unittest.mock import patch

from core import characters


class HungarianCharacterDetectionTest(unittest.TestCase):
    TEXT = (
        '– Gyere ide! – mondta Kovács Anna. Péter nem felelt, de Annának igaza volt. '
        'Péterrel ment a piacra. – Na és? – kérdezte Péter. Szabóné kinézett. '
        '– Mi az? – kiáltotta Gábor bácsi.'
    )

    def test_hungarian_gender_by_given_name_in_family_first_order(self):
        self.assertEqual(characters.detect_gender_by_name('Kovács Anna'), 'female')
        self.assertEqual(characters.detect_gender_by_name('Nagy Péter'), 'male')
        self.assertEqual(characters.detect_gender_by_name('Szabó Jánosné'), 'female')
        self.assertEqual(characters.detect_gender_by_name('Gábor bácsi'), 'male')

    def test_inflected_names_fold_into_base_name(self):
        known = {'Anna', 'Péter', 'Kovács Anna'}
        self.assertEqual(characters._hungarian_base_name('Annának', known), 'Anna')
        self.assertEqual(characters._hungarian_base_name('Péterrel', known), 'Péter')
        self.assertEqual(characters._hungarian_base_name('Pécs', known), 'Pécs')

    def test_sentence_initial_words_are_not_glued_to_names(self):
        strip = characters._strip_leading_non_name
        self.assertEqual(strip('Délre Péter'), 'Péter')
        self.assertEqual(strip('Reggel Anna'), 'Anna')
        self.assertEqual(strip('Városban Kiss Béla'), 'Kiss Béla')
        for surname_first in ('Kovács Anna', 'Nagy Imre', 'Tót Pál', 'Szabó J. Éva'):
            self.assertEqual(strip(surname_first), surname_first)

    def test_regex_fallback_finds_attributed_speakers(self):
        with patch.object(characters, '_get_hungarian_nlp', return_value=None):
            names = [c['name'] for c in characters.extract_characters_hungarian(self.TEXT)]
        self.assertIn('Kovács Anna', names)
        self.assertIn('Péter', names)
        self.assertIn('Gábor', names)

    def test_profiles_have_no_foreign_accent(self):
        with patch.object(characters, '_get_hungarian_nlp', return_value=None):
            result = characters.extract_characters(self.TEXT, language='hu')
        self.assertTrue(result)
        for item in result:
            self.assertNotIn('accent', item['instruct'])
        anna = next(item for item in result if item['name'] == 'Kovács Anna')
        self.assertEqual(anna['gender'], 'female')


if __name__ == '__main__':
    unittest.main()
