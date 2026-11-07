from pathlib import Path

p = Path('/runtime/configuration.yaml')
text = p.read_text(encoding='utf-8')
bad = ('    payload: \'{"action": "{{ action }}", "params": {{"client_id": "ha-small"}}, '
       '"idempotency_key": "{{ key }}", "client_id": "ha-small"}\'\n')
good = ('    payload: \'{"action": "{{ action }}", "params": {"client_id": "ha-small"}, '
        '"idempotency_key": "{{ key }}", "client_id": "ha-small"}\'\n')
if good in text:
    print('already-fixed')
elif bad in text:
    p.write_text(text.replace(bad, good), encoding='utf-8')
    print('fixed')
else:
    raise SystemExit('neither pattern found')
