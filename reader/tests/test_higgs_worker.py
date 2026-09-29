import os
import tempfile
import unittest

import numpy as np
import soundfile as sf
import torch

from core import higgs_worker


class _Model:
    def __init__(self):
        self.calls = 0

    def _encode_reference(self, waveform, sample_rate):
        self.calls += 1
        return torch.zeros((int(waveform.shape[-1]) // 100, 8), dtype=torch.long)


class ReferenceCodesTest(unittest.TestCase):
    def setUp(self):
        higgs_worker._REFERENCE_CODES.clear()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ref = os.path.join(self.tmp.name, "ref.wav")
        sf.write(self.ref, np.zeros(24000, dtype="float32"), 24000)

    def tearDown(self):
        higgs_worker._REFERENCE_CODES.clear()
        self.tmp.cleanup()

    def test_reference_is_encoded_once_and_reused(self):
        model = _Model()
        first = higgs_worker.reference_codes(model, self.ref)
        second = higgs_worker.reference_codes(model, self.ref)
        self.assertEqual(model.calls, 1)
        self.assertTrue(torch.equal(first, second))
        self.assertEqual(tuple(first.shape), (240, 8))

    def test_models_without_encoder_fall_back(self):
        self.assertIsNone(higgs_worker.reference_codes(object(), self.ref))


if __name__ == "__main__":
    unittest.main()
