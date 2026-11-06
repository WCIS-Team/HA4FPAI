# -*- coding: utf-8 -*-
"""G02/G03 验收：语音多轮/危险确认状态机、控制台读模型与可续游标。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_g_voice_readmodel.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import ids, persons, readmodel, samples, voice_session as vs   # noqa: E402
from control.events import EventLog                                        # noqa: E402
from control.settings import Settings                                      # noqa: E402
from control.store import Store                                            # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-g-')
    try:
        # ---- G02 语音多轮 ----
        s = vs.new_session()
        r = vs.ask_confirmation(s, 'record.start', 'digest-1')
        check(r['action'] == 'ask' and s['state'] == 'awaiting_confirm',
              'G02：危险操作先问确认')
        rr = vs.handle(s, vs.CONFIRM, params_digest='wrong')
        check(rr['action'] == 'noop' and not s['executed'], 'G02：确认内容不符 → 拒绝不执行')
        ok = vs.handle(s, vs.CONFIRM, params_digest='digest-1')
        check(ok['action'] == 'execute' and s['executed'], 'G02：正确确认 → 执行一次')
        dup = vs.handle(s, vs.CONFIRM, params_digest='digest-1')
        check(dup['action'] == 'noop' and '已经执行' in dup['reply'],
              'G02：重复确认不双执行')
        # 否定取消
        s2 = vs.new_session()
        vs.ask_confirmation(s2, 'record.stop', 'd2')
        neg = vs.handle(s2, vs.NEGATION)
        check(neg['action'] == 'cancel' and s2['pending'] is None, 'G02：否定 → 取消')
        # 超时 unknown
        s3 = vs.new_session()
        vs.ask_confirmation(s3, 'purge.delete', 'd3')
        to = vs.handle(s3, vs.TIMEOUT)
        check(to['action'] == 'unknown' and s3['unknown'] is True,
              'G02：确认超时 → 标记 unknown')
        # 固定回复/疑问只用结构化结果，不掺 LLM
        s4 = vs.new_session()
        fixed = vs.handle(s4, vs.FIXED, structured={'status': 'succeeded', 'message': '音量已设为 30'})
        check('音量已设为 30' in fixed['reply'] and fixed['action'] is None,
              'G02：固定回复来自结构化结果')
        q = vs.handle(s4, vs.QUESTION, info_text='当前录像模式是 off')
        check(q['action'] is None and 'off' in q['reply'], 'G02：疑问只答结构化信息')
        cl = vs.handle(s4, vs.CLARIFY, clarify_question='你要设置音量还是阈值？')
        check(cl['action'] == 'clarify' and s4['state'] == 'awaiting_clarify',
              'G02：澄清进入 awaiting_clarify')
        check('无法确认' in vs.render_result({'status': 'unknown'}), 'G02：unknown 结果措辞如实')

        # ---- G03 读模型 ----
        store = Store(os.path.join(tmp, 'b.sqlite3'))
        store.settings = Settings(store)
        ev = EventLog(store)
        p = persons.create(store, ev, '甲')['person_id']
        samples.register_model(store, ev, 'face-4', 'face', 4, model_sha256='1' * 64)
        samples.enroll(store, ev, 'face', p, 'face-4', [0.1, 0.2, 0.3, 0.4])
        now = ids.now_iso()
        with store.tx() as conn:
            conn.execute('INSERT INTO recordings(recording_id, device_id, trash_state,'
                         " size_bytes, created_at, updated_at) VALUES('r1','cam','none',"
                         ' 1024, ?, ?)', (now, now))
            conn.execute('INSERT INTO tasks(task_id, action, status, params_json, created_at,'
                         " updated_at) VALUES('t1','system.time','active','{}',?,?)", (now, now))
            conn.execute('INSERT INTO commands(command_id, request_id, idempotency_key, action,'
                         " params_json, params_digest, status, created_at, updated_at)"
                         " VALUES('cmd00001','req1','k1','system.time','{}',?, 'succeeded',?,?)",
                         ('0' * 64, now, now))
            conn.execute("INSERT INTO command_attempts(attempt_id, command_id, operation_id,"
                         " started_at, finished_at, outcome) VALUES('a0000001','cmd00001','op1',?,?,"
                         "'succeeded')", (now, now))
        e1 = ev.append('test.one', {'x': 1})
        e2 = ev.append('test.two', {'x': 2})
        with store.tx() as conn:
            conn.execute('UPDATE commands SET event_id=? WHERE command_id=?',
                         (e1['event_id'], 'cmd00001'))
        store.settings.update({'audio.output.volume': 33})     # 未回执 → pending

        snap = readmodel.snapshot(store)
        check(snap['counts']['persons'] == 1 and snap['counts']['events'] >= 2,
              'G03：快照计数正确')
        check(snap['persons'][0]['display_name'] == '甲' and snap['recordings'][0]['recording_id'] == 'r1',
              'G03：人员/录像列表可见')
        vol = [x for x in snap['settings'] if x['key'] == 'audio.output.volume'][0]
        check(vol['pending'] is True and vol['applied'] is False,
              'G03：设置 desired/applied/pending 可见')

        page1 = readmodel.events_page(store, after_cursor=0, limit=2)
        check(page1['has_more'] and len(page1['items']) == 2, 'G03：事件分页第一页')
        page2 = readmodel.events_page(store, after_cursor=page1['next_cursor'], limit=100)
        ids1 = {x['event_id'] for x in page1['items']}
        ids2 = {x['event_id'] for x in page2['items']}
        check(not (ids1 & ids2), 'G03：游标续读不重复')
        check(e1['event_id'] in ids1 | ids2 and e2['event_id'] in ids1 | ids2,
              'G03：事件均可见（无漏）')
        trace = readmodel.command_trace(store, 'cmd00001')
        check(trace and trace['attempts'][0]['operation_id'] == 'op1' and trace['event'],
              'G03：命令可追踪到 attempt 与 event')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_g_voice_readmodel: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
