# -*- coding: utf-8 -*-
"""旧数据迁入业务主库：声纹写路径迁移 + klar 实体种子（可重跑、幂等）。

规划依据（联合规划 WP6/7：单一写入口 = 业务主库；同名不自动合并）：
  * 旧 JSON 只读解析复用 voiceprint_import（preview / load_store_file），
    本模块是它们唯一的写路径；坏 JSON / 坏结构在写库前抛 ConflictError，库保持原样。
  * 旧文件不含模型指纹：model_id 必须先经 samples.register_model 人工登记，
    未登记抛 DomainError('model_unknown')，绝不猜模型；文件 dim 与模型 dim 不一致
    在写库前拒绝（ConflictError 'dim_mismatch'）。
  * 默认 activate=False：迁入样本 active=0（隔离不激活，不参与比对），核对后另行激活。
  * 同名只列待核对：'skip' 或缺 decision 且业务库已有同名人员 → skipped_needs_decision，
    绝不自动合并；缺 decision 且无同名人员时按 'new' 建人（无从合并，且样本隔离、
    带 identity_map 可追溯，重跑由 identity 幂等兜底）。
  * 旧 ID（源库名 source + 姓名）经 external_identity_map 保留；
    identity_add 前先 identity_resolve，已映射即整人跳过（skipped_existing），
    不得重复建人或重复登记样本——这是重跑幂等的依据。
  * 失败粒度 = 人/向量级：某人建人失败或某向量校验失败只记 report['errors']，
    不中断整文件（persons.create / samples.enroll 各自短事务，中断后重跑续迁）。

seed_entities 的 import 链路：本模块按 __file__ 定位仓库根（control/ 的上一级），
插入 sys.path[0] 后 `import klar.make_registry`——仓库 klar/ 目录无 __init__.py，
按 PEP 420 命名空间包导入，取其硬编码 ENTITIES/AREAS；klar 目录缺失或被挪走时
ImportError 原样抛出，不做降级假种子。
"""
import json
import os
import sys

from . import ids
from . import persons
from . import samples
from . import voiceprint_import
from .errors import ConflictError, DomainError


def _load_vectors(path):
    """二次只读解析取向量原文（preview 只给计数不给向量）；坏文件在写库前即抛。"""
    parsed = voiceprint_import.load_store_file(path)
    return dict((person['name'], person['vectors']) for person in parsed['people'])


def import_voiceprints(store, events, path, source, model_id, decisions=None, activate=False):
    """旧声纹库 → 业务主库（人员 + identity_map + 隔离样本），返回机器可读报告。

    decisions: {姓名: 'new' | person_id | 'skip'}；
      * 'new' → persons.create + identity_add（同名已存在时也建新人，显式指定即新建）；
      * person_id → 校验存在且 active 后仅做 identity_add，不建新人；
      * 'skip' → 记 skipped_needs_decision（reason='decision_skip'），不写库；
      * 缺 decision 且业务库有同名人员（display_name 相同，不限状态）→
        skipped_needs_decision（reason='same_name_needs_decision'），不动原人；
      * 缺 decision 且无同名人员 → 按 'new' 处理。
    返回 {"created","mapped","enrolled","skipped_existing","skipped_needs_decision",
    "errors"}；skipped_existing 为人数，另两项为 {name, reason} 列表，errors 为
    {owner, reason[, index, person_id]} 列表。逐向量解析细节可另调
    voiceprint_import.preview 查看，本报告只收迁移动作自身的错误。
    """
    decisions = dict(decisions or {})
    preview = voiceprint_import.preview(path, source=source)
    model = store.query_one('SELECT * FROM models WHERE model_id=?', (model_id,))
    if model is None:
        raise DomainError('model_unknown',
                          '模型 %s 未登记；先 samples.register_model 再迁移' % model_id)
    if model['modality'] != 'voice':
        raise ConflictError('model_modality_mismatch', '模型 %s 不是 voice 模态' % model_id)
    vectors_by_name = _load_vectors(path)
    if preview['dim'] is not None and int(preview['dim']) != int(model['dim']):
        raise ConflictError('dim_mismatch',
                            '文件 dim %s 与模型 %s（dim %s）不一致；先核对模型指纹再迁移'
                            % (preview['dim'], model_id, model['dim']))
    namespace = 'voiceprint:' + source
    source_store_id = store.meta('store_id')
    report = {'created': 0, 'mapped': 0, 'enrolled': 0, 'skipped_existing': 0,
              'skipped_needs_decision': [], 'errors': []}
    no_valid = set(e['owner'] for e in preview['errors']
                   if e.get('reason') == 'no_valid_vectors')
    for person in preview['people']:
        name = person['name']
        if name in no_valid or person['valid_samples'] == 0:
            report['errors'].append({'owner': name, 'reason': 'no_valid_vectors'})
            continue
        known = persons.identity_resolve(store, namespace, source_store_id, name)
        if known is not None:
            report['skipped_existing'] += 1
            continue
        decision = decisions.get(name)
        if decision == 'skip':
            report['skipped_needs_decision'].append({'name': name, 'reason': 'decision_skip'})
            continue
        if decision is None:
            same = store.query_one(
                'SELECT person_id FROM persons WHERE display_name=? LIMIT 1', (name,))
            if same is not None:
                report['skipped_needs_decision'].append(
                    {'name': name, 'reason': 'same_name_needs_decision',
                     'person_id': same['person_id']})
                continue
        try:
            if decision is None or decision == 'new':
                row = persons.create(store, events, name)
                person_id = row['person_id']
                report['created'] += 1
            else:
                row = persons.get(store, decision)
                if row is None or row['status'] != 'active':
                    report['errors'].append({'owner': name, 'reason': 'decision_person_invalid',
                                             'person_id': decision})
                    continue
                person_id = decision
            persons.identity_add(store, namespace, source_store_id, name, person_id)
            report['mapped'] += 1
        except ConflictError as exc:
            report['errors'].append({'owner': name, 'reason': exc.code})
            continue
        for index, vector in enumerate(vectors_by_name.get(name, [])):
            try:
                samples.enroll(store, events, 'voice', person_id, model_id, vector,
                               dtype=None,  # 跟随模型登记的编码，迁移不另做决定
                               source_ref='legacy:%s#%d' % (name, index),
                               active=activate)
                report['enrolled'] += 1
            except ConflictError as exc:
                report['errors'].append({'owner': name, 'index': index, 'reason': exc.code})
    return report


