"""Verify real speech and library persistence through a running packaged API."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def request(url, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    return urllib.request.urlopen(urllib.request.Request(url, data=data,
        headers={'Content-Type': 'application/json'}), timeout=180)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:17903')
    parser.add_argument('--output', type=Path, default=Path('build/windows/smoke'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    body = {'input': 'Szia! Ez az Auris első Windows alkalmazásának valódi magyar felolvasási próbája.',
            'voice': '', 'language': 'hu', 'response_format': 'wav'}
    started = time.monotonic()
    for attempt in range(60):
        try:
            with request(args.url + '/v1/audio/speech', body) as response:
                audio = response.read()
            break
        except urllib.error.HTTPError as exc:
            message = exc.read().decode()
            if exc.code != 503:
                raise RuntimeError(message) from exc
            time.sleep(1)
        except urllib.error.URLError:
            time.sleep(1)
    else:
        raise RuntimeError('Speech engine did not become ready')
    wave = args.output / 'hungarian-speech.wav'
    wave.write_bytes(audio)
    import numpy as np
    import soundfile as sf
    samples, rate = sf.read(wave)
    duration = len(samples) / rate
    rms = float(np.sqrt(np.mean(samples ** 2)))
    assert 2 < duration < 40, duration
    assert rms > 0.005, rms
    with request(args.url + '/api/import/tools') as response:
        tools = json.load(response)
    with request(args.url + '/api/settings') as response:
        settings = json.load(response)
    with request(args.url + '/api/tts/status') as response:
        engine = json.load(response)
    with request(args.url + '/api/setup/status') as response:
        setup = json.load(response)
    with request(args.url + '/v1/audio/speech', {**body, 'response_format': 'mp3'}) as response:
        mp3 = response.read()
        assert response.headers.get_content_type() == 'audio/mpeg'
    assert len(mp3) > 10_000
    (args.output / 'hungarian-speech.mp3').write_bytes(mp3)
    import requests
    preview = requests.post(args.url + '/api/import/preview', files={
        'file': ('windows-kiadas.txt', 'Első fejezet\n\nAuris próbakönyv. A könyvek és a beállítások az újraindítás és a frissítés után is megmaradnak.'.encode('utf-8'), 'text/plain')}, timeout=60)
    preview.raise_for_status()
    confirm = requests.post(args.url + '/api/import/confirm', json={
        'token': preview.json()['token'], 'title': 'Windows kiadási próba', 'author': 'Auris QA',
        'narration_mode': 'single', 'allow_duplicate': True}, timeout=60)
    confirm.raise_for_status()
    book_id = confirm.json()['book_id']
    changed = requests.post(args.url + '/api/settings', json={'theme': 'paper'}, timeout=60)
    changed.raise_for_status()
    result = {'duration_seconds': duration, 'rms': rms, 'sample_rate': rate,
              'request_seconds': round(time.monotonic() - started, 2), 'engine': engine,
              'selected_engine': settings.get('tts_engine'), 'tools': tools,
              'setup': setup, 'mp3_bytes': len(mp3), 'persisted_book_id': book_id}
    (args.output / 'real-speech.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
