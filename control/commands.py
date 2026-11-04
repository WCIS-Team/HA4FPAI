# -*- coding: utf-8 -*-
"""统一命令管线：输入 → 校验 → 确认 → 幂等落库 → 域执行 → 状态与事件。

规划 §命令、任务与重试：
  * 状态 proposed/awaiting_confirmation/accepted/running/succeeded/failed/
    cancelled/unknown；HTTP 成功不等于动作成功
  * 幂等键唯一：同键同摘要复用原命令；同键异参 409
  * 危险动作必须持绑定（动作+参数摘要+主体+有效期+一次性）的确认
  * unknown 表示无法确认下游是否执行；先查真实状态，不盲目重试
  * 迟到旧版本应用按 (scope, epoch, revision) 拒绝（settings.apply_feedback）
"""
import json

from . import ids
from .errors import (ConflictError, DomainError, ForbiddenError, NotFoundError,
                     UnknownOutcome)

STATUS_ACCEPTED = 'accepted'
STATUS_RUNNING = 'running'
STATUS_SUCCEEDED = 'succeeded'
STATUS_FAILED = 'failed'
STATUS_UNKNOWN = 'unknown'
STATUS_CANCELLED = 'cancelled'


def canonical_params(params):
    return ids.canonical_json(params or {})


def validate_params(spec, params):
    rules = spec.get('params') or {}
    problems = []
    for name, rule in rules.items():
        value = params.get(name)
        required = rule.get('required', False)
        if value is None:
            if required:
                problems.append({'param': name, 'reason': 'missing'})
            continue
        ptype = rule.get('type', 'str')
        if ptype == 'int':
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append({'param': name, 'reason': 'not_int'})
                continue
            if 'min' in rule and value < rule['min']:
                problems.append({'param': name, 'reason': 'too_small'})
            if 'max' in rule and value > rule['max']:
                problems.append({'param': name, 'reason': 'too_large'})
        elif ptype == 'float':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append({'param': name, 'reason': 'not_number'})
        elif ptype == 'enum':
            if value not in rule['choices']:
                problems.append({'param': name, 'reason': 'invalid_choice',
                                 'choices': rule['choices']})
        elif ptype == 'list':
            if not isinstance(value, list):
                problems.append({'param': name, 'reason': 'not_list'})
        elif ptype == 'obj':
            if not isinstance(value, dict):
                problems.append({'param': name, 'reason': 'not_object'})
        else:
            if not isinstance(value, str):
                problems.append({'param': name, 'reason': 'not_str'})
    for name in params:
        if name not in rules:
            problems.append({'param': name, 'reason': 'unexpected'})
    return problems


def _public(row):
    return {'request_id': row['request_id'],
            'command_id': row['command_id'],
            'status': row['status'],
            'code': row['code'],
            'message': row['message'],
            'desired_revision': row['desired_revision'],
            'applied_revision': row['applied_revision'],
            'data': json.loads(row['result_json']) if row['result_json'] else None,
            'event_id': row['event_id']}


def get_command(store, command_id):
    row = store.query_one('SELECT * FROM commands WHERE command_id=?', (command_id,))
    if row is None:
        raise NotFoundError('command_not_found', '命令不存在: %s' % command_id)
    return _public(row)


def create(store, registry, events, action, params=None, target=None,
           idempotency_key=None, request_id=None, session_id=None, client_id=None,
           principal_id=None, expected_revision=None, confirmation_id=None):
    spec = registry.get(action)
    if spec is None:
        raise NotFoundError('unknown_action', '未登记的动作: %s' % action)
    params = params or {}
    request_id = request_id or ids.new_id()
    params_digest = ids.digest_of({'action': action, 'target': target, 'params': params})
    idempotency_key = idempotency_key or (
        (principal_id or 'anon') + ':' + request_id)

    row = store.query_one('SELECT * FROM commands WHERE idempotency_key=?', (idempotency_key,))
    if row is not None:
        if (row['params_digest'] == params_digest and row['action'] == action
                and (row['target'] or '') == (target or '')):
            return {'replayed': True, 'command': _public(row)}
        raise ConflictError('idempotency_conflict',
                            '同一幂等键对应不同请求', {'command_id': row['command_id']})

    problems = validate_params(spec, params)
    if problems:
        raise DomainError('invalid_params', '参数校验失败', {'problems': problems})

    if spec.get('destructive'):
        _consume_confirmation(store, confirmation_id, action, params_digest,
                              principal_id, request_id)

    command_id = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO commands(command_id, request_id, idempotency_key, principal_id,'
            ' session_id, client_id, action, target, params_json, params_digest, status,'
            ' created_at, updated_at)'
            " VALUES(?,?,?,?,?,?,?,?,?,?,'accepted',?,?)",
            (command_id, request_id, idempotency_key, principal_id, session_id,
             client_id, action, target, canonical_params(params), params_digest, now, now))
        conn.execute(
            'INSERT INTO command_attempts(attempt_id, command_id, operation_id, started_at,'
            " outcome) VALUES(?,?,?,?, 'running')",
            (ids.new_id(), command_id, ids.new_id(), now))

    run = spec.get('run')
    try:
        outcome = run(params=params, target=target, expected_revision=expected_revision,
                      principal_id=principal_id, command_id=command_id) if run else {'data': None}
    except DomainError as exc:
        _finish(store, events, command_id, STATUS_FAILED, exc.code, exc.message,
                exc.data, None, None)
    except UnknownOutcome as exc:
        _finish(store, events, command_id, STATUS_UNKNOWN, 'unknown_outcome',
                '无法确认是否执行：%s' % exc.detail, {'operation_id': exc.operation_id},
                None, None)
    else:
        data = outcome.get('data')
        message = outcome.get('message')
        code = outcome.get('code')
        _finish(store, events, command_id, STATUS_SUCCEEDED, code, message,
                data, outcome.get('desired_revision'), outcome.get('applied_revision'))

    if spec.get('destructive') and confirmation_id:
        with store.tx() as conn:
            conn.execute('UPDATE pending_confirmations SET command_id=? WHERE confirmation_id=?',
                         (command_id, confirmation_id))
    return {'replayed': False, 'command': get_command(store, command_id)}


