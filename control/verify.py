# -*- coding: utf-8 -*-
"""融合授权：同一身份 + 新鲜证据（E04）。

规则（规划 §融合授权）：
  * 一次核验 = 一个 session（绑定 purpose/有效期/挑战 boot）。
  * face 与 voice 证据必须落到**同一个 person_id**；任一 reject/不新鲜/过期/重放即拒。
  * 证据新鲜以单调时钟窗口判定；旧 boot、旧缓存帧、重复 frame 一律不算。
  * 授权结果写回 session 状态，动作本身仍走统一确认/权限（由 commands 层负责）。
"""
import datetime

from . import ids
from .errors import ConflictError, NotFoundError

FRESH_WINDOW_NS = 2 * 1000 * 1000 * 1000      # 2s 证据新鲜窗口
MODALITIES = ('face', 'voice')


def _iso_plus(seconds):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=int(seconds))).isoformat(timespec='milliseconds')


def open_session(store, purpose='authorize', ttl_s=60, boot_id=None):
    sid = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute('INSERT INTO verification_sessions(session_id, purpose, status,'
                     ' created_at, expires_at) VALUES(?,?,?,?,?)',
                     (sid, purpose, 'open', now, _iso_plus(ttl_s)))
    row = get_session(store, sid)
    row['boot_id'] = boot_id or ids.boot_id()
    return row


def get_session(store, session_id):
    row = store.query_one('SELECT * FROM verification_sessions WHERE session_id=?',
                          (session_id,))
    if row is None:
        raise NotFoundError('session_unknown', '核验会话不存在: %s' % session_id)
    return dict(row)


def _expired(store, session):
    import time
    now_iso = ids.now_iso()
    # 用字符串比较（ISO 同格式可比）；过期即不可再提交/授权
    return session['expires_at'] is not None and now_iso > session['expires_at']


def submit_evidence(store, session_id, modality, person_id, verdict, *,
                    model_id=None, template_revision=None, frame_id=None,
                    frame_token=None, captured_monotonic_ns=None, boot_id=None,
                    raw_json=None, now_monotonic_ns=None, window_ns=FRESH_WINDOW_NS):
    if modality not in MODALITIES:
        raise ConflictError('invalid_modality', 'modality 只能是 face/voice')
    if verdict not in ('accept', 'reject'):
        raise ConflictError('invalid_verdict', 'verdict 只能是 accept/reject')
    session = get_session(store, session_id)
    if session['status'] != 'open':
        raise ConflictError('session_closed', '会话已关闭: %s' % session['status'])
    if _expired(store, session):
        raise ConflictError('session_expired', '会话已过期')
    if person_id is not None:
        p = store.query_one('SELECT status FROM persons WHERE person_id=?', (person_id,))
        if p is None or p['status'] != 'active':
            raise ConflictError('person_inactive', '人员不存在或非 active: %s' % person_id)
    current_boot = ids.boot_id()
    if boot_id is not None and boot_id != current_boot:
        raise ConflictError('stale_boot', '证据来自旧 boot，不可用于本会话')
    now_mono = ids.monotonic_ns() if now_monotonic_ns is None else int(now_monotonic_ns)
    fresh = 1 if (captured_monotonic_ns is not None
                  and abs(now_mono - int(captured_monotonic_ns)) <= int(window_ns)) else 0
    if frame_id is not None:
        used = store.query_one('SELECT evidence_id FROM verification_evidence WHERE frame_id=?',
                               (frame_id,))
        if used is not None:
            raise ConflictError('replayed_frame', '该帧已被使用过（拒重放）: %s' % frame_id)
    eid = ids.new_id()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO verification_evidence(evidence_id, session_id, modality, person_id,'
            ' verdict, model_id, template_revision, frame_id, frame_token,'
            ' captured_monotonic_ns, wall_time, fresh, raw_json, created_at)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (eid, session_id, modality, person_id, verdict, model_id, template_revision,
             frame_id, frame_token, captured_monotonic_ns, ids.now_iso(), fresh,
             raw_json, ids.now_iso()))
    return {'evidence_id': eid, 'fresh': bool(fresh), 'modality': modality,
            'person_id': person_id, 'verdict': verdict}


def authorize(store, session_id):
    session = get_session(store, session_id)
    if session['status'] != 'open':
        return {'authorized': False, 'reason': 'session_closed', 'status': session['status']}
    if _expired(store, session):
        return {'authorized': False, 'reason': 'session_expired'}
    rows = [dict(r) for r in store.query(
        'SELECT * FROM verification_evidence WHERE session_id=? ORDER BY created_at',
        (session_id,))]
    by_mod = {}
    for row in rows:
        if row['verdict'] != 'accept' or not row['fresh'] or row['person_id'] is None:
            continue
        by_mod[row['modality']] = row          # 后到覆盖，取最新
    if 'face' not in by_mod or 'voice' not in by_mod:
        return {'authorized': False, 'reason': 'missing_modality',
                'have': sorted(by_mod.keys())}
    if by_mod['face']['person_id'] != by_mod['voice']['person_id']:
        return {'authorized': False, 'reason': 'identity_mismatch',
                'face': by_mod['face']['person_id'], 'voice': by_mod['voice']['person_id']}
    person_id = by_mod['face']['person_id']
    with store.tx() as conn:
        conn.execute("UPDATE verification_sessions SET status='authorized' WHERE session_id=?",
                     (session_id,))
    return {'authorized': True, 'person_id': person_id,
            'face_evidence': by_mod['face']['evidence_id'],
            'voice_evidence': by_mod['voice']['evidence_id']}
