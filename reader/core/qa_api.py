"""Quality-control API: chapter checks, alternative takes, approval, loudness."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading

from flask import Blueprint, jsonify, render_template, request

from core import jobs, qa
from core.database import get_conn

log = logging.getLogger(__name__)
bp = Blueprint("qa", __name__)

_tables_ready: set[str] = set()
_tables_lock = threading.Lock()


def ensure_tables() -> None:
    from core.database import get_db_path

    path = get_db_path()
    with _tables_lock:
        if path in _tables_ready and os.path.exists(path):
            return
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS segment_qa (
                cache_key     TEXT PRIMARY KEY,
                book_id       INTEGER NOT NULL,
                chapter_id    INTEGER NOT NULL,
                segment_index INTEGER,
                text_hash     TEXT,
                status        TEXT NOT NULL,
                cer           REAL,
                wer           REAL,
                heard         TEXT,
                flags_json    TEXT NOT NULL DEFAULT '[]',
                metrics_json  TEXT NOT NULL DEFAULT '{}',
                asr_model     TEXT,
                approved      INTEGER NOT NULL DEFAULT 0,
                checked_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_segment_qa_chapter ON segment_qa(book_id, chapter_id);
            CREATE TABLE IF NOT EXISTS segment_takes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id       INTEGER NOT NULL,
                chapter_id    INTEGER NOT NULL,
                text_hash     TEXT NOT NULL,
                take          INTEGER NOT NULL,
                cache_key     TEXT NOT NULL,
                audio_path    TEXT NOT NULL,
                duration_sec  REAL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(book_id, chapter_id, text_hash, take)
            );
            """
        )
        columns = {r[1] for r in conn.execute("PRAGMA table_info(segment_takes)")}
        if "voice_label" not in columns:
            # A take may use another saved voice profile than the character's own.
            conn.execute("ALTER TABLE segment_takes ADD COLUMN voice_label TEXT")
    from core import alignment, sfx

    alignment.ensure_table()
    sfx.ensure_table()
    with _tables_lock:
        _tables_ready.add(path)


def segment_hash(seg: dict) -> str:
    """Identity of a spoken unit that survives segment-row rebuilds."""
    payload = "|".join(str(seg.get(k) or "") for k in (
        "enriched_text", "instruct", "character_name", "speed"))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _app():
    import app as application

    return application


def _voice_item(application, book_id: int, seg: dict, chars: dict, narrator, language):
    char = chars.get(seg.get("character_name")) if seg.get("character_name") else None
    if char:
        ref_audio = char.get("ref_audio_path") or None
        ref_text = (char.get("ref_text") or None) if ref_audio else None
    else:
        ref_audio, ref_text = narrator
    return {
        "text": seg["enriched_text"], "instruct": seg["instruct"],
        "ref_audio": ref_audio, "ref_text": ref_text,
        "speed": seg["speed"], "language": language,
    }


def _book_context(application, book_id: int):
    with get_conn() as conn:
        book = conn.execute("SELECT language FROM books WHERE id=?", (book_id,)).fetchone()
        chars = {r["name"]: dict(r) for r in conn.execute(
            "SELECT * FROM characters WHERE book_id=?", (book_id,)).fetchall()}
    language = book["language"] if book and book["language"] else None
    narrator = application._book_narrator_reference(book_id)
    return language, chars, narrator


def _asr_prompt(chars: dict) -> str:
    names = [name for name in chars if name][:12]
    return ", ".join(names)


def _evaluate(path: str, text: str, language, *, transcriber, prompt: str,
              cache_key: str | None = None, align: bool = True,
              confirm_above: float = qa.DEFAULT_CER_WARN) -> dict:
    from core import alignment

    metrics = qa.analyze_audio(path, text)
    result = {"metrics": metrics, "flags": list(metrics["flags"]), "cer": None,
              "wer": None, "heard": None}
    if transcriber is not None:
        single_pass = getattr(transcriber, "single_pass_words", False)
        heard = transcriber.transcribe(path, language, prompt=prompt, word_timestamps=single_pass)
        score = qa.score_transcript(text, heard["text"], language)
        if getattr(transcriber, "confirms", False) and score["cer"] >= confirm_above:
            # Hybrid ASR: a suspicious sentence is re-heard by Whisper; the
            # better reading counts, so a Parakeet slip never fails good audio.
            second = transcriber.confirm(path, language, prompt=prompt)
            second_score = qa.score_transcript(text, second["text"], language)
            if second_score["cer"] < score["cer"]:
                score, heard = second_score, {**second, "words": heard.get("words")}
        result.update(cer=score["cer"], wer=score["wer"], heard=heard["text"],
                      missing=score["missing_words"])
        if align and cache_key:
            # Word timestamps drive read-along highlighting (a second Whisper
            # pass; Parakeet already returned them).
            try:
                timed = heard if single_pass and heard.get("words") else transcriber.transcribe(
                    path, language, word_timestamps=True)
                timings = alignment.align_words(text, timed["words"], metrics["duration_sec"], language)
                alignment.store(cache_key, timings, "asr")
            except Exception as exc:
                log.warning("Word alignment failed for %s: %s", cache_key, exc)
    return result