def _consume_confirmation(store, confirmation_id, action, params_digest,
                          principal_id, request_id):
    if not confirmation_id:
        from .errors import ConfirmationRequired
        raise ConfirmationRequired(action)
    now = ids.now_iso()
    with store.tx() as conn:
        row = conn.execute('SELECT * FROM pending_confirmations WHERE confirmation_id=?',
                           (confirmation_id,)).fetchone()
        if row is None:
            raise ForbiddenError('confirmation_invalid', '确认不存在')
        row = dict(row)
        if row['consumed_at']:
            raise ForbiddenError('confirmation_used', '确认已使用（一次性）')
        if row['expires_at'] <= now:
            raise ForbiddenError('confirmation_expired', '确认已过期')
        if row['action'] != action or row['params_digest'] != params_digest:
            raise ForbiddenError('confirmation_mismatch', '确认与动作/参数不匹配')
        if principal_id and row['principal_id'] and row['principal_id'] != principal_id:
            raise ForbiddenError('confirmation_actor', '确认属于其他主体')
        conn.execute('UPDATE pending_confirmations SET consumed_at=? WHERE confirmation_id=?',
                     (now, confirmation_id))


def _finish(store, events, command_id, status, code, message, data,
            desired_revision, applied_revision):
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'UPDATE commands SET status=?, code=?, message=?, result_json=?,'
            ' desired_revision=?, applied_revision=?, updated_at=? WHERE command_id=?',
            (status, code, message,
             json.dumps(data, ensure_ascii=False) if data is not None else None,
             desired_revision, applied_revision, now, command_id))
        conn.execute("UPDATE command_attempts SET outcome=?, finished_at=? WHERE command_id=?",
                     (status, now, command_id))
    event = events.append('command.' + status,
                          {'command_id': command_id, 'code': code,
                           'message': message, 'data': data})
    with store.tx() as conn:
        conn.execute('UPDATE commands SET event_id=? WHERE command_id=?',
                     (event['event_id'], command_id))


def fail_unknown(store, events, command_id, detail=''):
    """显式把命令置为 unknown（例如回执丢失）；调用方随后核对真实状态。"""
    row = store.query_one('SELECT * FROM commands WHERE command_id=?', (command_id,))
    if row is None:
        raise NotFoundError('command_not_found', '命令不存在: %s' % command_id)
    if row['status'] in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_UNKNOWN):
        raise ConflictError('status_final', '命令已是终态 %s' % row['status'])
    operation = store.query_one(
        'SELECT operation_id FROM command_attempts WHERE command_id=?'
        ' ORDER BY started_at DESC LIMIT 1', (command_id,))
    _finish(store, events, command_id, STATUS_UNKNOWN, 'unknown_outcome',
            detail or '执行回执丢失', {'operation_id': operation['operation_id'] if operation else None},
            None, None)
    return get_command(store, command_id)


def create_confirmation(store, registry, action, params, target=None,
                        principal_id=None, ttl_s=120):
    spec = registry.get(action)
    if spec is None:
        raise NotFoundError('unknown_action', '未登记的动作: %s' % action)
    params = params or {}
    digest = ids.digest_of({'action': action, 'target': target, 'params': params})
    confirmation_id = ids.new_id()
    import datetime
    expires = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(seconds=int(ttl_s))).isoformat(timespec='milliseconds')
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO pending_confirmations(confirmation_id, principal_id, action,'
            ' params_digest, target, created_at, expires_at) VALUES(?,?,?,?,?,?,?)',
            (confirmation_id, principal_id, action, digest, target, ids.now_iso(), expires))
    return {'confirmation_id': confirmation_id, 'action': action,
            'params_digest': digest, 'expires_at': expires}
