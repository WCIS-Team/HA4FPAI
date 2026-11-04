# -*- coding: utf-8 -*-
"""持久任务：schema、到期调度与幂等尝试（规划 §命令、任务与重试）。

worker 常驻循环属部署层（WP10）；本模块提供 run_due(now) 幂等触发：
  * (task_id, occurrence_key) 唯一 → 重跑不重复执行
  * 破坏性任务到期 → 生成待确认 + 记 needs_confirmation，不自动执行
  * 错过到期按 missed_policy：skip（跳过） / latest（只补最近一次）
"""
import datetime
import json

from . import commands, ids
from .errors import ConflictError, NotFoundError


def create(store, registry, action, params, run_at=None, interval_s=None,
           iana_tz='Asia/Shanghai', missed_policy='skip'):
    if registry is not None and action not in registry:
        raise NotFoundError('unknown_action', '未登记的动作: %s' % action)
    if missed_policy not in ('skip', 'latest'):
        raise ConflictError('invalid_policy', 'missed_policy 只能是 skip/latest')
    if interval_s is not None and int(interval_s) < 60:
        raise ConflictError('interval_too_short', '间隔最小 60 秒（防误配打满 CPU）')
    run_at = run_at or (ids.now_iso() if interval_s is None else None)
    task_id = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO tasks(task_id, action, params_json, run_at, interval_s, iana_tz,'
            ' missed_policy, status, next_due_at, created_at, updated_at)'
            " VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
            (task_id, action, commands.canonical_params(params), run_at,
             int(interval_s) if interval_s is not None else None,
             iana_tz, missed_policy, run_at, now, now))
    return store.query_one('SELECT * FROM tasks WHERE task_id=?', (task_id,))


def get(store, task_id):
    row = store.query_one('SELECT * FROM tasks WHERE task_id=?', (task_id,))
    if row is None:
        raise NotFoundError('task_not_found', '任务不存在: %s' % task_id)
    return row


def list_tasks(store, status=None):
    if status:
        return store.query('SELECT * FROM tasks WHERE status=? ORDER BY created_at', (status,))
    return store.query('SELECT * FROM tasks ORDER BY created_at')


def cancel(store, task_id):
    now = ids.now_iso()
    with store.tx() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise NotFoundError('task_not_found', '任务不存在: %s' % task_id)
        conn.execute("UPDATE tasks SET status='cancelled', updated_at=? WHERE task_id=?",
                     (now, task_id))
    return get(store, task_id)


def _next_due(row, from_iso):
    if row['interval_s']:
        base = from_iso or ids.now_iso()
        dt = datetime.datetime.fromisoformat(base)
        dt = dt + datetime.timedelta(seconds=int(row['interval_s']))
        return dt.isoformat(timespec='milliseconds')
    return None


def run_due(store, registry, events, commands_svc, now=None):
    """触发到期任务；幂等，可由 worker 进程或手工端点调用。"""
    now = now or ids.now_iso()
    due = store.query("SELECT * FROM tasks WHERE status='active' AND next_due_at IS NOT NULL"
                      ' AND next_due_at <= ?', (now,))
    results = []
    for row in due:
        occurrence = row['next_due_at']
        spec = registry.get(row['action']) if registry else None
        state = 'needs_confirmation'
        command = None
        confirm = None
        if spec is not None and not spec.get('destructive'):
            # 命令自管事务；必须在本函数事务外调用（SQLite 不允许嵌套 BEGIN）
            outcome = commands_svc.create(
                store, registry, events, row['action'],
                params=json.loads(row['params_json']),
                idempotency_key='task:%s:%s' % (row['task_id'], occurrence),
                principal_id='task-worker')
            command = outcome['command']
            state = command['status']
        else:
            confirm = commands.create_confirmation(
                store, registry, row['action'], json.loads(row['params_json']),
                principal_id='task-worker')
        attempt_id = ids.new_id()
        try:
            with store.tx() as conn:
                conn.execute(
                    'INSERT INTO task_attempts(attempt_id, task_id, occurrence_key,'
                    ' command_id, state, started_at, finished_at) VALUES(?,?,?,?,?,?,?)',
                    (attempt_id, row['task_id'], occurrence,
                     command['command_id'] if command else None, state, now, now))
                if confirm is not None:
                    conn.execute('UPDATE task_attempts SET state=? WHERE attempt_id=?',
                                 ('needs_confirmation:' + confirm['confirmation_id'],
                                  attempt_id))
                nxt = _next_due(row, occurrence) if row['interval_s'] else None
                if row['interval_s']:
                    conn.execute('UPDATE tasks SET next_due_at=?, updated_at=? WHERE task_id=?',
                                 (nxt, ids.now_iso(), row['task_id']))
                else:
                    conn.execute("UPDATE tasks SET status='done', updated_at=? WHERE task_id=?",
                                 (ids.now_iso(), row['task_id']))
        except Exception:
            # (task_id, occurrence) 撞唯一 = 已处理过；幂等跳过
            continue
        results.append({'task_id': row['task_id'], 'occurrence': occurrence,
                        'state': state, 'command_id': command['command_id'] if command else None,
                        'confirmation_id': confirm['confirmation_id'] if confirm else None})
    return results
