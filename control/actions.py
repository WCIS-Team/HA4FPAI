# -*- coding: utf-8 -*-
"""动作注册表：后端校验、前端按钮、语音映射和能力清单的单一来源。

动作实现在程序里；设备/实体数据来自业务库（规划 §动作目录与请求合同）。
camera.* / recording.* 是留空桩：执行返回 video_domain_not_connected，
不做任何假成功。直接短语表只整句匹配，供 /v1/interpret 与后续语音链共用。
"""
from . import commands, ids, persons, samples, settings as settings_mod
from . import voiceprint_import
from .adapters import klar as klar_adapter
from .adapters import video
from .errors import ConflictError, NotFoundError

DIRECT_PHRASES = {
    '小克小克': {'reply': '我在。', 'end_request': True},
    '再见': {'reply': '拜拜。', 'end_request': True},
    '现在几点': {'action': 'system.time', 'params': {}},
    '系统状态': {'action': 'system.status', 'params': {}},
    '看看是谁': {'action': 'camera.recognize', 'params': {}},
    '拍张照片': {'action': 'camera.snapshot', 'params': {}},
    '开始录制': {'action': 'recording.set_mode', 'params': {'mode': 'manual'}},
    '停止录制': {'action': 'recording.set_mode', 'params': {'mode': 'off'}},
    '有人时再录': {'action': 'recording.set_mode', 'params': {'mode': 'auto'}},
    '查看今天的录像': {'action': 'recording.list', 'params': {}},
}


def _require_person(params, store):
    row = persons.get(store, params['person_id'])
    if row is None:
        raise NotFoundError('person_not_found', '人员不存在: %s' % params['person_id'])
    return row


