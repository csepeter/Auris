"""Sound effects attached to single sentences (children's books, radio plays).

One effect per spoken unit, keyed like takes by ``qa_api.segment_hash`` so it
survives segment-row rebuilds while the sentence stays the same. At export the
effect is mixed into the already merged WAV in place: nothing is inserted, so
subtitle, EPUB Media Overlay and word timings stay exact.

Positions:
- ``with``: starts together with the sentence, under the voice;
- ``before``: ends where the sentence starts, i.e. plays in the pause before it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import numpy as np
import soundfile as sf

from core.database import get_conn

SAMPLE_RATE = 24000
MAX_SFX_SEC = 30.0
POSITIONS = ("with", "before")
MANAGED_PREFIX = "sfx_"
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a"}

_ready: set[str] = set()


def ensure_table() -> None:
    from core.database import get_db_path

    path = get_db_path()
    if path in _ready and os.path.exists(path):
        return
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS segment_sfx (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id    INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                text_hash  TEXT NOT NULL,
                path       TEXT NOT NULL,
                name       TEXT,
                gain_db    REAL NOT NULL DEFAULT -8,
                position   TEXT NOT NULL DEFAULT 'with',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(book_id, chapter_id, text_hash)
            )
            """
        )
    _ready.add(path)


def convert_upload(src: str, name: str, folder: str) -> str:
    """Store an uploaded effect as mono 24 kHz WAV (max 30 s) in ``folder``."""
    if not shutil.which("ffmpeg"):
        raise ValueError("A hangeffekthez ffmpeg szükséges.")
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, f"{MANAGED_PREFIX}{uuid.uuid4().hex}.wav")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-t", str(MAX_SFX_SEC),
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", dst]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode or not os.path.exists(dst) or sf.info(dst).frames == 0:
        _remove(dst)
        raise ValueError(f"A(z) {name} nem olvasható hangfájlként.")
    return dst


def set_effect(book_id: int, chapter_id: int, text_hash: str, path: str, name: str,
               gain_db: float = -8.0, position: str = "with") -> dict:
    ensure_table()
    if position not in POSITIONS:
        raise ValueError("Ismeretlen hangeffekt-pozíció.")
    gain_db = max(-40.0, min(6.0, float(gain_db)))
    with get_conn() as conn:
        old = conn.execute(
            "SELECT path FROM segment_sfx WHERE book_id=? AND chapter_id=? AND text_hash=?",
            (book_id, chapter_id, text_hash)).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO segment_sfx (book_id,chapter_id,text_hash,path,name,gain_db,position) "
            "VALUES (?,?,?,?,?,?,?)", (book_id, chapter_id, text_hash, path, name, gain_db, position))
    if old and old["path"] != path:
        _remove_managed(old["path"])
    return {"name": name, "gain_db": gain_db, "position": position}


def remove_effect(book_id: int, chapter_id: int, text_hash: str) -> bool:
    ensure_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT path FROM segment_sfx WHERE book_id=? AND chapter_id=? AND text_hash=?",
            (book_id, chapter_id, text_hash)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM segment_sfx WHERE book_id=? AND chapter_id=? AND text_hash=?",
                     (book_id, chapter_id, text_hash))
    _remove_managed(row["path"])
    return True


def effects_for_chapter(book_id: int, chapter_id: int) -> dict[str, dict]:
    ensure_table()
    with get_conn() as conn:
        return {r["text_hash"]: dict(r) for r in conn.execute(
            "SELECT * FROM segment_sfx WHERE book_id=? AND chapter_id=?", (book_id, chapter_id))}


def attach(book_id: int, chapter_id: int, segments: list[dict]) -> list[dict]:
    """Add ``seg['sfx']`` to segments that carry an effect; returns the list."""
    from core.qa_api import segment_hash

    effects = effects_for_chapter(book_id, chapter_id)
    if effects:
        for seg in segments:
            effect = effects.get(segment_hash(seg))
            if effect and os.path.exists(effect["path"]):
                seg["sfx"] = {"path": effect["path"], "gain_db": effect["gain_db"],
                              "position": effect["position"]}
    return segments


def cue(seg: dict, start_frame: int) -> tuple[int, str, float, str] | None:
    effect = seg.get("sfx")
    if not effect:
        return None
    return (start_frame, effect["path"], float(effect.get("gain_db", -8.0)),
            effect.get("position", "with"))


def overlay(wav_path: str, cues: list) -> int:
    """Mix effects into ``wav_path`` in place; returns how many were applied."""
    applied = 0
    cues = [c for c in cues if c]
    if not cues:
        return 0
    with sf.SoundFile(wav_path, "r+") as out:
        if out.samplerate != SAMPLE_RATE:
            return 0
        total = out.frames
        for start, path, gain_db, position in cues:
            try:
                effect, sr = sf.read(path, dtype="float32", always_2d=True)
            except Exception:
                continue
            effect = effect.mean(axis=1)
            if sr != SAMPLE_RATE:
                from core.local_engines import resample

                effect = resample(effect, sr, SAMPLE_RATE)
            effect = effect * (10.0 ** (gain_db / 20.0))
            begin = start - len(effect) if position == "before" else start
            if begin < 0:
                effect = effect[-begin:]
                begin = 0
            length = min(len(effect), total - begin)
            if length <= 0:
                continue
            out.seek(begin)
            region = out.read(length, dtype="float32", always_2d=True)[:, 0]
            mixed = np.clip(region + effect[:len(region)], -1.0, 1.0)
            out.seek(begin)
            out.write(mixed)
            applied += 1
    return applied


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_managed(path: str | None) -> None:
    """Delete an effect file only when Auris created it (sfx/sfx_<hex>.wav)."""
    if not path:
        return
    folder, name = os.path.split(os.path.abspath(path))
    if os.path.basename(folder) == "sfx" and name.startswith(MANAGED_PREFIX) and name.endswith(".wav"):
        _remove(path)
