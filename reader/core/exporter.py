"""
Audio + subtitle exporter.

Modes:
  - chapter_folder : numbered, per-chapter files in a book folder
  - chapter_zip    : zip of per-chapter audio + subtitle files
  - single         : one chapter audio + subtitle

Audio formats:
  - mp3  (via pydub + ffmpeg if available)
  - wav  (always available, soundfile)

Subtitle formats:
  - ass  (Advanced SubStation Alpha — per-character colours/styles)
  - srt  (plain SubRip — universal)
  - none (audio only)
"""

import io
import json
import os
import re
import subprocess
import zipfile
import logging
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
EXPORTS_DIR = str(Path(__file__).resolve().parent.parent / 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)

DEFAULT_SEGMENT_PAUSE_SEC = 0.35
DIALOGUE_TURN_PAUSE_SEC = 0.55
PARAGRAPH_PAUSE_SEC = 0.85
ELLIPSIS_PAUSE_SEC = 1.5
# Silence between chapters of a single-file audiobook (M4B).
CHAPTER_GAP_SEC = 2.0
MASTERING_TARGET_I = -19.0
MASTERING_TARGET_LRA = 9.0
MASTERING_TARGET_TP = -3.0

# Conservative audiobook polish before program-level loudness matching.
# Compression narrows voice-to-voice level differences without normalizing
# every sentence independently (which would create audible pumping).
_MASTERING_PRE_FILTERS = (
    'highpass=f=55,'
    'equalizer=f=180:t=q:w=1:g=-1,'
    'equalizer=f=3500:t=q:w=1:g=1,'
    'acompressor=threshold=0.125:ratio=2.5:attack=20:release=250:'
    'makeup=1.6:knee=2.8:detection=rms:link=average'
)


# ── ffmpeg / pydub detection ──────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


def _wav_to_mp3_bytes(
    wav_path: str,
    tags: dict[str, str] | None = None,
) -> bytes | None:
    if not _ffmpeg_available():
        return None
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_wav(wav_path)
        buf = io.BytesIO()
        clean_tags = {
            str(key): str(value)
            for key, value in (tags or {}).items()
            if str(value).strip()
        }
        seg.export(
            buf,
            format='mp3',
            bitrate='192k',
            tags=clean_tags or None,
        )
        return buf.getvalue()
    except Exception as e:
        log.warning(f'MP3 conversion failed: {e}')
        return None


def _extract_loudnorm_measurements(stderr: str) -> dict:
    matches = re.findall(
        r'\{\s*"input_i".*?\}',
        str(stderr or ''),
        flags=re.DOTALL,
    )
    if not matches:
        raise ValueError('FFmpeg did not return loudness measurements.')
    measurements = json.loads(matches[-1])
    required = (
        'input_i', 'input_lra', 'input_tp', 'input_thresh', 'target_offset',
    )
    for key in required:
        value = str(measurements.get(key, '')).strip().lower()
        if not value or value in {'-inf', 'inf', 'nan'}:
            raise ValueError(f'Invalid FFmpeg loudness measurement: {key}')
    return measurements


