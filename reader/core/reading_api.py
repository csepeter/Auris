"""Reading: chapter text and editor, speaker corrections, progress and bookmarks."""

import os
import uuid
from flask import Blueprint, jsonify, request
from core.database import get_conn
from core import text_editor
from core import characters as char_module
from core import enrichment

import app as application

bp = Blueprint('reading', __name__)


@bp.route('/api/books/<int:book_id>/chapters/<int:chapter_id>/editor')
def get_chapter_editor(book_id, chapter_id):
    with get_conn() as conn:
        chapter = conn.execute('SELECT * FROM chapters WHERE id=? AND book_id=?',
                               (chapter_id, book_id)).fetchone()
    if chapter is None:
        return jsonify({'error': 'A fejezet nem található.'}), 404
    return jsonify(application._editor_payload(chapter))


@bp.route('/api/books/<int:book_id>/chapters/<int:chapter_id>/editor', methods=['PUT'])
def save_chapter_text(book_id, chapter_id):
    return application._save_editor(book_id, chapter_id)


@bp.route('/api/books/<int:book_id>/chapters/<int:chapter_id>/editor/restore', methods=['POST'])
def restore_chapter_text(book_id, chapter_id):
    return application._save_editor(book_id, chapter_id, restore=True)


@bp.route('/api/books/<int:book_id>/chapters/<int:chapter_id>/editor/preview', methods=['POST'])
def preview_chapter_text(book_id, chapter_id):
    with get_conn() as conn:
        chapter = conn.execute('SELECT id FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)).fetchone()
    if not chapter:
        return jsonify({'error': 'A fejezet nem található.'}), 404
    body = request.get_json(silent=True)
    try:
        blocks = text_editor.validate_blocks([body.get('block') if isinstance(body, dict) else None])
        if len(blocks[0]['text']) > 1500:
            raise ValueError('Az előnézethez legfeljebb 1500 karakteres blokkot válassz vagy bontsd kisebb részekre.')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if application.tts.status()['state'] != 'ready':
        return jsonify({'error': 'Az előnézethez előbb töltsd be a hangmodellt a Beállításokban.'}), 503
    book = dict(application._load_book(book_id))
    segs = text_editor.enrich_blocks(blocks, {}, application._book_narrator_instruct(book), True, None)
    from core import experience
    ref_audio, ref_text = application._book_narrator_reference(book_id)
    try:
        for seg in segs:
            result = application.tts.generate(
                text=experience.apply_pronunciation(seg['enriched_text'], book_id),
                instruct=seg['instruct'], speed=seg['speed'], language=book['language'],
                ref_audio=ref_audio, ref_text=ref_text)
            seg['audio_path'] = result['audio_path']
        # Include the selected trailing pause in the preview so it is audible.
        import numpy as np
        import soundfile as sf
        from core.exporter import _merge_wavs, pause_after_segment, SAMPLE_RATE
        from core.tts_engine import AUDIO_CACHE_DIR
        waveform = _merge_wavs(segs)
        if segs[-1].get('pause_ms') is None:
            waveform = np.concatenate([waveform, np.zeros(int(SAMPLE_RATE * pause_after_segment(segs[-1])))])
        key = 'preview_' + uuid.uuid4().hex
        os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
        sf.write(os.path.join(AUDIO_CACHE_DIR, key + '.wav'), waveform, SAMPLE_RATE)
        return jsonify({'audio_url': f'/api/audio/{key}'})
    except Exception as exc:
        application.log.exception('Chapter text preview failed')
        return jsonify({'error': str(exc)}), 500


@bp.route('/api/books/<int:book_id>/chapters/<int:chapter_id>')
def get_chapter(book_id, chapter_id):
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)
        ).fetchone()
    if not row:
        return jsonify({'error': 'Nem található'}), 404
    return jsonify(dict(row))


