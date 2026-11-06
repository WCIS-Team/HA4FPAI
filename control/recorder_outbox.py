# -*- coding: utf-8 -*-
"""recorder 域回执的持久 outbox（F02）：至少一次、退避、死信、幂等。

规则（规划 §持久回执）：
  * 段封存/trash/purge 事件用 operation_id 标识，payload 带内容摘要；提交后入 outbox。
  * 投递至少一次；失败按指数退避重试，超次数进死信（不再复活）。
  * operation_id 唯一：重复入队幂等（同摘要返回既有），同 id 不同内容报冲突。
"""
import datetime
import json

from . import ids
from .errors import ConflictError


def _iso_plus(seconds):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=float(seconds))).isoformat(timespec='milliseconds')


def enqueue(store, operation_id, kind, payload):
    digest = ids.digest_of(payload)
    row = store.query_one('SELECT * FROM recorder_receipt_outbox WHERE operation_id=?',
                          (operation_id,))
    if row is not None:
        if row['digest'] == digest and row['kind'] == kind:
            return {'deduped': True, 'outbox_id': row['outbox_id'], 'state': row['state']}
        raise ConflictError('operation_conflict',
                            'operation_id %s 已存在但内容不同' % operation_id)
    oid = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO recorder_receipt_outbox(outbox_id, operation_id, kind, payload_json,'
            " digest, state, attempts, next_attempt_at, created_at)"
            " VALUES(?,?,?,?,?, 'pending', 0, ?, ?)",
            (oid, operation_id, kind, json.dumps(payload, ensure_ascii=False),
             digest, now, now))
    return {'deduped': False, 'outbox_id': oid, 'state': 'pending'}


def due(store, now=None):
    now = now or ids.now_iso()
    return [dict(r) for r in store.query(
        "SELECT * FROM recorder_receipt_outbox WHERE state='pending'"
        ' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at', (now,))]


def deliver_due(store, sender, now=None, max_attempts=3, base_delay_s=2):
    """对到期 pending 尝试投递。sender(payload)->真值即成功。返回统计。"""
    now = now or ids.now_iso()
    report = {'delivered': [], 'retried': [], 'dead': []}
    for row in due(store, now):
        payload = json.loads(row['payload_json'])
        try:
            ok = bool(sender(payload))
            err = None
        except Exception as exc:                    # noqa: BLE001
            ok = False
            err = repr(exc)
        attempts = int(row['attempts']) + 1
        if ok:
            with store.tx() as conn:
                conn.execute("UPDATE recorder_receipt_outbox SET state='delivered',"
                             ' attempts=?, delivered_at=?, last_error=NULL WHERE outbox_id=?',
                             (attempts, ids.now_iso(), row['outbox_id']))
            report['delivered'].append(row['operation_id'])
        elif attempts >= int(max_attempts):
            with store.tx() as conn:
                conn.execute("UPDATE recorder_receipt_outbox SET state='dead', attempts=?,"
                             ' last_error=? WHERE outbox_id=?',
                             (attempts, err, row['outbox_id']))
            report['dead'].append(row['operation_id'])
        else:
            nxt = _iso_plus(base_delay_s * (2 ** (attempts - 1)))
            with store.tx() as conn:
                conn.execute('UPDATE recorder_receipt_outbox SET attempts=?, next_attempt_at=?,'
                             ' last_error=? WHERE outbox_id=?',
                             (attempts, nxt, err, row['outbox_id']))
            report['retried'].append(row['operation_id'])
    return report


def status(store, operation_id):
    row = store.query_one('SELECT * FROM recorder_receipt_outbox WHERE operation_id=?',
                          (operation_id,))
    return dict(row) if row else None


def reconcile(store):
    """启动扫描：按状态计数 + 尚未成功的到期项数量（不复活 dead）。"""
    rows = [dict(r) for r in store.query(
        'SELECT state, COUNT(*) AS n FROM recorder_receipt_outbox GROUP BY state')]
    counts = {r['state']: int(r['n']) for r in rows}
    return {'counts': counts, 'pending': counts.get('pending', 0),
            'dead': counts.get('dead', 0), 'delivered': counts.get('delivered', 0)}
