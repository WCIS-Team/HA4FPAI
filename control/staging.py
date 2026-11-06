# -*- coding: utf-8 -*-
"""JSON1 迁移暂存桥与幂等导入（C02/C03/C04）。

流程：load_source 把外部 JSON（gallery schema2 / 两类声纹）按源展开成 stg_* 行，
坏行入 migration_reject（保源定位），成功/坏行总数等于源总数；import_staged 用 SQL
从暂存结构提字段、业务入口（persons/samples）生 person_id 并入库，靠 identity_map 与
stg 行 imported 标记做幂等；audit_model 核对模型指纹与样本一致性（C04）。
"""
import hashlib
import json
import math

from . import ids, json1, persons, samples
from .errors import ConflictError, DomainError, NotFoundError

GALLERY = 'gallery'
VOICE_BOARDS = 'voice_boards'
VOICE_LANG = 'voice_lang'


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _detect(data):
    """按内容判定源类型；返回 (kind, schema_note) 或 (None, reason)。"""
    if not isinstance(data, dict):
        return None, 'top-level-not-object'
    if data.get('schema_version') == 2 and isinstance(data.get('people'), list):
        return GALLERY, 'gallery-schema2'
    speakers = data.get('speakers')
    if isinstance(speakers, dict):
        sample = next(iter(speakers.values()), None)
        if isinstance(sample, dict) and 'vectors' in sample:
            return VOICE_LANG, 'voiceprint-lang-multisample'
        if isinstance(sample, list):
            return VOICE_BOARDS, 'voiceprint-boards-single'
    return None, 'unrecognized-shape'


def _valid_name(name):
    if not isinstance(name, str):
        return False
    n = name.strip()
    return bool(n) and len(n) <= 40 and all(ord(c) >= 32 for c in n)


def _vector_ok(vec):
    """返回 (ok, reason, dim_or_None)。"""
    if vec is None:
        return False, 'null_vector', None
    if not isinstance(vec, list):
        return False, 'wrong_type', None
    if not vec:
        return False, 'empty_vector', 0
    for v in vec:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, 'non_numeric', len(vec)
        if not math.isfinite(float(v)):
            return False, 'non_finite', len(vec)
    return True, None, len(vec)


def _reject(conn, source_id, stage, src_key, reason, detail, now):
    conn.execute(
        'INSERT INTO migration_reject(source_id, stage, src_key, reason, detail, rejected_at)'
        ' VALUES(?,?,?,?,?,?)', (source_id, stage, src_key, reason, str(detail)[:500], now))


