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
from core import database, exporter, sfx
from core import settings as app_settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None
SR = 24000


def _write(path, seconds, amp=0.0, freq=440.0):
    t = np.arange(int(SR * seconds)) / SR
    sf.write(path, (amp * np.sin(2 * np.pi * freq * t)).astype("float32"), SR)
    return path


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = _write(os.path.join(self.tmp.name, "base.wav"), 3.0)
        self.effect = _write(os.path.join(self.tmp.name, "fx.wav"), 0.5, amp=0.5)

    def tearDown(self):
        self.tmp.cleanup()

    def _energy(self, start, end):
        audio, _ = sf.read(self.base, dtype="float32")
        return float(np.abs(audio[int(start * SR):int(end * SR)]).max())

    def test_with_position_starts_at_the_sentence(self):
        applied = sfx.overlay(self.base, [(SR, self.effect, 0.0, "with")])
        self.assertEqual(applied, 1)
        self.assertEqual(sf.info(self.base).frames, 3 * SR)  # nothing inserted
        self.assertLess(self._energy(0, 0.99), 1e-3)
        self.assertGreater(self._energy(1.0, 1.5), 0.3)
        self.assertLess(self._energy(1.51, 3.0), 1e-3)

    def test_before_position_ends_at_the_sentence_and_gain_applies(self):
        sfx.overlay(self.base, [(2 * SR, self.effect, -6.0, "before")])
        self.assertGreater(self._energy(1.5, 2.0), 0.2)
        self.assertLess(self._energy(1.5, 2.0), 0.3)  # -6 dB of 0.5
        self.assertLess(self._energy(2.01, 3.0), 1e-3)

    def test_cues_outside_the_file_are_clipped_or_ignored(self):
        self.assertEqual(sfx.overlay(self.base, [(SR // 4, self.effect, 0.0, "before"),
                                                 (10 * SR, self.effect, 0.0, "with"), None]), 1)

    def test_merged_chapter_keeps_timings_and_mixes_effect(self):
        a = _write(os.path.join(self.tmp.name, "a.wav"), 1.0, amp=0.1, freq=200)
        b = _write(os.path.join(self.tmp.name, "b.wav"), 1.0, amp=0.1, freq=200)
        segments = [{"audio_path": a, "duration_sec": 1.0},
                    {"audio_path": b, "duration_sec": 1.0,
                     "sfx": {"path": self.effect, "gain_db": 0.0, "position": "with"}}]
        plain = [dict(s) for s in segments]
        plain[1].pop("sfx")
        out_fx = os.path.join(self.tmp.name, "fx_chapter.wav")
        out_plain = os.path.join(self.tmp.name, "plain_chapter.wav")
        with patch.object(exporter, "_export_option", side_effect=lambda key, default=None: False):
            exporter._write_merged_wav(segments, out_fx)
            exporter._write_merged_wav(plain, out_plain)
        self.assertEqual(sf.info(out_fx).frames, sf.info(out_plain).frames)
        self.assertEqual(segments[1]["t_start"], plain[1]["t_start"])
        fx_audio, _ = sf.read(out_fx, dtype="float32")
        plain_audio, _ = sf.read(out_plain, dtype="float32")
        start = int(segments[1]["t_start"] * SR)
        self.assertGreater(float(np.abs(fx_audio[start:start + SR // 2] - plain_audio[start:start + SR // 2]).max()), 0.3)
        self.assertTrue(np.allclose(fx_audio[:start], plain_audio[:start], atol=1e-4))


class _Engine:
    engine_name = "omnivoice"

    def status(self):
        return {"state": "ready"}

    def __getattr__(self, name):
        return lambda *a, **k: None


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class SfxApiTest(unittest.TestCase):
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
            conn.execute("INSERT INTO books (id,title,file_path,file_type,language,single_narrator_mode) "
                         "VALUES (1,'K','k.txt','txt','hu',1)")
            conn.execute("INSERT INTO chapters (id,book_id,title,order_num,content) VALUES "
                         "(1,1,'F',0,'Kopogtak az ajtón. Senki sem nyitott ajtót.')")
        self.client = app_module.app.test_client()
        self.effect = _write(os.path.join(self.tmp.name, "door.wav"), 0.4, amp=0.4)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_attach_list_export_attach_and_remove(self):
        with open(self.effect, "rb") as handle:
            data = {"file": (io.BytesIO(handle.read()), "ajto.wav"), "position": "before", "gain_db": "-14"}
        response = self.client.post("/api/books/1/chapters/1/segments/0/sfx", data=data,
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        listed = self.client.get("/api/books/1/chapters/1/qa").get_json()["segments"]
        self.assertEqual(listed[0]["sfx"], {"name": "ajto.wav", "gain_db": -14.0, "position": "before"})
        segs = sfx.attach(1, 1, app_module._get_chapter_segments(1, 1))
        self.assertEqual(segs[0]["sfx"]["position"], "before")
        stored = segs[0]["sfx"]["path"]
        self.assertEqual(sf.info(stored).samplerate, SR)
        self.assertEqual(self.client.delete("/api/books/1/chapters/1/segments/0/sfx").get_json(), {"ok": True})
        self.assertFalse(os.path.exists(stored))

    def test_rejects_non_audio_upload(self):
        data = {"file": (io.BytesIO(b"hello"), "notes.txt")}
        response = self.client.post("/api/books/1/chapters/1/segments/0/sfx", data=data,
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
