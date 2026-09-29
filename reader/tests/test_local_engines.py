import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core import local_engines as le
from core.cancellation import GenerationAborted


class _FakeEngine(le.LocalEngineBase):
    engine_name = "supertonic"

    def __init__(self):
        super().__init__()
        self.calls = []

    def _load_model(self):
        self.model = object()
        self._detail = "fake"

    def voice_identity(self, instruct, ref_audio, ref_text, language):
        return "style=" + le.choose_preset_voice(instruct, le.SUPERTONIC_VOICES, "F1")

    def _synthesize(self, text, instruct, ref_audio, ref_text, speed, language):
        self.calls.append((text, instruct, speed))
        return np.full(44100, 0.1, dtype=np.float32), 44100


class PresetVoiceTest(unittest.TestCase):
    def test_gender_and_explicit_voice_choose_a_preset(self):
        self.assertIn(le.choose_preset_voice("female, middle-aged", le.PIPER_VOICES, "anna"),
                      {"anna", "berta"})
        self.assertEqual(le.choose_preset_voice("male, elderly", le.PIPER_VOICES, "anna"), "imre")
        self.assertEqual(le.choose_preset_voice("male, voice:berta", le.PIPER_VOICES, "anna"), "berta")
        self.assertEqual(le.choose_preset_voice("", le.PIPER_VOICES, "anna"), "anna")
        self.assertTrue(le.choose_preset_voice("male", le.SUPERTONIC_VOICES, "F1").startswith("M"))

    def test_choice_is_deterministic(self):
        first = le.choose_preset_voice("female, young adult, high pitch", le.SUPERTONIC_VOICES, "F1")
        for _ in range(3):
            self.assertEqual(
                le.choose_preset_voice("female, young adult, high pitch", le.SUPERTONIC_VOICES, "F1"),
                first,
            )

    def test_prepare_text_strips_expression_tags_and_normalizes(self):
        text = le.prepare_text("[laughter] Ha-ha! Kb. 5 perc [sigh]", "hu", True)
        self.assertNotIn("[", text)
        self.assertIn("Körülbelül öt perc", text)


class LocalEngineBaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(le, "AUDIO_CACHE_DIR", self.tmp.name)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_reference_audio_is_not_part_of_a_no_clone_cache_key(self):
        engine = _FakeEngine()
        a = engine.cache_key("Szia.", "female", "a.wav", 1.0, language="hu")
        b = engine.cache_key("Szia.", "female", "b.wav", 1.0, language="hu")
        self.assertEqual(a, b)
        self.assertNotEqual(a, engine.cache_key("Szia.", "male", None, 1.0, language="hu"))

    def test_generate_resamples_to_24k_and_caches(self):
        engine = _FakeEngine()
        engine.load_sync()
        result = engine.generate("Jó napot.", instruct="female", language="hu")
        info = sf.info(result["audio_path"])
        self.assertEqual(info.samplerate, 24000)
        self.assertAlmostEqual(result["duration_sec"], 1.0, places=2)
        again = engine.generate("Jó napot.", instruct="female", language="hu")
        self.assertTrue(again["cache_hit"])
        self.assertEqual(len(engine.calls), 1)

    def test_generate_many_propagates_cancellation(self):
        engine = _FakeEngine()
        engine.load_sync()
        items = [{"text": f"Mondat {i}.", "instruct": "male", "language": "hu"} for i in range(3)]

        def stop(index, result):
            raise GenerationAborted("stop")

        with self.assertRaises(GenerationAborted):
            engine.generate_many(items, on_item=stop)
        self.assertEqual(len(engine.calls), 1)

    def test_not_loaded_engine_reports_clear_error(self):
        engine = _FakeEngine()
        with self.assertRaises(RuntimeError):
            engine.generate("Szia.", instruct="female", language="hu")

    def test_status_includes_capabilities(self):
        engine = _FakeEngine()
        status = engine.status()
        self.assertFalse(status["capabilities"]["voice_clone"])
        self.assertEqual(status["state"], "not_loaded")


class RouterTest(unittest.TestCase):
    def test_router_creates_new_engines(self):
        from core import tts_router

        for name in ("piper", "supertonic", "moss_nano", "moss_tts"):
            engine = tts_router.TTSEngineRouter._create(name)
            self.assertEqual(engine.engine_name, name)
        with patch("core.settings.get", return_value="bogus"):
            self.assertEqual(tts_router.selected_engine_name(), "omnivoice")


if __name__ == "__main__":
    unittest.main()
