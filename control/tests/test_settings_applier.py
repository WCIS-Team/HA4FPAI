# -*- coding: utf-8 -*-
"""D01–D06 验收：设置分类、回执绑定、原子覆盖、受限重启、统一写入口、recorder 回执。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_settings_applier.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import applier, ids                     # noqa: E402
from control.errors import ConflictError              # noqa: E402
from control.settings import Settings, OVERLAY_KEYS   # noqa: E402
from control.store import Store                        # noqa: E402

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


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-set-')
    try:
        store = Store(os.path.join(tmp, 'b.sqlite3'))
        st = Settings(store)
        epoch = store.restore_epoch()

        # ---- D01 分类 ----
        defs = st.definitions()
        check(len(defs) == 5 and all('kind' in d and 'consumer' in d for d in defs),
              'D01：5 个键都带 kind/consumer 分类')
        check('ui.theme' not in OVERLAY_KEYS and 'audio.output.volume' in OVERLAY_KEYS,
              'D01/D03：覆盖文件白名单只含业务 overlay 键（不含 ui.theme）')
        check(isinstance(st.sensitive_keys(), list), 'D01：sensitive_keys 可枚举')

        # ---- D02 回执绑定：未消费不标 applied ----
        st.update({'audio.output.volume': 30})
        e = st.get('audio.output.volume')
        check(e['revision'] == 1 and e['applied'] is False and e['pending'] is True,
              'D02：入库未消费 → applied=False/pending=True')
        fb = st.apply_feedback('audio', epoch, 1)
        check(fb['accepted'] == ['audio.output.volume'], 'D02：正确版本回执被接受')
        e = st.get('audio.output.volume')
        check(e['applied'] is True and e['pending'] is False, 'D02：消费后才标 applied')
        # stale epoch
        fb = st.apply_feedback('audio', epoch + 1, 1)
        check(fb.get('reason') == 'stale_epoch' and not fb['accepted'], 'D02：错 epoch 拒绝')
        # future revision
        fb = st.apply_feedback('audio', epoch, 9)
        check(any(r['reason'] == 'future_revision' for r in fb.get('rejected', [])),
              'D02：未来修订拒绝')
        # digest binding
        good_digest = ids.digest_of({'audio.output.volume': e['value']})
        fb_ok = st.apply_feedback('audio', epoch, 1, digest=good_digest)
        check(fb_ok['accepted'] == ['audio.output.volume'], 'D02：摘要匹配回执接受')
        fb_bad = st.apply_feedback('audio', epoch, 1, digest='0' * 64)
        check(any(r['reason'] == 'digest_mismatch' for r in fb_bad.get('rejected', [])),
              'D02：摘要不符拒绝')
        # superseded（旧版本回执）
        fb_old = st.apply_feedback('audio', epoch, 0)
        check(fb_old['superseded'] == ['audio.output.volume'] or
              any(r['reason'] == 'future_revision' for r in fb_old.get('rejected', []))
              or not fb_old['accepted'], 'D02：倒退/旧回执不覆盖新值')

        # ---- D03 覆盖文件原子 apply ----
        path = os.path.join(tmp, 'override.yaml')
        res = applier.apply(st, path, dry_run=True)
        check(res['dry_run'] is True and not os.path.exists(path),
              'D03：dry_run 不落盘')
        res = applier.apply(st, path)
        check(os.path.exists(path) and res['sha256'], 'D03：原子写出覆盖文件')
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        ok, why = applier.validate_text(text, applier.render_overlay(st))
        check(ok, 'D03：覆盖文件回读与 desired 一致（%s）' % why)
        check('ui.theme' not in text, 'D03：覆盖文件不含非白名单键')
        st.update({'audio.output.volume': 55})
        res2 = applier.apply(st, path)
        check(os.path.exists(path + '.bak') and res2.get('backup'),
              'D03：二次写保留旧 .bak 备份')

        # ---- D04 受限重启 ----
        dry = applier.restricted_restart(['lang-server-http'])
        check(dry['dry_run'] is True and dry['planned'] == ['lang-server-http'],
              'D04：白名单服务 dry_run 计划')
        ref1 = applier.restricted_restart(['npuserv'])
        ref2 = applier.restricted_restart(['rm -rf /'])
        check(ref1['refused'] == ['npuserv'] and 'npuserv' not in ref1['restarted'],
              'D04：拒绝重启 npuserv')
        check(ref2['refused'] == ['rm -rf /'], 'D04：拒绝任意未知/注入服务名')
        called = []
        def runner(name, timeout):
            called.append(name)
        res = applier.restricted_restart(['lang-server-loop'], runner=runner,
                                         health_probe=lambda n: True)
        check(res['restarted'] == ['lang-server-loop'] and called == ['lang-server-loop'],
              'D04：白名单服务按注入 runner 重启且健康探针通过')
        bad = applier.restricted_restart(['lang-server-loop'], runner=runner,
                                         health_probe=lambda n: False)
        check(bad['failed'] and bad['failed'][0]['reason'] == 'unhealthy',
              'D04：健康探测失败 → 记 failed 不谎报成功')

        # ---- D05 统一写入口 ----
        st.write_param('face.match.threshold', 0.5)
        check(st.get('face.match.threshold')['revision'] == 1, 'D05：write_param 单改')
        raises(lambda: st.write_batch({'face.match.threshold': 0.6,
                                       'voice.match.threshold': 0.9},
                                      expected_revisions={'face.match.threshold': 0,
                                                          'voice.match.threshold': 0}),
               ConflictError, 'D05：批量里某键修订不符 → 整批拒绝')
        check(st.get('face.match.threshold')['revision'] == 1,
              'D05：批量失败不改任何键（原子）')
        revs = st.write_batch({'face.match.threshold': 0.6, 'voice.match.threshold': 0.9},
                             expected_revisions={'face.match.threshold': 1,
                                                 'voice.match.threshold': 0})
        check(revs.get('face.match.threshold') == 2 and revs.get('voice.match.threshold') == 1,
              'D05：批量合法 → 一次提交')
        st.reset_defaults(['face.match.threshold'])
        check(st.get('face.match.threshold')['value'] == 0.40, 'D05：reset_defaults 回默认')

        # ---- D06 recorder 模式回执 ----
        st.update({'recording.mode': 'manual'})
        rev = st.get('recording.mode')['revision']
        r1 = st.recorder_feedback('manual', rev - 1, epoch)
        check(r1['accepted'] is False and r1['reason'] == 'stale_revision',
              'D06：旧 revision 回执拒绝')
        r2 = st.recorder_feedback('off', rev, epoch)
        check(r2['accepted'] is False and r2['reason'] == 'mode_mismatch',
              'D06：实际模式不符拒绝')
        r3 = st.recorder_feedback('manual', rev, epoch, operation_id='op1', armed=True)
        check(r3['accepted'] is True and r3['actively_recording'] is True,
              'D06：模式匹配回执接受且 armed→actively_recording')
        r4 = st.recorder_feedback('manual', rev, epoch, operation_id='op2', armed=False)
        check(r4['accepted'] is True and r4['actively_recording'] is False,
              'D06：armed 与 actively_recording 独立表示')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_settings_applier: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