def _master_wav(input_path: str, output_path: str, *, runner=None) -> tuple[bool, str | None]:
    """Apply gentle studio polish and two-pass EBU R128 loudness matching."""
    if not _ffmpeg_available():
        return False, 'FFmpeg is unavailable; studio mastering was skipped.'

    loudnorm_base = (
        f'loudnorm=I={MASTERING_TARGET_I}:LRA={MASTERING_TARGET_LRA}:'
        f'TP={MASTERING_TARGET_TP}'
    )
    first_filter = (
        f'{_MASTERING_PRE_FILTERS},{loudnorm_base}:print_format=json'
    )
    run = runner or subprocess.run
    first = run(
        [
            'ffmpeg', '-hide_banner', '-nostats', '-y',
            '-i', input_path,
            '-af', first_filter,
            '-f', 'null', os.devnull,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if first.returncode != 0:
        return False, (
            'FFmpeg mastering analysis failed: '
            + (first.stderr.strip().splitlines()[-1] if first.stderr else 'unknown error')
        )

    try:
        measured = _extract_loudnorm_measurements(first.stderr)
    except (ValueError, json.JSONDecodeError) as exc:
        return False, str(exc)

    second_filter = (
        f'{_MASTERING_PRE_FILTERS},{loudnorm_base}:'
        f'measured_I={measured["input_i"]}:'
        f'measured_LRA={measured["input_lra"]}:'
        f'measured_TP={measured["input_tp"]}:'
        f'measured_thresh={measured["input_thresh"]}:'
        f'offset={measured["target_offset"]}:'
        'linear=true:print_format=summary'
    )
    second = run(
        [
            'ffmpeg', '-hide_banner', '-nostats', '-y',
            '-i', input_path,
            '-af', second_filter,
            '-ar', str(SAMPLE_RATE),
            '-ac', '1',
            '-c:a', 'pcm_s16le',
            '-rf64', 'auto',
            output_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if second.returncode != 0 or not os.path.isfile(output_path):
        return False, (
            'FFmpeg mastering pass failed: '
            + (second.stderr.strip().splitlines()[-1] if second.stderr else 'unknown error')
        )
    return True, None


# ── Time formatting ───────────────────────────────────────────────────────────

def _fmt_ass(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h}:{m:02d}:{s:05.2f}'


def _fmt_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


# ── ASS subtitle builder ──────────────────────────────────────────────────────

_ASS_HEADER = """\
[Script Info]
Title: {title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narrator,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1
{char_styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}"""

_ASS_CHAR_STYLE = (
    'Style: {name},Arial,28,&H00{color},&H000000FF,&H00000000,'
    '&H80000000,0,-1,0,0,100,100,0,0,1,2,1,2,10,10,30,1'
)


def _hex_to_ass(hex_color: str) -> str:
    """Convert #RRGGBB → AABBGGRR (ASS BGR order, alpha=00)."""
    h = hex_color.lstrip('#')
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f'{b}{g}{r}'.upper()


def build_ass(segments: list[dict], character_colors: dict, title: str) -> str:
    char_styles = []
    seen = set()
    for name, color in character_colors.items():
        safe = name.replace(' ', '_')
        if safe in seen:
            continue
        seen.add(safe)
        char_styles.append(_ASS_CHAR_STYLE.format(
            name=safe, color=_hex_to_ass(color)
        ))

    events = []
    for seg in segments:
        start = _fmt_ass(seg['t_start'])
        end = _fmt_ass(seg['t_end'])
        char = seg.get('character_name') or 'Narrator'
        style = char.replace(' ', '_') if char in character_colors else 'Narrator'
        text = seg['text'].replace('\n', '\\N')
        if seg.get('is_dialogue'):
            text = '{\\i1}' + text + '{\\i0}'
        events.append(
            f'Dialogue: 0,{start},{end},{style},{char},0000,0000,0000,,{text}'
        )

    return _ASS_HEADER.format(
        title=title,
        char_styles='\n'.join(char_styles),
        events='\n'.join(events),
    )


def build_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _fmt_srt(seg['t_start'])
        end = _fmt_srt(seg['t_end'])
        lines.append(f'{i}\n{start} --> {end}\n{seg["text"]}\n')
    return '\n'.join(lines)


# ── Audio merge ───────────────────────────────────────────────────────────────

def pause_after_segment(segment: dict, next_segment: dict | None = None) -> float:
    """Return the spoken-program pause after a segment.

    Ellipses represent an intentional trailing-off pause. Consecutive dialogue
    segments get a slightly longer beat so a new voice does not cut in
    unnaturally fast. Paragraph endings keep the author's larger structural
    pauses in both live playback and exported audio.
    """
    if segment.get('pause_ms') is not None:
        return max(0, min(5000, float(segment['pause_ms']))) / 1000
    if segment.get('block_kind') in ('heading', 'subheading') and segment.get('ends_paragraph'):
        return 1.2
    text = str(segment.get('text') or '').rstrip()
    text = text.rstrip('"\'”’»').rstrip()
    if text.endswith(('...', '…')):
        return ELLIPSIS_PAUSE_SEC
    if segment.get('ends_paragraph'):
        return PARAGRAPH_PAUSE_SEC
    if (
        next_segment
        and segment.get('is_dialogue')
        and next_segment.get('is_dialogue')
    ):
        return DIALOGUE_TURN_PAUSE_SEC
    return DEFAULT_SEGMENT_PAUSE_SEC


TRIM_THRESHOLD_DB = -50.0
TRIM_KEEP_SEC = 0.04
ROOM_TONE_DB = -68.0
ROOM_TONE_HEAD_SEC = 0.6
ROOM_TONE_TAIL_SEC = 2.0


def _export_option(key: str, default: bool) -> bool:
    try:
        from core import settings

        return bool(settings.get(key, default))
    except Exception:
        return default


def trim_edges(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Remove the model's own leading/trailing silence, keeping a short margin.

    Pauses between sentences then come only from ``pause_after_segment``,
    so they are the same length whichever engine rendered the sentence.
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio
    frame = max(1, int(sample_rate * 0.01))
    usable = audio.size // frame * frame
    if usable == 0:
        return audio
    rms = np.sqrt(np.mean(audio[:usable].reshape(-1, frame) ** 2, axis=1))
    loud = np.flatnonzero(rms > 10 ** (TRIM_THRESHOLD_DB / 20))
    if loud.size == 0:
        return audio
    keep = int(sample_rate * TRIM_KEEP_SEC)
    start = max(0, loud[0] * frame - keep)
    end = min(audio.size, (loud[-1] + 1) * frame + keep)
    return audio[start:end]


def silence(frames: int, room_tone: bool = False, seed: int = 0) -> np.ndarray:
    """Digital silence, or very quiet room tone (ACX rejects dead silence)."""
    frames = max(0, int(frames))
    if not room_tone or frames == 0:
        return np.zeros(frames, dtype=np.float32)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(frames).astype(np.float32)
    # Gentle low-pass so the tone sounds like a quiet room, not hiss.
    kernel = np.ones(8, dtype=np.float32) / 8
    noise = np.convolve(noise, kernel, mode='same')
    noise /= max(float(np.sqrt(np.mean(noise ** 2))), 1e-9)
    return (noise * 10 ** (ROOM_TONE_DB / 20)).astype(np.float32)


def read_segment_audio(path: str, *, trim: bool) -> np.ndarray:
    audio, sr = sf.read(path, dtype='float32', always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError('Érvénytelen mondathang vagy mintavételi frekvencia.')
    return trim_edges(audio) if trim else audio


def _export_workers() -> int:
    """Parallel FFmpeg chapter jobs; each already uses several threads."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _write_merged_wav(segments: list[dict], path: str) -> float:
    """Stream segment audio and pauses into one 16-bit WAV; return seconds.

    Segments get their actual ``t_start``/``t_end`` written back, so subtitles
    follow the trimmed audio exactly.
    """
    trim = _export_option('trim_segment_silence', True)
    room_tone = _export_option('export_room_tone', False)
    playable = [
        seg
        for seg in segments
        if seg.get('audio_path') and os.path.exists(seg['audio_path'])
    ]
    frames = 0
    with sf.SoundFile(path, 'w', samplerate=SAMPLE_RATE, channels=1,
                      format='RF64', subtype='PCM_16') as out:
        if not playable:
            out.write(np.zeros(SAMPLE_RATE, dtype='float32'))
            return 1.0
        if room_tone:
            head = silence(int(SAMPLE_RATE * ROOM_TONE_HEAD_SEC), True, 1)
            out.write(head)
            frames += len(head)
        for idx, seg in enumerate(playable):
            audio = read_segment_audio(seg['audio_path'], trim=trim)
            seg['t_start'] = frames / SAMPLE_RATE
            out.write(audio)
            frames += len(audio)
            seg['t_end'] = frames / SAMPLE_RATE
            if idx + 1 < len(playable) or seg.get('pause_ms') is not None:
                pause = pause_after_segment(
                    seg, playable[idx + 1] if idx + 1 < len(playable) else None
                )
                gap = silence(int(SAMPLE_RATE * pause), room_tone, idx + 2)
                out.write(gap)
                frames += len(gap)
        if room_tone:
            tail = silence(int(SAMPLE_RATE * ROOM_TONE_TAIL_SEC), True, 0)
            out.write(tail)
            frames += len(tail)
    return frames / SAMPLE_RATE


def _wav_to_mp3_file(wav_path: str, mp3_path: str, tags: dict[str, str] | None = None) -> bool:
    """Encode with FFmpeg directly (streaming, no in-memory copy)."""
    if not _ffmpeg_available():
        return False
    command = ['ffmpeg', '-hide_banner', '-nostats', '-y', '-i', wav_path,
               '-codec:a', 'libmp3lame', '-b:a', '192k', '-id3v2_version', '3']
    for key, value in (tags or {}).items():
        if str(value).strip():
            command += ['-metadata', f'{key}={value}']
    command.append(mp3_path)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isfile(mp3_path):
        log.warning('FFmpeg MP3 encoding failed: %s', (result.stderr or '')[-400:])
        return False
    return True


def _merge_wavs(segments: list[dict]) -> np.ndarray:
    arrays = []
    playable = [
        seg
        for seg in segments
        if seg.get('audio_path') and os.path.exists(seg['audio_path'])
    ]
    for idx, seg in enumerate(playable):
        p = seg['audio_path']
        if p and os.path.exists(p):
            data, _ = sf.read(p)
            if data.ndim > 1:
                data = data.mean(axis=1)
            arrays.append(data)
            if idx + 1 < len(playable) or seg.get('pause_ms') is not None:
                pause = pause_after_segment(seg, playable[idx + 1] if idx + 1 < len(playable) else None)
                arrays.append(np.zeros(int(SAMPLE_RATE * pause)))
    return np.concatenate(arrays) if arrays else np.zeros(SAMPLE_RATE)


# ── Segment timeline builder ──────────────────────────────────────────────────

def build_timeline(segments_db: list[dict]) -> list[dict]:
    """
    segments_db: rows from tts_segments with audio_path + duration_sec.
    Returns same list enriched with t_start / t_end fields.
    """
    timeline = []
    cursor = 0.0
    for idx, seg in enumerate(segments_db):
        dur = seg.get('duration_sec') or 0.0
        timeline.append({**seg, 't_start': cursor, 't_end': cursor + dur})
        next_seg = segments_db[idx + 1] if idx + 1 < len(segments_db) else None
        cursor += dur
        if next_seg is not None:
            cursor += pause_after_segment(seg, next_seg)
    return timeline


# ── Public export functions ───────────────────────────────────────────────────

def export_single_chapter(
    chapter_title: str,
    book_title: str,
    segments: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    output_dir: str | None = None,
    file_stem: str | None = None,
    mastering: bool = False,
    book_author: str = 'Unknown',
    track_number: int | None = None,
) -> dict:
    """Returns {'audio_path': ..., 'subtitle_path': ..., 'audio_fmt': ..., 'sub_fmt': ...}"""
    output_dir = output_dir or _book_export_dir(book_author, book_title)
    os.makedirs(output_dir, exist_ok=True)
    safe_title = _safe_name(file_stem or chapter_title)
    # Actual positions are written into the timeline while the WAV is built.
    timeline = [dict(seg) for seg in build_timeline(segments)]

    wav_path = os.path.join(output_dir, f'{safe_title}.wav')
    mastering_applied = False
    mastering_warning = None
    if mastering:
        raw_wav_path = os.path.join(output_dir, f'.{safe_title}.premaster.wav')
        _write_merged_wav(timeline, raw_wav_path)
        try:
            mastering_applied, mastering_warning = _master_wav(
                raw_wav_path,
                wav_path,
            )
            if not mastering_applied:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
                os.replace(raw_wav_path, wav_path)
        finally:
            if os.path.exists(raw_wav_path):
                os.remove(raw_wav_path)
    else:
        _write_merged_wav(timeline, wav_path)

    out_audio = wav_path
    actual_fmt = 'wav'
    if audio_fmt == 'mp3':
        tags = {
            'title': chapter_title,
            'artist': book_author,
            'album': book_title,
            'track': str(track_number) if track_number is not None else '',
        }
        mp3_path = wav_path[:-4] + '.mp3'
        if _wav_to_mp3_file(wav_path, mp3_path, tags):
            out_audio = mp3_path
            actual_fmt = 'mp3'
            os.remove(wav_path)
        else:
            mp3 = _wav_to_mp3_bytes(wav_path, tags=tags)
            if mp3:
                out_audio = mp3_path
                with open(out_audio, 'wb') as f:
                    f.write(mp3)
                actual_fmt = 'mp3'
                os.remove(wav_path)

    sub_path = None
    sub_ext = 'none'
    if sub_fmt != 'none':
        sub_content = (
            build_ass(timeline, character_colors, f'{book_title} — {chapter_title}')
            if sub_fmt == 'ass'
            else build_srt(timeline)
        )
        sub_ext = 'ass' if sub_fmt == 'ass' else 'srt'
        sub_path = os.path.join(output_dir, f'{safe_title}.{sub_ext}')
        with open(sub_path, 'w', encoding='utf-8') as f:
            f.write(sub_content)

    return {
        'audio_path': out_audio,
        'subtitle_path': sub_path,
        'audio_fmt': actual_fmt,
        'sub_fmt': sub_ext,
        'mastering_applied': mastering_applied,
        'mastering_warning': mastering_warning,
    }


def export_chapter_zip(
    book_title: str,
    chapters_data: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    mastering: bool = False,
    book_author: str = 'Unknown',
) -> str:
    """chapters_data: list of {chapter_title, segments}. Returns zip file path."""
    safe_book = _safe_name(book_title)
    output_dir = _book_export_dir(book_author, book_title)
    os.makedirs(output_dir, exist_ok=True)
    zip_path = os.path.join(output_dir, f'{safe_book}_chapters.zip')

    used_names: set[str] = set()
    # Encoded audio does not compress; storing avoids a slow, useless deflate.
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for fallback_number, ch in enumerate(chapters_data, 1):
            track_number = int(ch.get('chapter_number') or fallback_number)
            result = export_single_chapter(
                ch['chapter_title'], book_title, ch['segments'],
                character_colors, audio_fmt, sub_fmt,
                mastering=mastering,
                book_author=book_author,
                track_number=track_number,
            )
            ch_safe = f"{track_number:02d}_{_safe_name(ch['chapter_title'])}"
            base, suffix = ch_safe, 2
            while ch_safe.lower() in used_names:
                ch_safe = f'{base}_{suffix}'
                suffix += 1
            used_names.add(ch_safe.lower())
            ext = result['audio_fmt']
            zf.write(result['audio_path'], f'{ch_safe}.{ext}')
            if result['subtitle_path']:
                zf.write(result['subtitle_path'], f'{ch_safe}.{result["sub_fmt"]}')

    return zip_path


def export_chapter_folder(
    book_title: str,
    chapters_data: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    mastering: bool = False,
    book_author: str = 'Unknown',
) -> dict:
    """Write numbered chapter files beneath ``exports/<author> - <book title>``."""
    output_dir = _book_export_dir(book_author, book_title)
    os.makedirs(output_dir, exist_ok=True)
    max_number = max(
        (int(ch.get('chapter_number', 0)) for ch in chapters_data),
        default=len(chapters_data),
    )
    number_width = max(2, len(str(max_number)))

    def render(item):
        fallback_number, chapter = item
        number = int(chapter.get('chapter_number') or fallback_number)
        title = chapter['chapter_title']
        stem = f'{number:0{number_width}d}_{_safe_name(title)}'
        return export_single_chapter(
            title,
            book_title,
            chapter['segments'],
            character_colors,
            audio_fmt,
            sub_fmt,
            output_dir=output_dir,
            file_stem=stem,
            mastering=mastering,
            book_author=book_author,
            track_number=number,
        )

    # Mastering and encoding run in FFmpeg processes; chapters are independent.
    from concurrent.futures import ThreadPoolExecutor

    items = list(enumerate(chapters_data, 1))
    workers = min(_export_workers(), len(items)) or 1
    if workers == 1:
        files = [render(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='chapter-export') as pool:
            files = list(pool.map(render, items))

    return {'directory_path': output_dir, 'chapters': files}


def _ffmetadata_plain(value: str) -> str:
    """The single-line text FFmpeg stores for a metadata value."""
    return ' '.join(str(value or '').replace('\r', '\n').split('\n'))


def _ffmetadata_value(value: str) -> str:
    return (
        _ffmetadata_plain(value).replace('\\', '\\\\').replace('=', '\\=')
        .replace(';', '\\;').replace('#', '\\#')
    )


def export_m4b(book_title, chapters_data, character_colors=None, *, sub_fmt='none',
               book_author='Unknown', mastering=False, book_metadata=None,
               on_progress=None, check_cancelled=None):
    from core.m4b_export import export
    return export(book_title, chapters_data, character_colors, sub_fmt=sub_fmt,
                  book_author=book_author, mastering=mastering, book_metadata=book_metadata,
                  on_progress=on_progress, check_cancelled=check_cancelled)


def parse_chapter_selection(selection: str | None, chapter_count: int) -> list[int]:
    """Parse print-style chapter numbers such as ``1,3,5-8``."""
    if chapter_count < 1:
        raise ValueError('This book has no chapters to export.')

    value = (selection or '').strip().lower()
    if value in ('', '*', 'all', 'mind', 'összes'):
        return list(range(1, chapter_count + 1))

    chosen: set[int] = set()
    for item in value.split(','):
        item = item.strip()
        if not item:
            raise ValueError('Empty item in chapter selection.')
        match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', item)
        if match:
            start, end = (int(part) for part in match.groups())
            if start > end:
                raise ValueError(f'Invalid descending chapter range: {item}')
            chosen.update(range(start, end + 1))
        elif item.isdigit():
            chosen.add(int(item))
        else:
            raise ValueError(
                'Use chapter numbers, commas and ranges, for example: 1,3,5-8.'
            )

    invalid = sorted(number for number in chosen if not 1 <= number <= chapter_count)
    if invalid:
        raise ValueError(
            f'Chapter number out of range: {invalid[0]} '
            f'(valid range: 1-{chapter_count}).'
        )
    return sorted(chosen)


def _safe_name(name: str) -> str:
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80] or 'export'


def _book_export_dir(book_author: str, book_title: str) -> str:
    """Return a filesystem-safe ``Author - Title`` directory below exports."""
    author = (book_author or '').strip() or 'Unknown'
    title = (book_title or '').strip() or 'Untitled'
    folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', f'{author} - {title}')
    folder_name = re.sub(r'\s+', ' ', folder_name).strip(' .')
    return os.path.join(EXPORTS_DIR, folder_name[:160] or 'Unknown - Untitled')
