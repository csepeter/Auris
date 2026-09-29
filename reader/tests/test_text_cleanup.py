import unittest

from core import text_cleanup


class TextCleanupTest(unittest.TestCase):
    def test_invisible_characters_and_nbsp_are_cleaned(self):
        self.assertEqual(
            text_cleanup.clean_text('szó­köz​ és itt', 'hu'),
            'szóköz és itt',
        )

    def test_hungarian_legacy_glyphs_are_repaired(self):
        text = 'Az õ kertjében nem volt semmi, csak egy tûzhely és a fû.'
        self.assertEqual(
            text_cleanup.clean_text(text, 'hu'),
            'Az ő kertjében nem volt semmi, csak egy tűzhely és a fű.',
        )

    def test_other_languages_keep_their_letters(self):
        self.assertEqual(text_cleanup.clean_text('São João, Õnne', 'pt'), 'São João, Õnne')

    def test_compound_hyphen_after_proper_name_is_kept(self):
        self.assertFalse(text_cleanup.should_join_hyphenated('Kossuth', 'díjat kapott'))
        self.assertFalse(text_cleanup.should_join_hyphenated('a Duna', 'parton'))

    def test_typesetting_hyphen_is_joined(self):
        self.assertTrue(text_cleanup.should_join_hyphenated('az asztal', 'terítő'))
        self.assertTrue(text_cleanup.should_join_hyphenated('meg', 'érkezett'))

    def test_parsed_book_blocks_titles_and_content_stay_consistent(self):
        book = {
            'language': 'hu',
            'title': 'Tûz­',
            'chapters': [{
                'title': 'Elsõ fejezet',
                'content': 'x',
                'blocks': [
                    {'text': 'Elsõ fejezet', 'kind': 'heading'},
                    {'text': 'Nem volt ott senki, csak õ és a kutya.', 'kind': 'paragraph'},
                    {'text': '​', 'kind': 'paragraph'},
                ],
            }],
        }
        text_cleanup.clean_parsed_book(book)
        chapter = book['chapters'][0]
        self.assertEqual(book['title'], 'Tűz')
        self.assertEqual(chapter['title'], 'Első fejezet')
        self.assertEqual(len(chapter['blocks']), 2)
        self.assertEqual(
            chapter['content'],
            'Első fejezet\n\nNem volt ott senki, csak ő és a kutya.',
        )


if __name__ == '__main__':
    unittest.main()