@bp.route(
    '/api/books/<int:book_id>/chapters/<int:chapter_id>/speaker-annotations',
    methods=['PUT'],
)
def update_speaker_annotation(book_id, chapter_id):
    """Persist a human correction for one stable speaker unit."""
    body = request.get_json(silent=True) or {}
    try:
        unit_index = int(body.get('unit_index'))
    except (TypeError, ValueError):
        return jsonify({'error': 'A valid unit_index is required'}), 400

    raw_name = body.get('speaker_name')
    speaker_name = (
        ' '.join(str(raw_name).strip().split()) if raw_name is not None else ''
    )
    if len(speaker_name) > 100:
        return jsonify({'error': 'A beszélő neve túl hosszú'}), 400

    with get_conn() as conn:
        chapter = conn.execute(
            'SELECT content FROM chapters WHERE id=? AND book_id=?',
            (chapter_id, book_id),
        ).fetchone()
        if not chapter:
            return jsonify({'error': 'A fejezet nem található'}), 404

        units = enrichment.build_speaker_units(chapter['content'])
        if unit_index < 0 or unit_index >= len(units):
            return jsonify({'error': 'A szövegegység nem található'}), 404
        annotation_rows = conn.execute(
            'SELECT unit_index, speaker_name FROM speaker_annotations '
            'WHERE chapter_id=?',
            (chapter_id,),
        ).fetchall()
        effective_annotations = enrichment.expand_speaker_annotations(
            chapter['content'],
            {
                int(row['unit_index']): str(row['speaker_name'] or '')
                for row in annotation_rows
            },
        )
        unit = units[unit_index]
        requested_scope = str(body.get('scope') or '').strip().lower()
        if not requested_scope:
            requested_scope = 'turn' if body.get('apply_to_turn') else 'sentence'
        if requested_scope not in {'sentence', 'turn_tail', 'turn', 'range'}:
            return jsonify({'error': 'Érvénytelen javítási tartomány'}), 400

        target_indexes = [unit_index]
        range_end_unit_index = unit_index
        if requested_scope == 'range':
            try:
                range_end_unit_index = int(body.get('range_end_unit_index'))
            except (TypeError, ValueError):
                return jsonify({'error': 'A valid range end is required'}), 400
            if (
                range_end_unit_index < unit_index
                or range_end_unit_index >= len(units)
            ):
                return jsonify({'error': 'Érvénytelen beszélőtartomány'}), 400
            if range_end_unit_index - unit_index > 200:
                return jsonify({
                    'error': 'A beszélőtartomány legfeljebb 201 szövegegység lehet'
                }), 400
            target_indexes = list(range(unit_index, range_end_unit_index + 1))
        elif requested_scope != 'sentence' and unit.get('turn_index') is not None:
            target_indexes = [
                int(candidate['index'])
                for candidate in units
                if candidate.get('dialogue_candidate')
                and candidate.get('turn_index') == unit.get('turn_index')
                and (
                    requested_scope == 'turn'
                    or int(candidate['index']) >= unit_index
                )
            ]

        canonical_name = ''
        character_id = None
        boundary_clear_indexes = []
        if speaker_name:
            existing = conn.execute(
                'SELECT id, name FROM characters '
                'WHERE book_id=? AND name=? COLLATE NOCASE',
                (book_id, speaker_name),
            ).fetchone()
            if existing:
                character_id = existing['id']
                canonical_name = existing['name']
            else:
                profile = char_module.generate_voice_profile(
                    speaker_name, 'unknown'
                )
                cursor = conn.execute(
                    'INSERT INTO characters '
                    '(book_id, name, gender, frequency, instruct, color_hex) '
                    'VALUES (?,?,?,?,?,?)',
                    (
                        book_id, speaker_name, 'unknown', 0,
                        profile['instruct'], profile['color_hex'],
                    ),
                )
                character_id = cursor.lastrowid
                canonical_name = speaker_name

            for target_index in target_indexes:
                target_unit = units[target_index]
                conn.execute(
                    'INSERT INTO speaker_annotations '
                    '(book_id, chapter_id, unit_index, unit_text, speaker_name, '
                    'confidence, source) VALUES (?,?,?,?,?,1.0,?) '
                    'ON CONFLICT(chapter_id, unit_index) DO UPDATE SET '
                    'unit_text=excluded.unit_text, speaker_name=excluded.speaker_name, '
                    'confidence=excluded.confidence, source=excluded.source',
                    (
                        book_id, chapter_id, target_index, target_unit['text'],
                        canonical_name, 'manual',
                    ),
                )

            # An explicit range has an exact endpoint. Clear any old,
            # contiguous assignment to the same speaker after that endpoint;
            # otherwise the reader would merge the automatic tail back into
            # the range the next time the editor is opened.
            if requested_scope == 'range':
                canonical_key = canonical_name.casefold()
                next_index = target_indexes[-1] + 1
                while (
                    next_index < len(units)
                    and str(effective_annotations.get(next_index) or '')
                    .strip()
                    .casefold() == canonical_key
                ):
                    boundary_clear_indexes.append(next_index)
                    next_index += 1

                for clear_index in boundary_clear_indexes:
                    clear_unit = units[clear_index]
                    conn.execute(
                        'INSERT INTO speaker_annotations '
                        '(book_id, chapter_id, unit_index, unit_text, '
                        'speaker_name, confidence, source) '
                        'VALUES (?,?,?,?,?,1.0,?) '
                        'ON CONFLICT(chapter_id, unit_index) DO UPDATE SET '
                        'unit_text=excluded.unit_text, '
                        'speaker_name=excluded.speaker_name, '
                        'confidence=excluded.confidence, '
                        'source=excluded.source',
                        (
                            book_id, chapter_id, clear_index,
                            clear_unit['text'], '', 'manual',
                        ),
                    )
        else:
            for target_index in target_indexes:
                target_unit = units[target_index]
                conn.execute(
                    'INSERT INTO speaker_annotations '
                    '(book_id, chapter_id, unit_index, unit_text, speaker_name, '
                    'confidence, source) VALUES (?,?,?,?,?,1.0,?) '
                    'ON CONFLICT(chapter_id, unit_index) DO UPDATE SET '
                    'unit_text=excluded.unit_text, speaker_name=excluded.speaker_name, '
                    'confidence=excluded.confidence, source=excluded.source',
                    (
                        book_id, chapter_id, target_index,
                        target_unit['text'], '', 'manual',
                    ),
                )

        conn.execute(
            'UPDATE characters SET frequency=('
            'SELECT COUNT(*) FROM speaker_annotations a '
            'WHERE a.book_id=characters.book_id '
            'AND a.speaker_name=characters.name COLLATE NOCASE'
            ') WHERE book_id=?',
            (book_id,),
        )
        conn.execute(
            'DELETE FROM tts_segments WHERE book_id=? AND chapter_id=?',
            (book_id, chapter_id),
        )

    return jsonify({
        'ok': True,
        'unit_index': unit_index,
        'speaker_name': canonical_name or None,
        'character_id': character_id,
        'source': 'manual',
        'updated_units': len(target_indexes),
        'assigned_units': len(target_indexes),
        'boundary_cleared_units': len(boundary_clear_indexes),
        'scope': requested_scope,
        'range_end_unit_index': target_indexes[-1],
        'segments_cleared': True,
    })


