import unittest
from unittest.mock import patch

from core import qa, qa_api


class BackendChoiceTest(unittest.TestCase):
    def _choose(self, requested, *, gpu, parakeet=True, language="hu"):
        with patch("core.settings.get", side_effect=lambda key, default=None:
                   requested if key == "asr_backend" else default), \
             patch.object(qa, "parakeet_available", return_value=parakeet), \
             patch("torch.cuda.is_available", return_value=gpu):
            return qa.asr_backend_for(language)

    def test_auto_keeps_whisper_on_gpu_and_uses_hybrid_on_cpu(self):
        self.assertEqual(self._choose("auto", gpu=True), "whisper")
        self.assertEqual(self._choose("auto", gpu=False), "hybrid")

    def test_falls_back_to_whisper_without_package_or_language(self):
        self.assertEqual(self._choose("auto", gpu=False, parakeet=False), "whisper")
        self.assertEqual(self._choose("parakeet", gpu=False, language="ja"), "whisper")
        self.assertEqual(self._choose("hybrid", gpu=True, parakeet=False), "whisper")

    def test_explicit_choices_are_respected(self):
        self.assertEqual(self._choose("parakeet", gpu=True), "parakeet")
        self.assertEqual(self._choose("whisper", gpu=False), "whisper")
        self.assertEqual(self._choose("nonsense", gpu=True), "whisper")


class ParakeetWordsTest(unittest.TestCase):
    def test_subword_tokens_become_timed_words(self):
        tokens = [" Á", "r", "víz", " tü", "kör", ".", " ", "8", " már", "cius"]
        stamps = [0.0, 0.1, 0.2, 0.5, 0.6, 0.8, 1.0, 1.1, 1.4, 1.6]
        words = qa._parakeet_words(tokens, stamps, 2.0)
        self.assertEqual([w["word"] for w in words], ["Árvíz", "tükör.", "8", "március"])
        self.assertEqual((words[0]["start"], words[0]["end"]), (0.0, 0.5))
        self.assertEqual(words[-1]["end"], 2.0)


class _Fake:
    single_pass_words = True
    confirms = True
    model_id = "fake"

    def __init__(self, first, second):
        self.first, self.second, self.confirmed = first, second, 0

    def transcribe(self, path, language=None, *, prompt="", word_timestamps=False):
        return {"text": self.first, "words": [{"word": "x", "start": 0.0, "end": 0.5}]}

    def confirm(self, path, language=None, *, prompt=""):
        self.confirmed += 1
        return {"text": self.second, "words": []}


class HybridEvaluationTest(unittest.TestCase):
    def _evaluate(self, fake, text):
        metrics = {"flags": [], "duration_sec": 1.0}
        with patch.object(qa, "analyze_audio", return_value=metrics):
            return qa_api._evaluate("a.wav", text, "hu", transcriber=fake, prompt="", align=False,
                                    confirm_above=0.05)

    def test_clean_sentence_is_not_rechecked(self):
        fake = _Fake("Gyere ide, mondta Anna.", "whatever")
        result = self._evaluate(fake, "– Gyere ide! – mondta Anna.")
        self.assertEqual(fake.confirmed, 0)
        self.assertEqual(result["cer"], 0.0)

    def test_suspicious_sentence_uses_better_whisper_reading(self):
        fake = _Fake("Gyerejde mondta Kovácsan a.", "Gyere ide, mondta Kovács Anna.")
        result = self._evaluate(fake, "– Gyere ide! – mondta Kovács Anna.")
        self.assertEqual(fake.confirmed, 1)
        self.assertEqual(result["cer"], 0.0)
        self.assertEqual(result["heard"], "Gyere ide, mondta Kovács Anna.")

    def test_worse_confirmation_keeps_first_reading(self):
        fake = _Fake("Gyere ide mondta Kovácsan a.", "teljesen más")
        result = self._evaluate(fake, "– Gyere ide! – mondta Kovács Anna.")
        self.assertEqual(result["heard"], "Gyere ide mondta Kovácsan a.")


if __name__ == "__main__":
    unittest.main()
