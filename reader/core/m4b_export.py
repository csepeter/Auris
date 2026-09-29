"""Bounded-memory audiobook assembly, metadata and verified atomic publication."""
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def export(book_title, chapters_data, character_colors=None, *, sub_fmt='none',
           book_author='Unknown', mastering=False, book_metadata=None,
           on_progress=None, check_cancelled=None, background=None):
    from core import exporter as e

    if not chapters_data:
        raise ValueError('Nincs exportálható fejezet.')
    if not e._ffmpeg_available() or not e.shutil.which('ffprobe'):
        raise RuntimeError('Az M4B-exporthoz FFmpeg és ffprobe szükséges.')
    destination = Path(e._book_export_dir(book_author, book_title))
    destination.mkdir(parents=True, exist_ok=True)
    safe = e._safe_name(book_title)
    target = destination / (safe + '.m4b')
    metadata = dict(book_metadata or {})

    def report(message):
        if check_cancelled:
            check_cancelled()
        if on_progress:
            on_progress(message)

    def run(command, **kwargs):
        # Log to disk to avoid pipe deadlocks and retain cancellability even in
        # the two mastering passes. Do not accumulate FFmpeg output in memory.
        with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as stdin:
            value = kwargs.get('input', '')
            stdin.write(value.encode('utf-8') if isinstance(value, str) else value)
            stdin.seek(0)
            proc = subprocess.Popen(command, stdin=stdin, stdout=log, stderr=log)
            try:
                while proc.poll() is None:
                    if check_cancelled:
                        check_cancelled()
                    time.sleep(.1)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            log.seek(0 if command[0] == 'ffprobe' else max(0, log.tell() - 65536))
            output = log.read().decode('utf-8', errors='replace')
            return subprocess.CompletedProcess(command, proc.returncode, output, output)

    with tempfile.TemporaryDirectory(prefix='.m4b-', dir=destination) as work:
        work = Path(work)
        raw = work / 'source.wav'
        total = sum(len(c.get('segments') or []) for c in chapters_data)
        done = 0
        # 1) Each chapter is written to its own PCM file (bounded memory).
        pieces = []  # (path, frames, [(segment, start_frame, end_frame)], title)
        trim = e._export_option('trim_segment_silence', True)
        room_tone = e._export_option('export_room_tone', False)
        for ci, chapter in enumerate(chapters_data):
            segments = chapter.get('segments') or []
            if not segments:
                raise ValueError('Üres fejezet nem exportálható.')
            piece = work / f'chapter-{ci:05d}.wav'
            local, cursor = [], 0
            with sf.SoundFile(piece, 'w', samplerate=e.SAMPLE_RATE, channels=1,
                              format='RF64', subtype='PCM_16') as out:
                for si, segment in enumerate(segments):
                    report(f'Hangok összefűzése: {done}/{total}')
                    path = segment.get('audio_path')
                    if not path or not Path(path).is_file():
                        raise ValueError('Hiányzó mondathang. Generáld újra a fejezetet.')
                    if si == 0 and ci == 0 and room_tone:
                        head = e.silence(int(e.SAMPLE_RATE * e.ROOM_TONE_HEAD_SEC), True, 1)
                        out.write(head)
                        cursor += len(head)
                    seg_start = cursor
                    with sf.SoundFile(path) as source:
                        if source.samplerate != e.SAMPLE_RATE or not len(source):
                            raise ValueError('Érvénytelen mondathang vagy mintavételi frekvencia.')
                    audio = e.read_segment_audio(path, trim=trim)
                    out.write(audio)
                    cursor += len(audio)
                    local.append((segment, seg_start, cursor))
                    last_of_book = ci + 1 == len(chapters_data) and si + 1 == len(segments)
                    pause = (e.pause_after_segment(segment, segments[si + 1])
                             if si + 1 < len(segments) else
                             e.CHAPTER_GAP_SEC if ci + 1 < len(chapters_data) else
                             e.ROOM_TONE_TAIL_SEC if room_tone and last_of_book else 0)
                    gap = e.silence(round(pause * e.SAMPLE_RATE), room_tone, done + 2)
                    if len(gap):
                        out.write(gap)
                        cursor += len(gap)
                    done += 1
            pieces.append([piece, cursor, local,
                           chapter.get('chapter_title') or f'Fejezet {ci + 1}'])
        report(f'Hangok összefűzése: {total}/{total}')

        # 2) Chapters are mastered in parallel FFmpeg processes; loudness is
        #    matched per chapter, as in the chapter-folder export.
        applied, warning = False, None
        if mastering:
            from concurrent.futures import ThreadPoolExecutor

            report('Hangerő-kiegyenlítés: elemzés és feldolgozás…')

            def master(piece):
                target_path = piece[0].with_name(piece[0].stem + '-mastered.wav')
                ok, problem = e._master_wav(str(piece[0]), str(target_path), runner=run)
                return target_path if ok else None, problem

            workers = min(e._export_workers(), len(pieces)) or 1
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='m4b-master') as pool:
                outcomes = list(pool.map(master, pieces))
            problems = [problem for path, problem in outcomes if path is None and problem]
            if all(path is not None for path, _ in outcomes):
                applied = True
                for piece, (path, _) in zip(pieces, outcomes):
                    piece[0] = path
            else:
                # All or nothing: mixing mastered and raw chapters would make
                # loudness jump between chapters.
                warning = problems[0] if problems else 'A hangerő-kiegyenlítés nem sikerült.'
                for path, _ in outcomes:
                    if path is not None:
                        path.unlink(missing_ok=True)

        if background and background.get('path'):
            report('Háttérzene keverése…')
            for piece in pieces:
                mixed = piece[0].with_name(piece[0].stem + '-music.wav')
                if e.mix_background(str(piece[0]), background['path'], str(mixed),
                                    music_db=background.get('db', -22.0)):
                    piece[0] = mixed

        # 3) Chapters are joined; boundaries come from the actual file lengths.
        report('Fejezetek összefűzése…')
        timeline, chapters, cursor = [], [], 0
        with sf.SoundFile(raw, 'w', samplerate=e.SAMPLE_RATE, channels=1,
                          format='RF64', subtype='PCM_16') as out:
            for path, raw_frames, local, title in pieces:
                start = cursor
                with sf.SoundFile(path) as source:
                    for block in source.blocks(blocksize=262144, dtype='float32', always_2d=True):
                        if check_cancelled:
                            check_cancelled()
                        out.write(block.mean(axis=1))
                        cursor += len(block)
                length = cursor - start
                scale = length / raw_frames if raw_frames else 1.0
                for segment, seg_start, seg_end in local:
                    timeline.append({**segment,
                                     't_start': (start + seg_start * scale) / e.SAMPLE_RATE,
                                     't_end': (start + seg_end * scale) / e.SAMPLE_RATE})
                chapters.append((start, cursor, title))
                Path(path).unlink(missing_ok=True)
        tags = {'title': book_title, 'artist': book_author, 'album': book_title,
                'language': metadata.get('language'), 'description': metadata.get('description'),
                'series': metadata.get('series'), 'series-part': metadata.get('series_index'),
                'publisher': metadata.get('publisher'), 'date': metadata.get('published')}
        lines = [';FFMETADATA1'] + [f'{key}={e._ffmetadata_value(value)}'
                                            for key, value in tags.items() if value]
        for start, end, title in chapters:
            lines.extend(['[CHAPTER]', f'TIMEBASE=1/{e.SAMPLE_RATE}',
                          f'START={start}', f'END={end}', f'title={e._ffmetadata_value(title)}'])
        meta = work / 'metadata.txt'
        meta.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        staged = work / 'book.m4b'
        command = ['ffmpeg', '-hide_banner', '-nostats', '-y', '-i', str(raw),
                   '-f', 'ffmetadata', '-i', str(meta)]
        cover = metadata.get('cover_b64')
        if cover:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(base64.b64decode(cover, validate=True))) as picture:
                picture.thumbnail((1600, 1600))
                picture.convert('RGB').save(work / 'cover.jpg')
            command += ['-i', str(work / 'cover.jpg')]
        command += ['-map', '0:a', '-map_metadata', '1', '-map_chapters', '1']
        if cover:
            command += ['-map', '2:v', '-c:v', 'copy', '-disposition:v:0', 'attached_pic']
        command += ['-c:a', 'aac', '-b:a', '192k', '-metadata', 'media_type=2', str(staged)]
        report('M4B kódolása…')
        result = run(command)
        if result.returncode:
            raise RuntimeError('M4B kódolási hiba: ' + result.stderr[-1500:])
        from core.mp4_metadata import add_book_tags
        add_book_tags(staged, {key: tags[key] for key in ('language', 'series', 'series-part', 'publisher')})
        report('M4B ellenőrzése…')
        probe = run(['ffprobe', '-v', 'error', '-show_chapters', '-show_format',
                     '-show_streams', '-of', 'json', str(staged)])
        if probe.returncode:
            raise RuntimeError('Az elkészült M4B nem ellenőrizhető.')
        info = json.loads(probe.stdout)
        actual = info.get('chapters', [])
        if len(actual) != len(chapters) or abs(float(info['format']['duration']) - cursor / e.SAMPLE_RATE) > .25:
            raise RuntimeError('Az M4B hossza vagy fejezetszáma eltér a forrástól.')
        for item, (start, end, title) in zip(actual, chapters):
            if (abs(float(item['start_time']) - start / e.SAMPLE_RATE) > .02
                    or abs(float(item['end_time']) - end / e.SAMPLE_RATE) > .02
                    or item.get('tags', {}).get('title') != e._ffmetadata_plain(title)):
                raise RuntimeError('Az M4B fejezethatárai vagy címei hibásak.')
        if cover and not any(s.get('disposition', {}).get('attached_pic') for s in info['streams']):
            raise RuntimeError('A borító nem került az M4B-fájlba.')
        subtitle = None
        if sub_fmt != 'none':
            subtitle = destination / (safe + ('.ass' if sub_fmt == 'ass' else '.srt'))
            content = (e.build_ass(timeline, character_colors or {}, book_title)
                       if sub_fmt == 'ass' else e.build_srt(timeline))
            (work / 'subtitle').write_text(content, encoding='utf-8')
        report('Export véglegesítése…')
        if subtitle:
            os.replace(work / 'subtitle', subtitle)
        os.replace(staged, target)
    return {'audio_path': str(target), 'subtitle_path': str(subtitle) if subtitle else None,
            'audio_fmt': 'm4b', 'sub_fmt': sub_fmt, 'chapter_count': len(chapters),
            'mastering_applied': applied, 'mastering_warning': warning}