def _expand(data, kind):
    """展开成统一条目：[(person_key,name), (person_key, sample_key, vector_or_None, reason)]。

    返回 (person_rows, sample_rows)。sample_rows 里 vector=None 表示该样本坏，带 reason。
    """
    person_rows = []
    sample_rows = []
    if kind == GALLERY:
        declared = None
        for p_idx, person in enumerate(data.get('people') or []):
            if not isinstance(person, dict):
                person_rows.append((str(p_idx), None, 'person_not_object'))
                continue
            key = str(person.get('id') or p_idx)
            name = person.get('name')
            if not _valid_name(name):
                person_rows.append((key, name if isinstance(name, str) else None,
                                    'invalid_name'))
            else:
                person_rows.append((key, name.strip(), None))
            photos = person.get('photos') or []
            if not isinstance(photos, list):
                sample_rows.append((key, key + '#photos', None, 'photos_not_list'))
                continue
            for i, photo in enumerate(photos):
                skey = '%s#%s' % (key, (photo.get('id') if isinstance(photo, dict) else i) or i)
                emb = photo.get('embedding') if isinstance(photo, dict) else None
                ok, reason, dim = _vector_ok(emb)
                if declared is None and ok:
                    declared = dim
                if ok and declared is not None and dim != declared:
                    sample_rows.append((key, skey, None, 'dim_mismatch'))
                else:
                    sample_rows.append((key, skey, emb if ok else None, reason))
    elif kind == VOICE_BOARDS:
        declared = data.get('dim')
        for idx, (name, vec) in enumerate((data.get('speakers') or {}).items()):
            key = str(name)
            if not _valid_name(name):
                person_rows.append((key, None, 'invalid_name'))
            else:
                person_rows.append((key, name.strip(), None))
            ok, reason, dim = _vector_ok(vec)
            if ok and declared is not None and int(dim) != int(declared):
                sample_rows.append((key, key + '#0', None, 'dim_mismatch'))
            else:
                sample_rows.append((key, key + '#0', vec if ok else None, reason))
    elif kind == VOICE_LANG:
        declared = data.get('dim')
        for name, entry in (data.get('speakers') or {}).items():
            key = str(name)
            if not _valid_name(name):
                person_rows.append((key, None, 'invalid_name'))
            else:
                person_rows.append((key, name.strip(), None))
            vecs = entry.get('vectors') if isinstance(entry, dict) else None
            if not isinstance(vecs, list) or not vecs:
                sample_rows.append((key, key + '#none', None, 'no_vectors'))
                continue
            first_dim = None
            for i, vec in enumerate(vecs):
                ok, reason, dim = _vector_ok(vec)
                if ok and first_dim is None:
                    first_dim = dim
                if ok and first_dim is not None and dim != first_dim:
                    sample_rows.append((key, '%s#%d' % (key, i), None, 'dim_mismatch'))
                elif ok and declared is not None and int(dim) != int(declared):
                    sample_rows.append((key, '%s#%d' % (key, i), None, 'dim_mismatch'))
                else:
                    sample_rows.append((key, '%s#%d' % (key, i), vec if ok else None, reason))
    return person_rows, sample_rows


def load_source(store, path, kind=None):
    """加载一个外部 JSON 到暂存区（幂等：同 sha 重载先清旧暂存行再写）。返回报告。"""
    info = json1.require(store.conn)          # C01 门禁：能力不足即拒绝
    with open(path, 'rb') as f:
        data_bytes = f.read()
    sha = _sha256_bytes(data_bytes)
    source_id = sha[:16]
    now = ids.now_iso()
    report = {'source_id': source_id, 'sha256': sha, 'bytes': len(data_bytes),
              'kind': None, 'schema': None, 'n_rows': 0, 'n_ok': 0, 'n_bad': 0,
              'json1_sqlite': info['sqlite_version'], 'rejects': []}
    try:
        text = data_bytes.decode('utf-8')
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        with store.tx() as conn:
            conn.execute('DELETE FROM stg_person WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM stg_sample WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM migration_reject WHERE source_id=?', (source_id,))
            conn.execute(
                'INSERT OR REPLACE INTO migration_source(source_id, kind, path, sha256, bytes,'
                " detected_schema, loaded_at, n_rows, n_ok, n_bad)"
                " VALUES(?,?,?,?,?,?,?,0,0,1)",
                (source_id, kind or 'unknown', path, sha, len(data_bytes), 'invalid-json', now))
            _reject(conn, source_id, 'file', None, 'invalid_json', repr(exc), now)
        report.update(kind=kind or 'unknown', schema='invalid-json', n_rows=1, n_bad=1,
                      rejects=[{'stage': 'file', 'reason': 'invalid_json'}])
        return report
    detected, schema = _detect(data)
    kind = kind or detected
    if kind is None or detected is None or (detected != kind):
        raise DomainError('unrecognized_source',
                          '无法识别的源结构（detect=%r，指定=%r）' % (detected, kind))
    person_rows, sample_rows = _expand(data, kind)
    n_ok = n_bad = 0
    with store.tx() as conn:
        conn.execute('DELETE FROM stg_person WHERE source_id=?', (source_id,))
        conn.execute('DELETE FROM stg_sample WHERE source_id=?', (source_id,))
        conn.execute('DELETE FROM migration_reject WHERE source_id=?', (source_id,))
        for ordn, (key, name, reason) in enumerate(person_rows):
            if reason is None:
                n_ok += 1
                conn.execute('INSERT INTO stg_person(source_id, src_key, name, status, ord)'
                             " VALUES(?,?,?,'ok',?)", (source_id, key, name, ordn))
            else:
                n_bad += 1
                conn.execute('INSERT INTO stg_person(source_id, src_key, name, status, reason, ord)'
                             " VALUES(?,?,?, 'bad', ?, ?)", (source_id, key, name, reason, ordn))
                _reject(conn, source_id, 'person', key, reason, name, now)
        for ordn, (owner, skey, vec, reason) in enumerate(sample_rows):
            if reason is None:
                n_ok += 1
                conn.execute(
                    'INSERT INTO stg_sample(source_id, src_key, owner_key, dim, dtype,'
                    ' vector_json, sha256, status, ord)'
                    " VALUES(?,?,?,?,?,?,?, 'ok', ?)",
                    (source_id, skey, owner, len(vec), 'float64-le',
                     json.dumps(vec), _sha256_bytes(json.dumps(vec).encode('utf-8')), ordn))
            else:
                n_bad += 1
                conn.execute(
                    'INSERT INTO stg_sample(source_id, src_key, owner_key, status, reason, ord)'
                    " VALUES(?,?,?, 'bad', ?, ?)", (source_id, skey, owner, reason, ordn))
                _reject(conn, source_id, 'sample', skey, reason, owner, now)
        conn.execute(
            'INSERT OR REPLACE INTO migration_source(source_id, kind, path, sha256, bytes,'
            ' detected_schema, loaded_at, n_rows, n_ok, n_bad) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (source_id, kind, path, sha, len(data_bytes), schema, now,
             n_ok + n_bad, n_ok, n_bad))
    report.update(kind=kind, schema=schema, n_rows=n_ok + n_bad, n_ok=n_ok, n_bad=n_bad)
    report['rejects'] = [{'stage': r['stage'], 'key': r['key'] if 'key' in r else None,
                          'reason': r['reason']}
                         for r in store.query(
                             'SELECT stage, src_key AS key, reason FROM migration_reject'
                             ' WHERE source_id=? ORDER BY reject_id', (source_id,))]
    return report


