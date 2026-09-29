"""Language-model assistance jobs: voice casting and speaker review."""

from __future__ import annotations

import logging
import re

from flask import Blueprint, jsonify, request

from core import jobs, llm_review
from core.database import get_conn
from core.enrichment import build_speaker_units

log = logging.getLogger(__name__)
bp = Blueprint("assist", __name__)

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def _app():
    import app as application

    return application


def _character_context(book_id: int) -> list[dict]:
    with get_conn() as conn:
        chars = [dict(r) for r in conn.execute(
            "SELECT id, name, gender FROM characters WHERE book_id=? ORDER BY frequency DESC, name",
            (book_id,))]
        contents = [r["content"] for r in conn.execute(
            "SELECT content FROM chapters WHERE book_id=? ORDER BY order_num", (book_id,))]
        for character in chars:
            character["lines"] = [r[0] for r in conn.execute(
                "SELECT unit_text FROM speaker_annotations WHERE book_id=? AND speaker_name=? "
                "COLLATE NOCASE ORDER BY chapter_id, unit_index LIMIT 6",
                (book_id, character["name"]))]
    for character in chars:
        pattern = re.compile(r"\b" + re.escape(character["name"].split()[-1]))
        mentions = []
        for content in contents:
            for sentence in _SENTENCE_RE.split(content or ""):
                if pattern.search(sentence) and not sentence.lstrip().startswith(("–", "—", "-", "„", '"')):
                    mentions.append(sentence.strip())
                    if len(mentions) >= 4:
                        break
            if len(mentions) >= 4:
                break
        character["mentions"] = mentions
    return chars[:40]


def _with_llm(job, work):
    """Run ``work(llm, config)`` with the same VRAM hand-off as the analysis."""
    application = _app()
    from core import settings

    config = settings.load()
    llm = application._selected_llm_config(config)
    if llm["provider"] == "openai" and not llm["api_key"]:
        raise RuntimeError("Az OpenAI-hoz API-kulcs szükséges.")
    if not llm.get("model"):
        raise RuntimeError("Nincs kiválasztott nyelvi modell a Beállításokban.")
    local = llm["provider"] == "local"
    if local:
        application._character_analysis_reserve()
        application.tts.unload()
    try:
        with application._character_analysis_lock:
            if local and not application.tts.wait_until_unloaded(timeout=600):
                raise RuntimeError("A beszédmotor nem szabadította fel a videomemóriát.")
            return work(llm, config)
    finally:
        if local:
            application._character_analysis_release()


def run_voice_suggestions(job_id: str, book_id: int) -> None:
    application = _app()
    job = application._legacy_job(jobs.get_job(job_id))
    try:
        job.update(state="running", message="Szereplők adatainak összegyűjtése…", total=1)
        application._persist_job(job)
        with get_conn() as conn:
            book = conn.execute("SELECT title FROM books WHERE id=?", (book_id,)).fetchone()
        characters = _character_context(book_id)
        if not characters:
            raise RuntimeError("A könyvhöz még nincs szereplő.")
        job["message"] = "Hangjavaslatok kérése a nyelvi modelltől…"
        application._persist_job(job)
        suggestions = _with_llm(job, lambda llm, config: llm_review.suggest_voices(
            title=book["title"], characters=characters, llm=llm,
            timeout=float(config.get("llm_timeout_sec", 600)),
            max_tokens=int(config.get("llm_max_output_tokens", 8192)),
        ))
        job.update(state="complete", done=1, message=f"{len(suggestions)} hangjavaslat készült.",
                   result={"book_id": book_id, "suggestions": suggestions})
        application._persist_job(job)
    except application.JobCancelled:
        jobs.mark_cancelled(job_id, "Leállítva")
    except Exception as exc:
        log.exception("Voice suggestions failed for book %s", book_id)
        job.update(state="failed", error=str(exc), message="A hangjavaslat nem sikerült")
        application._persist_job(job)
    finally:
        application._close_orphaned_job(job_id)


