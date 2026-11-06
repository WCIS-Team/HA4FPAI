# -*- coding: utf-8 -*-
"""F02 验收：recorder 持久回执 outbox（幂等/退避/死信/对账）。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_recorder_outbox.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import recorder_outbox as ro    # noqa: E402
from control.errors import ConflictError      # noqa: E402
from control.store import Store               # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def raises(fn, exc_type, name):
    try:
        fn()
        check(False, name + '（未抛出）')
    except exc_type:
        check(True, name)
    except Exception as exc:                 # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-outbox-')
    try:
        store = Store(os.path.join(tmp, 'b.sqlite3'))
        p1 = {'recording_id': 'r1', 'segments': [1, 2], 'state': 'sealed'}
        e1 = ro.enqueue(store, 'op-1', 'segment_sealed', p1)
        check(e1['deduped'] is False and e1['state'] == 'pending', 'F02：首入队 pending')
        e1b = ro.enqueue(store, 'op-1', 'segment_sealed', p1)
        check(e1b['deduped'] is True and e1b['outbox_id'] == e1['outbox_id'],
              'F02：同 operation/摘要 → 幂等去重')
        raises(lambda: ro.enqueue(store, 'op-1', 'segment_sealed', {'x': 1}),
               ConflictError, 'F02：同 operation 不同内容 → 冲突')

        # 投递成功
        sent = []
        rep = ro.deliver_due(store, lambda payload: sent.append(payload) or True)
        check(rep['delivered'] == ['op-1'] and sent and sent[0]['recording_id'] == 'r1',
              'F02：投递成功标记 delivered')

        # 失败 → 退避重试 → 死信
        p2 = {'recording_id': 'r2', 'state': 'purged'}
        ro.enqueue(store, 'op-2', 'purged', p2)
        rep = ro.deliver_due(store, lambda payload: (_ for _ in ()).throw(RuntimeError('net')))
        check(rep['retried'] == ['op-2'], 'F02：首次失败进重试（退避）')
        st = ro.status(store, 'op-2')
        check(st['state'] == 'pending' and st['attempts'] == 1 and st['next_attempt_at'],
              'F02：重试后带 attempts 与 next_attempt_at')
        # 立即再投（未到退避时间）不应命中
        rep = ro.deliver_due(store, lambda payload: True)
        check('op-2' not in rep['delivered'], 'F02：退避未到不重投')
        # 用未来时间推进到死信
        future = '2999-01-01T00:00:00.000+00:00'
        rep = ro.deliver_due(store, lambda payload: (_ for _ in ()).throw(RuntimeError('net')),
                             now=future, max_attempts=2)
        check(rep['dead'] == ['op-2'], 'F02：超过最大尝试 → 死信')
        check(ro.status(store, 'op-2')['state'] == 'dead', 'F02：死信状态落库')
        # 死信不复活
        rep = ro.deliver_due(store, lambda payload: True, now=future)
        check('op-2' not in rep['delivered'], 'F02：死信不被再次投递（不复活）')
        rec = ro.reconcile(store)
        check(rec['dead'] == 1 and rec['delivered'] == 1,
              'F02：对账统计 dead/delivered')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_recorder_outbox: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