def build_registry(store, events, settings=None):
    settings = settings or settings_mod.Settings(store)
    reg = {}

    def add(name, run, params=None, destructive=False, capability='system',
            connected=True, summary=''):
        reg[name] = {'name': name, 'run': run, 'params': params or {},
                     'destructive': destructive, 'capability': capability,
                     'connected': connected, 'summary': summary}

    # ---- system（本服务原生）----
    def run_system_time(params, target, expected_revision, principal_id, command_id):
        import datetime
        now = datetime.datetime.now()
        return {'message': '现在 %s' % now.strftime('%H:%M'),
                'data': {'iso': ids.now_iso(), 'local': now.strftime('%Y-%m-%d %H:%M:%S'),
                         'boot_id': ids.boot_id()}}

    def run_system_status(params, target, expected_revision, principal_id, command_id):
        return {'message': '状态汇总',
                'data': {'store': store.identity(), 'integrity': store.integrity(),
                         'video': video.status(),
                         'actions_connected': sorted(
                             n for n, s in reg.items() if s['connected'])}}

    add('system.status', run_system_status, summary='聚合状态：存储世代、视频域、能力清单')
    add('system.time', run_system_time, summary='当前时间（板上本地）')

    # ---- person / models / samples（资料域，业务库单写入口）----
    add('person.create',
        lambda params, target, expected_revision, principal_id, command_id:
        {'message': '已创建人员', 'data': persons.create(store, events, params['display_name'])},
        params={'display_name': {'type': 'str', 'required': True}},
        summary='创建人员（同名不自动合并）')
    add('person.update',
        lambda params, target, expected_revision, principal_id, command_id:
        {'message': '已更新人员',
         'data': persons.update(store, events, params['person_id'],
                                display_name=params.get('display_name'),
                                status=params.get('status'),
                                expected_revision=params.get('expected_revision'))},
        params={'person_id': {'type': 'str', 'required': True},
                'display_name': {'type': 'str'},
                'status': {'type': 'enum', 'choices': ['active', 'paused']},
                'expected_revision': {'type': 'int'}},
        summary='改显示名/状态；带 expected_revision 防并发覆盖')
    add('person.delete',
        lambda params, target, expected_revision, principal_id, command_id:
        {'message': '已软删除人员并撤销模板',
         'data': persons.delete(store, events, params['person_id'])},
        params={'person_id': {'type': 'str', 'required': True}},
        destructive=True, summary='软删除：撤销 active 模板；授权端在线核验撤销版本')

    def run_merge(params, target, expected_revision, principal_id, command_id):
        return {'message': '已合并人员',
                'data': persons.merge(store, events, params['source_person_id'],
                                      params['target_person_id'])}
    add('person.merge', run_merge,
        params={'source_person_id': {'type': 'str', 'required': True},
                'target_person_id': {'type': 'str', 'required': True}},
        destructive=True, summary='合并两个人员（保留旧 ID 映射）')

    def run_register_model(params, target, expected_revision, principal_id, command_id):
        return {'message': '已登记模型',
                'data': samples.register_model(
                    store, events, params['model_id'], params['modality'], params['dim'],
                    model_sha256=params.get('model_sha256'),
                    dtype=params.get('dtype', 'float32-le'),
                    legacy_pipeline_id=params.get('legacy_pipeline_id'))}
    add('models.register', run_register_model,
        params={'model_id': {'type': 'str', 'required': True},
                'modality': {'type': 'enum', 'choices': ['face', 'voice'], 'required': True},
                'dim': {'type': 'int', 'required': True, 'min': 1, 'max': 4096},
                'model_sha256': {'type': 'str'}, 'dtype': {'type': 'enum', 'choices': list(samples.DTYPES)},
                'legacy_pipeline_id': {'type': 'str'}},
        summary='登记模型指纹（同 ID 异指纹拒绝）')

    def run_enroll_voice(params, target, expected_revision, principal_id, command_id):
        _require_person(params, store)
        row = samples.enroll(store, events, 'voice', params['person_id'], params['model_id'],
                             params['vector'], dtype=params.get('dtype', 'float32-le'),
                             require_unit_norm=bool(params.get('require_unit_norm')),
                             source_ref=params.get('source_ref'))
        return {'message': '已登记声纹样本', 'data': {'sample_id': row['sample_id'],
                                                     'dim': row['dim'], 'dtype': row['dtype']}}
    add('person.enroll_voice', run_enroll_voice,
        params={'person_id': {'type': 'str', 'required': True},
                'model_id': {'type': 'str', 'required': True},
                'vector': {'type': 'list', 'required': True},
                'dtype': {'type': 'enum', 'choices': list(samples.DTYPES)},
                'require_unit_norm': {'type': 'enum', 'choices': [True, False]}},
        summary='登记声纹模板（向量由调用方/语音后端提供并核验）')

    def run_enroll_face(params, target, expected_revision, principal_id, command_id):
        _require_person(params, store)
        row = samples.enroll(store, events, 'face', params['person_id'], params['model_id'],
                             params['vector'], dtype=params.get('dtype', 'float32-le'),
                             require_unit_norm=bool(params.get('require_unit_norm')),
                             source_media_id=params.get('source_media_id'),
                             source_ref=params.get('source_ref'))
        return {'message': '已登记人脸样本', 'data': {'sample_id': row['sample_id'],
                                                     'dim': row['dim'], 'dtype': row['dtype']}}
    add('person.enroll_face', run_enroll_face,
        params={'person_id': {'type': 'str', 'required': True},
                'model_id': {'type': 'str', 'required': True},
                'vector': {'type': 'list', 'required': True},
                'source_media_id': {'type': 'str'}, 'source_ref': {'type': 'str'}},
        capability='face', connected=False,
        summary='登记人脸模板（向量为占位契约；真实提取端属视频域，接回前仅显式向量可用）')

    def run_import_preview(params, target, expected_revision, principal_id, command_id):
        result = voiceprint_import.preview(params['path'], source=params.get('source'))
        return {'message': '预览完成（只读，未写库）', 'data': result}
    add('voiceprint.import_preview', run_import_preview,
        params={'path': {'type': 'str', 'required': True},
                'source': {'type': 'enum', 'choices': ['boards', 'lang']}},
        summary='旧声纹库只读预览；入库迁移属 WP6/7，本动作不写库')

    # ---- settings（desired/applied）----
    def run_settings_update(params, target, expected_revision, principal_id, command_id):
        new_revisions = settings.update(params['updates'],
                                        expected_revisions=params.get('expected_revisions'),
                                        actor=principal_id)
        return {'message': '已保存设置', 'data': {'revisions': new_revisions}}
    add('settings.update', run_settings_update,
        params={'updates': {'type': 'obj', 'required': True}},
        summary='保存设置（预期修订不符返回 409）')

    # ---- 视频域：留空桩（capability=video, connected=False）----
    add('camera.recognize', lambda params, target, expected_revision, principal_id, command_id:
        video.recognize(target=target),
        capability='video', connected=False, summary='当前画面单帧识别（留空）')
    add('camera.snapshot', lambda params, target, expected_revision, principal_id, command_id:
        video.snapshot(target=target),
        capability='video', connected=False, summary='拍照存档（留空）')
    add('camera.open_view',
        lambda params, target, expected_revision, principal_id, command_id:
        video.open_view(client_id=params.get('client_id')),
        params={'client_id': {'type': 'str'}},
        capability='video', connected=False, summary='打开实时画面（留空）')
    add('recording.set_mode', lambda params, target, expected_revision, principal_id, command_id:
        video.set_mode(mode=params['mode'], device_id=target),
        params={'mode': {'type': 'enum', 'choices': ['off', 'auto', 'manual'], 'required': True}},
        capability='video', connected=False, summary='设置录像模式（留空；不做假 applied）')
    add('recording.list', lambda params, target, expected_revision, principal_id, command_id:
        video.list_recordings(target=target),
        capability='video', connected=False, summary='查询录像列表（留空）')
    add('recording.play', lambda params, target, expected_revision, principal_id, command_id:
        video.play(recording_id=params['recording_id']),
        params={'recording_id': {'type': 'str', 'required': True}},
        capability='video', connected=False, summary='回放指定录像（留空）')

    return reg


