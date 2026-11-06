# -*- coding: utf-8 -*-
"""设置：默认 → SQLite 用户覆盖 → 域回执(applied)；防乱序准入（D01/D02/D05）。

规划 §设置的期望值与实际生效：
  * 界面修改进库，带 revision；冲突 409，不半提交。
  * 域回执按 (scope, epoch, revision[, digest]) 单调准入：迟到旧版本不得覆盖新值；
    **未消费的值永不标 applied**（不再"入库即已生效"）。
  * 每个键登记 kind/apply_mode/consumer/sensitive，敏感值对外脱敏。
  * recorder 在视频域接回前保持 desired≠applied，如实报告。
"""
import json
import threading

from . import ids
from .errors import ConflictError

# D01：全参数注册与分类。kind：business=业务值（可进覆盖文件/DB）；hardware=硬件；credential=凭据。
DEFINITIONS = {
    'audio.output.volume': {
        'type': 'float', 'scope': 'audio', 'default': 50.0, 'mode': 'hot',
        'min': 0.0, 'max': 100.0, 'kind': 'business', 'consumer': 'audio',
        'sensitive': False, 'overlay': True},
    'face.match.threshold': {
        'type': 'float', 'scope': 'face', 'default': 0.40, 'mode': 'hot',
        'min': 0.0, 'max': 1.0, 'kind': 'business', 'consumer': 'face',
        'sensitive': False, 'overlay': True},
    'voice.match.threshold': {
        'type': 'float', 'scope': 'voiceprint', 'default': 0.5, 'mode': 'hot',
        'min': 0.0, 'max': 1.0, 'kind': 'business', 'consumer': 'voiceprint',
        'sensitive': False, 'overlay': True},
    'recording.mode': {
        'type': 'enum', 'choices': ['off', 'auto', 'manual'],
        'scope': 'recorder', 'default': 'off', 'mode': 'apply',
        'kind': 'business', 'consumer': 'recorder', 'sensitive': False, 'overlay': True},
    'ui.theme': {
        'type': 'enum', 'choices': ['dark', 'light'], 'scope': 'ui',
        'default': 'dark', 'mode': 'hot', 'kind': 'business', 'consumer': 'ui',
        'sensitive': False, 'overlay': False},
}

# 可写入覆盖文件的白名单（D03）：kind=business 且 overlay=True；绝不含凭据/硬件任意路径。
OVERLAY_KEYS = tuple(sorted(k for k, s in DEFINITIONS.items() if s.get('overlay')))


