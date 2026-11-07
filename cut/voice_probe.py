"""CONNECT-01: HA TTS synthesis via engine_id (current API shape)."""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

OUT = Path('/tmp/voice-results.json')
ENTITY = 'tts.ha_small_tone_tts'


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    status, cfg = request('/api/config', token=token)
    comps = cfg.get('components') or []
    check('wyoming-component-loaded', 'wyoming' in comps)
    check('tts-component-loaded', 'tts' in comps)

    status, state = request('/api/states/' + ENTITY, token=token)
    check('tts-entity-created', status == 200, 'status=%s' % status)

    status, body = request('/api/tts_get_url', {
        'engine_id': ENTITY,
        'message': 'Turn on the cut test switch please'}, token=token)
    url = body.get('url') if isinstance(body, dict) else None
    check('tts-get-url', status == 200 and bool(url),
          'status=%s url=%s body=%s' % (status, url, str(body)[:120]))

    ok = False
    detail = ''
    if url:
        path = url if url.startswith('http') else 'http://127.0.0.1:8123' + url
        try:
            with urlopen(Request(path, headers={'Authorization': 'Bearer ' + token}),
                         timeout=60) as r:
                blob = r.read()
                ok = r.status == 200 and len(blob) > 1000
                detail = 'http=%s bytes=%d' % (r.status, len(blob))
                if ok:
                    Path('/tmp/tts-output.mp3').write_bytes(blob)
        except Exception as exc:  # noqa: BLE001
            detail = '%s:%s' % (type(exc).__name__, str(exc)[:110])
    check('tts-audio-download', ok, detail)

    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'boundary': 'Wyoming TTS lab server returns generated tone audio, not speech. Proves '
                       'HA wyoming config entry -> tts entity -> tts_get_url -> audio bytes. '
                       'No microphone, no speaker, no STT round trip.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('voice-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