def interpret(store, registry, text, session=None, klar_post=None, klar_url=None):
    """文本 → 提案（不执行）。direct 整句匹配优先，其次 klar V2，最后交 LLM。"""
    direct = klar_adapter.match_direct(text, DIRECT_PHRASES)
    if direct is not None:
        if 'reply' in direct:
            return {'source': 'direct_reply', 'proposals': [], 'reply': direct['reply'],
                    'end_request': bool(direct.get('end_request'))}
        return {'source': 'direct', 'proposals': [direct]}
    parsed = klar_adapter.parse_v2(klar_post or klar_adapter.default_post, text,
                                   url=klar_url, session=session)
    if parsed.get('decision') == 'execute':
        proposals = []
        for intent in parsed.get('intents', []):
            proposals.append({'klar_intent': intent.get('intent'), 'slots': intent.get('slots')})
        return {'source': 'klar', 'proposals': proposals, 'speech': parsed.get('speech'),
                'note': 'klar 意图到动作表的映射尚未接（WP9）；此处仅提案'}
    if parsed.get('decision') in ('clarify', 'confirm', 'reject'):
        return {'source': 'klar', 'proposals': [], 'decision': parsed.get('decision'),
                'speech': parsed.get('speech')}
    return {'source': 'llm_handoff', 'proposals': [],
            'note': '非设备指令，交语言链 LLM；不得把生成文字冒充执行回执'}


def capabilities_payload(store, registry):
    return {'service': 'ha4fpai-control', 'version': _version(),
            'store': store.identity(),
            'video_domain': video.status(),
            'actions': [
                {'action': name, 'summary': spec['summary'],
                 'destructive': spec['destructive'], 'capability': spec['capability'],
                 'connected': spec['connected'], 'params': spec['params']}
                for name, spec in sorted(registry.items())]}


def _version():
    from . import __version__
    return __version__