def _store(book_id, chapter_id, seg, cache_key, evaluation, status, model) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO segment_qa (cache_key,book_id,chapter_id,segment_index,text_hash,status,"
            "cer,wer,heard,flags_json,metrics_json,asr_model,approved,checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT approved FROM segment_qa WHERE cache_key=?),0),datetime('now')) "
            "ON CONFLICT(cache_key) DO UPDATE SET status=excluded.status, cer=excluded.cer, "
            "wer=excluded.wer, heard=excluded.heard, flags_json=excluded.flags_json, "
            "metrics_json=excluded.metrics_json, asr_model=excluded.asr_model, "
            "segment_index=excluded.segment_index, checked_at=datetime('now')",
            (cache_key, book_id, chapter_id, seg["segment_index"], segment_hash(seg), status,
             evaluation.get("cer"), evaluation.get("wer"), evaluation.get("heard"),
             json.dumps(evaluation.get("flags") or [], ensure_ascii=False),
             json.dumps(evaluation.get("metrics") or {}, ensure_ascii=False), model, cache_key),
        )


def _record_take(book_id, chapter_id, seg, take, result, voice_label=None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO segment_takes (book_id,chapter_id,text_hash,take,cache_key,"
            "audio_path,duration_sec,voice_label) VALUES (?,?,?,?,?,?,?,?)",
            (book_id, chapter_id, segment_hash(seg), take, result["cache_key"],
             result["audio_path"], result["duration_sec"], voice_label),
        )


def _profile_voice(profile_id) -> tuple[dict, str]:
    """Voice fields of a saved profile, for a take in another voice."""
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        raise ValueError("Érvénytelen hangprofil.") from None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise ValueError("A hangprofil nem található.")
    ref_audio = row["ref_audio_path"] if row["ref_audio_path"] and os.path.exists(row["ref_audio_path"]) else None
    return ({"instruct": row["instruct"], "ref_audio": ref_audio,
             "ref_text": (row["ref_text"] or None) if ref_audio else None}, row["name"])


def _use_take(seg_id: int, result: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tts_segments SET audio_path=?, duration_sec=?, cache_key=? WHERE id=?",
            (result["audio_path"], result["duration_sec"], result["cache_key"], seg_id),
        )


def _options() -> dict:
    from core import settings

    return {
        "cer_warn": float(settings.get("qa_cer_warn", qa.DEFAULT_CER_WARN)),
        "cer_fail": float(settings.get("qa_cer_fail", qa.DEFAULT_CER_FAIL)),
        "max_takes": int(settings.get("qa_max_takes", 3)),
    }


def run_qa_job(job_id: str, book_id: int, chapter_id: int, use_asr: bool, auto_regenerate: bool):
    application = _app()
    job = application._legacy_job(jobs.get_job(job_id))
    ensure_tables()
    application._export_exclusive_begin()
    try:
        job.update(state="running", message="Szövegrészek betöltése…")
        application._persist_job(job)
        application._wait_for_engine(job)
        segs = application._get_chapter_segments(chapter_id, book_id)
        job["total"] = len(segs)
        job["done"] = 0
        application._ensure_audio_for_chapter(book_id, chapter_id, segs, job)
        language, chars, narrator = _book_context(application, book_id)
        transcriber = qa.Transcriber.for_language(language) if use_asr else None
        prompt = _asr_prompt(chars)
        options = _options()
        capabilities = application.tts.status().get("capabilities") or {}
        can_retake = bool(capabilities.get("takes"))
        job.update(done=0, message="Ellenőrzés…")
        evaluations = []
        for index, seg in enumerate(segs):
            application._check_job_cancelled(job)
            path = seg.get("audio_path")
            if not path or not os.path.exists(path):
                evaluations.append(None)
                continue
            evaluation = _evaluate(path, seg["text"], language, transcriber=transcriber,
                                   prompt=prompt, cache_key=seg.get("cache_key"),
                                   confirm_above=options["cer_warn"])
            evaluations.append(evaluation)
            job["done"] = index + 1
            job["message"] = f"Ellenőrzés ({index + 1}/{len(segs)})"
            application._persist_job(job)
        outliers = qa.duration_outliers([e["metrics"] if e else {} for e in evaluations])
        regenerated = 0
        failed = 0
        for index, (seg, evaluation) in enumerate(zip(segs, evaluations)):
            if evaluation is None:
                continue
            if index in outliers and "duration_outlier" not in evaluation["flags"]:
                evaluation["flags"].append("duration_outlier")
            status = qa.classify(evaluation["cer"], evaluation["flags"],
                                 warn=options["cer_warn"], fail=options["cer_fail"])
            if status == "fail" and auto_regenerate and can_retake:
                application._check_job_cancelled(job)
                job["message"] = f"Újragenerálás: {index + 1}. szakasz"
                application._persist_job(job)
                _record_take(book_id, chapter_id, seg, 0, {
                    "cache_key": seg["cache_key"], "audio_path": seg["audio_path"],
                    "duration_sec": seg.get("duration_sec")})
                best = (evaluation, status, {"cache_key": seg["cache_key"], "audio_path": seg["audio_path"],
                                             "duration_sec": seg.get("duration_sec")})
                item = _voice_item(application, book_id, seg, chars, narrator, language)
                for take in range(1, options["max_takes"] + 1):
                    application._check_job_cancelled(job)
                    result = application.tts.generate(**item, take=take)
                    _record_take(book_id, chapter_id, seg, take, result)
                    candidate = _evaluate(result["audio_path"], seg["text"], language,
                                          transcriber=transcriber, prompt=prompt,
                                          cache_key=result["cache_key"],
                                          confirm_above=options["cer_warn"])
                    candidate_status = qa.classify(candidate["cer"], candidate["flags"],
                                                   warn=options["cer_warn"], fail=options["cer_fail"])
                    _store(book_id, chapter_id, seg, result["cache_key"], candidate,
                           candidate_status, transcriber.model_id if transcriber else None)
                    better = (candidate["cer"] or 0) < (best[0]["cer"] or 0) or (
                        candidate["cer"] == best[0]["cer"] and len(candidate["flags"]) < len(best[0]["flags"]))
                    if better:
                        best = (candidate, candidate_status, result)
                    if candidate_status == "ok":
                        break
                if best[2]["cache_key"] != seg["cache_key"]:
                    _use_take(seg["id"], best[2])
                    seg.update(best[2])
                    regenerated += 1
                evaluation, status = best[0], best[1]
            _store(book_id, chapter_id, seg, seg["cache_key"], evaluation, status,
                   transcriber.model_id if transcriber else None)
            failed += status == "fail"
        from core import settings

        if not settings.get("asr_keep_loaded", False):
            qa.Transcriber.unload_all()
        job.update(state="complete", message="Elkészült", done=len(segs),
                   result={"book_id": book_id, "chapter_id": chapter_id,
                           "regenerated": regenerated, "failed": failed})
        application._persist_job(job)
    except application.JobCancelled:
        jobs.mark_cancelled(job_id, "Ellenőrzés leállítva")
    except Exception as exc:
        log.exception("QA job %s failed", job_id)
        job.update(state="failed", error=str(exc), message="Az ellenőrzés nem sikerült")
        application._persist_job(job)
    finally:
        application._close_orphaned_job(job_id)
        application._export_exclusive_end()


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route("/qa/<int:book_id>")
def qa_page(book_id):
    with get_conn() as conn:
        book = conn.execute("SELECT id, title, author, language FROM books WHERE id=?",
                            (book_id,)).fetchone()
        chapters = conn.execute(
            "SELECT id, title, order_num FROM chapters WHERE book_id=? ORDER BY order_num",
            (book_id,)).fetchall()
    if not book:
        return "Not found", 404
    return render_template("qa.html", book=dict(book), chapters=[dict(c) for c in chapters])


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/qa", methods=["POST"])
def start_qa(book_id, chapter_id):
    application = _app()
    ensure_tables()
    body = request.get_json(silent=True) or {}
    if application.tts.status().get("state") == "not_loaded":
        application.tts.load_async()  # the job waits for it
    with application._work_dispatch_lock:
        conflict = application._work_conflict_response()
        if conflict is not None:
            return conflict
        stored = jobs.create_job(
            "qa_chapter",
            {"book_id": book_id, "chapter_id": chapter_id,
             "asr": bool(body.get("asr", True)),
             "auto_regenerate": bool(body.get("auto_regenerate", True))},
            book_id=book_id, chapter_id=chapter_id,
        )
        application._launch_durable_job_unlocked(stored)
    return jsonify(job_id=stored["id"])


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/qa")
def chapter_qa(book_id, chapter_id):
    application = _app()
    ensure_tables()
    segs = application._get_chapter_segments(chapter_id, book_id)
    with get_conn() as conn:
        rows = {r["cache_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM segment_qa WHERE book_id=? AND chapter_id=?", (book_id, chapter_id))}
        from core import sfx

        effects = sfx.effects_for_chapter(book_id, chapter_id)
        takes = {}
        for r in conn.execute(
                "SELECT text_hash, take, cache_key, duration_sec, voice_label FROM segment_takes "
                "WHERE book_id=? AND chapter_id=? ORDER BY take", (book_id, chapter_id)):
            takes.setdefault(r["text_hash"], []).append(dict(r))
    items = []
    summary = {"ok": 0, "warn": 0, "fail": 0, "unchecked": 0, "approved": 0}
    for seg in segs:
        row = rows.get(seg.get("cache_key") or "")
        status = row["status"] if row else "unchecked"
        summary[status] = summary.get(status, 0) + 1
        if row and row["approved"]:
            summary["approved"] += 1
        items.append({
            "segment_index": seg["segment_index"],
            "text": seg["text"],
            "character_name": seg.get("character_name"),
            "has_audio": bool(seg.get("audio_path") and os.path.exists(seg["audio_path"])),
            "audio_url": f"/api/audio/{seg['cache_key']}" if seg.get("audio_path") else None,
            "cache_key": seg.get("cache_key"),
            "status": status,
            "cer": row["cer"] if row else None,
            "heard": row["heard"] if row else None,
            "flags": json.loads(row["flags_json"]) if row else [],
            "metrics": json.loads(row["metrics_json"]) if row else {},
            "approved": bool(row and row["approved"]),
            "sfx": ({key: effects[segment_hash(seg)][key] for key in ("name", "gain_db", "position")}
                    if segment_hash(seg) in effects else None),
            "takes": [
                {**t, "audio_url": f"/api/audio/{t['cache_key']}",
                 "selected": t["cache_key"] == seg.get("cache_key")}
                for t in takes.get(segment_hash(seg), [])
            ],
        })
    return jsonify(segments=items, summary=summary)


def _segment_by_index(application, book_id, chapter_id, index):
    segs = application._get_chapter_segments(chapter_id, book_id)
    for seg in segs:
        if int(seg["segment_index"]) == int(index):
            return seg
    raise ValueError("A szakasz nem található.")


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/segments/<int:index>/takes",
          methods=["POST"])
