# -*- coding: utf-8 -*-
"""control.migrate 自检：声纹写路径迁移 + klar 实体种子（纯标准库、临时目录）。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_migrate.py
输出 PASS/FAIL 计数；全绿退出码 0。不碰模型/网络服务/板端。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import klar.make_registry as make_registry  # noqa: E402  种子源常量（仓库 klar/）
from control import migrate, persons, samples  # noqa: E402
from control.errors import ConflictError, DomainError  # noqa: E402
from control.events import EventLog  # noqa: E402
from control.outbox import export_registry, read_current  # noqa: E402
from control.store import Store  # noqa: E402

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
    except Exception as exc:  # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-control-migrate-')
    try:
        run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_migrate: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


def _write_json(path, value):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False)


def run_all(tmp):
    db = os.path.join(tmp, 'business.sqlite3')
    store = Store(db)
    events = EventLog(store)
    samples.register_model(store, events, 'campplus-migrate', 'voice', 4,
                           model_sha256='c' * 64)

    boards_path = os.path.join(tmp, 'voiceprints-boards.json')
    _write_json(boards_path, {'version': 1, 'dim': 4,
                              'speakers': {'张三': [0.5, 0.4, 0.3, 0.2],
                                           '赵六': [float('nan'), 0.1, 0.1, 0.1]}})
    lang_path = os.path.join(tmp, 'voiceprints-lang.json')
    _write_json(lang_path, {'version': 1, 'dim': 4, 'updated': '2026-11-04T00:00:00+00:00',
                            'speakers': {'李四': {'vectors': [[0.1, 0.1, 0.1, 0.1],
                                                              [0.2, 0.2, 0.2, 0.2]],
                                                 'updated': 'x'},
                                         '张三': {'vectors': [[0.6, 0.5, 0.4, 0.3]],
                                                 'updated': 'y'}}})

    # ---- boards 首迁：显式 'new' 建人、坏向量人只记 errors ----
    r1 = migrate.import_voiceprints(store, events, boards_path, 'boards',
                                    'campplus-migrate', decisions={'张三': 'new'})
    check(r1['created'] == 1 and r1['mapped'] == 1 and r1['enrolled'] == 1,
          'boards report 计数 created/mapped/enrolled=1/1/1')
    check(r1['skipped_existing'] == 0 and r1['skipped_needs_decision'] == [],
          'boards 首迁无 skip')
    check(any(e.get('owner') == '赵六' and e.get('reason') == 'no_valid_vectors'
              for e in r1['errors']), '坏向量的人记入 errors 且不建人')
    zhang = persons.identity_resolve(store, 'voiceprint:boards',
                                     store.meta('store_id'), '张三')
    check(zhang is not None and zhang['state'] == 'linked',
          '张三 identity_map 行存在（namespace=voiceprint:boards）')
    zhang_id = zhang['person_id']
    row = store.query_one('SELECT display_name, status FROM persons WHERE person_id=?',
                          (zhang_id,))
    check(row['display_name'] == '张三' and row['status'] == 'active', '张三人员已创建')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == 1,
          '赵六未建人（总人数 1）')
    rows = store.query('SELECT active, source_ref FROM voice_samples WHERE person_id=?',
                       (zhang_id,))
    check(len(rows) == 1 and rows[0]['active'] == 0, 'boards 样本隔离 active=0')
    check(rows[0]['source_ref'] == 'legacy:张三#0', 'source_ref 记旧库出处 legacy:姓名#序号')

    # ---- lang 首迁：无 decision 自动建人（无同名者）；同名张三只列待核对 ----
    r2 = migrate.import_voiceprints(store, events, lang_path, 'lang', 'campplus-migrate')
    check(r2['created'] == 1 and r2['mapped'] == 1 and r2['enrolled'] == 2,
          'lang report 计数（李四建人 + 2 样本）')
    check(any(d.get('name') == '张三' and d.get('reason') == 'same_name_needs_decision'
              for d in r2['skipped_needs_decision']),
          'lang 同名张三只列待核对，不自动合并')
    lisi = persons.identity_resolve(store, 'voiceprint:lang', store.meta('store_id'), '李四')
    check(lisi is not None and persons.identity_resolve(
        store, 'voiceprint:lang', store.meta('store_id'), '张三') is None,
        '李四已映射；张三(lang) 未映射')
    lisi_rows = store.query('SELECT active, source_ref FROM voice_samples WHERE person_id=?',
                            (lisi['person_id'],))
    check(len(lisi_rows) == 2 and all(r['active'] == 0 for r in lisi_rows),
          '李四 2 条多样本均 active=0')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == 2,
          '总人数 2（张三/李四）')

    # ---- 重跑同参数：全部 skipped_existing，人数/样本数不涨 ----
    r1b = migrate.import_voiceprints(store, events, boards_path, 'boards',
                                     'campplus-migrate', decisions={'张三': 'new'})
    r2b = migrate.import_voiceprints(store, events, lang_path, 'lang', 'campplus-migrate')
    check(r1b['skipped_existing'] == 1 and r1b['created'] == 0 and r1b['mapped'] == 0
          and r1b['enrolled'] == 0, 'boards 重跑幂等（skipped_existing=1）')
    check(r2b['skipped_existing'] == 1 and r2b['created'] == 0 and r2b['enrolled'] == 0,
          'lang 重跑幂等（李四 skipped_existing）')
    check(any(d.get('name') == '张三' for d in r2b['skipped_needs_decision']),
          'lang 重跑张三仍待核对')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == 2
          and store.query_one('SELECT COUNT(*) AS c FROM voice_samples')['c'] == 3,
          '重跑人数/样本数不涨')

    # ---- decision 给 person_id：不建新人只映射 ----
    r3 = migrate.import_voiceprints(store, events, lang_path, 'lang', 'campplus-migrate',
                                    decisions={'张三': zhang_id})
    check(r3['created'] == 0 and r3['mapped'] == 1 and r3['enrolled'] == 1,
          'decision 指向既有 person：只映射不建人')
    remapped = persons.identity_resolve(store, 'voiceprint:lang',
                                        store.meta('store_id'), '张三')
    check(remapped is not None and remapped['person_id'] == zhang_id,
          'lang 张三映射到 boards 张三同一 person_id')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == 2,
          'decision 映射不新增人员')
    check(store.query_one('SELECT COUNT(*) AS c FROM voice_samples WHERE person_id=?',
                          (zhang_id,))['c'] == 2, '张三名下 2 条隔离样本')

    # ---- decision='skip' 与指向不存在人员 ----
    skip_path = os.path.join(tmp, 'voiceprints-skip.json')
    _write_json(skip_path, {'version': 1, 'dim': 4,
                            'speakers': {'王五': [0.3, 0.3, 0.3, 0.3]}})
    r4 = migrate.import_voiceprints(store, events, skip_path, 'boards',
                                    'campplus-migrate', decisions={'王五': 'skip'})
    check(any(d.get('name') == '王五' and d.get('reason') == 'decision_skip'
              for d in r4['skipped_needs_decision'])
          and r4['created'] == 0 and r4['mapped'] == 0 and r4['enrolled'] == 0,
          "decision='skip' 记入待核对且不写库")
    r5 = migrate.import_voiceprints(store, events, skip_path, 'boards',
                                    'campplus-migrate', decisions={'王五': 'no-such-person'})
    check(any(e.get('owner') == '王五' and e.get('reason') == 'decision_person_invalid'
              for e in r5['errors']) and r5['created'] == 0,
          'decision 指向不存在人员：记 errors 不建人')

    # ---- 坏 JSON：ConflictError 透传且库无变化 ----
    bad_path = os.path.join(tmp, 'bad.json')
    with open(bad_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('{not json')
    persons_before = store.query_one('SELECT COUNT(*) AS c FROM persons')['c']
    revision_before = store.meta('domain_revision')
    raises(lambda: migrate.import_voiceprints(store, events, bad_path, 'boards',
                                              'campplus-migrate'),
           ConflictError, '坏 JSON ConflictError 透传')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == persons_before
          and store.meta('domain_revision') == revision_before, '坏 JSON 库无变化')

    # ---- 未知模型 / 模型 dim 不一致：写库前拒绝 ----
    try:
        migrate.import_voiceprints(store, events, boards_path, 'boards', 'ghost-model')
        check(False, '未知模型拒绝（未抛出）')
    except DomainError as exc:
        check(exc.code == 'model_unknown', '未知模型拒绝 code=model_unknown')
    except Exception as exc:  # noqa: BLE001
        check(False, '未知模型拒绝（抛了别的: %r）' % exc)
    samples.register_model(store, events, 'campplus-mismatch', 'voice', 8,
                           model_sha256='d' * 64)
    raises(lambda: migrate.import_voiceprints(store, events, boards_path, 'boards',
                                              'campplus-mismatch'),
           ConflictError, '文件 dim 与模型 dim 不一致拒绝')
    check(store.query_one('SELECT COUNT(*) AS c FROM persons')['c'] == persons_before,
          'dim 不一致时库无变化')

    # ---- activate=True 对照：迁入即激活 ----
    r6 = migrate.import_voiceprints(store, events, skip_path, 'lang', 'campplus-migrate',
                                    activate=True)
    check(r6['created'] == 1 and r6['enrolled'] == 1, 'activate=True 建人并登记样本')
    wangwu = persons.identity_resolve(store, 'voiceprint:lang',
                                      store.meta('store_id'), '王五')
    check(wangwu is not None and store.query_one(
        'SELECT active FROM voice_samples WHERE person_id=?',
        (wangwu['person_id'],))['active'] == 1, 'activate=True 样本 active=1')

    # ---- samples.enroll 默认 active=1（现状不破坏）----
    direct = samples.enroll(store, events, 'voice', zhang_id, 'campplus-migrate',
                            [0.4, 0.4, 0.4, 0.4], source_ref='direct-call')
    check(direct['active'] == 1, 'samples.enroll 默认 active=1')

    # ---- seed_entities：首调全插、重跑全更新、devices 不造 ----
    s1 = migrate.seed_entities(store, events)
    check(s1['areas'] == len(make_registry.AREAS)
          and s1['entities'] == len(make_registry.ENTITIES) and s1['updated'] == 0,
          'seed 首调全量插入（updated=0）')
    check(store.query_one('SELECT COUNT(*) AS c FROM areas')['c'] == len(make_registry.AREAS)
          and store.query_one('SELECT COUNT(*) AS c FROM entities')['c']
          == len(make_registry.ENTITIES), 'areas/entities 行数与源常量一致')
    row = store.query_one('SELECT name, area_id, capabilities FROM entities'
                          " WHERE entity_id='switch.face_recognize'")
    check(row['name'] == '人脸识别' and row['area_id'] == 'npu'
          and row['capabilities'] == '[]', '实体字段落库（name/area/capabilities JSON 串）')
    check(store.query_one('SELECT COUNT(*) AS c FROM devices')['c'] == 0,
          'make_registry 无设备数据，devices 表不造')
    s2 = migrate.seed_entities(store, events)
    check(s2['areas'] == s1['areas'] and s2['entities'] == s1['entities']
          and s2['updated'] == len(make_registry.AREAS) + len(make_registry.ENTITIES),
          'seed 重跑全走 UPDATE（幂等）')
    check(store.query_one('SELECT COUNT(*) AS c FROM areas')['c'] == len(make_registry.AREAS)
          and store.query_one('SELECT COUNT(*) AS c FROM entities')['c']
          == len(make_registry.ENTITIES), 'seed 重跑行数不涨')

    # ---- 种子后导出：manifest 存在，entities.json 包含种子实体 ----
    exports_dir = os.path.join(tmp, 'exports')
    exp = export_registry(store, exports_dir)
    manifest = read_current(exports_dir)
    check(manifest is not None and manifest['snapshot'] == exp['manifest']['snapshot'],
          'seed 后导出 manifest 存在且 current 指向它')
    with open(os.path.join(exports_dir, manifest['snapshot'], 'entities.json'),
              'r', encoding='utf-8') as f:
        ent_doc = json.load(f)
    exported_ids = set(e['entity_id'] for e in ent_doc['data']['entities'])
    check(len(ent_doc['data']['entities']) == len(make_registry.ENTITIES)
          and 'switch.face_recognize' in exported_ids,
          '导出 entities.json 非空且包含种子实体')


if __name__ == '__main__':
    sys.exit(main())
