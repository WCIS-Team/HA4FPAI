"""Insert a rest_command bridging HA to the local control service (lab only)."""
from pathlib import Path
import sys

token = Path('/tmp/bridge-token.txt').read_text().strip()
path = Path('/runtime/configuration.yaml')
lines = path.read_text(encoding='utf-8').splitlines()
if any('ha_bridge_probe' in line for line in lines):
    print('already-present')
    sys.exit(0)
start = lines.index('rest_command:')
end = start + 1
while end < len(lines) and (lines[end].startswith('  ') or lines[end] == ''):
    end += 1
block = [
    '  ha_bridge_probe:',
    '    url: http://127.0.0.1:18100/v1/commands',
    '    method: POST',
    '    headers:',
    '      Authorization: "Bearer %s"' % token,
    '      Content-Type: application/json',
    '    content_type: application/json',
    "    payload: '{\"action\": \"{{ action }}\", \"params\": {{\"client_id\": \"ha-small\"}}, \"idempotency_key\": \"{{ key }}\", \"client_id\": \"ha-small\"}'",
]
lines[end:end] = block
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('inserted at', end)