def new_take(book_id, chapter_id, index):
    """Render one more alternative of a sentence and select it."""
    application = _app()
    ensure_tables()
    if application._export_exclusive_active():
        return jsonify(error=application._WORK_BUSY_MESSAGE), 409
    if application.tts.status().get("state") != "ready":
        return jsonify(error="A beszédmotor még nem áll készen."), 503
    seg = _segment_by_index(application, book_id, chapter_id, index)
    language, chars, narrator = _book_context(application, book_id)
    with get_conn() as conn:
        taken = [r[0] for r in conn.execute(
            "SELECT take FROM segment_takes WHERE book_id=? AND chapter_id=? AND text_hash=?",
            (book_id, chapter_id, segment_hash(seg)))]
        if not taken and seg.get("audio_path"):
            conn.execute(
                "INSERT OR IGNORE INTO segment_takes (book_id,chapter_id,text_hash,take,cache_key,"
                "audio_path,duration_sec) VALUES (?,?,?,?,?,?,?)",
                (book_id, chapter_id, segment_hash(seg), 0, seg["cache_key"], seg["audio_path"],
                 seg.get("duration_sec")))
            taken = [0]
    take = max(taken or [0]) + 1
    item = _voice_item(application, book_id, seg, chars, narrator, language)
    body = request.get_json(silent=True) or {}
    voice_label = None
    if body.get("profile_id"):
        try:
            voice, voice_label = _profile_voice(body["profile_id"])
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        item.update(voice)
    result = application.tts.generate(**item, take=take)
    _record_take(book_id, chapter_id, seg, take, result, voice_label)
    _use_take(seg["id"], result)
    return jsonify(take=take, cache_key=result["cache_key"],
                   audio_url=f"/api/audio/{result['cache_key']}")


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/segments/<int:index>/sfx",
          methods=["POST", "DELETE"])
