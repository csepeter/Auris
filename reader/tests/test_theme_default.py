import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from core import database
from core import settings as app_settings


class ThemeDefaultTest(unittest.TestCase):
    """Every page starts in the saved theme, not in the OS colour scheme."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            patch.object(database, "DB_PATH", os.path.join(self.tmp.name, "reader.db")),
            patch.object(app_settings, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json"),
            patch.object(app_module, "_startup_complete", True),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        self.client = app_module.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_pages_use_saved_theme_before_first_paint(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("window.AURIS_THEME = 'night'", html)
        self.assertNotIn("prefers-color-scheme: light", html)
        app_settings.save({"theme": "sepia"})
        for path in ("/", "/settings", "/jobs"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn("window.AURIS_THEME = 'sepia'", html, path)

    def test_unknown_theme_falls_back_to_night(self):
        app_settings.save({"theme": "<script>"})
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("window.AURIS_THEME = 'night'", html)


if __name__ == "__main__":
    unittest.main()
