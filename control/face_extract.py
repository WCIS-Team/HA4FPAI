# -*- coding: utf-8 -*-
"""无副作用的人脸提取合同（E02）。

规则（规划 §人脸提取）：
  * 提取只返回 (embedding, pipeline_id, quality, source_sha256, model_id)，**不写任何图库/JSON 身份**。
  * 由业务库把 embedding 提交到指定 person_id（samples.enroll）；是否登记是业务决定。
  * 多脸/低质/错模型/坏返回值一律拒绝，不静默接受。
本模块不做真实推理：推理端由 `extract(adapter, source)` 的 adapter 注入（视频 serve_photos 的
extract-only 端点接回后即成为该 adapter），因此可无板单测。
"""
import hashlib
import math

from . import samples
from .errors import ConflictError

REQUIRED_FIELDS = ('embedding', 'pipeline_id', 'quality', 'source_sha256', 'model_id')


def normalize_result(raw):
    """校验并归一化提取端返回；返回干净 dict，坏数据抛 ConflictError。"""
    if not isinstance(raw, dict):
        raise ConflictError('bad_extract', '提取结果必须是对象')
    try:
        faces = int(raw.get('faces', 1))
    except (TypeError, ValueError):
        raise ConflictError('bad_extract', 'faces 计数非法')
    if faces > 1:
        raise ConflictError('multiple_faces', '画面含 %d 张人脸，拒绝（需单人）' % faces)
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ConflictError('bad_extract', '提取结果缺字段: %s' % ','.join(missing))
    emb = raw['embedding']
    if not isinstance(emb, (list, tuple)) or not emb:
        raise ConflictError('bad_extract', 'embedding 必须是非空列表')
    values = []
    for v in emb:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ConflictError('bad_extract', 'embedding 含非有限/非数值元素')
        values.append(float(v))
    q = raw['quality']
    if isinstance(q, bool) or not isinstance(q, (int, float)) or not (0.0 <= float(q) <= 1.0):
        raise ConflictError('bad_extract', 'quality 必须是 [0,1] 数值')
    sha = raw['source_sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ConflictError('bad_extract', 'source_sha256 必须是 64 位十六进制')
    if not isinstance(raw['model_id'], str) or not raw['model_id']:
        raise ConflictError('bad_extract', 'model_id 必须是非空字符串')
    return {'embedding': values, 'pipeline_id': str(raw['pipeline_id']),
            'quality': float(q), 'source_sha256': sha, 'model_id': raw['model_id']}


def extract(adapter, source):
    """调用注入的提取端（如视频 serve_photos extract-only 端点）并归一化。"""
    return normalize_result(adapter.extract(source))


def enroll_extracted(store, events, person_id, extracted, *, expected_pipeline=None,
                     min_quality=0.5, model_id=None, activate=True):
    """把提取结果提交到业务库（face 模态）。**不触任何图库/JSON 身份。**"""
    data = normalize_result(extracted)
    if data['quality'] < float(min_quality):
        raise ConflictError('low_quality',
                            '质量 %.3f 低于阈值 %.3f' % (data['quality'], min_quality))
    if expected_pipeline is not None and data['pipeline_id'] != expected_pipeline:
        raise ConflictError('pipeline_mismatch',
                            '提取 pipeline=%s 与期望 %s 不一致' % (data['pipeline_id'],
                                                              expected_pipeline))
    mid = model_id or data['model_id']
    return samples.enroll(store, events, 'face', person_id, mid, data['embedding'],
                          dtype=None, source_ref='extract:%s' % data['source_sha256'],
                          quality=str(data['quality']), active=activate)
