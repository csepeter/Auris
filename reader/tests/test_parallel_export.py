import threading
import unittest

from core.tts_engine import TTSExportPool, _partition_export_items


class _FakeExportEngine:
    def __init__(self, label):
        self.worker_label = label
        self.calls = []
        self.prompts = []

    def _get_voice_clone_prompt(self, ref_audio, ref_text):
        self.prompts.append((ref_audio, ref_text))
        return object()

    def generate_many(
        self,
        items,
        *,
        num_step,
        on_item=None,
        on_status=None,
    ):
        self.calls.append((list(items), num_step, threading.current_thread().name))
        if on_status:
            on_status(f"{len(items)} items")
        results = []
        for i, item in enumerate(items):
            result = {
                "audio_path": f"{self.worker_label}-{item['id']}.wav",
                "duration_sec": 1.0,
                "cache_hit": False,
                "cache_key": f"k-{item['id']}",
            }
            results.append(result)
            if on_item:
                on_item(i, result)
        return results


def _clone_items(count):
    return [
        {
            "id": i,
            "text": "x" * (40 + i * 3),
            "ref_audio": "speaker.wav",
            "ref_text": "Reference.",
        }
        for i in range(count)
    ]


class ParallelExportPartitionTest(unittest.TestCase):
    def test_partition_preserves_order_and_balances_cost(self):
        items = _clone_items(20)
        lanes = _partition_export_items(items, 2)

        flattened = [idx for lane in lanes for idx, _ in lane]
        self.assertEqual(flattened, list(range(20)))
        self.assertTrue(all(lanes))

        costs = [
            sum(max(32, len(item["text"])) ** 2 for _, item in lane)
            for lane in lanes
        ]
        self.assertLess(max(costs) / min(costs), 1.35)

    def test_dual_pool_maps_callbacks_to_original_indices(self):
        primary = _FakeExportEngine("lane-1")
        replica = _FakeExportEngine("lane-2")
        pool = TTSExportPool(primary, requested_workers=2)
        pool.engines = [primary, replica]
        items = _clone_items(20)
        seen = {}
        statuses = []

        results = pool.generate_many(
            items,
            num_step=16,
            on_item=lambda idx, result: seen.__setitem__(idx, result["cache_key"]),
            on_status=statuses.append,
        )

        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(replica.calls), 1)
        self.assertEqual(set(seen), set(range(20)))
        self.assertEqual([result["cache_key"] for result in results], [
            f"k-{i}" for i in range(20)
        ])
        self.assertTrue(any("worker 1/2" in status for status in statuses))
        self.assertTrue(any("worker 2/2" in status for status in statuses))
        self.assertEqual(primary.prompts, [("speaker.wav", "Reference.")])
        self.assertEqual(replica.prompts, [("speaker.wav", "Reference.")])

    def test_non_clone_items_stay_on_primary(self):
        primary = _FakeExportEngine("primary")
        replica = _FakeExportEngine("lane-2")
        pool = TTSExportPool(primary, requested_workers=2)
        pool.engines = [primary, replica]
        items = _clone_items(20)
        for item in items:
            item["ref_audio"] = None

        pool.generate_many(items, num_step=16)

        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(replica.calls), 0)


if __name__ == "__main__":
    unittest.main()


class CancellationPropagationTest(unittest.TestCase):
    def test_dual_pool_surfaces_real_cancellation(self):
        from core.cancellation import GenerationAborted, SiblingAborted

        class Cancelled(GenerationAborted):
            pass

        primary = _FakeExportEngine("lane-1")
        replica = _FakeExportEngine("lane-2")
        pool = TTSExportPool(primary, requested_workers=2)
        pool.engines = [primary, replica]
        calls = []

        def on_item(idx, result):
            calls.append(idx)
            raise Cancelled("stop")

        with self.assertRaises(Cancelled) as ctx:
            pool.generate_many(_clone_items(20), num_step=16, on_item=on_item)
        self.assertNotIsInstance(ctx.exception, SiblingAborted)

    def test_engine_emit_reraises_generation_aborted(self):
        import os
        import tempfile
        from unittest.mock import patch

        import numpy as np
        import soundfile as sf

        from core import tts_engine
        from core.cancellation import GenerationAborted

        engine = tts_engine.TTSEngine()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tts_engine, "AUDIO_CACHE_DIR", tmp), \
                patch.object(tts_engine, "_voice_design_anchor_enabled", return_value=False):
            items = [{"text": f"Mondat {i}.", "instruct": "x"} for i in range(3)]
            for item in items:
                key = engine.cache_key(item["text"], tts_engine._stabilize_voice_design_instruct("x"),
                                       None, 1.0, language=None,
                                       normalize_text=tts_engine._normalize_text_enabled(),
                                       num_step=16, variant=engine._render_variant)
                sf.write(os.path.join(tmp, f"{key}.wav"), np.zeros(240), 24000)

            def on_item(idx, result):
                raise GenerationAborted("cancel")

            with self.assertRaises(GenerationAborted):
                engine.generate_many(items, num_step=16, on_item=on_item)


class VoiceDesignAnchorTest(unittest.TestCase):
    def test_design_voice_is_anchored_once_and_cloned(self):
        import tempfile
        from unittest.mock import patch

        import numpy as np

        from core import tts_engine

        engine = tts_engine.TTSEngine()
        calls = []

        def fake_batch(texts, instruct=None, ref_audio=None, **kw):
            calls.append((tuple(texts), instruct, ref_audio, kw.get("language")))
            rng = np.random.default_rng(len(calls))
            return [rng.standard_normal(2400).astype("float32") * 0.1 for _ in texts]

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tts_engine, "AUDIO_CACHE_DIR", tmp), \
                patch.object(tts_engine, "VOICE_REF_DIR", tmp), \
                patch.object(tts_engine, "_voice_design_anchor_enabled", return_value=True), \
                patch.object(engine, "_synthesize_batch", side_effect=fake_batch):
            items = [
                {"text": f"Ez a {i}. mondat.", "instruct": "female, middle-aged", "language": "hu"}
                for i in range(3)
            ]
            results = engine.generate_many(items, num_step=16)
            anchored = engine.anchor_items(items)

        # First call renders the Hungarian anchor sentence with the description.
        self.assertIn("Jó napot kívánok", calls[0][0][0])
        self.assertEqual(calls[0][1], "female, middle-aged")
        # Segments are then cloned from the anchor clip, not designed again.
        self.assertTrue(all(call[1] is None and call[2] for call in calls[1:]))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(item["ref_audio"] and item["instruct"] is None for item in anchored))
