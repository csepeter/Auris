import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import publish  # noqa: E402

SR = 24_000
HAS_FFMPEG = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None
NS = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'smil': 'http://www.w3.org/ns/SMIL',
    'epub': 'http://www.idpf.org/2007/ops',
    'x': 'http://www.w3.org/1999/xhtml',
}


def write_wav(path, seconds, value=0.1, rate=SR):
    frames = int(round(seconds * rate))
    sf.write(str(path), np.full(frames, value, dtype=np.float32), rate, subtype='PCM_16')
    return str(path)


def png_b64(color=(200, 30, 30), fmt='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', (40, 60), color).save(buffer, fmt)
    return base64.b64encode(buffer.getvalue()).decode()


def sample_book(**overrides):
    book = {
        'title': 'Egri csillagok – „próba” <&>', 'author': 'Gárdonyi Géza', 'language': 'hu',
        'narrator': 'Kovács Anna', 'description': 'Leírás „idézettel” & jelekkel',
        'publisher': 'Auris Kiadó', 'published': '1901', 'series': 'Klasszikusok',
        'series_index': '2', 'cover_b64': png_b64(), 'identifier': None,
    }
    book.update(overrides)
    return book


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ── EPUB 3 Media Overlays ─────────────────────────────────────────────────────

class EpubMediaOverlayTests(TempDirTest):
    def chapters(self, audio1, audio2):
        return [
            {'title': '1. fejezet', 'audio_path': audio1, 'segments': [
                {'text': 'Első fejezet', 't_start': 0.0, 't_end': 1.0,
                 'block_kind': 'heading', 'ends_paragraph': True},
                {'text': '„Hol volt, hol nem volt” – mondta.', 't_start': 1.2, 't_end': 2.5,
                 'block_kind': 'paragraph', 'ends_paragraph': False},
                {'text': 'Második mondat & <valami>.', 't_start': 2.6, 't_end': 3.9004,
                 'block_kind': 'paragraph', 'ends_paragraph': True},
                {'text': 'Alcím', 't_start': 4.0, 't_end': 4.5,
                 'block_kind': 'subheading', 'ends_paragraph': True},
                {'text': 'Új bekezdés.', 't_start': 4.6, 't_end': 5.8,
                 'block_kind': None, 'ends_paragraph': True},
            ]},
            {'title': '2. fejezet', 'audio_path': audio2, 'segments': [
                {'text': 'Nincs címsor.', 't_start': 0.1, 't_end': 1.9,
                 'block_kind': 'paragraph', 'ends_paragraph': False},
                {'text': 'Még egy.', 't_start': 2.0, 't_end': 3.5,
                 'block_kind': 'paragraph', 'ends_paragraph': True},
            ]},
            {'title': 'Függelék', 'audio_path': None, 'segments': [
                {'text': 'Csak szöveg.', 'block_kind': 'paragraph', 'ends_paragraph': True},
            ]},
        ]

    def fake_mp3(self, name):
        # MP3 input is copied verbatim, so real encoding is not needed here.
        path = self.tmp / name
        path.write_bytes(b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\xff\xfb' * 64)
        return str(path)

    def build(self, **book_overrides):
        output = self.tmp / 'out' / 'book.epub'
        chapters = self.chapters(self.fake_mp3('c1.mp3'), self.fake_mp3('c2.mp3'))
        result = publish.build_epub3_media_overlay(sample_book(**book_overrides), chapters, output)
        self.assertEqual(result, str(output))
        return zipfile.ZipFile(output)

    def test_container_layout_and_mimetype_first_and_stored(self):
        with self.build() as epub:
            first = epub.infolist()[0]
            self.assertEqual(first.filename, 'mimetype')
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(first.extra, b'')
            self.assertEqual(epub.read('mimetype'), b'application/epub+zip')
            container = ET.fromstring(epub.read('META-INF/container.xml'))
            rootfile = container.find('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile')
            self.assertEqual(rootfile.get('full-path'), 'OEBPS/content.opf')
            names = set(epub.namelist())
            for name in ('OEBPS/nav.xhtml', 'OEBPS/style.css', 'OEBPS/chap01.xhtml',
                         'OEBPS/chap01.smil', 'OEBPS/chap02.smil', 'OEBPS/chap03.xhtml',
                         'OEBPS/audio/chap01.mp3', 'OEBPS/audio/chap02.mp3', 'OEBPS/cover.png'):
                self.assertIn(name, names)
            self.assertNotIn('OEBPS/chap03.smil', names)
            self.assertIn('.-epub-media-overlay-active { background: #ffe89a;',
                          epub.read('OEBPS/style.css').decode())
        self.assertEqual([p.name for p in (self.tmp / 'out').iterdir()], ['book.epub'])

    def test_opf_metadata_manifest_and_durations(self):
        with self.build() as epub:
            opf = ET.fromstring(epub.read('OEBPS/content.opf'))
        self.assertEqual(opf.get('version'), '3.0')
        meta = opf.find('opf:metadata', NS)
        self.assertEqual(meta.find('dc:title', NS).text, 'Egri csillagok – „próba” <&>')
        self.assertEqual(meta.find('dc:creator', NS).text, 'Gárdonyi Géza')
        self.assertEqual(meta.find('dc:language', NS).text, 'hu')
        identifier = meta.find('dc:identifier', NS)
        self.assertEqual(identifier.get('id'), opf.get('unique-identifier'))
        self.assertRegex(identifier.text, r'^urn:uuid:[0-9a-f-]{36}$')
        props = {}
        for item in meta.findall('opf:meta', NS):
            props.setdefault((item.get('property'), item.get('refines')), []).append(item.text)
        self.assertRegex(props[('dcterms:modified', None)][0], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(props[('media:narrator', None)], ['Kovács Anna'])
        self.assertEqual(props[('media:active-class', None)], ['-epub-media-overlay-active'])
        # chapter 1: 1.0 + 1.3 + 1.3 + 0.5 + 1.2 = 5.3 s; chapter 2: 1.8 + 1.5 = 3.3 s
        self.assertEqual(props[('media:duration', '#mo-chap01')], ['0:00:05.300'])
        self.assertEqual(props[('media:duration', '#mo-chap02')], ['0:00:03.300'])
        self.assertEqual(props[('media:duration', None)], ['0:00:08.600'])
        self.assertEqual(props[('belongs-to-collection', None)], ['Klasszikusok'])
        self.assertEqual(props[('group-position', '#series')], ['2'])

        items = {i.get('id'): i for i in opf.find('opf:manifest', NS)}
        self.assertEqual(items['chap01'].get('media-overlay'), 'mo-chap01')
        self.assertEqual(items['chap02'].get('media-overlay'), 'mo-chap02')
        self.assertIsNone(items['chap03'].get('media-overlay'))
        self.assertEqual(items['mo-chap01'].get('media-type'), 'application/smil+xml')
        self.assertEqual(items['audio-chap01'].get('media-type'), 'audio/mpeg')
        self.assertEqual(items['cover-image'].get('properties'), 'cover-image')
        self.assertEqual(items['nav'].get('properties'), 'nav')
        spine = [i.get('idref') for i in opf.find('opf:spine', NS)]
        self.assertEqual(spine, ['cover', 'chap01', 'chap02', 'chap03'])

    def test_smil_references_existing_spans_with_three_decimal_clips(self):
        with self.build() as epub:
            smil = ET.fromstring(epub.read('OEBPS/chap01.smil'))
            xhtml = ET.fromstring(epub.read('OEBPS/chap01.xhtml'))
        self.assertEqual(smil.get('version'), '3.0')
        seq = smil.find('smil:body/smil:seq', NS)
        self.assertEqual(seq.get('{%s}textref' % NS['epub']), 'chap01.xhtml')
        span_ids = {el.get('id') for el in xhtml.iter('{%s}span' % NS['x'])}
        pars = seq.findall('smil:par', NS)
        self.assertEqual(len(pars), 5)
        self.assertEqual(len({p.get('id') for p in pars}), 5)
        expected = [('0.000s', '1.000s'), ('1.200s', '2.500s'), ('2.600s', '3.900s'),
                    ('4.000s', '4.500s'), ('4.600s', '5.800s')]
        for par, (begin, end) in zip(pars, expected):
            text = par.find('smil:text', NS).get('src')
            audio = par.find('smil:audio', NS)
            doc, fragment = text.split('#')
            self.assertEqual(doc, 'chap01.xhtml')
            self.assertIn(fragment, span_ids)
            self.assertEqual(audio.get('src'), 'audio/chap01.mp3')
            self.assertEqual((audio.get('clipBegin'), audio.get('clipEnd')), (begin, end))
            self.assertRegex(audio.get('clipEnd'), r'^\d+\.\d{3}s$')

    def test_xhtml_groups_segments_into_headings_and_paragraphs(self):
        with self.build() as epub:
            xhtml = ET.fromstring(epub.read('OEBPS/chap01.xhtml'))
            second = ET.fromstring(epub.read('OEBPS/chap02.xhtml'))
            nav = ET.fromstring(epub.read('OEBPS/nav.xhtml'))
        self.assertEqual(xhtml.get('{http://www.w3.org/XML/1998/namespace}lang'), 'hu')
        section = xhtml.find('x:body/x:section', NS)
        blocks = [(el.tag.split('}')[1], [s.get('id') for s in el]) for el in section]
        self.assertEqual(blocks, [('h1', ['s0001']), ('p', ['s0002', 's0003']),
                                  ('h2', ['s0004']), ('p', ['s0005'])])
        paragraph = section.findall('x:p', NS)[0]
        self.assertEqual(''.join(paragraph.itertext()),
                         '„Hol volt, hol nem volt” – mondta. Második mondat & <valami>.')
        # Chapters without a heading segment get the chapter title as <h1>.
        first = second.find('x:body/x:section', NS)[0]
        self.assertEqual((first.tag, first.text), ('{%s}h1' % NS['x'], '2. fejezet'))
        toc = nav.find('.//x:nav', NS)
        self.assertEqual(toc.get('{%s}type' % NS['epub']), 'toc')
        self.assertEqual([a.get('href') for a in toc.iter('{%s}a' % NS['x'])],
                         ['chap01.xhtml', 'chap02.xhtml', 'chap03.xhtml'])

    def test_shared_audio_file_is_included_once(self):
        mp3 = self.fake_mp3('book.mp3')
        chapters = self.chapters(mp3, mp3)
        output = self.tmp / 'shared.epub'
        publish.build_epub3_media_overlay(sample_book(cover_b64=None, narrator=''), chapters, output)
        with zipfile.ZipFile(output) as epub:
            audio = [n for n in epub.namelist() if n.startswith('OEBPS/audio/')]
            self.assertEqual(audio, ['OEBPS/audio/chap01.mp3'])
            smil2 = ET.fromstring(epub.read('OEBPS/chap02.smil'))
            opf = epub.read('OEBPS/content.opf').decode()
        self.assertEqual(smil2.find('.//smil:audio', NS).get('src'), 'audio/chap01.mp3')
        self.assertNotIn('media:narrator', opf)
        self.assertNotIn('cover-image', opf)

    def test_identifier_and_invalid_input(self):
        with self.build(identifier='3F2504E0-4F89-11D3-9A0C-0305E82C3301') as epub:
            self.assertIn(b'urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301', epub.read('OEBPS/content.opf'))
        with self.assertRaises(ValueError):
            publish.build_epub3_media_overlay(sample_book(), [], self.tmp / 'x.epub')
        output = self.tmp / 'missing.epub'
        with self.assertRaises(ValueError):
            publish.build_epub3_media_overlay(
                sample_book(), [{'title': 'A', 'audio_path': str(self.tmp / 'nope.mp3'), 'segments': []}], output)
        self.assertFalse(output.exists())
        self.assertEqual([p for p in self.tmp.iterdir() if p.name.startswith('.')], [])
        with self.assertRaises(ValueError):
            publish.build_epub3_media_overlay(sample_book(cover_b64='not base64!'),
                                              self.chapters(None, None), self.tmp / 'c.epub')

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg nem érhető el')
    def test_wav_audio_is_transcoded_to_mp3(self):
        wav = write_wav(self.tmp / 'c1.wav', 6.0)
        output = self.tmp / 'wav.epub'
        publish.build_epub3_media_overlay(sample_book(), self.chapters(wav, wav), output)
        with zipfile.ZipFile(output) as epub:
            self.assertIn('OEBPS/audio/chap01.mp3', epub.namelist())
            self.assertFalse(any(n.endswith('.wav') for n in epub.namelist()))
            data = epub.read('OEBPS/audio/chap01.mp3')
            opf = ET.fromstring(epub.read('OEBPS/content.opf'))
        extracted = self.tmp / 'x.mp3'
        extracted.write_bytes(data)
        self.assertAlmostEqual(publish.audio_duration(extracted), 6.0, delta=0.2)
        types = {i.get('href'): i.get('media-type') for i in opf.find('opf:manifest', NS)}
        self.assertEqual(types['audio/chap01.mp3'], 'audio/mpeg')


# ── DAW package ───────────────────────────────────────────────────────────────

class DawPackageTests(TempDirTest):
    def segments(self):
        return [
            {'text': 'A narrátor kezdi a mondatot, és ez egy nagyon hosszú mondat lesz, '
                     'hogy a címke levágását is ellenőrizzük.',
             't_start': 0.0, 't_end': 1.0, 'character_name': None,
             'audio_path': write_wav(self.tmp / 's1.wav', 1.0, 0.25)},
            {'text': '„Jó napot!”', 't_start': 1.5, 't_end': 2.0, 'character_name': 'Kis/Pista:',
             'audio_path': write_wav(self.tmp / 's2.wav', 0.5, 0.5)},
            {'text': 'Folytatja.', 't_start': 2.5, 't_end': 3.0, 'character_name': '',
             'audio_path': write_wav(self.tmp / 's3.wav', 0.5, 0.125)},
        ]

    def test_package_contents_tracks_labels_and_lof(self):
        output = self.tmp / 'daw' / 'chapter.zip'
        result = publish.build_daw_package('1. fejezet: A kezdet', self.segments(), output)
        self.assertEqual(result, str(output))
        extract = self.tmp / 'extract'
        with zipfile.ZipFile(output) as archive:
            infos = {i.filename: i for i in archive.infolist()}
            folder = '1. fejezet A kezdet'
            self.assertEqual(set(infos), {f'{folder}/Narrátor.wav', f'{folder}/KisPista.wav',
                                          f'{folder}/mix.wav', f'{folder}/labels.txt',
                                          f'{folder}/{folder}.lof'})
            for name, info in infos.items():
                if name.endswith('.wav'):
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            archive.extractall(extract)
        base = extract / folder
        narrator, rate = sf.read(base / 'Narrátor.wav', dtype='float32')
        pista, _ = sf.read(base / 'KisPista.wav', dtype='float32')
        mix, _ = sf.read(base / 'mix.wav', dtype='float32')
        info = sf.info(str(base / 'mix.wav'))
        self.assertEqual((rate, info.channels, info.subtype), (SR, 1, 'PCM_16'))
        self.assertEqual(len(narrator), 3 * SR)
        self.assertEqual(len(pista), 3 * SR)
        self.assertEqual(len(mix), 3 * SR)
        tol = 1e-3
        self.assertAlmostEqual(float(narrator[SR // 2]), 0.25, delta=tol)
        self.assertAlmostEqual(float(narrator[int(1.7 * SR)]), 0.0, delta=tol)
        self.assertAlmostEqual(float(narrator[int(2.7 * SR)]), 0.125, delta=tol)
        self.assertAlmostEqual(float(pista[int(1.4 * SR)]), 0.0, delta=tol)
        self.assertAlmostEqual(float(pista[int(1.7 * SR)]), 0.5, delta=tol)
        np.testing.assert_allclose(mix, np.clip(narrator + pista, -1, 1), atol=tol)

        labels = (base / 'labels.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(labels), 3)
        start, end, label = labels[0].split('\t')
        self.assertEqual((start, end), ('0.000000', '1.000000'))
        self.assertTrue(label.startswith('Narrátor: A narrátor kezdi'))
        self.assertEqual(len(label), len('Narrátor: ') + 60)
        self.assertEqual(labels[1].split('\t'), ['1.500000', '2.000000', 'Kis/Pista:: „Jó napot!”'])
        lof = (base / f'{folder}.lof').read_text(encoding='utf-8').splitlines()
        self.assertEqual(lof, ['file "Narrátor.wav" offset 0', 'file "KisPista.wav" offset 0'])

    def test_duplicate_sanitized_names_and_mix_name_are_disambiguated(self):
        segments = self.segments()
        segments[1]['character_name'] = 'mix'
        segments.append({'text': 'x', 't_start': 3.0, 't_end': 3.2, 'character_name': 'Mix?',
                         'audio_path': write_wav(self.tmp / 's4.wav', 0.2)})
        output = self.tmp / 'd.zip'
        publish.build_daw_package('Fejezet', segments, output)
        with zipfile.ZipFile(output) as archive:
            names = sorted(archive.namelist())
            lof = archive.read('Fejezet/Fejezet.lof').decode('utf-8')
        self.assertEqual(names, ['Fejezet/Fejezet.lof', 'Fejezet/Mix (3).wav', 'Fejezet/Narrátor.wav', 'Fejezet/labels.txt',
                                 'Fejezet/mix (2).wav', 'Fejezet/mix.wav'])
        self.assertNotIn('file "mix.wav"', lof)

    def test_audio_longer_than_timing_is_not_truncated(self):
        segment = {'text': 'x', 't_start': 0.5, 't_end': 1.0, 'character_name': None,
                   'audio_path': write_wav(self.tmp / 'long.wav', 1.0, 0.3)}
        output = self.tmp / 'long.zip'
        publish.build_daw_package('F', [segment], output)
        with zipfile.ZipFile(output) as archive:
            data = archive.read('F/Narrátor.wav')
        audio, _ = sf.read(io.BytesIO(data), dtype='float32')
        self.assertEqual(len(audio), int(1.5 * SR))

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            publish.build_daw_package('F', [], self.tmp / 'a.zip')
        with self.assertRaises(ValueError):
            publish.build_daw_package('F', [{'text': 'x', 't_start': 0, 't_end': 1,
                                             'audio_path': str(self.tmp / 'nope.wav')}], self.tmp / 'b.zip')
        wrong = write_wav(self.tmp / 'w.wav', 0.5, rate=22050)
        output = self.tmp / 'c.zip'
        with self.assertRaises(ValueError):
            publish.build_daw_package('F', [{'text': 'x', 't_start': 0, 't_end': 0.5,
                                             'audio_path': wrong}], output)
        self.assertFalse(output.exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith('.')], [])


# ── Audiobookshelf ────────────────────────────────────────────────────────────

class AudiobookshelfFolderTests(TempDirTest):
    def test_folder_names(self):
        j = os.path.join
        self.assertEqual(publish.audiobookshelf_folder_name(sample_book()),
                         j('Gárdonyi Géza', 'Klasszikusok',
                           'Vol 2 - 1901 - Egri csillagok – „próba” & {Kovács Anna}'))
        self.assertEqual(publish.audiobookshelf_folder_name(
            {'title': 'Cím', 'author': 'Szerző'}), j('Szerző', 'Cím'))
        self.assertEqual(publish.audiobookshelf_folder_name(
            {'title': 'Cím', 'author': 'Szerző', 'series_index': '3', 'published': '2020-05-01'}),
            j('Szerző', '2020 - Cím'))
        self.assertEqual(publish.audiobookshelf_folder_name(
            {'title': 'Cím', 'author': 'Szerző', 'series': 'Sorozat', 'series_index': '1a'}),
            j('Szerző', 'Sorozat', 'Cím'))
        self.assertEqual(publish.audiobookshelf_folder_name(
            {'title': 'Cím', 'author': 'Szerző', 'series': 'Sorozat', 'series_index': '1.50'}),
            j('Szerző', 'Sorozat', 'Vol 1.5 - Cím'))
        # Without an author ABS would read the series folder as the author.
        self.assertEqual(publish.audiobookshelf_folder_name(
            {'title': 'Cím', 'series': 'Sorozat', 'series_index': '1'}), 'Cím')

    def test_folder_name_is_windows_safe(self):
        name = publish.audiobookshelf_folder_name({
            'title': 'Mi? Ez: <a> "b" | c* / d \\ e...', 'author': 'CON',
            'narrator': 'Nagy {Béla}. '})
        author, leaf = name.split(os.sep)
        self.assertEqual(author, 'CON_')
        self.assertFalse(re.search(r'[<>:"/\\|?*]', leaf))
        self.assertEqual(leaf, 'Mi Ez - a b c d e {Nagy (Béla)}')
        long_name = publish.audiobookshelf_folder_name({'title': 'á' * 400, 'author': 'X',
                                                        'narrator': 'Olvasó'})
        leaf = long_name.split(os.sep)[-1]
        self.assertLessEqual(len(leaf), 120)
        self.assertTrue(leaf.endswith(' {Olvasó}'))

    def test_metadata_json_schema(self):
        data = publish.audiobookshelf_metadata(
            sample_book(author='Első Szerző; Második Szerző', published='1901-03-04'),
            [{'title': 'Egy', 'start': 0, 'end': 12.3456}])
        self.assertEqual(data['title'], 'Egri csillagok – „próba” <&>')
        self.assertEqual(data['authors'], ['Első Szerző', 'Második Szerző'])
        self.assertEqual(data['narrators'], ['Kovács Anna'])
        self.assertEqual(data['series'], ['Klasszikusok #2'])
        self.assertEqual(data['publishedYear'], '1901')
        self.assertEqual(data['publishedDate'], '1901-03-04')
        self.assertEqual(data['publisher'], 'Auris Kiadó')
        self.assertEqual(data['language'], 'hu')
        self.assertEqual(data['chapters'], [{'id': 0, 'start': 0.0, 'end': 12.346, 'title': 'Egy'}])
        for key in ('tags', 'genres', 'subtitle', 'isbn', 'asin', 'explicit', 'abridged', 'description'):
            self.assertIn(key, data)
        self.assertEqual(publish.audiobookshelf_metadata({'title': 'T', 'series': 'S'})['series'], ['S'])

    def test_build_folder_with_chapter_files(self):
        files = [write_wav(self.tmp / 'a.wav', 1.0), write_wav(self.tmp / 'b.wav', 2.0)]
        root = self.tmp / 'library'
        folder = publish.build_audiobookshelf_folder(
            sample_book(), files, root, chapters=[{'title': 'Első: rész'}, {'title': 'Második'}])
        folder = Path(folder)
        self.assertEqual(folder, root / publish.audiobookshelf_folder_name(sample_book()))
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['01 - Első - rész.wav', '02 - Második.wav', 'cover.jpg', 'desc.txt',
                          'metadata.json', 'reader.txt'])
        with Image.open(folder / 'cover.jpg') as cover:
            self.assertEqual(cover.format, 'JPEG')
        self.assertEqual((folder / 'reader.txt').read_text(encoding='utf-8').strip(), 'Kovács Anna')
        self.assertIn('„idézettel”', (folder / 'desc.txt').read_text(encoding='utf-8'))
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        self.assertEqual([(c['start'], c['end']) for c in meta['chapters']], [(0.0, 1.0), (1.0, 3.0)])
        self.assertEqual(meta['chapters'][0]['title'], 'Első: rész')
        with self.assertRaises(ValueError):
            publish.build_audiobookshelf_folder(sample_book(), files, root)
        again = publish.build_audiobookshelf_folder(sample_book(description=''), files[:1], root,
                                                    overwrite=True)
        self.assertEqual(Path(again), folder)
        names = sorted(p.name for p in folder.iterdir())
        self.assertNotIn('desc.txt', names)
        self.assertIn('Egri csillagok – „próba” &.wav', names)
        self.assertEqual(len(list(folder.parent.iterdir())), 1)

    def test_build_folder_single_file_and_errors(self):
        m4b = self.tmp / 'book.m4b'
        m4b.write_bytes(b'fake')
        jpeg = png_b64(fmt='JPEG')
        folder = Path(publish.build_audiobookshelf_folder(
            {'title': 'Cím', 'author': 'Szerző', 'cover_b64': jpeg}, [m4b], self.tmp / 'lib',
            chapters=[{'title': 'A', 'start': 0, 'end': 5}, {'title': 'B', 'start': 5, 'end': 9}]))
        self.assertEqual((folder / 'cover.jpg').read_bytes(), base64.b64decode(jpeg))
        self.assertTrue((folder / 'Cím.m4b').is_file())
        self.assertFalse((folder / 'reader.txt').exists())
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        self.assertEqual([c['title'] for c in meta['chapters']], ['A', 'B'])
        with self.assertRaises(ValueError):
            publish.build_audiobookshelf_folder({'title': 'X'}, [], self.tmp / 'lib')
        with self.assertRaises(ValueError):
            publish.build_audiobookshelf_folder({'title': 'X'}, [self.tmp / 'nope.mp3'], self.tmp / 'lib')


class _AbsHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        server.requests.append(('GET', self.path, dict(self.headers), b''))
        if self.path == '/abs/redirect/api/libraries':
            self.send_response(302)
            self.send_header('Location', 'http://evil.example.com/api/libraries')
            self.end_headers()
            return
        if self.headers.get('Authorization') != 'Bearer good-token':
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'Unauthorized')
            return
        body = json.dumps({'libraries': [
            {'id': 'lib1', 'name': 'Hangoskönyvek', 'mediaType': 'book',
             'folders': [{'id': 'fold1', 'fullPath': '/audiobooks', 'libraryId': 'lib1'}]},
            {'id': 'lib2', 'name': 'Üres', 'mediaType': 'book', 'folders': []},
        ]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers['Content-Length'])
        body = self.rfile.read(length)
        self.server.requests.append(('POST', self.path, dict(self.headers), body))
        if self.headers.get('Authorization') != 'Bearer good-token':
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')


def parse_multipart(content_type, body):
    boundary = content_type.split('boundary=')[1].encode()
    fields, files = {}, {}
    for part in body.split(b'--' + boundary)[1:]:
        if part.startswith(b'--'):
            break
        head, _, data = part[2:].partition(b'\r\n\r\n')
        data = data[:-2]
        disposition = head.decode('utf-8').split('\r\n')[0]
        name = re.search(r' name="([^"]*)"', disposition).group(1)
        filename = re.search(r'filename="([^"]*)"', disposition)
        if filename:
            files[name] = (filename.group(1), data)
        else:
            fields[name] = data.decode('utf-8')
    return fields, files


class AudiobookshelfApiTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _AbsHandler)
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/abs/'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_list_libraries(self):
        libraries = publish.list_libraries(self.url, 'good-token')
        self.assertEqual(libraries[0], {'id': 'lib1', 'name': 'Hangoskönyvek', 'mediaType': 'book',
                                        'folders': [{'id': 'fold1', 'fullPath': '/audiobooks'}]})
        method, path, headers, _ = self.server.requests[0]
        self.assertEqual((method, path), ('GET', '/abs/api/libraries'))
        self.assertEqual(headers['Authorization'], 'Bearer good-token')
        with self.assertRaisesRegex(ValueError, 'API-token'):
            publish.list_libraries(self.url, 'bad-token')
        with self.assertRaisesRegex(ValueError, 'másik gépre'):
            publish.list_libraries(self.url + 'redirect', 'good-token')
        with self.assertRaisesRegex(ValueError, 'szervercím'):
            publish.list_libraries('ftp://example.com', 'good-token')
        with self.assertRaisesRegex(ValueError, 'token'):
            publish.list_libraries(self.url, ' ')

    def test_upload_streams_multipart_with_fields_and_files(self):
        files = [write_wav(self.tmp / 'a.wav', 0.2), write_wav(self.tmp / 'b.wav', 0.3)]
        folder = publish.build_audiobookshelf_folder(sample_book(), files, self.tmp / 'lib')
        result = publish.upload_to_audiobookshelf(folder, self.url, 'good-token', 'lib1')
        self.assertTrue(result['ok'])
        self.assertEqual(result['folder_id'], 'fold1')
        method, path, headers, body = self.server.requests[-1]
        self.assertEqual((method, path), ('POST', '/abs/api/upload'))
        self.assertEqual(headers['Authorization'], 'Bearer good-token')
        fields, uploaded = parse_multipart(headers['Content-Type'], body)
        self.assertEqual(fields, {
            'title': 'Vol 2 - 1901 - Egri csillagok – „próba” & {Kovács Anna}',
            'author': 'Gárdonyi Géza', 'series': 'Klasszikusok', 'library': 'lib1', 'folder': 'fold1'})
        self.assertEqual(sorted(uploaded), [str(i) for i in range(len(uploaded))])
        names = {name: data for name, data in uploaded.values()}
        self.assertEqual(set(names), set(result['files']))
        self.assertIn('metadata.json', names)
        for name, data in names.items():
            self.assertEqual(data, (Path(folder) / name).read_bytes())

    def test_upload_with_explicit_folder_and_errors(self):
        folder = self.tmp / 'book'
        folder.mkdir()
        (folder / 'x.mp3').write_bytes(b'abc')
        result = publish.upload_to_audiobookshelf(folder, self.url, 'good-token', 'lib1', folder_id='f9')
        self.assertEqual(result['folder_id'], 'f9')
        self.assertEqual(len(self.server.requests), 1)  # no library lookup needed
        fields, _ = parse_multipart(self.server.requests[0][2]['Content-Type'], self.server.requests[0][3])
        self.assertEqual(fields, {'title': 'book', 'library': 'lib1', 'folder': 'f9'})
        with self.assertRaisesRegex(ValueError, 'jogosultság'):
            publish.upload_to_audiobookshelf(folder, self.url, 'bad-token', 'lib1', folder_id='f9')
        with self.assertRaisesRegex(ValueError, 'nem található'):
            publish.upload_to_audiobookshelf(folder, self.url, 'good-token', 'missing')
        with self.assertRaisesRegex(ValueError, 'nincs mappája'):
            publish.upload_to_audiobookshelf(folder, self.url, 'good-token', 'lib2')
        with self.assertRaisesRegex(ValueError, 'nem található'):
            publish.upload_to_audiobookshelf(self.tmp / 'nope', self.url, 'good-token', 'lib1')

    def test_connection_error_is_reported_in_hungarian(self):
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        with self.assertRaisesRegex(ValueError, 'Nem sikerült kapcsolódni'):
            publish.list_libraries(f'http://127.0.0.1:{port}', 'good-token', timeout=2)
        # Restart a server so tearDown's shutdown() has a running loop to stop.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _AbsHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()


# ── Retail sample ─────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg nem érhető el')
class RetailSampleTests(TempDirTest):
    def test_sample_is_cut_faded_and_encoded(self):
        rng = np.random.default_rng(1)
        source = self.tmp / 'book.wav'
        sf.write(str(source), (rng.standard_normal(SR * 12) * 0.2).astype(np.float32), SR)
        output = self.tmp / 'minta' / 'sample.mp3'
        result = publish.retail_sample(source, output, start_sec=2.0, duration_sec=5)
        self.assertEqual(result, str(output))
        probe = json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)],
            capture_output=True, check=True).stdout)
        self.assertEqual(probe['streams'][0]['codec_name'], 'mp3')
        self.assertAlmostEqual(float(probe['format']['duration']), 5.0, delta=0.15)
        self.assertAlmostEqual(int(probe['format']['bit_rate']) / 1000, 192, delta=12)
        decoded = self.tmp / 'decoded.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(output), str(decoded)], check=True)
        audio, rate = sf.read(str(decoded), dtype='float32')
        audio = audio if audio.ndim == 1 else audio.mean(axis=1)
        head = np.abs(audio[int(0.05 * rate):int(0.15 * rate)]).mean()
        middle = np.abs(audio[int(2 * rate):int(3 * rate)]).mean()
        self.assertLess(head, middle * 0.35)

    def test_sample_clamped_to_end_and_invalid_ranges(self):
        source = write_wav(self.tmp / 'short.wav', 3.0, 0.2)
        output = self.tmp / 's.mp3'
        publish.retail_sample(source, output, start_sec=1.0, duration_sec=300)
        self.assertAlmostEqual(publish.audio_duration(output), 2.0, delta=0.15)
        for kwargs in ({'start_sec': -1}, {'duration_sec': 0}, {'start_sec': 10}):
            with self.assertRaises(ValueError):
                publish.retail_sample(source, self.tmp / 'bad.mp3', **kwargs)
        self.assertFalse((self.tmp / 'bad.mp3').exists())
        with self.assertRaises(ValueError):
            publish.retail_sample(self.tmp / 'nope.wav', output)


if __name__ == '__main__':
    unittest.main()