def seed_entities(store, events):
    """klar/make_registry 的硬编码 ENTITIES/AREAS → areas/entities 幂等种子。

    区域先于实体（entities.area_id 外键）；make_registry 无设备清单
    （实体 device_id 恒为 None），devices 表不造数据。实体 capabilities 源里
    不携带，按 schema 默认 '[]' JSON 串落库（outbox 导出时 json.loads 还原）。
    存在则按 id 以源数据更新（name/area_id/capabilities/device_id），不重复插。
    每次调用 bump 一次 domain_revision。返回 {"areas","entities","updated"}：
    前两者 = 本次处理条数（等于源常量长度），updated = 其中已存在走 UPDATE 的条数
    （首调 0，重跑 = areas+entities，行数不涨）。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import klar.make_registry as make_registry  # noqa: E402  导入失败如实抛 ImportError
    areas_total = 0
    entities_total = 0
    updated = 0
    now = ids.now_iso()
    with store.tx() as conn:
        for area_id, name, _aliases in make_registry.AREAS:
            areas_total += 1
            row = conn.execute('SELECT area_id FROM areas WHERE area_id=?',
                               (area_id,)).fetchone()
            if row is None:
                conn.execute('INSERT INTO areas(area_id, name, created_at) VALUES(?,?,?)',
                             (area_id, name, now))
            else:
                conn.execute('UPDATE areas SET name=? WHERE area_id=?', (name, area_id))
                updated += 1
        for entity_id, name, area_id, _platform in make_registry.ENTITIES:
            entities_total += 1
            row = conn.execute('SELECT entity_id FROM entities WHERE entity_id=?',
                               (entity_id,)).fetchone()
            if row is None:
                conn.execute(
                    'INSERT INTO entities(entity_id, device_id, area_id, name, capabilities,'
                    ' created_at) VALUES(?,?,?,?,?,?)',
                    (entity_id, None, area_id, name, '[]', now))
            else:
                conn.execute(
                    'UPDATE entities SET name=?, area_id=?, device_id=?, capabilities=?'
                    ' WHERE entity_id=?',
                    (name, area_id, None, '[]', entity_id))
                updated += 1
        store.bump_domain_revision(conn)
    events.append('entities.seeded', {'areas': areas_total, 'entities': entities_total,
                                      'updated': updated})
    return {'areas': areas_total, 'entities': entities_total, 'updated': updated}
