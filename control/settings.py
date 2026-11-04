# -*- coding: utf-8 -*-
"""设置：YAML/代码默认 → SQLite 用户覆盖 → applied 回执；防乱序准入。

规划 §设置的期望值与实际生效：
  * 界面修改进库，带 revision；冲突 409，不半提交
  * 域回执按 (scope, epoch, revision) 单调准入：迟到旧版本不得覆盖新值
    （例：期望 13=manual、14=off，13 的迟到重试不得重新开启录像）
  * recorder 作用域在视频域接回前保持 desired≠applied，如实报告
"""
import threading

from . import ids
from .errors import ConflictError, DomainError

DEFINITIONS = {
    'audio.output.volume': {'type': 'float', 'scope': 'audio', 'default': 50.0, 'mode': 'hot'},
    'face.match.threshold': {'type': 'float', 'scope': 'face', 'default': 0.40, 'mode': 'hot'},
    'voice.match.threshold': {'type': 'float', 'scope': 'voiceprint', 'default': 0.5, 'mode': 'hot'},
    'recording.mode': {'type': 'enum', 'choices': ['off', 'auto', 'manual'],
                       'scope': 'recorder', 'default': 'off', 'mode': 'apply'},
    'ui.theme': {'type': 'enum', 'choices': ['dark', 'light'], 'scope': 'ui', 'default': 'dark', 'mode': 'hot'},
}


class Settings(object):
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()

    # ---- 校验 ----
    def _validate(self, key, value):
        spec = DEFINITIONS.get(key)
        if spec is None:
            raise ConflictError('unknown_setting', '未登记的设置键: %s' % key)
        if spec['type'] == 'float':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConflictError('invalid_value', '%s 需要数值' % key)
            return float(value)
        if spec['type'] == 'enum':
            if value not in spec['choices']:
                raise ConflictError('invalid_value',
                                    '%s 只能取 %s' % (key, '/'.join(spec['choices'])))
            return value
        raise ConflictError('invalid_value', '%s 类型不支持' % key)

    # ---- 读 ----
    def effective(self):
        rows = {(r['scope'], r['key']): r for r in
                self.store.query('SELECT * FROM settings')}
        out = []
        for key, spec in sorted(DEFINITIONS.items()):
            row = rows.get((spec['scope'], key))
            entry = {'key': key, 'scope': spec['scope'], 'mode': spec['mode'],
                     'value': spec['default'], 'source': 'default', 'revision': 0}
            if row is not None:
                entry.update({'value': _decode(spec, row['value']),
                              'source': 'db', 'revision': row['revision']})
            applied = self.store.query_one(
                'SELECT * FROM settings_applied WHERE scope=? AND key=?',
                (spec['scope'], key))
            if spec['mode'] == 'hot':
                entry['applied'] = True if row is not None else None
            else:
                entry['applied'] = bool(
                    applied is not None and applied['revision'] == entry['revision']
                    and applied['epoch'] == self.store.restore_epoch())
            if applied is not None:
                entry['applied_revision'] = applied['revision']
                entry['applied_epoch'] = applied['epoch']
            out.append(entry)
        return out

    def get(self, key):
        for entry in self.effective():
            if entry['key'] == key:
                return entry
        raise ConflictError('unknown_setting', '未登记的设置键: %s' % key)

    # ---- 写（desired）----
    def update(self, updates, expected_revisions=None, actor=None):
        if not isinstance(updates, dict) or not updates:
            raise ConflictError('invalid_update', 'updates 必须是非空对象')
        expected_revisions = expected_revisions or {}
        validated = dict((k, self._validate(k, v)) for k, v in updates.items())
        now = ids.now_iso()
        with self.store.tx() as conn:
            new_revisions = {}
            for key, value in validated.items():
                scope = DEFINITIONS[key]['scope']
                row = conn.execute('SELECT * FROM settings WHERE scope=? AND key=?',
                                   (scope, key)).fetchone()
                current_rev = 0 if row is None else dict(row)['revision']
                want = expected_revisions.get(key, current_rev)
                if int(want) != current_rev:
                    raise ConflictError(
                        'revision_conflict',
                        '%s 修订号不匹配（期望 %s，当前 %s）' % (key, want, current_rev),
                        {'key': key, 'current_revision': current_rev})
                encoded = ids.canonical_json(value)
                vtype = DEFINITIONS[key]['type']
                if row is None:
                    conn.execute(
                        'INSERT INTO settings(scope, key, value_type, value, revision,'
                        ' updated_at, updated_by) VALUES(?,?,?,?,1,?,?)',
                        (scope, key, vtype, encoded, now, actor))
                    new_revisions[key] = 1
                else:
                    conn.execute(
                        'UPDATE settings SET value=?, revision=revision+1, updated_at=?,'
                        ' updated_by=? WHERE scope=? AND key=?',
                        (encoded, now, actor, scope, key))
                    new_revisions[key] = dict(row)['revision'] + 1
                conn.execute(
                    'INSERT INTO settings_changes(scope, key, old_revision, new_revision,'
                    ' changed_at, changed_by) VALUES(?,?,?,?,?,?)',
                    (scope, key, current_rev, new_revisions[key], now, actor))
                self.store.bump_domain_revision(conn)
        return new_revisions

    # ---- 域回执（applied；防乱序）----
    def apply_feedback(self, scope, epoch, revision, keys=None):
        """域服务回报已应用；epoch 过期或 revision 倒退一律拒绝，不得覆盖新值。"""
        current_epoch = self.store.restore_epoch()
        if int(epoch) != current_epoch:
            return {'accepted': False, 'reason': 'stale_epoch',
                    'current_epoch': current_epoch}
        targets = [entry['key'] for entry in self.effective()
                   if entry['scope'] == scope and (keys is None or entry['key'] in keys)]
        if not targets:
            raise ConflictError('unknown_scope', '未知作用域: %s' % scope)
        accepted, superseded = [], []
        now = ids.now_iso()
        with self.store.tx() as conn:
            for key in targets:
                row = conn.execute('SELECT * FROM settings_applied WHERE scope=? AND key=?',
                                   (scope, key)).fetchone()
                if row is not None:
                    old = (int(row['epoch']), int(row['revision']))
                    new = (int(epoch), int(revision))
                    if new < old:
                        superseded.append(key)
                        continue
                cur = conn.execute('UPDATE settings_applied SET epoch=?, revision=?, applied_at=?'
                                   ' WHERE scope=? AND key=?',
                                   (int(epoch), int(revision), now, scope, key))
                if cur.rowcount == 0:
                    conn.execute('INSERT INTO settings_applied(scope, key, epoch, revision,'
                                 ' applied_at) VALUES(?,?,?,?,?)',
                                 (scope, key, int(epoch), int(revision), now))
                accepted.append(key)
        return {'accepted': accepted, 'superseded': superseded, 'epoch': int(epoch),
                'revision': int(revision)}


def _decode(spec, encoded):
    import json
    value = json.loads(encoded)
    return value
