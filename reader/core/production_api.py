"""Book production dashboard: one place that answers "how far is this book?"."""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, render_template, request

from core import jobs
from core.database import get_conn

log = logging.getLogger(__name__)
bp = Blueprint("production", __name__)


def _app():
    import app as application

    return application


@bp.route("/production/<int:book_id>")
def production_page(book_id):
    with get_conn() as conn:
        book = conn.execute("SELECT id, title, author, language, cover_b64 FROM books WHERE id=?",
                            (book_id,)).fetchone()
    if not book:
        return "Not found", 404
    return render_template("production.html", book=dict(book))


def _voice_source(char: dict) -> str:
    if char.get("ref_audio_path") and os.path.exists(char["ref_audio_path"]):
        return "reference"
    return "description"


@bp.route("/api/books/<int:book_id>/production")
def production_summary(book_id):
    application = _app()
    from core.qa_api import ensure_tables

    ensure_tables()
    with get_conn() as conn:
        book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not book:
            return jsonify(error="A könyv nem található."), 404
        chapters = [dict(r) for r in conn.execute(
            "SELECT id, title, order_num, word_count FROM chapters WHERE book_id=? ORDER BY order_num",
            (book_id,))]
        characters = [dict(r) for r in conn.execute(
            "SELECT id, name, gender, frequency, instruct, ref_audio_path, ref_audio_name "
            "FROM characters WHERE book_id=? ORDER BY frequency DESC, name", (book_id,))]
        qa_rows = conn.execute(
            "SELECT chapter_id, status, approved, COUNT(*) AS n FROM segment_qa WHERE book_id=? "
            "GROUP BY chapter_id, status, approved", (book_id,)).fetchall()
    qa_by_chapter: dict[int, dict] = {}
    for row in qa_rows:
        entry = qa_by_chapter.setdefault(row["chapter_id"], {"ok": 0, "warn": 0, "fail": 0, "approved": 0})
        if row["status"] in entry:
            entry[row["status"]] += row["n"]
        if row["approved"]:
            entry["approved"] += row["n"]
    totals = {"segments": 0, "ready": 0, "duration_sec": 0.0, "words": 0,
              "qa_checked": 0, "qa_fail": 0, "qa_warn": 0}
    chapter_rows = []
    for number, chapter in enumerate(chapters, 1):
        segs = application._get_chapter_segments(chapter["id"], book_id)
        ready = [s for s in segs if s.get("audio_path") and os.path.exists(s["audio_path"])]
        duration = sum(float(s.get("duration_sec") or 0) for s in ready)
        qa = qa_by_chapter.get(chapter["id"], {"ok": 0, "warn": 0, "fail": 0, "approved": 0})
        checked = qa["ok"] + qa["warn"] + qa["fail"]
        chapter_rows.append({
            "id": chapter["id"], "number": number, "title": chapter["title"],
            "words": chapter.get("word_count") or 0, "segments": len(segs), "ready": len(ready),
            "duration_sec": round(duration, 1), "qa": qa, "qa_checked": checked,
            "speakers": sorted({s["character_name"] for s in segs if s.get("character_name")}),
        })
        totals["segments"] += len(segs)
        totals["ready"] += len(ready)
        totals["duration_sec"] += duration
        totals["words"] += chapter.get("word_count") or 0
        totals["qa_checked"] += checked
        totals["qa_fail"] += qa["fail"]
        totals["qa_warn"] += qa["warn"]
    single = bool(book["single_narrator_mode"])
    speaking = {name for row in chapter_rows for name in row["speakers"]}
    cast = [{
        "id": c["id"], "name": c["name"], "gender": c["gender"], "lines": c["frequency"] or 0,
        "voice_source": _voice_source(c), "instruct": c["instruct"],
        "speaks": c["name"] in speaking,
    } for c in characters]
    analysis = book["character_analysis_status"] or "pending"
    steps = [
        {"key": "import", "label": "Import", "done": True},
        {"key": "characters", "label": "Szereplők",
         "done": single or analysis in ("complete", "partial"),
         "note": "egy narrátor" if single else analysis},
        {"key": "voices", "label": "Hangok",
         "done": single or all(c["voice_source"] == "reference" or c["instruct"] for c in cast),
         "note": f"{sum(1 for c in cast if c['voice_source'] == 'reference')} referenciával"},
        {"key": "generate", "label": "Generálás",
         "done": totals["segments"] > 0 and totals["ready"] == totals["segments"],
         "note": f"{totals['ready']}/{totals['segments']}"},
        {"key": "check", "label": "Ellenőrzés",
         "done": totals["segments"] > 0 and totals["qa_checked"] >= totals["segments"] and totals["qa_fail"] == 0,
         "note": f"{totals['qa_checked']} ellenőrizve, {totals['qa_fail']} hibás"},
        {"key": "export", "label": "Export", "done": False},
    ]
    status = application.tts.status()
    active = [j for j in jobs.list_active_jobs(book_id=book_id)]
    return jsonify(
        book={"id": book["id"], "title": book["title"], "author": book["author"],
              "language": book["language"], "single_narrator_mode": single},
        steps=steps, chapters=chapter_rows, characters=cast,
        totals={**totals, "duration_sec": round(totals["duration_sec"], 1)},
        engine={"name": status.get("engine"), "state": status.get("state"),
                "capabilities": status.get("capabilities") or {}},
        active_jobs=[{"id": j["id"], "type": j["type"], "state": j["state"], "message": j["message"],
                      "done": j["done"], "total": j["total"]} for j in active],
    )