def _apply_corrections(application, book_id: int, chapter_id: int, corrections: list[dict]) -> int:
    anchors = application._capture_position_anchors(book_id, chapter_id)
    changed = 0
    with get_conn() as conn:
        chapter = conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()
        units = {int(u["index"]): u for u in build_speaker_units(chapter["content"])}
        for item in corrections:
            index = item["unit_index"]
            existing = conn.execute(
                "SELECT source FROM speaker_annotations WHERE chapter_id=? AND unit_index=?",
                (chapter_id, index)).fetchone()
            if existing and existing["source"] == "manual":
                continue  # human choices always win
            if not item["speaker"]:
                changed += conn.execute(
                    "DELETE FROM speaker_annotations WHERE chapter_id=? AND unit_index=? "
                    "AND COALESCE(source,'automatic') <> 'manual'", (chapter_id, index)).rowcount
                continue
            conn.execute(
                "INSERT INTO speaker_annotations (book_id,chapter_id,unit_index,unit_text,speaker_name,"
                "confidence,source) VALUES (?,?,?,?,?,?,'llm-review') "
                "ON CONFLICT(chapter_id, unit_index) DO UPDATE SET speaker_name=excluded.speaker_name, "
                "source='llm-review', confidence=excluded.confidence",
                (book_id, chapter_id, index, units.get(index, {}).get("text", ""), item["speaker"], 0.9))
            changed += 1
        if changed:
            conn.execute("DELETE FROM tts_segments WHERE book_id=? AND chapter_id=?", (book_id, chapter_id))
    if changed:
        rebuilt = application._compute_segments_for_chapter(book_id, chapter_id)
        if rebuilt:
            application._store_segments(book_id, chapter_id, rebuilt)
        index = application._best_segment_index(rebuilt, anchors["progress_text"])
        if index is not None:
            with get_conn() as conn:
                conn.execute("UPDATE reading_progress SET position=? WHERE book_id=? AND chapter_id=?",
                             (index, book_id, chapter_id))
    return changed


def run_speaker_review(job_id: str, book_id: int, chapter_ids: list[int]) -> None:
    application = _app()
    job = application._legacy_job(jobs.get_job(job_id))
    try:
        job.update(state="running", total=len(chapter_ids), done=0, message="Beszélők ellenőrzése…")
        application._persist_job(job)
        with get_conn() as conn:
            book = conn.execute("SELECT title FROM books WHERE id=?", (book_id,)).fetchone()
            roster = [r[0] for r in conn.execute(
                "SELECT name FROM characters WHERE book_id=? ORDER BY frequency DESC", (book_id,))]
            rows = conn.execute(
                "SELECT id, title, content FROM chapters WHERE book_id=? AND id IN (%s) ORDER BY order_num"
                % ",".join("?" * len(chapter_ids)), (book_id, *chapter_ids)).fetchall()

        def work(llm, config):
            summary = []
            for row in rows:
                application._check_job_cancelled(job)
                job["message"] = f"Ellenőrzés: {row['title']}"
                application._persist_job(job)
                units = build_speaker_units(row["content"])
                with get_conn() as conn:
                    speakers = {r["unit_index"]: r["speaker_name"] for r in conn.execute(
                        "SELECT unit_index, speaker_name FROM speaker_annotations WHERE chapter_id=?",
                        (row["id"],))}
                for unit in units:
                    unit["speaker"] = speakers.get(int(unit["index"]), "")
                corrections = []
                # Keep prompts small enough for local models: ~120 units per call.
                for start in range(0, len(units), 120):
                    corrections += llm_review.review_speakers(
                        title=book["title"], units=units[start:start + 120], roster=roster, llm=llm,
                        timeout=float(config.get("llm_timeout_sec", 600)),
                        max_tokens=int(config.get("llm_max_output_tokens", 8192)),
                    )
                changed = _apply_corrections(application, book_id, row["id"], corrections)
                summary.append({"chapter_id": row["id"], "title": row["title"], "changed": changed,
                                "corrections": corrections[:50]})
                job["done"] += 1
                application._persist_job(job)
            return summary

        summary = _with_llm(job, work)
        total = sum(item["changed"] for item in summary)
        job.update(state="complete", message=f"{total} beszélő-hozzárendelés javítva.",
                   result={"book_id": book_id, "chapters": summary, "changed": total})
        application._persist_job(job)
    except application.JobCancelled:
        jobs.mark_cancelled(job_id, "Leállítva")
    except Exception as exc:
        log.exception("Speaker review failed for book %s", book_id)
        job.update(state="failed", error=str(exc), message="A beszélők ellenőrzése nem sikerült")
        application._persist_job(job)
    finally:
        application._close_orphaned_job(job_id)


def _start(job_type: str, payload: dict, book_id: int):
    application = _app()
    with application._work_dispatch_lock:
        conflict = application._work_conflict_response()
        if conflict is not None:
            return conflict
        stored = jobs.create_job(job_type, payload, book_id=book_id)
        application._launch_durable_job_unlocked(stored)
    return jsonify(job_id=stored["id"])


@bp.route("/api/books/<int:book_id>/voice-suggestions", methods=["POST"])
def start_voice_suggestions(book_id):
    return _start("voice_suggestions", {"book_id": book_id}, book_id)


@bp.route("/api/books/<int:book_id>/speaker-review", methods=["POST"])
def start_speaker_review(book_id):
    body = request.get_json(silent=True) or {}
    with get_conn() as conn:
        all_ids = [r[0] for r in conn.execute(
            "SELECT id FROM chapters WHERE book_id=? ORDER BY order_num", (book_id,))]
    wanted = [int(c) for c in body.get("chapter_ids") or all_ids if int(c) in set(all_ids)]
    if not wanted:
        return jsonify(error="Nincs ellenőrizhető fejezet."), 400
    return _start("speaker_review", {"book_id": book_id, "chapter_ids": wanted}, book_id)


