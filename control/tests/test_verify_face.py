# -*- coding: utf-8 -*-
"""E02/E04 验收：人脸 extract-only 合同、融合授权（同一身份+新鲜证据）。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_verify_face.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import face_extract, ids, persons, samples, verify   # noqa: E402
from control.errors import ConflictError, DomainError            # noqa: E402
from control.events import EventLog                              # noqa: E402
from control.store import Store                                  # noqa: E402

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
    except Exception as exc:                            # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


class MockExtractor(object):
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def extract(self, source):
        self.calls += 1
        return dict(self.payload)


SHA = 'a' * 64


def good_payload(**over):
    p = {'embedding': [0.1, 0.2, 0.3, 0.4], 'pipeline_id': 'yunyu-mf-npu:abc',
         'quality': 0.9, 'source_sha256': SHA, 'model_id': 'face-4', 'faces': 1}
    p.update(over)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-verify-')
    try:
        store = Store(os.path.join(tmp, 'b.sqlite3'))
        ev = EventLog(store)
        samples.register_model(store, ev, 'face-4', 'face', 4, model_sha256='1' * 64)
        samples.register_model(store, ev, 'voice-4', 'voice', 4, model_sha256='2' * 64)
        alice = persons.create(store, ev, '爱丽丝')['person_id']
        bob = persons.create(store, ev, '鲍勃')['person_id']

        # ---- E02 extract-only 合同 ----
        gallery_dir = os.path.join(tmp, 'gallery')
        os.makedirs(gallery_dir)
        ad = MockExtractor(good_payload())
        res = face_extract.extract(ad, b'image-bytes')
        check(res['embedding'] == [0.1, 0.2, 0.3, 0.4] and ad.calls == 1,
              'E02：注入式提取归一化并调用一次')
        sample = face_extract.enroll_extracted(store, ev, alice,
                                               good_payload(quality=0.9),
                                               expected_pipeline='yunyu-mf-npu:abc')
        check(sample.get('model_id') == 'face-4' and sample['source_ref'] == 'extract:' + SHA,
              'E02：提取结果提交业务库（face 样本，source_ref=extract:sha）')
        check(os.listdir(gallery_dir) == [], 'E02：提取路径不写图库（图库目录仍空）')
        # 一人多样本
        s2 = face_extract.enroll_extracted(store, ev, alice,
                                           good_payload(source_sha256='b' * 64, quality=0.8))
        n_face = store.query_one("SELECT COUNT(*) AS n FROM face_samples WHERE person_id=?",
                                 (alice,))['n']
        check(n_face == 2, 'E02：同一人可登记多种本')
        raises(lambda: face_extract.enroll_extracted(store, ev, alice,
                                                     good_payload(quality=0.2)),
               ConflictError, 'E02：低质量拒绝')
        raises(lambda: face_extract.enroll_extracted(store, ev, alice, good_payload(),
                                                     expected_pipeline='other-pipe'),
               ConflictError, 'E02：pipeline 不符拒绝')
        raises(lambda: face_extract.extract(MockExtractor(good_payload(faces=2)), b'x'),
               ConflictError, 'E02：多脸拒绝')
        raises(lambda: face_extract.extract(
            MockExtractor(good_payload(embedding=['x', 1, 2, 3])), b'x'),
               ConflictError, 'E02：坏向量拒绝')
        raises(lambda: face_extract.enroll_extracted(store, ev, alice,
                                                     good_payload(model_id='voice-4')),
               ConflictError, 'E02：错模型（voice 当 face）拒绝')

        # ---- E04 融合授权 ----
        sess = verify.open_session(store, purpose='authorize', ttl_s=60)
        sid = sess['session_id']
        now = ids.monotonic_ns()
        verify.submit_evidence(store, sid, 'face', alice, 'accept',
                               captured_monotonic_ns=now - 1000, now_monotonic_ns=now,
                               frame_id='f1')
        verify.submit_evidence(store, sid, 'voice', alice, 'accept',
                               captured_monotonic_ns=now - 1000, now_monotonic_ns=now,
                               frame_id='v1')
        r = verify.authorize(store, sid)
        check(r['authorized'] is True and r['person_id'] == alice,
              'E04：同人 face+voice 新鲜证据 → 授权')

        # A脸 + B声 不能合成授权
        s2sess = verify.open_session(store)
        verify.submit_evidence(store, s2sess['session_id'], 'face', alice, 'accept',
                               captured_monotonic_ns=ids.monotonic_ns(),
                               now_monotonic_ns=ids.monotonic_ns(), frame_id='f2')
        verify.submit_evidence(store, s2sess['session_id'], 'voice', bob, 'accept',
                               captured_monotonic_ns=ids.monotonic_ns(),
                               now_monotonic_ns=ids.monotonic_ns(), frame_id='v2')
        r2 = verify.authorize(store, s2sess['session_id'])
        check(r2['authorized'] is False and r2['reason'] == 'identity_mismatch',
              'E04：A脸+B声不同人 → identity_mismatch 拒绝')

        # 过期证据（超出窗口）不算
        s3 = verify.open_session(store)
        t = ids.monotonic_ns()
        verify.submit_evidence(store, s3['session_id'], 'face', alice, 'accept',
                               captured_monotonic_ns=t - 5 * 10 ** 9, now_monotonic_ns=t)
        verify.submit_evidence(store, s3['session_id'], 'voice', alice, 'accept',
                               captured_monotonic_ns=t, now_monotonic_ns=t, frame_id='v3')
        r3 = verify.authorize(store, s3['session_id'])
        check(r3['authorized'] is False and r3['reason'] == 'missing_modality',
              'E04：过期 face 证据不计数 → 缺模态拒绝')

        # 重放帧
        s4 = verify.open_session(store)
        verify.submit_evidence(store, s4['session_id'], 'face', alice, 'accept',
                               captured_monotonic_ns=t, now_monotonic_ns=t, frame_id='dup')
        raises(lambda: verify.submit_evidence(store, s4['session_id'], 'voice', alice,
                                              'accept', captured_monotonic_ns=t,
                                              now_monotonic_ns=t, frame_id='dup'),
               ConflictError, 'E04：重复 frame_id → 拒重放')
        # 旧 boot
        raises(lambda: verify.submit_evidence(store, s4['session_id'], 'voice', alice,
                                              'accept', captured_monotonic_ns=t,
                                              now_monotonic_ns=t, boot_id='old-boot'),
               ConflictError, 'E04：旧 boot 证据拒绝')
        # 会话过期
        exp = verify.open_session(store, ttl_s=-1)
        raises(lambda: verify.submit_evidence(store, exp['session_id'], 'face', alice,
                                              'accept', captured_monotonic_ns=t,
                                              now_monotonic_ns=t),
               ConflictError, 'E04：过期会话拒绝提交')
        # 已关闭会话不能再提交
        raises(lambda: verify.submit_evidence(store, sid, 'face', alice, 'accept',
                                              captured_monotonic_ns=ids.monotonic_ns(),
                                              now_monotonic_ns=ids.monotonic_ns()),
               ConflictError, 'E04：已授权(关闭)会话拒绝再提交')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_verify_face: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