def run_book_generation(job_id: str, book_id: int, chapter_ids: list[int]) -> None:
    """Generate every pending segment of the selected chapters in one pass."""
    application = _app()
    job = application._legacy_job(jobs.get_job(job_id))
    pool = None
    application._export_exclusive_begin()
    try:
        job.update(state="running", message="Fejezetek betöltése…")
        application._persist_job(job)
        segments = []
        for chapter_id in chapter_ids:
            application._check_job_cancelled(job)
            segments.extend(application._get_chapter_segments(chapter_id, book_id))
        job["total"] = len(segments)
        job["done"] = 0
        pool = application._start_export_pool(job)
        application._ensure_audio_for_chapter(
            book_id, chapter_ids[0] if chapter_ids else 0, segments, job, export_pool=pool)
        ready = sum(1 for s in segments if s.get("audio_path") and os.path.exists(s["audio_path"]))
        job.update(state="complete", done=len(segments), message="Elkészült",
                   result={"book_id": book_id, "ready": ready, "total": len(segments)})
        application._persist_job(job)
    except application.JobCancelled:
        jobs.mark_cancelled(job_id, "Generálás leállítva az aktuális csomag után")
    except Exception as exc:
        log.exception("Book generation %s failed", job_id)
        job.update(state="failed", error=str(exc), message="A generálás nem sikerült")
        application._persist_job(job)
    finally:
        if pool is not None:
            pool.close()
        application._close_orphaned_job(job_id)
        application._export_exclusive_end()


@bp.route("/api/books/<int:book_id>/generate", methods=["POST"])
def start_book_generation(book_id):
    application = _app()
    data = request.get_json(silent=True) or {}
    with get_conn() as conn:
        all_ids = [r[0] for r in conn.execute(
            "SELECT id FROM chapters WHERE book_id=? ORDER BY order_num", (book_id,))]
    wanted = [int(c) for c in (data.get("chapter_ids") or all_ids) if int(c) in set(all_ids)]
    if not wanted:
        return jsonify(error="Nincs generálható fejezet."), 400
    if application.tts.status().get("state") != "ready":
        application.tts.load_async()
        return jsonify(error="A beszédmotor betöltése elindult; próbáld újra, ha kész."), 503
    with application._work_dispatch_lock:
        conflict = application._work_conflict_response()
        if conflict is not None:
            return conflict
        stored = jobs.create_job("generate_book", {"book_id": book_id, "chapter_ids": wanted},
                                 book_id=book_id, total=0)
        application._launch_durable_job_unlocked(stored)
    return jsonify(job_id=stored["id"])
