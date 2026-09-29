import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import app as app_module
import auris_cli
from core import database
from core import settings as app_settings


class _Engine:
    engine_name = "omnivoice"

    def __init__(self, folder):
        self.folder = folder
        self.calls = []

    def status(self):
        return {"state": "ready"}

    def generate(self, **kw):
        self.calls.append(kw)
        path = os.path.join(self.folder, "a.wav")
        sf.write(path, np.zeros(2400, dtype="float32"), 24000)
        return {"audio_path": path, "cache_key": "k", "duration_sec": 0.1, "cache_hit": False}

    def __getattr__(self, name):
        return lambda *a, **k: None


class OpenAICompatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.engine = _Engine(self.tmp.name)
        self.patches = [
            patch.object(database, "DB_PATH", os.path.join(self.tmp.name, "reader.db")),
            patch.object(app_settings, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json"),
            patch.object(app_module, "_startup_complete", True),
            patch.object(app_module, "tts", self.engine),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        with database.get_conn() as conn:
            conn.execute("INSERT INTO voice_profiles (id,name,instruct) VALUES (5,'Mesélő','male, elderly, low pitch')")
        self.client = app_module.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_speech_returns_wav_with_saved_profile(self):
        response = self.client.post("/v1/audio/speech", json={
            "model": "tts-1", "input": "Jó napot!", "voice": "mesélő", "response_format": "wav"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertTrue(response.data.startswith(b"RIFF"))
        self.assertEqual(self.engine.calls[0]["instruct"], "male, elderly, low pitch")
        self.assertEqual(self.engine.calls[0]["language"], "hu")

    def test_preset_voice_and_validation(self):
        self.client.post("/v1/audio/speech", json={"input": "Szia", "voice": "anna", "response_format": "wav"})
        self.assertEqual(self.engine.calls[-1]["instruct"], "voice:anna")
        self.assertEqual(self.client.post("/v1/audio/speech", json={"input": ""}).status_code, 400)
        bad = self.client.post("/v1/audio/speech", json={"input": "x", "response_format": "ogg-vorbis"})
        self.assertEqual(bad.status_code, 400)

    def test_optional_bearer_token(self):
        app_settings.save({"api_token": "titok"})
        denied = self.client.post("/v1/audio/speech", json={"input": "Szia", "response_format": "wav"})
        self.assertEqual(denied.status_code, 401)
        allowed = self.client.post("/v1/audio/speech", json={"input": "Szia", "response_format": "wav"},
                                   headers={"Authorization": "Bearer titok"})
        self.assertEqual(allowed.status_code, 200)

    def test_models_and_voices_listing(self):
        models = self.client.get("/v1/models").get_json()
        self.assertIn("piper", [m["id"] for m in models["data"]])
        voices = self.client.get("/v1/audio/voices").get_json()["voices"]
        self.assertEqual(voices[0]["name"], "Mesélő")


class CliTest(unittest.TestCase):
    def test_parser_accepts_export_options(self):
        args = auris_cli.build_parser().parse_args(
            ["export", "3", "--format", "opus", "--package", "epub3", "--intro", "--chapters", "1-3"])
        self.assertEqual((args.book_id, args.format, args.package, args.intro, args.chapters),
                         (3, "opus", "epub3", True, "1-3"))

    def test_chapter_selection_maps_numbers_to_ids(self):
        class Fake:
            def request(self, method, path, data=None, **kw):
                return [{"id": 10}, {"id": 11}, {"id": 12}, {"id": 13}]

        self.assertEqual(auris_cli._chapter_ids(Fake(), 1, "1,3-4"), [10, 12, 13])
        self.assertEqual(auris_cli._chapter_ids(Fake(), 1, None), [10, 11, 12, 13])


if __name__ == "__main__":
    unittest.main()
