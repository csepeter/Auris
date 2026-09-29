import os
import tempfile
import unittest

from core import database, text_editor


class AnnotationRealignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, 'reader.db')
        database.init_db()
        content = 'IV. Béla király volt.\n\n– Gyere ide! – mondta Anna.\n\n– Nem megyek – felelte Péter.'
        with database.get_conn() as conn:
            conn.execute("INSERT INTO books (id, title, file_path, file_type) VALUES (1,'K','k.txt','txt')")
            conn.execute(
                "INSERT INTO chapters (id, book_id, title, order_num, content) VALUES (1,1,'F',0,?)",
                (content,),
            )
            # Numbering of an older splitter that cut "IV." off as its own unit.
            for index, text, speaker in ((2, '– Gyere ide!', 'Anna'), (4, '– Nem megyek', 'Péter'),
                                         (0, 'IV.', 'Narrátor')):
                conn.execute(
                    'INSERT INTO speaker_annotations (book_id,chapter_id,unit_index,unit_text,'
                    'speaker_name,confidence,source) VALUES (1,1,?,?,?,1.0,?)',
                    (index, text, speaker, 'automatic'),
                )

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def test_annotations_follow_their_text_after_a_splitter_change(self):
        with database.get_conn() as conn:
            text_editor.migrate_legacy_annotations(conn)
            rows = conn.execute(
                'SELECT unit_index, unit_text, speaker_name FROM speaker_annotations '
                'ORDER BY unit_index'
            ).fetchall()
        mapping = {row['speaker_name']: row['unit_index'] for row in rows}
        self.assertEqual(mapping['Anna'], 1)
        self.assertEqual(mapping['Péter'], 3)
        # "IV." now lives inside unit 0 ("IV. Béla király volt.").
        self.assertEqual(mapping['Narrátor'], 0)


if __name__ == '__main__':
    unittest.main()
