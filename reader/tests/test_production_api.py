import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import app as app_module
from core import database, jobs
from core import settings as app_settings


class _Engine:
    engine_name = "supertonic"

    def __init__(self, folder):
        self.folder = folder

    def status(self):
        return {"state": "ready", "engine": "supertonic", "capabilities": {"voice_clone": False}}

    def generate_many(self, items, on_item=None, **kw):
        results = []
        for index, item in enumerate(items):
            path = os.path.join(self.folder, f"{abs(hash(item['text']))}.wav")
            sf.write(path, np.zeros(2400, dtype="float32"), 24000)
            result = {"audio_path": path, "cache_key": f"k{abs(hash(item['text']))}",
                      "duration_sec": 0.1, "cache_hit": False}
            results.append(result)
            if on_item:
                on_item(index, result)
        return results

    def __getattr__(self, name):
        return lambda *a, **k: None


class ProductionApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            patch.object(database, "DB_PATH", os.path.join(self.tmp.name, "reader.db")),
            patch.object(app_settings, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json"),
            patch.object(app_module, "_startup_complete", True),
            patch.object(app_module, "tts", _Engine(self.tmp.name)),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        jobs.init_jobs()
        with database.get_conn() as conn:
            conn.execute("INSERT INTO books (id,title,file_path,file_type,language,single_narrator_mode) "
                         "VALUES (1,'K','k','txt','hu',1)")
            conn.execute("INSERT INTO chapters (id,book_id,title,order_num,content,word_count) "
                         "VALUES (1,1,'Egy',0,'Első mondat. Második mondat.',4)")
            conn.execute("INSERT INTO chapters (id,book_id,title,order_num,content,word_count) "
                         "VALUES (2,1,'Kettő',1,'Harmadik mondat.',2)")
        self.client = app_module.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_summary_before_and_after_book_generation(self):
        data = self.client.get("/api/books/1/production").get_json()
        self.assertEqual(len(data["chapters"]), 2)
        self.assertEqual(data["totals"]["ready"], 0)
        steps = {s["key"]: s for s in data["steps"]}
        self.assertTrue(steps["characters"]["done"])  # single narrator
        self.assertFalse(steps["generate"]["done"])

        job_id = self.client.post("/api/books/1/generate", json={}).get_json()["job_id"]
        for _ in range(200):
            job = jobs.get_job(job_id)
            if job["state"] not in ("pending", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "complete", job)
        data = self.client.get("/api/books/1/production").get_json()
        self.assertEqual(data["totals"]["ready"], data["totals"]["segments"])
        self.assertTrue({s["key"]: s for s in data["steps"]}["generate"]["done"])

    def test_page_renders(self):
        response = self.client.get("/production/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Teljes könyv generálása".encode("utf-8"), response.data)

    def test_generation_started_while_engine_loads_waits_for_it(self):
        engine = app_module.tts
        states = iter(["not_loaded", "loading", "loading"])
        loads = []
        engine.status = lambda: {"state": next(states, "ready"), "engine": "supertonic"}
        engine.load_async = lambda: loads.append(1)
        response = self.client.post("/api/books/1/generate", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        job_id = response.get_json()["job_id"]
        for _ in range(200):
            job = jobs.get_job(job_id)
            if job["state"] not in ("pending", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "complete", job)
        self.assertTrue(loads)
        self.assertEqual(job["result"]["ready"], job["result"]["total"])
        self.assertGreater(job["result"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
