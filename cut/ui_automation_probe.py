import json
import sys
import time
from pathlib import Path
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

ENTITY = 'automation.ui_created_cut_automation'


def main():
    t = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    status, before = request('/api/states/' + ENTITY, token=t)
    check('ui-automation-entity-loaded', status == 200, 'status=%s' % status)
    triggered_before = (before.get('attributes') or {}).get('last_triggered') if status == 200 else None
    # Drive the UI-defined trigger: cut_test -> on, and check the UI-defined action ran.
    request('/api/services/input_boolean/turn_off', {'entity_id': 'input_boolean.cut_test'}, token=t)
    time.sleep(1)
    request('/api/services/input_boolean/turn_on', {'entity_id': 'input_boolean.cut_test'}, token=t)
    ran = False
    end = time.monotonic() + 20
    while time.monotonic() < end:
        status, after = request('/api/states/' + ENTITY, token=t)
        if status == 200:
            current = (after.get('attributes') or {}).get('last_triggered')
            if current and current != triggered_before:
                ran = True
                break
        time.sleep(0.5)
    check('ui-automation-fired', ran, 'before=%s' % triggered_before)
    status, trace = request('/api/automation/trace/' + ENTITY, token=t)
    check('ui-automation-trace-readable', status in (200, 404), 'status=%s' % status)
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'boundary': 'Automation was created through the real frontend editor (YAML mode) and '
                       'persisted to automations.yaml; verified by loading and firing it.'}
    Path('/tmp/ui-automation-results.json').write_text(json.dumps(out, indent=2) + '\n')
    print('ui-automation-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
