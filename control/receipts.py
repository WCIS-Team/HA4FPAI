# -*- coding: utf-8 -*-
"""录像回执接收端（V6 的 HA 侧半边）：recorder 经 outbox 协议提交，本模块落库。

规划 §文件与数据库的一致性 / §录像迁移不变量：
  * recorder 拥有媒体生命周期与提交协议（part/ready/fsync/最终文件）；
    本模块**只镜像回执**，绝不发明录像状态——complete 只在 recorder 的
    segment-complete 回执（且声明文件齐备）时落库。
  * 幂等：operation_id 唯一；同 op 同内容重复投递 → deduplicated；同 op 异内容 → 冲突。
  * 回执不复活：restore 只对 trash_state='deleted' 生效；purging/purged 一律拒绝。
  * 软删不挪媒体、purging 先持久化再逐文件删——这些仍在 recorder 侧；本模块只记业务目录。
视频侧发送端（recorder 适配器）属视频仓工作，接法见 docs/VIDEO-DOMAIN-PLACEHOLDER.md V6。
"""
from . import ids
from .errors import ConflictError, DomainError

KINDS = ('segment-complete', 'trash', 'restore', 'purge-progress', 'purge-done', 'abandon')


def _require(recipt, key):
    value = recipt.get(key)
    if not value or not isinstance(value, str):
        raise DomainError('invalid_receipt', '回执缺少 %s' % key, {'field': key})
    return value


def ingest(store, events, receipt):
    """处理一条回执；返回 {'deduplicated':bool, 'recording':row|None}。"""
    if not isinstance(receipt, dict):
        raise DomainError('invalid_receipt', '回执必须是对象')
    operation_id = _require(receipt, 'operation_id')
    kind = _require(receipt, 'kind')
    if kind not in KINDS:
        raise DomainError('invalid_receipt', '未知回执类型: %s' % kind, {'kinds': list(KINDS)})
    prev = store.query_one('SELECT * FROM media_operations WHERE operation_id=?', (operation_id,))
    if prev is not None:
        same = (prev['kind'] == kind and prev['state'] == receipt.get('state', 'done')
                and (prev['recording_id'] or '') == (receipt.get('recording_id') or ''))
        if same:
            return {'deduplicated': True, 'recording': None}
        raise ConflictError('receipt_conflict', '同一 operation_id 对应不同回执内容',
                            {'operation_id': operation_id})
    recording_id = receipt.get('recording_id')
    if kind != 'abandon' and not recording_id:
        raise DomainError('invalid_receipt', '回执类型 %s 需要 recording_id' % kind)
    now = ids.now_iso()
    state = receipt.get('state') or ('done' if kind != 'purge-progress' else 'in-progress')
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO media_operations(operation_id, kind, recording_id, media_id,'
            ' state, detail, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)',
            (operation_id, kind, recording_id, receipt.get('media_id'), state,
             ids.canonical_json(receipt.get('payload') or {}), now, now))
        recording = None
        if kind == 'segment-complete':
            # 只信 recorder 的完成回执；摘要字段有则存，无则 NULL，不猜
            existing = conn.execute('SELECT * FROM recordings WHERE recording_id=?',
                                    (recording_id,)).fetchone()
            fields = receipt.get('recording') or {}
            if existing is None:
                conn.execute(
                    'INSERT INTO recordings(recording_id, device_id, format, index_format,'
                    ' first_wall_time, last_wall_time, duration_s, size_bytes, trash_state,'
                    ' legacy_ref, created_at, updated_at)'
                    " VALUES(?,?,?,?,?,?,?,?, 'none', ?, ?, ?)",
                    (recording_id, fields.get('device_id'), fields.get('format'),
                     fields.get('index_format'), fields.get('first_wall_time'),
                     fields.get('last_wall_time'), fields.get('duration_s'),
                     fields.get('size_bytes'), receipt.get('legacy_ref'), now, now))
            else:
                conn.execute('UPDATE recordings SET updated_at=? WHERE recording_id=?',
                             (now, recording_id))
            recording = conn.execute('SELECT * FROM recordings WHERE recording_id=?',
                                     (recording_id,)).fetchone()
        elif kind in ('trash', 'restore', 'purge-progress', 'purge-done'):
            row = conn.execute('SELECT trash_state FROM recordings WHERE recording_id=?',
                               (recording_id,)).fetchone()
            if row is None:
                raise ConflictError('recording_unknown', '回执指向未知录像: %s' % recording_id)
            if kind == 'trash':
                conn.execute("UPDATE recordings SET trash_state='deleted', deleted_at=?,"
                             ' updated_at=? WHERE recording_id=?', (now, now, recording_id))
            elif kind == 'restore':
                if dict(row)['trash_state'] in ('purging', 'purged'):
                    raise ConflictError('restore_after_purge', 'purging/purged 不可恢复（回执不复活）')
                conn.execute("UPDATE recordings SET trash_state='none', deleted_at=NULL,"
                             ' updated_at=? WHERE recording_id=?', (now, recording_id))
            elif kind == 'purge-progress':
                conn.execute("UPDATE recordings SET trash_state='purging', purge_started_at=?,"
                             ' updated_at=? WHERE recording_id=?', (now, now, recording_id))
            else:
                conn.execute("UPDATE recordings SET trash_state='purged', purge_finished_at=?,"
                             ' updated_at=? WHERE recording_id=?', (now, now, recording_id))
        # abandon：只记 media_operations，录像行不动（recorder 保留了失败现场）
        store.bump_domain_revision(conn)
        if recording is not None:
            recording = dict(recording)
    events.append('recording.receipt',
                  {'operation_id': operation_id, 'kind': kind,
                   'recording_id': recording_id, 'state': state},
                  recording_id=recording_id)
    return {'deduplicated': False,
            'recording': recording,
            'operation_id': operation_id}
