import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import app as app_module
from core import database, reference_audio
from core import settings as app_settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _noisy_speech(path, sr=44100):
    rng = np.random.default_rng(3)
    t = np.arange(sr * 3) / sr
    speech = 0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)
    pad = np.zeros(sr)
    x = np.concatenate([pad, speech, pad])
    x = x + 0.005 * rng.standard_normal(len(x)) + 0.03 * np.sin(2 * np.pi * 50 * np.arange(len(x)) / sr)
    sf.write(path, x.astype("float32"), sr)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class CleanReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.src = os.path.join(self.tmp.name, "in.wav")
        self.dst = os.path.join(self.tmp.name, "out.wav")

    def tearDown(self):
        self.tmp.cleanup()

    def test_trims_silence_and_resamples_to_mono_24k(self):
        _noisy_speech(self.src)
        result = reference_audio.clean_reference(self.src, self.dst)
        self.assertTrue(result["cleaned"], result)
        info = sf.info(self.dst)
        self.assertEqual((info.samplerate, info.channels), (24000, 1))
        self.assertLess(info.frames / info.samplerate, 4.0)  # 5 s in, ~3.3 s out
        self.assertGreater(info.frames / info.samplerate, 2.5)

    def test_failure_keeps_original_and_writes_nothing(self):
        result = reference_audio.clean_reference(os.path.join(self.tmp.name, "missing.wav"), self.dst)
        self.assertFalse(result["cleaned"])
        self.assertFalse(os.path.exists(self.dst))

    def test_silence_only_input_is_not_replaced(self):
        sf.write(self.src, np.zeros(24000 * 3, dtype="float32"), 24000)
        result = reference_audio.clean_reference(self.src, self.dst)
        self.assertFalse(result["cleaned"])
        self.assertFalse(os.path.exists(self.dst))


class _Engine:
    engine_name = "omnivoice"

    def invalidate_voice_prompt(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class UploadCleaningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            patch.object(database, "DB_PATH", os.path.join(self.tmp.name, "reader.db")),
            patch.object(app_settings, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json"),
            patch.object(app_module, "UPLOAD_DIR", self.tmp.name),
            patch.object(app_module, "_startup_complete", True),
            patch.object(app_module, "tts", _Engine()),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        with database.get_conn() as conn:
            conn.execute("INSERT INTO books (id,title,file_path,file_type) VALUES (1,'K','k','txt')")
            conn.execute("INSERT INTO characters (id,book_id,name) VALUES (7,1,'Anna')")
        self.client = app_module.app.test_client()
        self.wav = os.path.join(self.tmp.name, "take.wav")
        _noisy_speech(self.wav)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _upload(self, clean):
        with open(self.wav, "rb") as handle:
            data = {"file": (io.BytesIO(handle.read()), "anna.wav"), "ref_text": "Szia", "clean": clean}
        response = self.client.post("/api/characters/7/ref-audio", data=data,
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        with database.get_conn() as conn:
            return conn.execute("SELECT ref_audio_path FROM characters WHERE id=7").fetchone()[0]

    def test_clean_flag_stores_processed_reference(self):
        stored = self._upload("1")
        self.assertEqual(sf.info(stored).samplerate, 24000)
        raw = self._upload("0")
        self.assertEqual(sf.info(raw).samplerate, 44100)
        self.assertNotEqual(stored, raw)


if __name__ == "__main__":
    unittest.main()
