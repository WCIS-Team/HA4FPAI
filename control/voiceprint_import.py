# -*- coding: utf-8 -*-
"""旧声纹库只读预览适配（不写库、不装载模型）。

两种现存格式（规划核对结论，不能靠共用路径归并）：
  * boards/voiceprint.py 版：{version, dim, speakers: {姓名: [浮点...]}}——
    单向量、同名覆盖、无锁直接整写
  * lang/server backends/spk 版：{version, dim, updated,
    speakers: {姓名: {vectors: [[...]], updated}}}——多样本累加

本模块只解析与校验，产出迁移预览（人数、样本数、坏行、同名冲突）；
真正入库走显式迁移（WP6/7），且模型指纹缺失的记录必须人工核对后才可激活。
"""
import json
import math

from .errors import ConflictError

BOARDS_FMT = 'boards-voiceprints-v1'
LANG_FMT = 'lang-vertify-library-v1'


def _load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except OSError as exc:
        raise ConflictError('store_unreadable', '读取失败: %s' % exc)
    except ValueError as exc:
        raise ConflictError('store_bad_json', 'JSON 解析失败: %s（保留原件，禁止按空库覆盖）' % exc)


def load_store_file(path):
    """按形状识别格式并归一为 people 列表；不通过模型验证只做结构检查。"""
    data = _load(path)
    if not isinstance(data, dict):
        raise ConflictError('store_bad_shape', '顶层不是对象')
    dim = data.get('dim')
    speakers = data.get('speakers')
    if not isinstance(speakers, dict):
        raise ConflictError('store_bad_shape', '缺少 speakers 对象')
    people = []
    if all(isinstance(v, dict) and 'vectors' in v for v in speakers.values()):
        fmt = LANG_FMT
        for name, entry in speakers.items():
            people.append({'name': str(name), 'vectors': entry.get('vectors') or [],
                           'updated': entry.get('updated')})
    else:
        fmt = BOARDS_FMT
        for name, vec in speakers.items():
            if not isinstance(vec, (list, tuple)):
                raise ConflictError('store_bad_shape', 'boards 格式 %s 的值不是向量' % name)
            people.append({'name': str(name), 'vectors': [vec], 'updated': None})
    return {'format': fmt, 'dim': dim, 'file': path, 'version': data.get('version'),
            'people': people}


def _check_vector(vector, dim, errors, owner):
    if not isinstance(vector, (list, tuple)) or not vector:
        errors.append({'owner': owner, 'reason': 'vector_not_list'})
        return False
    if dim is not None and len(vector) != int(dim):
        errors.append({'owner': owner, 'reason': 'dim_mismatch',
                       'got': len(vector), 'want': int(dim)})
        return False
    for v in vector:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            errors.append({'owner': owner, 'reason': 'non_finite_or_non_numeric'})
            return False
    return True


def preview(path, source=None):
    """只读预览：人数、样本数、坏向量与告警；不写任何库。"""
    parsed = load_store_file(path)
    errors = []
    warnings = []
    people = []
    for person in parsed['people']:
        name = person['name']
        good = []
        for idx, vec in enumerate(person['vectors']):
            if _check_vector(vec, parsed['dim'], errors, name):
                good.append({'index': idx, 'dim': len(vec)})
        if not good:
            errors.append({'owner': name, 'reason': 'no_valid_vectors'})
        if parsed['format'] == BOARDS_FMT and len(good) > 1:
            warnings.append({'owner': name, 'reason': 'boards_format_has_multiple_vectors'})
        people.append({'name': name, 'valid_samples': len(good),
                       'total_vectors': len(person['vectors']),
                       'updated': person['updated']})
    if parsed['dim'] is None:
        warnings.append({'reason': 'dim_missing'})
    return {'source': source or ('boards' if parsed['format'] == BOARDS_FMT else 'lang'),
            'format': parsed['format'],
            'file': parsed['file'],
            'dim': parsed['dim'],
            'model_hint': 'unknown-model（文件不含模型指纹；入库前必须人工登记并核对模型）',
            'people_count': len(people),
            'sample_count': sum(p['valid_samples'] for p in people),
            'people': people,
            'errors': errors,
            'warnings': warnings}


def compare_previews(previews):
    """跨来源同名冲突清单：同名只提待核对，不允许自动合并为同一 person_id。"""
    by_name = {}
    for item in previews:
        for person in item.get('people', []):
            by_name.setdefault(person['name'], []).append(
                {'source': item.get('source'), 'samples': person['valid_samples']})
    conflicts = [{'name': name, 'occurrences': owners}
                 for name, owners in sorted(by_name.items()) if len(owners) > 1]
    return {'names_total': len(by_name), 'name_conflicts': conflicts}