def segment_sfx(book_id, chapter_id, index):
    """Attach (POST, multipart ``file``) or remove (DELETE) a sentence sound effect."""
    import tempfile

    from core import sfx

    application = _app()
    ensure_tables()
    seg = _segment_by_index(application, book_id, chapter_id, index)
    if request.method == "DELETE":
        return jsonify(ok=sfx.remove_effect(book_id, chapter_id, segment_hash(seg)))
    upload = request.files.get("file")
    name = os.path.basename(str(getattr(upload, "filename", "") or ""))
    if not upload or os.path.splitext(name)[1].lower() not in sfx.ALLOWED_EXTENSIONS:
        return jsonify(error="WAV, MP3, OGG, Opus, FLAC vagy M4A hangfájlt válassz."), 400
    try:
        gain_db = float(request.form.get("gain_db", -8))
    except ValueError:
        return jsonify(error="Érvénytelen hangerő."), 400
    position = request.form.get("position", "with")
    with tempfile.TemporaryDirectory(prefix="auris-sfx-") as folder:
        raw = os.path.join(folder, "upload" + os.path.splitext(name)[1].lower())
        upload.save(raw)
        try:
            path = sfx.convert_upload(raw, name, os.path.join(application.UPLOAD_DIR, "sfx"))
            effect = sfx.set_effect(book_id, chapter_id, segment_hash(seg), path, name, gain_db, position)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    return jsonify(ok=True, sfx=effect)


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/segments/<int:index>/select-take",
          methods=["POST"])
