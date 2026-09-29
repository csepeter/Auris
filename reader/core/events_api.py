"""Server-sent events: one stream for engine state and job progress.

Pages used to poll several endpoints every 0.5–5 s. ``GET /api/events`` keeps
one connection open and pushes only changes: ``engine`` (TTS state) and
``jobs`` (active jobs plus jobs that finished since the last message).
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, request, stream_with_context

from core import jobs

bp = Blueprint("events", __name__)

POLL_SEC = 1.0
KEEPALIVE_SEC = 15.0
MAX_STREAM_SEC = 1800.0  # clients reconnect automatically (EventSource)


def _engine_state(application) -> dict:
    try:
        status = application.tts.status()
    except Exception as exc:  # the stream must never die on a status error
        return {"state": "error", "message": str(exc)}
    return {
        "state": status.get("state"), "engine": status.get("engine"),
        "message": status.get("message", ""), "model_exists": status.get("model_exists"),
        "generating": bool(status.get("generating")),
    }


def _job_view(job: dict) -> dict:
    return {key: job.get(key) for key in (
        "id", "type", "state", "message", "done", "total", "error", "book_id", "chapter_id",
        "book_title", "chapter_title", "updated_at")}


def _event(name: str, payload) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@bp.route("/api/events")
def events():
    import app as application

    book_id = request.args.get("book_id", type=int)

    @stream_with_context
    def stream():
        started = time.monotonic()
        last_engine = None
        last_jobs: dict[str, dict] = {}
        last_sent = 0.0
        yield "retry: 3000\n\n"
        while time.monotonic() - started < MAX_STREAM_SEC:
            engine = _engine_state(application)
            if engine != last_engine:
                last_engine = engine
                last_sent = time.monotonic()
                yield _event("engine", engine)
            try:
                jobs.ensure_jobs()
                active = {j["id"]: _job_view(j) for j in jobs.list_active_jobs(book_id=book_id)}
            except Exception:
                active = {}
            finished = []
            for job_id in set(last_jobs) - set(active):
                stored = jobs.get_job(job_id)
                if stored:
                    finished.append(_job_view(stored))
            changed = [view for job_id, view in active.items() if last_jobs.get(job_id) != view]
            if changed or finished:
                last_sent = time.monotonic()
                yield _event("jobs", {"active": list(active.values()), "changed": changed,
                                      "finished": finished})
            last_jobs = active
            if time.monotonic() - last_sent > KEEPALIVE_SEC:
                last_sent = time.monotonic()
                yield ": keepalive\n\n"
            time.sleep(POLL_SEC)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