def import_staged(store, events, source_id, model_id, decisions=None, activate=False):
    """把某源的 ok 暂存行幂等导入业务库（C03）。返回动作报告。"""
    decisions = dict(decisions or {})
    src = store.query_one('SELECT * FROM migration_source WHERE source_id=?', (source_id,))
    if src is None:
        raise NotFoundError('source_unknown', '暂存源不存在: %s' % source_id)
    model = store.query_one('SELECT * FROM models WHERE model_id=?', (model_id,))
    if model is None:
        raise DomainError('model_unknown',
                          '模型 %s 未登记；先 samples.register_model' % model_id)
    want_modality = 'face' if src['kind'] == GALLERY else 'voice'
    if model['modality'] != want_modality:
        raise ConflictError('model_modality_mismatch',
                            '源 %s 需要 %s 模型，%s 是 %s'
                            % (src['kind'], want_modality, model_id, model['modality']))
    namespace = 'stg:' + source_id
    store_id = store.meta('store_id')
    now = ids.now_iso()
    report = {'source_id': source_id, 'created': 0, 'mapped': 0, 'enrolled': 0,
              'skipped_existing': 0, 'skipped_needs_decision': [], 'errors': []}

    # 1) 人员：SQL 取暂存结构，业务入口生 person_id
    stg_people = store.query(
        "SELECT stg_id, src_key, name FROM stg_person WHERE source_id=? AND status='ok'"
        ' AND imported_at IS NULL ORDER BY ord', (source_id,))
    for row in stg_people:
        name = row['name']
        known = persons.identity_resolve(store, namespace, store_id, row['src_key'])
        if known is not None:
            with store.tx() as conn:
                conn.execute('UPDATE stg_person SET imported_person_id=?, imported_at=?'
                             ' WHERE stg_id=?', (known, now, row['stg_id']))
            report['skipped_existing'] += 1
            continue
        decision = decisions.get(name)
        if decision == 'skip':
            report['skipped_needs_decision'].append(
                {'name': name, 'reason': 'decision_skip'})
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
                person_id = persons.create(store, events, name)['person_id']
                report['created'] += 1
            else:
                target = persons.get(store, decision)
                if target is None or target['status'] != 'active':
                    report['errors'].append({'name': name, 'reason': 'decision_person_invalid'})
                    continue
                person_id = decision
            persons.identity_add(store, namespace, store_id, row['src_key'], person_id)
            report['mapped'] += 1
            with store.tx() as conn:
                conn.execute('UPDATE stg_person SET imported_person_id=?, imported_at=?'
                             ' WHERE stg_id=?', (person_id, now, row['stg_id']))
        except ConflictError as exc:
            report['errors'].append({'name': name, 'reason': exc.code})

    # 2) 样本：按 owner 的已导入 person_id 入业务库
    stg_samples = store.query(
        "SELECT s.stg_id, s.src_key, s.owner_key, s.vector_json FROM stg_sample s"
        " WHERE s.source_id=? AND s.status='ok' AND s.imported_at IS NULL ORDER BY s.ord",
        (source_id,))
    for row in stg_samples:
        pid_row = store.query_one(
            'SELECT imported_person_id FROM stg_person WHERE source_id=? AND src_key=?',
            (source_id, row['owner_key']))
        person_id = pid_row['imported_person_id'] if pid_row else None
        if not person_id:
            report['errors'].append({'sample': row['src_key'], 'reason': 'owner_not_imported'})
            continue
        try:
            vector = json.loads(row['vector_json'])
            sample = samples.enroll(store, events, want_modality, person_id, model_id, vector,
                                    dtype=None, source_ref='stg:%s#%s'
                                    % (source_id, row['src_key']), active=activate)
            report['enrolled'] += 1
            with store.tx() as conn:
                conn.execute('UPDATE stg_sample SET imported_sample_id=?, imported_at=?'
                             ' WHERE stg_id=?', (sample['sample_id'], now, row['stg_id']))
        except ConflictError as exc:
            report['errors'].append({'sample': row['src_key'], 'reason': exc.code})
    return report


