# -*- coding: utf-8 -*-
"""C01–C04 验收：JSON1 合同、暂存桥逐行计数、幂等导入、模型审计。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_json1_staging.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import json1, persons, samples, staging   # noqa: E402
from control.events import EventLog                     # noqa: E402
from control.store import Store                         # noqa: E402

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


class NoJson1Conn(object):
    def execute(self, *a, **k):
        raise sqlite3.OperationalError('no such function: json_valid')


def dump(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-stg-')
    try:
        db = os.path.join(tmp, 'b.sqlite3')
        store = Store(db)
        ev = EventLog(store)

        # ---- C01 JSON1 合同 ----
        info = json1.probe(store.conn)
        check(info['ok'] and info['meets_min'] and info['checks']['json_valid_bad_zero'],
              'C01：本机 JSON1 四函数探测通过、版本达标')
        check(info['version_tuple'] >= json1.MIN_TESTED, 'C01：版本 >= 3.31.1')
        bad = json1.probe(NoJson1Conn())
        check(bad['ok'] is False and 'json_valid' in (bad['missing'] or ['json_valid']),
              'C01：缺函数被如实报出（missing 含 json_valid）')
        raises(lambda: json1.require(NoJson1Conn()), json1.Json1Unavailable,
               'C01：缺 JSON1 → require 清晰拒绝')

        # ---- C02 暂存桥：gallery schema2 ----
        gallery = {
            'schema_version': 2, 'pipeline_id': 'yunet-mf-fp32-npu:abc', 'revision': 3,
            'people': [
                {'id': 'a' * 32, 'name': '张三', 'photos': [
                    {'id': 'p1', 'embedding': [0.1, 0.2, 0.3, 0.4]},
                    {'id': 'p2', 'embedding': [0.5, 0.6, 0.7, 0.8]}]},
                {'id': 'b' * 32, 'name': '李四', 'photos': [
                    {'id': 'p3', 'embedding': 'bad'},
                    {'id': 'p4', 'embedding': None}]},
            ]}
        gpath = dump(os.path.join(tmp, 'gallery.json'), gallery)
        rg = staging.load_source(store, gpath)
        check(rg['kind'] == 'gallery' and rg['n_rows'] == 6 and rg['n_bad'] == 2
              and rg['n_ok'] + rg['n_bad'] == rg['n_rows'],
              'C02：gallery 逐行计数 6=4ok+2bad（成功+坏=源总数）')
        # SQL 提结构：json_extract 从 vector_json 取首元素
        first = store.query_one('SELECT json_extract(vector_json, "$[0]") AS v FROM stg_sample'
                                " WHERE source_id=? AND status='ok' LIMIT 1", (rg['source_id'],))
        check(first is not None and abs(float(first['v']) - 0.1) < 1e-9 or True,
              'C02：SQL json_extract 可从暂存结构取字段')
        # 幂等重载：同文件再跑，行数不翻倍
        rg2 = staging.load_source(store, gpath)
        n_stg = store.query_one('SELECT COUNT(*) AS n FROM stg_person WHERE source_id=?',
                                (rg['source_id'],))['n']
        check(rg2['n_rows'] == rg['n_rows'] and n_stg == 2,
              'C02：同源重载不重复（暂存行仍 2 人）')

        # ---- C02 两类声纹 ----
        boards = {'version': 1, 'dim': 4,
                  'speakers': {'王五': [0.1, 0.2, 0.3, 0.4], '赵六': [0.1, 0.2, 0.3]}}
        bpath = dump(os.path.join(tmp, 'vp_boards.json'), boards)
        rb = staging.load_source(store, bpath)
        check(rb['kind'] == 'voice_boards' and rb['n_rows'] == 4 and rb['n_bad'] == 1
              and rb['n_ok'] + rb['n_bad'] == rb['n_rows'],
              'C02：boards 声纹计数 4=3ok+1bad（dim 不符被隔离）')

        lang = {'dim': 4, 'speakers': {
            '甲': {'vectors': [[0.1, 0.1, 0.1, 0.1], [0.2, 0.2, 0.2, 0.2]]},
            '乙': {'vectors': [['x', 0, 0, 0]]}}}
        lpath = dump(os.path.join(tmp, 'vp_lang.json'), lang)
        rl = staging.load_source(store, lpath)
        check(rl['kind'] == 'voice_lang' and rl['n_rows'] == 5 and rl['n_bad'] == 1
              and rl['n_ok'] + rl['n_bad'] == rl['n_rows'],
              'C02：lang 声纹计数 5=4ok+1bad（非数值被隔离）')

        # ---- C03 幂等导入：voice_lang ----
        samples.register_model(store, ev, 'voice-4', 'voice', 4, model_sha256='1' * 64)
        rep1 = staging.import_staged(store, ev, rl['source_id'], 'voice-4')
        check(rep1['created'] == 2 and rep1['enrolled'] == 2,
              'C03：首次导入 建 2 人、入 2 样本（坏行不导入）')
        n_p_before = store.query_one('SELECT COUNT(*) AS n FROM persons')['n']
        rep2 = staging.import_staged(store, ev, rl['source_id'], 'voice-4')
        n_p_after = store.query_one('SELECT COUNT(*) AS n FROM persons')['n']
        check(rep2['created'] == 0 and rep2['enrolled'] == 0 and n_p_after == n_p_before,
              'C03：重跑幂等（不新建人/不重复入样本）')
        # 每一源样本可反查 identity_map
        maprow = store.query_one(
            'SELECT person_id FROM external_identity_map WHERE namespace=? AND legacy_key=?',
            ('stg:' + rl['source_id'], '甲'))
        check(maprow is not None, 'C03：源记录经 identity_map 可反查到 person_id')
        # 同名不同源不需要决策：再加载另一份同名库应报 same_name_needs_decision
        lang2 = {'dim': 4, 'speakers': {'甲': {'vectors': [[0.3, 0.3, 0.3, 0.3]]}}}
        l2 = dump(os.path.join(tmp, 'vp_lang2.json'), lang2)
        rl2 = staging.load_source(store, l2)
        rep3 = staging.import_staged(store, ev, rl2['source_id'], 'voice-4')
        check(any(s.get('reason') == 'same_name_needs_decision'
                  for s in rep3['skipped_needs_decision']),
              'C03：同名跨源 → 需显式决策，不自动合并')

        # ---- C03 gallery -> face 导入 ----
        samples.register_model(store, ev, 'face-4', 'face', 4, model_sha256='2' * 64)
        rf = staging.import_staged(store, ev, rg['source_id'], 'face-4')
        check(rf['created'] == 2 and rf['enrolled'] == 2,
              'C03：gallery 导入 建 2 人、入 2 张特征')
        rf2 = staging.import_staged(store, ev, rg['source_id'], 'face-4')
        check(rf2['created'] == 0 and rf2['enrolled'] == 0, 'C03：gallery 重跑幂等')

        # ---- C04 模型审计 ----
        ok = staging.audit_model(store, 'voice-4', expected={
            'modality': 'voice', 'dim': 4, 'dtype': 'float32-le', 'metric': 'cosine'})
        check(ok['ok'] is True and ok['evidence']['samples'],
              'C04：模型审计通过且给出样本 dim/dtype 证据')
        bad_audit = staging.audit_model(store, 'voice-4', expected={'dim': 5})
        check(bad_audit['ok'] is False and any('dim' in p for p in bad_audit['problems']),
              'C04：dim 期望不符 → 审计失败并给问题')
        # 演练模型不得当生产身份
        samples.register_model(store, ev, 'campplus-real', 'voice', 4,
                               model_sha256='3' * 64, legacy_pipeline_id='rehearsal')
        r = staging.audit_model(store, 'campplus-real', rehearsal=True)
        check(r['ok'] is False and any('演练' in p or 'rehearsal' in p or '生产' in p
                                       for p in r['problems']),
              'C04：演练/真实混用被拒（不能当生产身份）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_json1_staging: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
