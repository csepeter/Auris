"""OpenAI-compatible speech endpoint for other tools on the same machine.

``POST /v1/audio/speech`` accepts the OpenAI request body
(``model``, ``input``, ``voice``, ``response_format``, ``speed``) and answers
with audio bytes, so readers such as OpenReader or Pandrator can speak with
Auris' engines and saved Hungarian voices. ``voice`` is the name (or id) of a
saved Auris voice profile, a voice description ("female, middle-aged"), or a
preset voice of the active engine ("anna", "F1"). ``model`` is informational;
the active Auris engine is used.

Only reachable through the usual Host check (loopback by default).
Optional bearer token: set ``api_token`` in settings.
"""

from __future__ import annotations

import hmac
import os
import subprocess

from flask import Blueprint, Response, jsonify, request

from core.database import get_conn

bp = Blueprint("openai_compat", __name__)

_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "flac": "audio/flac",
         "aac": "audio/aac", "pcm": "audio/L16"}
_FFMPEG_ARGS = {
    "mp3": ["-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"],
    "opus": ["-codec:a", "libopus", "-b:a", "48k", "-f", "ogg"],
    "flac": ["-codec:a", "flac", "-f", "flac"],
    "aac": ["-codec:a", "aac", "-b:a", "128k", "-f", "adts"],
    "pcm": ["-f", "s16le", "-acodec", "pcm_s16le"],
}


def _error(message: str, status: int, kind: str = "invalid_request_error"):
    return jsonify(error={"message": message, "type": kind}), status


def _authorized() -> bool:
    from core import settings

    token = str(settings.get("api_token", "") or "")
    if not token:
        return True
    header = request.headers.get("Authorization", "")
    return header.startswith("Bearer ") and hmac.compare_digest(header[7:], token)


def _resolve_voice(voice: str) -> dict:
    voice = str(voice or "").strip()
    with get_conn() as conn:
        row = None
        if voice.isdigit():
            row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (int(voice),)).fetchone()
        if row is None and voice:
            row = conn.execute(
                "SELECT * FROM voice_profiles WHERE name=? COLLATE NOCASE", (voice,)).fetchone()
    if row is not None:
        ref = row["ref_audio_path"] if row["ref_audio_path"] and os.path.exists(row["ref_audio_path"]) else None
        return {"instruct": row["instruct"], "ref_audio": ref, "ref_text": row["ref_text"] if ref else None}
    if "," in voice or voice.lower().split(" ")[0] in ("male", "female"):
        return {"instruct": voice, "ref_audio": None, "ref_text": None}
    if voice:
        # Preset voice name for Piper/Supertonic (ignored by other engines).
        return {"instruct": f"voice:{voice}", "ref_audio": None, "ref_text": None}
    import app as application

    return {"instruct": application._default_narrator_instruct(), "ref_audio": None, "ref_text": None}


def _encode(wav_path: str, fmt: str) -> bytes:
    if fmt == "wav":
        with open(wav_path, "rb") as handle:
            return handle.read()
    command = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", wav_path,
               *_FFMPEG_ARGS[fmt], "pipe:1"]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or b"").decode("utf-8", "replace")[-300:])
    return result.stdout


@bp.route("/v1/audio/speech", methods=["POST"])
def speech():
    import app as application

    if not _authorized():
        return _error("Invalid API token.", 401, "authentication_error")
    body = request.get_json(silent=True) or {}
    text = str(body.get("input") or "").strip()
    if not text:
        return _error("'input' is required.", 400)
    if len(text) > 4096:
        return _error("'input' must be at most 4096 characters.", 400)
    fmt = str(body.get("response_format") or "mp3").lower()
    if fmt not in _MIME:
        return _error(f"Unsupported response_format: {fmt}", 400)
    try:
        speed = max(0.25, min(4.0, float(body.get("speed") or 1.0)))
    except (TypeError, ValueError):
        return _error("'speed' must be a number.", 400)
    if application._export_exclusive_active():
        return _error("Auris is exporting; try again later.", 503, "server_error")
    status = application.tts.status()
    if status.get("state") != "ready":
        application.tts.load_async()
        return _error("The Auris speech engine is loading; retry shortly.", 503, "server_error")
    voice = _resolve_voice(body.get("voice"))
    language = str(body.get("language") or "hu")[:5]
    from core.parser.language import detect_language

    if not body.get("language"):
        language = detect_language(text, default="hu")
    result = application.tts.generate(
        text=text, instruct=voice["instruct"], ref_audio=voice["ref_audio"],
        ref_text=voice["ref_text"], speed=speed, language=language,
    )
    try:
        payload = _encode(result["audio_path"], fmt)
    except Exception as exc:
        return _error(f"Encoding failed: {exc}", 500, "server_error")
    return Response(payload, mimetype=_MIME[fmt])


@bp.route("/v1/models")
def models():
    from core.local_engines import ENGINE_INFO
    from core.tts_router import ENGINE_NAMES

    return jsonify(object="list", data=[
        {"id": name, "object": "model", "owned_by": "auris",
         "description": ENGINE_INFO.get(name, {}).get("label", name)}
        for name in ENGINE_NAMES
    ])


@bp.route("/v1/audio/voices")
def voices():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, instruct, ref_audio_path FROM voice_profiles ORDER BY name").fetchall()
    return jsonify(voices=[
        {"id": str(r["id"]), "name": r["name"], "clone": bool(r["ref_audio_path"]),
         "description": r["instruct"]} for r in rows
    ])