def select_take(book_id, chapter_id, index):
    application = _app()
    ensure_tables()
    body = request.get_json(silent=True) or {}
    seg = _segment_by_index(application, book_id, chapter_id, index)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM segment_takes WHERE book_id=? AND chapter_id=? AND text_hash=? AND take=?",
            (book_id, chapter_id, segment_hash(seg), int(body.get("take", 0)))).fetchone()
    if not row or not os.path.exists(row["audio_path"]):
        return jsonify(error="Ez a változat nem érhető el."), 404
    _use_take(seg["id"], dict(row))
    return jsonify(ok=True, cache_key=row["cache_key"])


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/segments/<int:index>/approve",
          methods=["POST"])
def approve_segment(book_id, chapter_id, index):
    application = _app()
    ensure_tables()
    body = request.get_json(silent=True) or {}
    seg = _segment_by_index(application, book_id, chapter_id, index)
    approved = 1 if body.get("approved", True) else 0
    with get_conn() as conn:
        updated = conn.execute("UPDATE segment_qa SET approved=? WHERE cache_key=?",
                               (approved, seg.get("cache_key"))).rowcount
        if not updated:
            conn.execute(
                "INSERT INTO segment_qa (cache_key,book_id,chapter_id,segment_index,text_hash,status,"
                "approved) VALUES (?,?,?,?,?,?,?)",
                (seg.get("cache_key"), book_id, chapter_id, seg["segment_index"],
                 segment_hash(seg), "unchecked", approved))
    return jsonify(ok=True, approved=bool(approved))


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/loudness")
def chapter_loudness(book_id, chapter_id):
    """Render the chapter timeline to a temporary WAV and measure it."""
    application = _app()
    from core import exporter

    segs = application._get_chapter_segments(chapter_id, book_id)
    if not any(seg.get("audio_path") and os.path.exists(seg["audio_path"]) for seg in segs):
        return jsonify(error="A fejezethez még nincs hang."), 404
    with tempfile.TemporaryDirectory(prefix="auris-loudness-") as tmp:
        path = os.path.join(tmp, "chapter.wav")
        exporter._write_merged_wav(exporter.build_timeline(segs), path)
        raw = qa.loudness_report(path)
        mastered_path = os.path.join(tmp, "mastered.wav")
        mastered = None
        applied, _warning = exporter._master_wav(path, mastered_path)
        if applied:
            mastered = qa.loudness_report(mastered_path)
    return jsonify(raw=raw, mastered=mastered)


@bp.route("/api/books/<int:book_id>/chapters/<int:chapter_id>/word-timings")
def word_timings(book_id, chapter_id):
    """Measured word timings of the chapter's current audio, by cache key."""
    from core import alignment

    ensure_tables()
    with get_conn() as conn:
        keys = [r[0] for r in conn.execute(
            "SELECT cache_key FROM tts_segments WHERE book_id=? AND chapter_id=? "
            "AND audio_path IS NOT NULL", (book_id, chapter_id))]
    return jsonify(timings=alignment.load_many(keys))