class Settings(object):
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()

    # ---- D01：注册表视图 ----
    def definitions(self):
        out = []
        for key, spec in sorted(DEFINITIONS.items()):
            out.append({'key': key, 'type': spec['type'], 'scope': spec['scope'],
                        'mode': spec['mode'], 'kind': spec['kind'],
                        'consumer': spec['consumer'], 'sensitive': spec['sensitive'],
                        'overlay': bool(spec.get('overlay')),
                        'choices': spec.get('choices'),
                        'min': spec.get('min'), 'max': spec.get('max'),
                        'default': spec['default']})
        return out

    def sensitive_keys(self):
        return [k for k, s in DEFINITIONS.items() if s.get('sensitive')]

    # ---- 校验 ----
    def _validate(self, key, value):
        spec = DEFINITIONS.get(key)
        if spec is None:
            raise ConflictError('unknown_setting', '未登记的设置键: %s' % key)
        if spec['type'] == 'float':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConflictError('invalid_value', '%s 需要数值' % key)
            f = float(value)
            if f != f or f in (float('inf'), float('-inf')):
                raise ConflictError('invalid_value', '%s 不能是 NaN/Inf' % key)
            if 'min' in spec and f < spec['min']:
                raise ConflictError('invalid_value', '%s 低于下限 %s' % (key, spec['min']))
            if 'max' in spec and f > spec['max']:
                raise ConflictError('invalid_value', '%s 超过上限 %s' % (key, spec['max']))
            return f
        if spec['type'] == 'enum':
            if value not in spec['choices']:
                raise ConflictError('invalid_value',
                                    '%s 只能取 %s' % (key, '/'.join(spec['choices'])))
            return value
        raise ConflictError('invalid_value', '%s 类型不支持' % key)

    # ---- 读 ----
    def effective(self):
        rows = {(r['scope'], r['key']): r for r in self.store.query('SELECT * FROM settings')}
        epoch = self.store.restore_epoch()
        out = []
        for key, spec in sorted(DEFINITIONS.items()):
            row = rows.get((spec['scope'], key))
            desired_rev = 0 if row is None else int(row['revision'])
            entry = {'key': key, 'scope': spec['scope'], 'mode': spec['mode'],
                     'kind': spec['kind'], 'sensitive': spec['sensitive'],
                     'value': spec['default'], 'source': 'default', 'revision': desired_rev}
            if row is not None:
                entry.update({'value': _decode(spec, row['value']),
                              'source': 'db', 'revision': int(row['revision'])})
            applied = self.store.query_one(
                'SELECT * FROM settings_applied WHERE scope=? AND key=?', (spec['scope'], key))
            # D02：无论 hot/apply，只有"域回执恰好对应 desired 版本且同 epoch"才算 applied
            is_applied = bool(applied is not None
                              and int(applied['revision']) == desired_rev
                              and int(applied['epoch']) == epoch)
            entry['applied'] = is_applied
            entry['pending'] = bool(row is not None and desired_rev > 0
                                    and (applied is None
                                         or int(applied['revision']) != desired_rev))
            if applied is not None:
                entry['applied_revision'] = int(applied['revision'])
                entry['applied_epoch'] = int(applied['epoch'])
            if spec['sensitive']:
                entry['value'] = '***'
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
        validated = dict((k, self._validate(k, v)) for k, v in updates.items())
        expected_revisions = expected_revisions or {}
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

    # ---- D05：三类统一写入口 ----
    def write_param(self, key, value, expected_revision=None, actor=None):
        er = {} if expected_revision is None else {key: expected_revision}
        return self.update({key: value}, expected_revisions=er, actor=actor)

    def write_batch(self, updates, expected_revisions=None, actor=None):
        """批量原子：任一键校验/修订失败整批不落库。"""
        return self.update(updates, expected_revisions=expected_revisions, actor=actor)

    def reset_defaults(self, keys=None, expected_revisions=None, actor=None):
        targets = list(keys) if keys else list(DEFINITIONS.keys())
        updates = {}
        for key in targets:
            if key not in DEFINITIONS:
                raise ConflictError('unknown_setting', '未登记的设置键: %s' % key)
            updates[key] = DEFINITIONS[key]['default']
        return self.update(updates, expected_revisions=expected_revisions or {}, actor=actor)

    # ---- 域回执（applied；防乱序 + 绑定版本/epoch/摘要）----
    def apply_feedback(self, scope, epoch, revision, keys=None, digest=None):
        """域服务回报已应用；epoch 过期、revision 倒退/未来、摘要不符一律拒绝。"""
        current_epoch = self.store.restore_epoch()
        if int(epoch) != current_epoch:
            return {'accepted': [], 'superseded': [], 'rejected': [],
                    'reason': 'stale_epoch', 'current_epoch': current_epoch}
        entries = [e for e in self.effective()
                   if e['scope'] == scope and (keys is None or e['key'] in keys)]
        if not entries:
            raise ConflictError('unknown_scope', '未知作用域: %s' % scope)
        rejected = []
        for e in entries:
            if e['source'] == 'db' and int(revision) > int(e['revision']):
                rejected.append({'key': e['key'], 'reason': 'future_revision'})
        if digest is not None:
            snapshot = {e['key']: e['value'] for e in entries if e['source'] == 'db'}
            if ids.digest_of(snapshot) != digest:
                rejected.append({'key': '*', 'reason': 'digest_mismatch'})
        if rejected:
            return {'accepted': [], 'superseded': [], 'rejected': rejected,
                    'epoch': int(epoch), 'revision': int(revision)}
        accepted, superseded = [], []
        now = ids.now_iso()
        with self.store.tx() as conn:
            for e in entries:
                key = e['key']
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
        return {'accepted': accepted, 'superseded': superseded, 'rejected': [],
                'epoch': int(epoch), 'revision': int(revision)}

    # ---- D06：recorder 模式回执（回执绑定实际模式与 operation）----
    def recorder_feedback(self, mode, revision, epoch, operation_id=None, armed=None):
        entry = self.get('recording.mode')
        if int(revision) != int(entry['revision']):
            return {'accepted': False, 'reason': 'stale_revision',
                    'desired_revision': entry['revision'], 'revision': int(revision)}
        if entry['value'] != mode:
            return {'accepted': False, 'reason': 'mode_mismatch',
                    'desired': entry['value'], 'reported': mode}
        fb = self.apply_feedback('recorder', epoch, revision, keys=['recording.mode'])
        if fb.get('rejected'):
            return {'accepted': False, 'reason': fb['rejected'][0]['reason']}
        return {'accepted': bool(fb.get('accepted')), 'operation_id': operation_id,
                'mode': mode, 'armed': bool(armed),
                'actively_recording': bool(mode == 'manual' and armed),
                'applied_revision': int(revision), 'epoch': int(epoch)}


def _decode(spec, encoded):
    return json.loads(encoded)
