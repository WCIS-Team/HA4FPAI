# -*- coding: utf-8 -*-
"""登记样本契约：人脸／声纹模板统一存业务库，带模型与预处理版本。

规划 §统一身份与模板契约：
  * 模板 = BLOB + 样本 ID + 模型/算法/预处理版本 + 维度 + 原媒体引用
  * 人脸与声纹独立表，不混用维度或阈值
  * 默认编码显式 little-endian IEEE754 FP32；需无损时用 float64-le
  * 模板有效性由对应后端校验；此处只做形状/有限值/模型契约检查
"""
import array
import math
import sys

from . import ids
from .errors import ConflictError, NotFoundError

DTYPES = ('float32-le', 'float64-le')


def encode_vector(vector, dtype):
    if dtype not in DTYPES:
        raise ConflictError('invalid_dtype', '不支持的向量编码: %s' % dtype)
    arr = array.array('d' if dtype == 'float64-le' else 'f', [float(v) for v in vector])
    if sys.byteorder == 'big':
        arr.byteswap()
    return arr.tobytes()


def decode_vector(blob, dtype, dim):
    arr = array.array('d' if dtype == 'float64-le' else 'f')
    arr.frombytes(bytes(blob))
    if len(arr) != int(dim):
        raise ConflictError('dim_mismatch', '解码长度 %d != 声明维度 %d' % (len(arr), dim))
    return [float(v) for v in arr]


def register_model(store, events, model_id, modality, dim, model_sha256=None,
                   dtype='float32-le', metric='cosine', legacy_pipeline_id=None,
                   manifest_sha256=None, notes=None):
    """登记模型指纹；同 model_id 重复注册时指纹不一致即拒绝（防模板串模型）。"""
    if modality not in ('face', 'voice'):
        raise ConflictError('invalid_modality', 'modality 只能是 face/voice')
    if dtype not in DTYPES:
        raise ConflictError('invalid_dtype', '不支持的向量编码: %s' % dtype)
    dim = int(dim)
    with store.tx() as conn:
        row = conn.execute('SELECT * FROM models WHERE model_id=?', (model_id,)).fetchone()
        if row is not None:
            row = dict(row)
            same = (row['modality'] == modality and row['dim'] == dim and row['dtype'] == dtype
                    and (row['model_sha256'] or '') == (model_sha256 or ''))
            if not same:
                raise ConflictError('model_conflict',
                                    '模型 %s 已按不同指纹登记' % model_id,
                                    {'existing': row})
            return row
        conn.execute(
            'INSERT INTO models(model_id, modality, dim, dtype, metric, model_sha256,'
            ' runtime, precision, preprocess_version, manifest_sha256, legacy_pipeline_id,'
            ' notes, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (model_id, modality, dim, dtype, metric, model_sha256, None, None, None,
             manifest_sha256, legacy_pipeline_id, notes, ids.now_iso()))
        store.bump_domain_revision(conn)
    events.append('model.registered', {'model_id': model_id, 'modality': modality, 'dim': dim})
    return store.query_one('SELECT * FROM models WHERE model_id=?', (model_id,))


def _check_vector(vector, dim, require_unit_norm):
    if not isinstance(vector, (list, tuple)) or not vector:
        raise ConflictError('invalid_vector', '向量必须是非空数值列表')
    values = []
    for v in vector:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConflictError('invalid_vector', '向量含非数值元素')
        f = float(v)
        if not math.isfinite(f):
            raise ConflictError('invalid_vector', '向量含 NaN/Inf')
        values.append(f)
    if dim is not None and len(values) != int(dim):
        raise ConflictError('dim_mismatch', '向量维度 %d != 模型维度 %s' % (len(values), dim))
    if require_unit_norm:
        norm = math.sqrt(sum(v * v for v in values))
        if abs(norm - 1.0) > 1e-3:
            raise ConflictError('not_unit_norm', '向量 L2 范数 %.6f 偏离 1' % norm)
    return values


def enroll(store, events, modality, person_id, model_id, vector,
           dtype='float32-le', require_unit_norm=False,
           source_media_id=None, source_ref=None, quality=None):
    if modality not in ('face', 'voice'):
        raise ConflictError('invalid_modality', 'modality 只能是 face/voice')
    table = modality + '_samples'
    model = store.query_one('SELECT * FROM models WHERE model_id=?', (model_id,))
    if model is None:
        raise NotFoundError('model_unknown',
                            '模型 %s 未登记；先走 models.register（防模板串模型）' % model_id)
    if model['modality'] != modality:
        raise ConflictError('model_modality_mismatch', '模型 %s 不是 %s 模态' % (model_id, modality))
    values = _check_vector(vector, model['dim'], require_unit_norm)
    dtype = dtype or model['dtype']
    blob = encode_vector(values, dtype)
    sample_id = ids.new_id()
    now = ids.now_iso()
    with store.tx() as conn:
        person = conn.execute('SELECT * FROM persons WHERE person_id=?', (person_id,)).fetchone()
        if person is None:
            raise NotFoundError('person_not_found', '人员不存在: %s' % person_id)
        if dict(person)['status'] != 'active':
            raise ConflictError('person_inactive', '人员 %s 不是 active 状态' % person_id)
        conn.execute(
            'INSERT INTO %s(sample_id, person_id, model_id, dim, dtype, blob, blob_sha256,'
            ' source_media_id, source_ref, quality, active, created_at)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,1,?)' % table,
            (sample_id, person_id, model_id, model['dim'], dtype, blob,
             ids.digest_of(blob), source_media_id, source_ref, quality, now))
        conn.execute('UPDATE persons SET revision=revision+1, updated_at=? WHERE person_id=?',
                     (now, person_id))
        store.bump_domain_revision(conn)
    events.append(modality + '.enrolled',
                  {'sample_id': sample_id, 'model_id': model_id, 'dim': model['dim']},
                  person_id=person_id)
    return store.query_one('SELECT * FROM %s WHERE sample_id=?' % table, (sample_id,))


def revoke(store, events, modality, sample_id):
    table = modality + '_samples'
    now = ids.now_iso()
    with store.tx() as conn:
        row = conn.execute('SELECT * FROM %s WHERE sample_id=?' % table, (sample_id,)).fetchone()
        if row is None:
            raise NotFoundError('sample_not_found', '样本不存在: %s' % sample_id)
        conn.execute('UPDATE %s SET active=0, revoked_at=? WHERE sample_id=?' % table,
                     (now, sample_id))
        store.bump_domain_revision(conn)
    events.append(modality + '.revoked', {'sample_id': sample_id},
                  person_id=dict(row)['person_id'])
    return store.query_one('SELECT * FROM %s WHERE sample_id=?' % table, (sample_id,))


def list_for_person(store, modality, person_id, active_only=True):
    table = modality + '_samples'
    where = 'WHERE person_id=?' + (' AND active=1' if active_only else '')
    return store.query('SELECT sample_id, person_id, model_id, dim, dtype, blob_sha256,'
                       ' source_media_id, source_ref, quality, active, created_at, revoked_at'
                       ' FROM %s %s ORDER BY created_at' % (table, where), (person_id,))