@bp.route("/api/jobs/<job_id>")
def job_detail(job_id):
    stored = jobs.get_job(job_id)
    if not stored:
        return jsonify(error="Ismeretlen feladat"), 404
    return jsonify(stored)


# ── Pronunciation assistance ────────────────────────────────────────────────

_FOREIGN_HINT_RE = re.compile(r"[wxyq]|th|ch|sh|ph|ck|ou|ee|oo|ae", re.IGNORECASE)
_CAPITAL_WORD_RE = re.compile(r"(?<![.!?…„\"–—]\s)(?<!^)\b([A-ZÁÉÍÓÖŐÚÜŰ][\wÁÉÍÓÖŐÚÜŰáéíóöőúüű'’-]{2,})")


@bp.route("/api/books/<int:book_id>/pronunciation-candidates")
def pronunciation_candidates(book_id):
    """Names and foreign-looking words worth a pronunciation check."""
    from collections import Counter

    from core import characters, experience

    with get_conn() as conn:
        contents = [r[0] for r in conn.execute(
            "SELECT content FROM chapters WHERE book_id=? ORDER BY order_num", (book_id,))]
    text = "\n\n".join(contents)[:400_000]
    ruled = {rule["source"].lower() for rule in experience.list_rules(book_id)}
    counts: Counter = Counter()
    kinds: dict[str, str] = {}
    nlp = characters._get_hungarian_nlp()
    if nlp is not None:
        for start in range(0, len(text), 100_000):
            for ent in nlp(text[start:start + 100_000]).ents:
                if ent.label_ in ("PER", "LOC", "ORG", "MISC") and 2 < len(ent.text) < 40:
                    word = ent.text.strip(" .,;:!?–—\"„”»«")
                    counts[word] += 1
                    kinds.setdefault(word, ent.label_)
    else:
        for match in _CAPITAL_WORD_RE.finditer(text):
            counts[match.group(1)] += 1
            kinds.setdefault(match.group(1), "NAME")
    items = []
    for word, count in counts.most_common(400):
        if word.lower() in ruled:
            continue
        foreign = bool(_FOREIGN_HINT_RE.search(word))
        items.append({"word": word, "count": count, "kind": kinds.get(word, ""), "foreign": foreign})
    items.sort(key=lambda item: (not item["foreign"], -item["count"]))
    return jsonify(candidates=items[:120])


_PHONEMIZER = None


@bp.route("/api/pronunciation/ipa")
def pronunciation_ipa():
    """IPA transcription via the espeak-ng data bundled with piper-tts."""
    global _PHONEMIZER
    import unicodedata

    text = str(request.args.get("text") or "")[:500]
    language = str(request.args.get("language") or "hu")[:5]
    try:
        from piper.phonemize_espeak import EspeakPhonemizer

        if _PHONEMIZER is None:
            _PHONEMIZER = EspeakPhonemizer()
        sentences = _PHONEMIZER.phonemize("hu" if language.startswith("hu") else language, text)
        ipa = " ".join(unicodedata.normalize("NFC", "".join(s)) for s in sentences).strip()
        return jsonify(ipa=ipa)
    except Exception as exc:
        log.info("IPA preview unavailable: %s", exc)
        return jsonify(ipa=None, error="Az IPA-átírás a Piper telepítésével érhető el.")


@bp.route("/api/pronunciation/listen", methods=["POST"])
def pronunciation_listen():
    """Speak a sentence with a candidate rule applied, in the narrator's voice."""
    from core import experience

    application = _app()
    data = request.get_json(silent=True) or {}
    book_id = int(data.get("book_id") or 0) or None
    text = str(data.get("text") or "").strip()[:600]
    source = str(data.get("source") or "").strip()
    replacement = str(data.get("replacement") or "").strip()
    if not text:
        return jsonify(error="Adj meg egy mondatot."), 400
    if application.tts.status().get("state") != "ready":
        return jsonify(error="A beszédmotor még nem áll készen."), 503
    rules = experience.list_rules(book_id)
    if source and replacement:
        rules = [r for r in rules if r["source"] != source] + [{"source": source, "replacement": replacement}]
    spoken = experience.apply_pronunciation(text, book_id, rules=rules)
    book = application._load_book(book_id) if book_id else None
    ref_audio, ref_text = application._book_narrator_reference(book_id) if book_id else (None, None)
    instruct = (book["narrator_instruct"] if book else None) or application._default_narrator_instruct()
    result = application.tts.generate(
        text=spoken, instruct=instruct, ref_audio=ref_audio, ref_text=ref_text, speed=1.0,
        language=(book["language"] if book else None) or "hu",
    )
    return jsonify(spoken=spoken, audio_url=f"/api/audio/{result['cache_key']}")
