"""Clean uploaded voice-cloning references before they are stored.

Cloning copies everything in the reference: hum, room noise, long silences
and level. A light, speech-safe chain improves every engine that clones:

- 70 Hz fourth-order high-pass and 50/60 Hz notches (rumble, mains hum);
- FFT denoise (``afftdn``) tuned for steady background noise;
- leading/trailing silence trimmed to ~0.15 s;
- loudness normalised to about -20 LUFS with -2 dBTP headroom;
- mono, 24 kHz, 16-bit PCM.

Any ffmpeg failure keeps the original file: cleaning is an improvement,
never a reason to lose an upload.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_EDGE = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15"
CLEAN_FILTER = ",".join([
    "highpass=f=70,highpass=f=70",  # 4th order: rumble and mains hum
    "bandreject=f=50:width_type=q:w=4,bandreject=f=60:width_type=q:w=4",
    "afftdn=nf=-25:nt=w",
    _EDGE, "areverse", _EDGE, "areverse",
    "loudnorm=I=-20:TP=-2:LRA=11",
])
MIN_CLEAN_SEC = 1.0


def clean_reference(src: str, dst: str) -> dict:
    """Write a cleaned copy of ``src`` to ``dst``.

    Returns ``{'cleaned': bool, 'duration_sec': float, 'message': str}``;
    when ``cleaned`` is false, ``dst`` was not written.
    """
    if not shutil.which("ffmpeg"):
        return {"cleaned": False, "duration_sec": 0.0, "message": "Az ffmpeg nem érhető el; a tisztítás kimaradt."}
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-af", CLEAN_FILTER, "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", dst]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        info = sf.info(dst)
        duration = float(info.frames) / float(info.samplerate or 1)
    except Exception as exc:  # keep the original upload on any failure
        log.warning("Reference cleaning failed for %s: %s", src, exc)
        _remove(dst)
        return {"cleaned": False, "duration_sec": 0.0, "message": "A tisztítás nem sikerült; az eredeti felvétel maradt."}
    if duration < MIN_CLEAN_SEC:
        _remove(dst)
        return {"cleaned": False, "duration_sec": duration,
                "message": "A tisztítás után túl rövid maradt a felvétel; az eredeti maradt."}
    return {"cleaned": True, "duration_sec": duration, "message": ""}


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
