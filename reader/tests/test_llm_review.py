import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from core import database, jobs, llm_review
from core import settings as app_settings


class LlmReviewUnitTest(unittest.TestCase):
    def test_voice_suggestions_become_accent_free_instructions(self):
        answer = {"voices": [
            {"name": "Anna", "gender": "female", "age": "young adult", "pitch": "high pitch",
             "description": "Élénk, gyors beszédű fiatal nő."},
            {"name": "Idegen", "gender": "male", "age": "elderly", "pitch": "low pitch", "description": "x"},
        ]}
        with patch.object(llm_review, "_chat", return_value=answer) as chat:
            result = llm_review.suggest_voices(
                title="K", characters=[{"id": 3, "name": "Anna", "lines": ["Gyere!"], "mentions": []}],
                llm={"base_url": "x", "api_key": "", "model": "m", "provider": "local"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["instruct"], "female, young adult, high pitch")
        self.assertEqual(result[0]["character_id"], 3)
        self.assertIn("voice_casting", chat.call_args.kwargs["schema_name"])

    def test_review_accepts_only_roster_names_and_dialogue_units(self):
        units = [
            {"index": 0, "text": "– Gyere ide!", "dialogue_candidate": True, "speaker": "Péter"},
            {"index": 1, "text": "– mondta Anna.", "dialogue_candidate": False},
        ]
        answer = {"corrections": [
            {"id": 0, "speaker": "anna", "reason": "attribution clause"},
            {"id": 1, "speaker": "Anna", "reason": "narration id"},
            {"id": 0, "speaker": "Kitalált Név", "reason": "invented"},
        ]}
        with patch.object(llm_review, "_chat", return_value=answer):
            result = llm_review.review_speakers(
                title="K", units=units, roster=["Anna", "Péter"],
                llm={"base_url": "x", "api_key": "", "model": "m", "provider": "local"})
        self.assertEqual(result, [{"unit_index": 0, "speaker": "Anna", "reason": "attribution clause"}])


class _Engine:
    engine_name = "omnivoice"

    def status(self):
        return {"state": "ready"}

    def unload(self):
        pass

    def wait_until_unloaded(self, timeout=600):
        return True

    def __getattr__(self, name):
        return lambda *a, **k: None


class AssistApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(database, "DB_PATH", os.path.join(self.tmp.name, "reader.db")),
            patch.object(app_settings, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json"),
            patch.object(app_module, "_startup_complete", True),
            patch.object(app_module, "tts", _Engine()),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        jobs.init_jobs()
        app_settings.save({"llm_provider": "local", "llm_model": "m", "llm_base_url": "http://127.0.0.1:9/v1"})
        content = "– Gyere ide! – mondta Anna.\n\n– Nem megyek – felelte Péter."
        with database.get_conn() as conn:
            conn.execute("INSERT INTO books (id,title,file_path,file_type,language) VALUES (1,'K','k','txt','hu')")
            conn.execute("INSERT INTO chapters (id,book_id,title,order_num,content) VALUES (1,1,'F',0,?)", (content,))
            conn.execute("INSERT INTO characters (id,book_id,name,gender) VALUES (1,1,'Anna','female')")
            conn.execute("INSERT INTO characters (id,book_id,name,gender) VALUES (2,1,'Péter','male')")
            conn.execute("INSERT INTO speaker_annotations (book_id,chapter_id,unit_index,unit_text,speaker_name,source) "
                         "VALUES (1,1,0,'– Gyere ide!','Péter','automatic')")
            conn.execute("INSERT INTO speaker_annotations (book_id,chapter_id,unit_index,unit_text,speaker_name,source) "
                         "VALUES (1,1,2,'– Nem megyek','Anna','manual')")
        self.client = app_module.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _wait(self, job_id):
        for _ in range(400):
            job = jobs.get_job(job_id)
            if job["state"] not in ("pending", "running"):
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_review_fixes_automatic_but_keeps_manual_speakers(self):
        corrections = [{"unit_index": 0, "speaker": "Anna", "reason": "x"},
                       {"unit_index": 2, "speaker": "Péter", "reason": "y"}]
        with patch.object(llm_review, "review_speakers", return_value=corrections):
            job_id = self.client.post("/api/books/1/speaker-review", json={}).get_json()["job_id"]
            job = self._wait(job_id)
        self.assertEqual(job["state"], "complete", job)
        with database.get_conn() as conn:
            rows = {r["unit_index"]: (r["speaker_name"], r["source"]) for r in conn.execute(
                "SELECT unit_index, speaker_name, source FROM speaker_annotations")}
        self.assertEqual(rows[0], ("Anna", "llm-review"))
        self.assertEqual(rows[2], ("Anna", "manual"))

    def test_voice_suggestions_job_returns_result(self):
        suggestion = [{"name": "Anna", "character_id": 1, "gender": "female",
                       "instruct": "female, young adult, high pitch", "description": "x"}]
        with patch.object(llm_review, "suggest_voices", return_value=suggestion):
            job_id = self.client.post("/api/books/1/voice-suggestions").get_json()["job_id"]
            job = self._wait(job_id)
        self.assertEqual(job["state"], "complete", job)
        detail = self.client.get(f"/api/jobs/{job_id}").get_json()
        self.assertEqual(detail["result"]["suggestions"][0]["instruct"], "female, young adult, high pitch")


if __name__ == "__main__":
    unittest.main()