@bp.route('/api/books/<int:book_id>/progress', methods=['POST'])
def save_progress(book_id):
    from core import playback_progress
    try:
        playback_progress.save(book_id, request.get_json(force=True))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True)


@bp.route('/api/books/<int:book_id>/progress')
def get_progress(book_id):
    from core import playback_progress
    return jsonify(playback_progress.load(book_id))


@bp.route('/api/books/<int:book_id>/bookmarks')
def list_bookmarks(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT b.*, c.title as chapter_title FROM bookmarks b '
            'JOIN chapters c ON b.chapter_id = c.id '
            'WHERE b.book_id=? ORDER BY b.created_at DESC',
            (book_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/books/<int:book_id>/bookmarks', methods=['POST'])
def add_bookmark(book_id):
    body = request.get_json(force=True) or {}
    chapter_id = body.get('chapter_id')
    segment_index = body.get('segment_index', 0)
    text_excerpt = (body.get('text_excerpt', '') or '')[:200]
    label = body.get('label', '')
    if not chapter_id:
        return jsonify({'error': 'chapter_id required'}), 400
    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO bookmarks (book_id, chapter_id, segment_index, text_excerpt, label) '
            'VALUES (?,?,?,?,?)',
            (book_id, chapter_id, segment_index, text_excerpt, label)
        )
    return jsonify({'ok': True, 'id': cur.lastrowid})


@bp.route('/api/books/<int:book_id>/bookmarks/<int:bm_id>', methods=['DELETE'])
def delete_bookmark(book_id, bm_id):
    with get_conn() as conn:
        conn.execute('DELETE FROM bookmarks WHERE id=? AND book_id=?', (bm_id, book_id))
    return jsonify({'ok': True})
