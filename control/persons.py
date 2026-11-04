# -*- coding: utf-8 -*-
"""人员与统一身份：person_id 全链贯穿，姓名只是可改显示属性。

规则（规划 §统一身份与模板契约）：
  * 同名不自动合并；新增登记必须显式选人或显式建人
  * 旧系统 ID（视频 UUID / 声纹库名）经 external_identity_map 映射
  * 删除为软删除并撤销 active 模板；合并保留旧 ID 映射与审计
"""
from . import ids
from .errors import ConflictError, NotFoundError


def _check_name(display_name):
    name = str(display_name or '').strip()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise ConflictError('invalid_name', '姓名需为 1～40 个可显示字符')
    return name


def _get_conn(conn, person_id):
    row = conn.execute('SELECT * FROM persons WHERE person_id=?', (person_id,)).fetchone()
    if row is None:
        raise NotFoundError('person_not_found', '人员不存在: %s' % person_id)
    return dict(row)


def get(store, person_id):
    return store.query_one('SELECT * FROM persons WHERE person_id=?', (person_id,))


def list_all(store, include_deleted=False, limit=200, offset=0):
    where = '' if include_deleted else "WHERE status='active'"
    return store.query(
        'SELECT * FROM persons %s ORDER BY created_at LIMIT ? OFFSET ?' % where,
        (int(limit), int(offset)))


def create(store, events, display_name, actor=None):
    name = _check_name(display_name)
    person_id = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO persons(person_id, display_name, status, revision, created_at, updated_at)'
            " VALUES(?,?, 'active', 1, ?, ?)",
            (person_id, name, now, now))
        store.bump_domain_revision(conn)
    events.append('person.created', {'display_name': name}, person_id=person_id)
    return get(store, person_id)


def update(store, events, person_id, display_name=None, status=None,
           expected_revision=None, actor=None):
    with store.tx() as conn:
        row = _get_conn(conn, person_id)
        if expected_revision is not None and int(expected_revision) != row['revision']:
            raise ConflictError('revision_conflict',
                                '人员修订号不匹配（期望 %s，当前 %s）'
                                % (expected_revision, row['revision']),
                                {'person_id': person_id, 'current_revision': row['revision']})
        name = _check_name(display_name) if display_name is not None else row['display_name']
        status = status or row['status']
        conn.execute(
            'UPDATE persons SET display_name=?, status=?, revision=revision+1, updated_at=?'
            ' WHERE person_id=?',
            (name, status, ids.now_iso(), person_id))
        store.bump_domain_revision(conn)
        row = _get_conn(conn, person_id)
    events.append('person.updated', {'display_name': row['display_name'], 'status': row['status']},
                  person_id=person_id)
    return row


def delete(store, events, person_id, actor=None):
    """软删除：撤销 active 模板；旧缓存不得再据此授权（授权端在线核验撤销版本）。"""
    now = ids.now_iso()
    with store.tx() as conn:
        _get_conn(conn, person_id)
        conn.execute("UPDATE persons SET status='deleted', deleted_at=?, updated_at=?,"
                     ' revision=revision+1 WHERE person_id=?', (now, now, person_id))
        for table in ('face_samples', 'voice_samples'):
            conn.execute('UPDATE %s SET active=0, revoked_at=? WHERE person_id=? AND active=1'
                         % table, (now, person_id))
        store.bump_domain_revision(conn)
    events.append('person.deleted', {}, person_id=person_id)
    return get(store, person_id)


def merge(store, events, source_person_id, target_person_id, actor=None):
    """合并：样本与旧 ID 映射整体改挂到目标；来源保留 merged 状态可追溯。"""
    if source_person_id == target_person_id:
        raise ConflictError('merge_self', '不能把人员合并到自身')
    now = ids.now_iso()
    with store.tx() as conn:
        src = _get_conn(conn, source_person_id)
        tgt = _get_conn(conn, target_person_id)
        if src['status'] != 'active' or tgt['status'] != 'active':
            raise ConflictError('merge_inactive', '只能合并 active 状态的人员')
        for table in ('face_samples', 'voice_samples'):
            conn.execute('UPDATE %s SET person_id=? WHERE person_id=?' % table,
                         (target_person_id, source_person_id))
        conn.execute('UPDATE external_identity_map SET person_id=? WHERE person_id=?',
                     (target_person_id, source_person_id))
        conn.execute(
            "INSERT INTO external_identity_map(namespace, source_store_id, legacy_key,"
            " person_id, state, created_at) VALUES('merge', ?, ?, ?, 'linked', ?)",
            (store.meta('store_id'), source_person_id, target_person_id, now))
        conn.execute("UPDATE persons SET status='merged', deleted_at=?, updated_at=?,"
                     ' revision=revision+1 WHERE person_id=?', (now, now, source_person_id))
        conn.execute('UPDATE persons SET revision=revision+1, updated_at=? WHERE person_id=?',
                     (now, target_person_id))
        store.bump_domain_revision(conn)
    events.append('person.merged',
                  {'source_person_id': source_person_id, 'target_person_id': target_person_id},
                  person_id=target_person_id)
    return get(store, target_person_id)


def identity_add(store, namespace, source_store_id, legacy_key, person_id, state='linked'):
    """登记旧系统 ID → person_id 映射；同名只生成待核对记录，不自动认定同一人。"""
    now = ids.now_iso()
    with store.tx() as conn:
        _get_conn(conn, person_id)
        conn.execute(
            'INSERT INTO external_identity_map(namespace, source_store_id, legacy_key,'
            ' person_id, state, created_at) VALUES(?,?,?,?,?,?)',
            (namespace, source_store_id, legacy_key, person_id, state, now))
    return {'namespace': namespace, 'source_store_id': source_store_id,
            'legacy_key': legacy_key, 'person_id': person_id, 'state': state}


def identity_resolve(store, namespace, source_store_id, legacy_key):
    return store.query_one(
        'SELECT * FROM external_identity_map WHERE namespace=? AND source_store_id=?'
        ' AND legacy_key=?', (namespace, source_store_id, legacy_key))
