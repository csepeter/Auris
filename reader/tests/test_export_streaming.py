import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core import exporter


def _segments(folder, count=3):
    segments = []
    for i in range(count):
        path = os.path.join(folder, f'seg{i}.wav')
        sf.write(path, (np.sin(np.arange(2400 * (i + 1)) / 7) * 0.3).astype('float32'),
                 exporter.SAMPLE_RATE)
        segments.append({'audio_path': path, 'duration_sec': 0.1 * (i + 1),
                         'text': f'Mondat {i}.', 'ends_paragraph': i == 1})
    return segments


class StreamingExportTest(unittest.TestCase):
    def test_streamed_wav_matches_in_memory_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = exporter.build_timeline(_segments(tmp))
            expected = exporter._merge_wavs(timeline)
            out = os.path.join(tmp, 'out.wav')
            seconds = exporter._write_merged_wav(timeline, out)
            actual, sr = sf.read(out)
            self.assertEqual(sr, exporter.SAMPLE_RATE)
            self.assertEqual(len(actual), len(expected))
            self.assertAlmostEqual(seconds, len(expected) / exporter.SAMPLE_RATE, places=4)
            # 16-bit quantisation only.
            self.assertLess(float(np.max(np.abs(actual - expected))), 1e-3)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_ffmpeg_mp3_encoding_writes_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, 'a.wav')
            sf.write(wav, np.zeros(24000, dtype='float32'), exporter.SAMPLE_RATE)
            mp3 = os.path.join(tmp, 'a.mp3')
            self.assertTrue(exporter._wav_to_mp3_file(wav, mp3, {'title': 'Első fejezet'}))
            self.assertGreater(os.path.getsize(mp3), 0)

    def test_chapter_folder_export_keeps_order_with_parallel_workers(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(exporter, 'EXPORTS_DIR', tmp), \
                patch.object(exporter, '_export_workers', return_value=3):
            chapters = [
                {'chapter_number': n, 'chapter_title': f'Fejezet {n}', 'segments': _segments(tmp, 2)}
                for n in (1, 2, 3, 4)
            ]
            result = exporter.export_chapter_folder('Könyv', chapters, {}, 'wav', 'none')
        names = [os.path.basename(item['audio_path']) for item in result['chapters']]
        self.assertEqual(names, ['01_Fejezet_1.wav', '02_Fejezet_2.wav',
                                 '03_Fejezet_3.wav', '04_Fejezet_4.wav'])


if __name__ == '__main__':
    unittest.main()