def audit_model(store, model_id, expected=None, manifest_path=None, rehearsal=False):
    """核对模型指纹与样本一致性（C04）。返回 {ok, problems, evidence}。"""
    model = store.query_one('SELECT * FROM models WHERE model_id=?', (model_id,))
    if model is None:
        raise NotFoundError('model_unknown', '模型未登记: %s' % model_id)
    expected = dict(expected or {})
    problems = []
    for field in ('modality', 'dim', 'dtype', 'metric'):
        if field in expected and str(model[field]) != str(expected[field]):
            problems.append('%s: 登记=%r 期望=%r' % (field, model[field], expected[field]))
    if not model['model_sha256']:
        problems.append('model_sha256 缺失（无法证明件指纹）')
    if manifest_path:
        import hashlib
        try:
            with open(manifest_path, 'rb') as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError as exc:
            problems.append('manifest 不可读: %r' % exc)
            actual = None
        if actual is not None and model['manifest_sha256'] and actual != model['manifest_sha256']:
            problems.append('manifest sha 不符（登记=%s 实际=%s）'
                            % (model['manifest_sha256'], actual))
    if rehearsal and (model['legacy_pipeline_id'] or 'campplus-real' in (model_id or '')):
        problems.append('演练/真实混用：%s 不能当生产身份激活' % model_id)
    table = 'face_samples' if model['modality'] == 'face' else 'voice_samples'
    dims = [dict(r) for r in store.query(
        'SELECT dim, dtype, COUNT(*) AS n FROM %s WHERE model_id=? GROUP BY dim, dtype' % table,
        (model_id,))]
    return {
        'ok': not problems,
        'problems': problems,
        'evidence': {
            'model': {k: model[k] for k in
                      ('model_id', 'modality', 'dim', 'dtype', 'metric', 'model_sha256',
                       'manifest_sha256', 'legacy_pipeline_id')},
            'samples': dims,
        },
    }
