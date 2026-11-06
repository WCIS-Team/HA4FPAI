# -*- coding: utf-8 -*-
"""业务控制台 / 视频工作台读模型（G03）。

规则（规划 §控制台）：每个按钮可追 command/event；pending/error 与未验能力清楚；
需要通知时用**可续游标**，不以推送消息代权威状态。本模块只读库，不产生副作用。
"""


def _counts(store):
    out = {}
    for name, table in (('persons', 'persons'), ('face_samples', 'face_samples'),
                        ('voice_samples', 'voice_samples'), ('recordings', 'recordings'),
                        ('tasks', 'tasks'), ('commands', 'commands'), ('events', 'events')):
        try:
            out[name] = store.query_one('SELECT COUNT(*) AS n FROM %s' % table)['n']
        except Exception:                            # noqa: BLE001
            out[name] = None
    return out


def snapshot(store, limit=50):
    """一次性读模型快照：人员/样本/录像/任务/命令/设置 desired-applied/世代。"""
    persons = [dict(r) for r in store.query(
        'SELECT person_id, display_name, status, revision, updated_at'
        ' FROM persons ORDER BY updated_at DESC LIMIT ?', (int(limit),))]
    recordings = [dict(r) for r in store.query(
        'SELECT recording_id, device_id, trash_state, size_bytes, updated_at'
        ' FROM recordings ORDER BY updated_at DESC LIMIT ?', (int(limit),))]
    tasks = [dict(r) for r in store.query(
        'SELECT task_id, action, status, next_due_at FROM tasks'
        ' ORDER BY updated_at DESC LIMIT ?', (int(limit),))]
    commands = [dict(r) for r in store.query(
        'SELECT command_id, action, status, code, updated_at FROM commands'
        ' ORDER BY updated_at DESC LIMIT ?', (int(limit),))]
    settings = [{'key': e['key'], 'value': e['value'], 'source': e['source'],
                 'revision': e['revision'], 'applied': e['applied'],
                 'pending': e['pending']}
                for e in store.settings.effective()] if hasattr(store, 'settings') else []
    return {
        'identity': store.identity(),
        'counts': _counts(store),
        'persons': persons,
        'recordings': recordings,
        'tasks': tasks,
        'commands': commands,
        'settings': settings,
    }


def events_page(store, after_cursor=0, limit=100):
    """可续事件游标：按 rowid 递增返回；重连从 last cursor 继续，不重复。"""
    limit = max(1, min(int(limit), 500))
    rows = [dict(r) for r in store.query(
        'SELECT rowid AS cursor, event_id, type, wall_time, producer_id, person_id,'
        ' payload_json FROM events WHERE rowid > ? ORDER BY rowid LIMIT ?',
        (int(after_cursor), limit))]
    next_cursor = rows[-1]['cursor'] if rows else int(after_cursor)
    return {'items': rows, 'next_cursor': next_cursor, 'has_more': len(rows) == limit}


def command_trace(store, command_id):
    """单个命令的角度：命令本身 + 关联事件 + 尝试记录（可追）。"""
    cmd = store.query_one('SELECT * FROM commands WHERE command_id=?', (command_id,))
    if cmd is None:
        return None
    attempts = [dict(r) for r in store.query(
        'SELECT attempt_id, operation_id, started_at, finished_at, outcome'
        ' FROM command_attempts WHERE command_id=? ORDER BY started_at', (command_id,))]
    ev = store.query_one('SELECT event_id FROM commands WHERE command_id=?', (command_id,))
    event = None
    if ev and ev['event_id']:
        event = store.query_one('SELECT event_id, type, wall_time FROM events WHERE event_id=?',
                                (ev['event_id'],))
    return {'command': dict(cmd), 'attempts': attempts,
            'event': dict(event) if event else None}
