import os
import sqlite3
import tempfile
import unittest

from core import database


class DatabaseMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, 'reader.db')

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def test_fresh_database_has_no_unique_cache_key_and_uses_wal(self):
        database.init_db()
        with database.get_conn() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='tts_segments'"
            ).fetchone()[0]
            mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            indexes = {r[1] for r in conn.execute('PRAGMA index_list(tts_segments)')}
        self.assertNotIn('UNIQUE', sql.upper())
        self.assertEqual(mode.lower(), 'wal')
        self.assertEqual(version, database.SCHEMA_VERSION)
        self.assertIn('idx_segments_cache_key', indexes)

    def test_unique_migration_preserves_speaker_unit_columns(self):
        database.init_db()
        conn = sqlite3.connect(database.get_db_path())
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            DROP TABLE tts_segments;
            CREATE TABLE tts_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
                segment_index INTEGER NOT NULL, text TEXT NOT NULL,
                enriched_text TEXT NOT NULL, character_name TEXT, instruct TEXT,
                speed REAL DEFAULT 1.0, is_dialogue INTEGER DEFAULT 0,
                audio_path TEXT, duration_sec REAL, cache_key TEXT UNIQUE,
                unit_index INTEGER, speaker_candidate INTEGER DEFAULT 0,
                ends_paragraph INTEGER DEFAULT 0
            );
            INSERT INTO tts_segments
                (book_id, chapter_id, segment_index, text, enriched_text, cache_key,
                 unit_index, speaker_candidate, ends_paragraph)
            VALUES (1, 1, 0, 'A.', 'A.', 'k1', 7, 1, 1);
        """)
        conn.commit()
        conn.close()
        database.init_db()
        with database.get_conn() as conn:
            row = conn.execute('SELECT * FROM tts_segments').fetchone()
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='tts_segments'"
            ).fetchone()[0]
        self.assertNotIn('UNIQUE', sql.upper())
        self.assertEqual((row['unit_index'], row['speaker_candidate'], row['ends_paragraph']), (7, 1, 1))


if __name__ == '__main__':
    unittest.main()
