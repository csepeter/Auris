"""Publishing and packaging helpers.

* :func:`build_epub3_media_overlay` – EPUB 3.3 read-along book with EPUB Media
  Overlays (SMIL) synchronised to the chapter audio.
* :func:`build_daw_package` – Audacity-friendly ZIP (one WAV per speaker, mix,
  label track and ``.lof`` project list).
* Audiobookshelf helpers – folder naming, sidecar metadata, REST upload.
* :func:`retail_sample` – faded MP3 excerpt for shops/retail previews.

All user-facing errors are ``ValueError`` (or ``RuntimeError`` for tool
failures) with Hungarian messages, matching the rest of the exporter code.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import http.client
import io
import json
import math
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import unicodedata
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape as _xml_escape

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24_000
ACTIVE_CLASS = '-epub-media-overlay-active'
NARRATOR_LABEL = 'Narrátor'

# Audio media types that are EPUB 3 core media types; anything else is
# transcoded to MP3 before it goes into the publication.
_EPUB_AUDIO_TYPES = {'.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4'}
_EPUB_IMAGE_TYPES = {'JPEG': ('image/jpeg', '.jpg'), 'PNG': ('image/png', '.png'),
                     'GIF': ('image/gif', '.gif'), 'WEBP': ('image/webp', '.webp')}
_INVALID_XML_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')
_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                     *(f'LPT{i}' for i in range(1, 10))}


# ── small helpers ─────────────────────────────────────────────────────────────

def _text(value) -> str:
    """Return a stripped string with characters that are illegal in XML removed."""
    if value is None:
        return ''
    return _INVALID_XML_CHARS.sub('', str(value)).strip()


def _esc(value) -> str:
    return _xml_escape(_text(value), {'"': '&quot;'})


def _ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


def _clock(seconds: float) -> str:
    """SMIL full clock value (``H:MM:SS.mmm``) used by ``media:duration``."""
    millis = int(round(max(0.0, float(seconds)) * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f'{hours}:{minutes:02d}:{secs:02d}.{millis:03d}'


def _clip(seconds: float) -> str:
    return f'{max(0.0, float(seconds)):.3f}s'


def _atomic_target(output_path) -> tuple[Path, Path]:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    os.close(fd)
    return target, Path(temp)


def _run_ffmpeg(args: list[str], what: str) -> None:
    if not _ffmpeg_available():
        raise RuntimeError(f'{what}: az FFmpeg nem érhető el.')
    proc = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode:
        message = proc.stderr.decode('utf-8', errors='replace')[-1200:].strip()
        raise RuntimeError(f'{what}: FFmpeg-hiba. {message}')


def audio_duration(path) -> float:
    """Duration of an audio file in seconds (soundfile, ffprobe fallback)."""
    try:
        info = sf.info(str(path))
        if info.samplerate and info.frames:
            return info.frames / info.samplerate
    except Exception:
        pass
    if shutil.which('ffprobe'):
        proc = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                               '-of', 'default=nw=1:nk=1', str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            return float(proc.stdout.decode().strip())
        except ValueError:
            pass
    raise ValueError(f'A hangfájl hossza nem állapítható meg: {Path(path).name}')


def _decode_cover(cover_b64) -> bytes | None:
    if not cover_b64:
        return None
    data = str(cover_b64)
    if data.startswith('data:') and ',' in data:
        data = data.split(',', 1)[1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('A borítókép nem érvényes base64-kódolású kép.') from None


def _year(published) -> str | None:
    match = re.search(r'(?<!\d)(\d{4})(?!\d)', str(published or ''))
    return match.group(1) if match else None


def _w3c_date(published) -> str | None:
    """Return ``published`` if it is a W3CDTF date (YYYY, YYYY-MM, YYYY-MM-DD)."""
    value = str(published or '').strip()
    if re.fullmatch(r'\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?', value):
        return value
    return _year(value)


def _split_names(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r'\s*(?:;|&|\s+és\s+)\s*', str(value or ''))
    seen, names = set(), []
    for item in items:
        name = str(item or '').strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


# ── 1. EPUB 3 with Media Overlays ─────────────────────────────────────────────

_CSS = """\
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2 { font-family: sans-serif; line-height: 1.25; }
h1 { font-size: 1.6em; margin: 1.5em 0 1em; }
h2 { font-size: 1.25em; margin: 1.2em 0 .8em; }
p { margin: 0; text-indent: 1.2em; text-align: justify; }
h1 + p, h2 + p { text-indent: 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100vh; }
.-epub-media-overlay-active { background: #ffe89a; color: #000; }
"""


def _identifier(book: dict) -> str:
    given = _text(book.get('identifier'))
    if given:
        try:
            return 'urn:uuid:' + str(uuid.UUID(given.removeprefix('urn:uuid:')))
        except ValueError:
            return given
    seed = '|'.join(_text(book.get(key)) for key in ('title', 'author', 'published'))
    return 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'auris-epub:' + seed))


def _chapter_blocks(segments: list[dict]) -> list[tuple[str, list[tuple[int, dict]]]]:
    """Group segments into (tag, [(segment_index, segment)]) blocks."""
    blocks: list[tuple[str, list]] = []
    current: list | None = None
    current_tag = None
    for index, segment in enumerate(segments):
        kind = segment.get('block_kind')
        tag = 'h1' if kind == 'heading' else 'h2' if kind == 'subheading' else 'p'
        if current is not None and tag != current_tag:
            blocks.append((current_tag, current))
            current = None
        if current is None:
            current, current_tag = [], tag
        current.append((index, segment))
        if segment.get('ends_paragraph'):
            blocks.append((current_tag, current))
            current = None
    if current:
        blocks.append((current_tag, current))
    return blocks


def _xhtml_document(title: str, language: str, body: str) -> str:
    lang = _esc(language)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{lang}" xml:lang="{lang}">\n<head>\n<meta charset="UTF-8"/>\n'
            f'<title>{_esc(title)}</title>\n'
            '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n'
            f'{body}</html>\n')


def _prepare_epub_audio(source: Path, work: Path, stem: str) -> tuple[Path, str, str]:
    """Return (local_path, archive_name, media_type) for a chapter audio file."""
    if not source.is_file():
        raise ValueError(f'A fejezet hangfájlja nem található: {source.name}')
    suffix = source.suffix.lower()
    if suffix in _EPUB_AUDIO_TYPES:
        return source, f'audio/{stem}{suffix}', _EPUB_AUDIO_TYPES[suffix]
    target = work / f'{stem}.mp3'
    _run_ffmpeg(['-i', str(source), '-vn', '-map_metadata', '-1', '-ac', '1',
                 '-c:a', 'libmp3lame', '-b:a', '128k', str(target)],
                'EPUB-hangátalakítás')
    return target, f'audio/{stem}.mp3', 'audio/mpeg'


def build_epub3_media_overlay(book: dict, chapters: list[dict], output_path) -> str:
    """Build an EPUB 3.3 read-along publication with Media Overlays.

    Each segment becomes a ``<span id="sNNNN">`` in the chapter XHTML and a
    ``<par>`` in the chapter SMIL pointing to ``clipBegin``/``clipEnd`` of the
    chapter audio.  Chapters without ``audio_path`` are included as plain text.
    Returns the output path.
    """
    book = dict(book or {})
    if not chapters:
        raise ValueError('Nincs exportálható fejezet.')
    title = _text(book.get('title')) or 'Névtelen könyv'
    language = _text(book.get('language')) or 'hu'
    authors = _split_names(book.get('author')) or ['Ismeretlen szerző']
    narrator = _text(book.get('narrator'))
    width = max(2, len(str(len(chapters))))
    modified = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    cover = _decode_cover(book.get('cover_b64'))
    target, temp = _atomic_target(output_path)

    try:
        with tempfile.TemporaryDirectory(prefix='.epub-', dir=target.parent) as work_dir:
            work = Path(work_dir)
            files: list[tuple[str, bytes | Path]] = []   # archive name → content
            manifest: list[str] = []
            spine: list[str] = []
            nav_items: list[str] = []
            durations: list[tuple[str, float]] = []
            audio_cache: dict[str, tuple[str, str]] = {}

            if cover:
                from PIL import Image
                try:
                    with Image.open(io.BytesIO(cover)) as picture:
                        fmt = picture.format
                        if fmt not in _EPUB_IMAGE_TYPES:
                            buffer = io.BytesIO()
                            picture.convert('RGB').save(buffer, 'JPEG', quality=92)
                            cover, fmt = buffer.getvalue(), 'JPEG'
                except Exception:
                    raise ValueError('A borítókép formátuma nem támogatott.') from None
                media_type, ext = _EPUB_IMAGE_TYPES[fmt]
                files.append((f'OEBPS/cover{ext}', cover))
                manifest.append(f'<item id="cover-image" href="cover{ext}" media-type="{media_type}" '
                                'properties="cover-image"/>')
                cover_body = ('<body>\n<section epub:type="cover" class="cover">\n'
                              f'<img src="cover{ext}" alt="{_esc(title)}"/>\n</section>\n</body>\n')
                files.append(('OEBPS/cover.xhtml',
                              _xhtml_document(title, language, cover_body).encode('utf-8')))
                manifest.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
                spine.append('<itemref idref="cover" linear="yes"/>')

            for number, chapter in enumerate(chapters, 1):
                stem = f'chap{number:0{width}d}'
                chapter_title = _text(chapter.get('title')) or f'{number}. fejezet'
                segments = [s for s in (chapter.get('segments') or []) if _text(s.get('text'))]
                audio_path = chapter.get('audio_path')
                has_audio = bool(audio_path)
                if has_audio:
                    key = os.path.normcase(os.path.abspath(str(audio_path)))
                    if key not in audio_cache:
                        local, arcname, media_type = _prepare_epub_audio(Path(audio_path), work, stem)
                        audio_id = 'audio-' + stem
                        files.append(('OEBPS/' + arcname, local))
                        manifest.append(f'<item id="{audio_id}" href="{arcname}" media-type="{media_type}"/>')
                        audio_cache[key] = (arcname, audio_id)
                    audio_href = audio_cache[key][0]

                # XHTML: one span per segment, grouped by paragraphs/headings.
                parts: list[str] = []
                blocks = _chapter_blocks(segments)
                if not blocks or blocks[0][0] != 'h1':
                    parts.append(f'<h1>{_esc(chapter_title)}</h1>')
                span_ids: dict[int, str] = {}
                counter = 0
                for tag, items in blocks:
                    spans = []
                    for index, segment in items:
                        counter += 1
                        span_id = f's{counter:04d}'
                        span_ids[index] = span_id
                        spans.append(f'<span id="{span_id}">{_esc(segment.get("text"))}</span>')
                    parts.append(f'<{tag}>' + ' '.join(spans) + f'</{tag}>')
                body = (f'<body>\n<section epub:type="chapter" id="{stem}" aria-label="{_esc(chapter_title)}">\n'
                        + '\n'.join(parts) + '\n</section>\n</body>\n')
                files.append((f'OEBPS/{stem}.xhtml',
                               _xhtml_document(chapter_title, language, body).encode('utf-8')))

                # SMIL: one <par> per timed segment.
                pars, overlay_seconds = [], 0.0
                if has_audio:
                    for index, segment in enumerate(segments):
                        try:
                            begin = max(0.0, float(segment.get('t_start')))
                            end = float(segment.get('t_end'))
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(begin) or not math.isfinite(end) or end - begin < 0.0005:
                            continue
                        if round(end, 3) <= round(begin, 3):
                            continue
                        span_id = span_ids[index]
                        overlay_seconds += round(end, 3) - round(begin, 3)
                        pars.append(f'<par id="p{span_id[1:]}"><text src="{stem}.xhtml#{span_id}"/>'
                                    f'<audio src="{audio_href}" clipBegin="{_clip(begin)}" '
                                    f'clipEnd="{_clip(end)}"/></par>')
                overlay_attr = ''
                if pars:
                    smil_id = 'mo-' + stem
                    smil = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                            '<smil xmlns="http://www.w3.org/ns/SMIL" '
                            'xmlns:epub="http://www.idpf.org/2007/ops" version="3.0">\n<body>\n'
                            f'<seq id="seq-{stem}" epub:textref="{stem}.xhtml" epub:type="chapter">\n'
                            + '\n'.join(pars) + '\n</seq>\n</body>\n</smil>\n')
                    files.append((f'OEBPS/{stem}.smil', smil.encode('utf-8')))
                    manifest.append(f'<item id="{smil_id}" href="{stem}.smil" '
                                    'media-type="application/smil+xml"/>')
                    durations.append((smil_id, overlay_seconds))
                    overlay_attr = f' media-overlay="{smil_id}"'
                manifest.append(f'<item id="{stem}" href="{stem}.xhtml" '
                                f'media-type="application/xhtml+xml"{overlay_attr}/>')
                spine.append(f'<itemref idref="{stem}"/>')
                nav_items.append(f'<li><a href="{stem}.xhtml">{_esc(chapter_title)}</a></li>')

            toc_label = 'Tartalom' if language.lower().startswith('hu') else 'Contents'
            nav_body = ('<body>\n<nav epub:type="toc" id="toc">\n'
                        f'<h1>{toc_label}</h1>\n<ol>\n' + '\n'.join(nav_items) + '\n</ol>\n</nav>\n</body>\n')
            files.append(('OEBPS/nav.xhtml', _xhtml_document(toc_label, language, nav_body).encode('utf-8')))
            files.append(('OEBPS/style.css', _CSS.encode('utf-8')))
            manifest[:0] = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                            '<item id="css" href="style.css" media-type="text/css"/>']

            meta = [f'<dc:identifier id="pub-id">{_esc(_identifier(book))}</dc:identifier>',
                    f'<dc:title>{_esc(title)}</dc:title>',
                    f'<dc:language>{_esc(language)}</dc:language>']
            for index, author in enumerate(authors, 1):
                meta.append(f'<dc:creator id="creator{index}">{_esc(author)}</dc:creator>')
                meta.append(f'<meta refines="#creator{index}" property="role" scheme="marc:relators">aut</meta>')
            if narrator:
                meta.append(f'<dc:contributor id="narrator">{_esc(narrator)}</dc:contributor>')
                meta.append('<meta refines="#narrator" property="role" scheme="marc:relators">nrt</meta>')
            if _text(book.get('publisher')):
                meta.append(f'<dc:publisher>{_esc(book.get("publisher"))}</dc:publisher>')
            if _w3c_date(book.get('published')):
                meta.append(f'<dc:date>{_w3c_date(book.get("published"))}</dc:date>')
            if _text(book.get('description')):
                meta.append(f'<dc:description>{_esc(book.get("description"))}</dc:description>')
            if _text(book.get('series')):
                meta.append(f'<meta property="belongs-to-collection" id="series">{_esc(book.get("series"))}</meta>')
                meta.append('<meta refines="#series" property="collection-type">series</meta>')
                index = _text(book.get('series_index'))
                if re.fullmatch(r'\d+(\.\d+)?', index):
                    meta.append(f'<meta refines="#series" property="group-position">{index}</meta>')
            meta.append(f'<meta property="dcterms:modified">{modified}</meta>')
            if durations:
                meta.append(f'<meta property="media:duration">{_clock(sum(d for _, d in durations))}</meta>')
                for smil_id, seconds in durations:
                    meta.append(f'<meta property="media:duration" refines="#{smil_id}">{_clock(seconds)}</meta>')
                if narrator:
                    meta.append(f'<meta property="media:narrator">{_esc(narrator)}</meta>')
                meta.append(f'<meta property="media:active-class">{ACTIVE_CLASS}</meta>')
            modes = ['textual', 'auditory'] if durations else ['textual']
            meta += [f'<meta property="schema:accessMode">{mode}</meta>' for mode in modes]
            meta.append('<meta property="schema:accessModeSufficient">textual</meta>')
            if durations:
                meta.append('<meta property="schema:accessModeSufficient">auditory</meta>')
                meta.append('<meta property="schema:accessibilityFeature">synchronizedAudioText</meta>')
            meta += ['<meta property="schema:accessibilityFeature">tableOfContents</meta>',
                     '<meta property="schema:accessibilityFeature">readingOrder</meta>',
                     '<meta property="schema:accessibilityHazard">none</meta>',
                     '<meta property="schema:accessibilitySummary">'
                     + ('Szöveg és szinkronizált felolvasás (EPUB Media Overlays).' if durations
                        else 'Szöveges kiadvány.') + '</meta>']
            opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                   f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" '
                   f'xml:lang="{_esc(language)}">\n'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
                   + '\n'.join(meta) + '\n</metadata>\n<manifest>\n' + '\n'.join(manifest)
                   + '\n</manifest>\n<spine>\n' + '\n'.join(spine) + '\n</spine>\n</package>\n')
            container = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                         '<rootfiles>\n<rootfile full-path="OEBPS/content.opf" '
                         'media-type="application/oebps-package+xml"/>\n</rootfiles>\n</container>\n')

            with zipfile.ZipFile(temp, 'w') as archive:
                info = zipfile.ZipInfo('mimetype', date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, b'application/epub+zip')
                archive.writestr('META-INF/container.xml', container, zipfile.ZIP_DEFLATED)
                archive.writestr('OEBPS/content.opf', opf.encode('utf-8'), zipfile.ZIP_DEFLATED)
                for name, content in files:
                    if isinstance(content, Path):
                        # Audio is already compressed; storing is faster and seekable.
                        archive.write(content, name, zipfile.ZIP_STORED)
                    else:
                        archive.writestr(name, content, zipfile.ZIP_DEFLATED)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return str(target)


# ── 2. DAW / Audacity package ─────────────────────────────────────────────────

def _safe_file_stem(name, fallback: str = 'Beszélő') -> str:
    """Filesystem-safe name: accents kept, separators/illegal chars removed."""
    value = unicodedata.normalize('NFC', str(name or ''))
    value = _WINDOWS_ILLEGAL.sub('', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    value = value[:100].rstrip(' .')
    if value.upper() in _WINDOWS_RESERVED:
        value += '_'
    return value or fallback


def _read_mono(path, sample_rate: int) -> np.ndarray:
    if not path or not Path(path).is_file():
        raise ValueError('Hiányzó mondathang. Generáld újra a fejezetet.')
    audio, rate = sf.read(str(path), dtype='float32', always_2d=True)
    if rate != sample_rate:
        raise ValueError(f'Váratlan mintavételi frekvencia ({rate} Hz, várt: {sample_rate} Hz): '
                         f'{Path(path).name}')
    return audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]


def build_daw_package(chapter_title, segments, output_zip, *, sample_rate: int = SAMPLE_RATE) -> str:
    """Build an Audacity-friendly ZIP for one chapter.

    Contents (inside a folder named after the chapter):
    ``<Speaker>.wav`` per speaker (mono PCM16, segment audio placed at
    ``t_start``), ``mix.wav``, ``labels.txt`` (Audacity label track) and
    ``<chapter>.lof`` (opens every speaker track aligned at offset 0).
    Memory: one speaker track plus the mix are held at a time.
    """
    segments = [s for s in (segments or []) if s is not None]
    if not segments:
        raise ValueError('Nincs exportálható mondat a fejezetben.')
    if sample_rate <= 0:
        raise ValueError('Érvénytelen mintavételi frekvencia.')

    # Speakers in order of appearance, narrator first; unique Windows-safe names.
    speakers: dict[str, list[dict]] = {}
    for segment in segments:
        name = _text(segment.get('character_name')) or NARRATOR_LABEL
        speakers.setdefault(name, []).append(segment)
    order = sorted(speakers, key=lambda n: n != NARRATOR_LABEL)
    file_names, used = {}, set()
    for speaker in order:
        stem = _safe_file_stem(speaker)
        candidate, n = stem, 2
        while candidate.casefold() in used or candidate.casefold() == 'mix':
            candidate, n = f'{stem} ({n})', n + 1
        used.add(candidate.casefold())
        file_names[speaker] = candidate + '.wav'

    def placement(segment):
        try:
            start = float(segment.get('t_start') or 0.0)
        except (TypeError, ValueError):
            raise ValueError('Érvénytelen mondatidőzítés.') from None
        return max(0, int(round(start * sample_rate)))

    total = 0
    for segment in segments:
        try:
            total = max(total, int(math.ceil(float(segment.get('t_end') or 0.0) * sample_rate)))
        except (TypeError, ValueError):
            raise ValueError('Érvénytelen mondatidőzítés.') from None
        path = segment.get('audio_path')
        if not path or not Path(path).is_file():
            raise ValueError('Hiányzó mondathang. Generáld újra a fejezetet.')
        # Never truncate audio that runs slightly past its t_end.
        total = max(total, placement(segment) + sf.info(str(path)).frames)
    if total <= 0:
        raise ValueError('A fejezet hossza nulla.')

    folder = _safe_file_stem(chapter_title, 'Fejezet')
    target, temp = _atomic_target(output_zip)
    try:
        with tempfile.TemporaryDirectory(prefix='.daw-', dir=target.parent) as work_dir, \
                zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            work = Path(work_dir)
            mix = np.zeros(total, dtype=np.float32)
            for speaker in order:
                track = np.zeros(total, dtype=np.float32)
                for segment in speakers[speaker]:
                    audio = _read_mono(segment.get('audio_path'), sample_rate)
                    start = placement(segment)
                    track[start:start + len(audio)] += audio
                mix += track
                path = work / file_names[speaker]
                sf.write(str(path), np.clip(track, -1.0, 1.0), sample_rate, subtype='PCM_16')
                del track
                archive.write(path, f'{folder}/{file_names[speaker]}', zipfile.ZIP_STORED)
                path.unlink()
            path = work / 'mix.wav'
            sf.write(str(path), np.clip(mix, -1.0, 1.0), sample_rate, subtype='PCM_16')
            del mix
            archive.write(path, f'{folder}/mix.wav', zipfile.ZIP_STORED)

            labels = []
            for segment in segments:
                speaker = _text(segment.get('character_name')) or NARRATOR_LABEL
                text = re.sub(r'\s+', ' ', _text(segment.get('text')))
                start = placement(segment) / sample_rate
                end = max(start, float(segment.get('t_end') or 0.0))
                labels.append(f'{start:.6f}\t{end:.6f}\t{speaker}: {text[:60]}')
            archive.writestr(f'{folder}/labels.txt', '\n'.join(labels) + '\n')
            lof = ''.join(f'file "{file_names[s]}" offset 0\n' for s in order)
            archive.writestr(f'{folder}/{folder}.lof', lof)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return str(target)


# ── 3. Audiobookshelf ─────────────────────────────────────────────────────────

_ABS_LEAF_LIMIT = 120


def _abs_component(value, limit: int = _ABS_LEAF_LIMIT) -> str:
    value = unicodedata.normalize('NFC', str(value or ''))
    value = value.replace(':', ' -')
    value = _WINDOWS_ILLEGAL.sub('', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    value = value[:limit].rstrip(' .')
    if value.upper() in _WINDOWS_RESERVED:
        value += '_'
    return value


def _series_sequence(book: dict) -> str | None:
    value = _text(book.get('series_index'))
    if not value:
        return None
    try:
        number = float(value.replace(',', '.'))
    except ValueError:
        return value
    return str(int(number)) if number.is_integer() else f'{number:g}'


def _abs_folder_parts(book: dict) -> list[str]:
    author = _abs_component(', '.join(_split_names(book.get('author'))))
    series = _abs_component(book.get('series'))
    title = _abs_component(book.get('title')) or 'Névtelen könyv'
    narrator = _abs_component(_text(book.get('narrator')).replace('{', '(').replace('}', ')'))
    # ABS only reads the series folder when an author folder sits above it.
    if not author:
        series = ''
    prefix = ''
    sequence = _series_sequence(book)
    # ABS parses "Vol N" only for 0-3 digit (optionally .dd) sequences.
    if series and sequence and re.fullmatch(r'\d{1,3}(\.\d{1,2})?', sequence):
        prefix += f'Vol {sequence} - '
    year = _year(book.get('published'))
    if year:
        prefix += f'{year} - '
    suffix = f' {{{narrator[:40].rstrip()}}}' if narrator else ''
    # ABS trims upload names to 255 UTF-16 bytes; keep well below and shorten
    # the title (never the parsed prefix/suffix).
    room = max(20, _ABS_LEAF_LIMIT - len(prefix) - len(suffix))
    name = prefix + title[:room].rstrip(' .') + suffix
    return [part for part in (author, series, name) if part]


def audiobookshelf_folder_name(book: dict) -> str:
    """Relative ``Author/Series/Vol N - YEAR - Title {Narrator}`` path.

    Follows the Audiobookshelf folder parser (server/utils/scandir.js):
    missing parts are omitted, ``Vol N`` only with a series, the year only when
    known and ``{Narrator}`` only when a narrator is set.
    """
    return os.path.join(*_abs_folder_parts(dict(book or {})))


def audiobookshelf_metadata(book: dict, chapters: list[dict] | None = None) -> dict:
    """Audiobookshelf ``metadata.json`` (book media type) as a dict."""
    book = dict(book or {})
    series = _text(book.get('series'))
    sequence = _series_sequence(book)
    series_entry = []
    if series:
        # "Name #seq" – the sequence may not contain whitespace or '#'.
        series_entry = [f'{series} #{sequence}' if sequence and re.fullmatch(r'[^#\s]+', sequence)
                        else series]
    published = _text(book.get('published'))
    date = published if re.fullmatch(r'\d{4}-\d{2}-\d{2}', published) else None
    data = {
        'tags': _split_names(book.get('tags')) if book.get('tags') else [],
        'chapters': [],
        'title': _text(book.get('title')) or 'Névtelen könyv',
        'subtitle': _text(book.get('subtitle')) or None,
        'authors': _split_names(book.get('author')),
        'narrators': _split_names(book.get('narrator')),
        'series': series_entry,
        'genres': _split_names(book.get('genres')) if book.get('genres') else [],
        'publishedYear': _year(published),
        'publishedDate': date,
        'publisher': _text(book.get('publisher')) or None,
        'description': _text(book.get('description')) or None,
        'isbn': _text(book.get('isbn')) or None,
        'asin': _text(book.get('asin')) or None,
        'language': _text(book.get('language')) or None,
        'explicit': bool(book.get('explicit', False)),
        'abridged': bool(book.get('abridged', False)),
    }
    for index, chapter in enumerate(chapters or []):
        data['chapters'].append({'id': index, 'start': round(float(chapter['start']), 3),
                                 'end': round(float(chapter['end']), 3),
                                 'title': _text(chapter.get('title')) or f'{index + 1}. fejezet'})
    return data


def _resolve_chapter_times(chapters, audio_files) -> list[dict]:
    if not chapters:
        return []
    if all('start' in c and 'end' in c for c in chapters):
        return [{'title': c.get('title'), 'start': float(c['start']), 'end': float(c['end'])}
                for c in chapters]
    if len(chapters) == len(audio_files) and len(audio_files) > 1:
        result, cursor = [], 0.0
        for chapter, path in zip(chapters, audio_files):
            length = audio_duration(path)
            result.append({'title': chapter.get('title'), 'start': cursor, 'end': cursor + length})
            cursor += length
        return result
    return []


def build_audiobookshelf_folder(book: dict, audio_files, dest_root, *, chapters=None,
                                overwrite: bool = False) -> str:
    """Create an Audiobookshelf-ready book folder below ``dest_root``.

    ``audio_files`` is a list of chapter audio files (copied as
    ``NN - Title.ext`` in the given order) or a single M4B/MP3.  ``chapters``
    (optional) is a list of ``{title, start, end}`` (seconds) or, for one file
    per chapter, a list of ``{title}`` whose timing is computed from the files.
    Writes ``cover.jpg``, ``desc.txt``, ``reader.txt`` and ``metadata.json``.
    The folder is staged and renamed atomically. Returns the folder path.
    """
    book = dict(book or {})
    files = [Path(p) for p in (audio_files or [])]
    if not files:
        raise ValueError('Nincs feltölthető hangfájl.')
    for path in files:
        if not path.is_file():
            raise ValueError(f'A hangfájl nem található: {path.name}')
    root = Path(dest_root)
    target = root.joinpath(*_abs_folder_parts(book))
    if target.exists() and not overwrite:
        raise ValueError(f'A célmappa már létezik: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.abs-', dir=target.parent))
    try:
        title = _abs_component(book.get('title')) or 'Konyv'
        width = max(2, len(str(len(files))))
        for number, source in enumerate(files, 1):
            if len(files) == 1:
                name = title + source.suffix.lower()
            else:
                label = title
                if chapters and len(chapters) == len(files):
                    label = _abs_component(chapters[number - 1].get('title')) or title
                name = f'{number:0{width}d} - {label}{source.suffix.lower()}'
            shutil.copy2(source, stage / name)
        cover = _decode_cover(book.get('cover_b64'))
        if cover:
            if cover[:3] == b'\xff\xd8\xff':
                (stage / 'cover.jpg').write_bytes(cover)
            else:
                from PIL import Image
                try:
                    with Image.open(io.BytesIO(cover)) as picture:
                        picture.convert('RGB').save(stage / 'cover.jpg', 'JPEG', quality=92)
                except Exception:
                    raise ValueError('A borítókép formátuma nem támogatott.') from None
        if _text(book.get('description')):
            (stage / 'desc.txt').write_text(_text(book.get('description')) + '\n', encoding='utf-8')
        if _text(book.get('narrator')):
            (stage / 'reader.txt').write_text(_text(book.get('narrator')) + '\n', encoding='utf-8')
        metadata = audiobookshelf_metadata(book, _resolve_chapter_times(chapters, files))
        (stage / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n',
                                             encoding='utf-8')
        if target.exists():
            backup = target.with_name(target.name + '.old-' + uuid.uuid4().hex[:8])
            os.replace(target, backup)
            os.replace(stage, target)
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(stage, target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return str(target)


class _Server:
    def __init__(self, server_url: str, api_token: str, timeout: float):
        parts = urlsplit(str(server_url or '').strip())
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('Érvénytelen Audiobookshelf-szervercím (http:// vagy https:// szükséges).')
        if not str(api_token or '').strip():
            raise ValueError('Hiányzik az Audiobookshelf API-token.')
        self.scheme, self.host = parts.scheme, parts.hostname
        self.port = parts.port
        self.base = parts.path.rstrip('/')
        self.token = str(api_token).strip()
        self.timeout = timeout

    def connection(self):
        if self.scheme == 'https':
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def headers(self, extra=None):
        return {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json',
                'User-Agent': 'Auris', **(extra or {})}

    def request(self, method, path, body_parts=None, headers=None, *, redirects=3):
        """Send a request; ``body_parts`` is a list of bytes or Path objects."""
        url_path = self.base + path
        try:
            conn = self.connection()
            try:
                conn.putrequest(method, url_path)
                for key, value in self.headers(headers).items():
                    conn.putheader(key, value)
                conn.endheaders()
                for part in body_parts or []:
                    if isinstance(part, Path):
                        with open(part, 'rb') as handle:
                            while chunk := handle.read(1024 * 1024):
                                conn.send(chunk)
                    else:
                        conn.send(part)
                response = conn.getresponse()
                status, payload = response.status, response.read(4 * 1024 * 1024)
                location = response.getheader('Location')
            finally:
                conn.close()
        except ssl.SSLError as exc:
            raise ValueError(f'TLS/SSL-hiba az Audiobookshelf-kapcsolatban: {exc}') from None
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError(f'Nem sikerült kapcsolódni az Audiobookshelf-szerverhez '
                             f'({self.host}): {exc}') from None
        if status in (301, 302, 303, 307, 308):
            target = urlsplit(urljoin(f'{self.scheme}://{self.host}{url_path}', location or ''))
            if target.hostname != self.host:
                raise ValueError('Az Audiobookshelf-szerver másik gépre irányít át; '
                                 'biztonsági okból ezt nem követjük. Ellenőrizd a szervercímet.')
            if method != 'GET' or redirects <= 0:
                raise ValueError('Az Audiobookshelf-szerver átirányít '
                                 f'({target.scheme}://{target.netloc}); add meg közvetlenül ezt a címet.')
            self.scheme, self.port = target.scheme, target.port
            new_path = target.path
            if new_path.endswith(path):
                self.base = new_path[:-len(path)].rstrip('/')
            return self.request(method, path, body_parts, headers, redirects=redirects - 1)
        return status, payload

    @staticmethod
    def check(status, payload, what: str):
        if 200 <= status < 300:
            return
        detail = payload.decode('utf-8', errors='replace').strip()[:300]
        messages = {
            400: f'Hibás kérés ({what}): {detail or "a szerver elutasította"}.',
            401: 'Az Audiobookshelf API-token érvénytelen vagy lejárt.',
            403: 'A felhasználónak nincs jogosultsága ehhez a művelethez '
                 '(feltöltési/könyvtár-hozzáférési jog szükséges).',
            404: f'Nem található ({what}): {detail or "ismeretlen könyvtár vagy mappa"}.',
            413: 'A feltöltés túl nagy a szerver (vagy a fordított proxy) számára.',
        }
        raise ValueError(messages.get(status, f'Audiobookshelf-hiba ({what}, HTTP {status}): {detail}'))


def list_libraries(server_url: str, api_token: str, *, timeout: float = 30) -> list[dict]:
    """Return ``[{id, name, mediaType, folders: [{id, fullPath}]}]`` via GET /api/libraries."""
    server = _Server(server_url, api_token, timeout)
    status, payload = server.request('GET', '/api/libraries')
    server.check(status, payload, 'könyvtárak lekérése')
    try:
        data = json.loads(payload.decode('utf-8'))
    except ValueError:
        raise ValueError('Az Audiobookshelf-szerver válasza nem értelmezhető JSON.') from None
    libraries = data.get('libraries', data) if isinstance(data, dict) else data
    if not isinstance(libraries, list):
        raise ValueError('Az Audiobookshelf-szerver válasza nem értelmezhető.')
    return [{'id': lib.get('id'), 'name': lib.get('name'), 'mediaType': lib.get('mediaType'),
             'folders': [{'id': f.get('id'), 'fullPath': f.get('fullPath')}
                         for f in lib.get('folders') or []]}
            for lib in libraries if isinstance(lib, dict)]


def _form_field(boundary: str, name: str, value: str) -> bytes:
    return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f'{value}\r\n').encode('utf-8')


def upload_to_audiobookshelf(folder, server_url: str, api_token: str, library_id: str, *,
                             folder_id: str | None = None, timeout: float = 600) -> dict:
    """Upload a folder built by :func:`build_audiobookshelf_folder` (POST /api/upload).

    The multipart body is streamed from disk (fields ``title``, ``author``,
    ``series``, ``library``, ``folder`` and files ``0``, ``1``, …).  ABS stores
    the files under ``<library folder>/<author>/<series>/<title>``; the local
    leaf folder name is sent as ``title`` so ``Vol N - YEAR - Title {Narrator}``
    is preserved.  When ``folder_id`` is omitted the library's first folder is
    used.  Raises ``ValueError`` with a Hungarian message on failure.
    """
    source = Path(folder)
    if not source.is_dir():
        raise ValueError(f'A feltöltendő mappa nem található: {source}')
    files = sorted((p for p in source.iterdir() if p.is_file() and not p.name.startswith('.')),
                   key=lambda p: p.name)
    if not files:
        raise ValueError('A feltöltendő mappa üres.')
    if not str(library_id or '').strip():
        raise ValueError('Hiányzik az Audiobookshelf-könyvtár azonosítója.')
    server = _Server(server_url, api_token, timeout)
    if not folder_id:
        libraries = list_libraries(server_url, api_token, timeout=min(timeout, 60))
        library = next((lib for lib in libraries if lib['id'] == library_id), None)
        if library is None:
            raise ValueError('A megadott Audiobookshelf-könyvtár nem található.')
        if not library['folders']:
            raise ValueError('Az Audiobookshelf-könyvtárnak nincs mappája.')
        folder_id = library['folders'][0]['id']

    author, series = '', ''
    meta_path = source / 'metadata.json'
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            author = ', '.join(meta.get('authors') or [])
            series = re.sub(r' #[^#\s]+$', '', (meta.get('series') or [''])[0] or '')
        except (ValueError, OSError, TypeError, AttributeError):
            pass
    fields = {'title': source.name, 'author': author, 'series': series,
              'library': str(library_id), 'folder': str(folder_id)}

    boundary = 'AurisBoundary' + uuid.uuid4().hex
    parts: list[bytes | Path] = [_form_field(boundary, k, v) for k, v in fields.items() if v]
    for index, path in enumerate(files):
        media_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        # Like browsers, send the raw UTF-8 file name; ABS configures
        # express-fileupload with defParamCharset 'utf8', so accents survive.
        safe_name = re.sub(r'[\r\n"]', '_', path.name)
        disposition = f'form-data; name="{index}"; filename="{safe_name}"'
        parts.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'
                     f'Content-Type: {media_type}\r\n\r\n'.encode('utf-8'))
        parts.append(path)
        parts.append(b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode('ascii'))
    length = sum(p.stat().st_size if isinstance(p, Path) else len(p) for p in parts)
    status, payload = server.request('POST', '/api/upload', parts, {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(length)})
    server.check(status, payload, 'feltöltés')
    return {'ok': True, 'status': status, 'library_id': str(library_id), 'folder_id': str(folder_id),
            'title': fields['title'], 'author': author or None, 'series': series or None,
            'files': [p.name for p in files], 'bytes': sum(p.stat().st_size for p in files)}


# ── 4. Retail sample ──────────────────────────────────────────────────────────

def retail_sample(input_audio, output_mp3, *, start_sec: float = 0.0, duration_sec: float = 300) -> str:
    """Cut a faded (1 s in/out) 192 kbps MP3 excerpt from ``input_audio``."""
    source = Path(input_audio)
    if not source.is_file():
        raise ValueError(f'A hangfájl nem található: {source.name}')
    start, duration = float(start_sec), float(duration_sec)
    if not math.isfinite(start) or start < 0:
        raise ValueError('A minta kezdete nem lehet negatív.')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('A minta hossza legyen pozitív.')
    total = audio_duration(source)
    if start >= total:
        raise ValueError('A minta kezdete a hangfájl végén túl van.')
    length = min(duration, total - start)
    fade = min(1.0, length / 2)
    target, temp = _atomic_target(output_mp3)
    try:
        _run_ffmpeg(['-ss', f'{start:.3f}', '-t', f'{length:.3f}', '-i', str(source), '-vn',
                     '-map_metadata', '-1',
                     '-af', f'afade=t=in:st=0:d={fade:.3f},afade=t=out:st={length - fade:.3f}:d={fade:.3f}',
                     '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '192k', '-f', 'mp3', str(temp)],
                    'Mintarészlet készítése')
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return str(target)
