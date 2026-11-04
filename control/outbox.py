# -*- coding: utf-8 -*-
"""导出投影：业务库快照 → 有版本导出目录 + current 原子切换。

规划 §JSON投影与缓存：JSON 是带版本的兼容导出，不是权威存储；
导出失败显示 export_pending 并重试，数据库不回写旧 JSON。
current 指针只前进；旧导出目录不可变。
"""
import json
import os

from . import ids
from .errors import DomainError

EXPORT_SCHEMA = 'klar-registry-v1'


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows 上目录 fsync 不可用；此处的写入顺序靠 replace 原子性兜底
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path, value):
    tmp = path + '.tmp-' + ids.new_id()[:8]
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _registry_payload(store):
    entities = store.query('SELECT * FROM entities ORDER BY entity_id')
    areas = store.query('SELECT * FROM areas ORDER BY area_id')
    # 形状对齐 klar 的 HA 兼容导出（make_registry 输出）；
    # 引擎如不接受额外字段，只喂它认识的键（规划 §JSON投影与缓存）。
    ent_doc = {'version': 1, 'minor_version': 1, 'key': 'core.entity_registry',
               'data': {'entities': [
                   {'entity_id': e['entity_id'], 'name': e['name'],
                    'area_id': e['area_id'], 'device_id': e['device_id'],
                    'capabilities': json.loads(e['capabilities'] or '[]'),
                    'platform': 'ha4fpai', 'disabled_by': None, 'hidden_by': None,
                    'entity_category': None, 'original_name': e['name'],
                    'unique_id': e['entity_id'], 'config_entry_id': None}
                   for e in entities],
                   'deleted_entities': []}}
    area_doc = {'version': 1, 'minor_version': 1, 'key': 'core.area_registry',
                'data': {'areas': [
                    {'id': a['area_id'], 'name': a['name']} for a in areas],
                    'deleted_areas': []}}
    return ent_doc, area_doc


def export_registry(store, exports_dir):
    """生成不可变导出目录并原子切换 current；同 revision 重复导出返回既有目录。"""
    exports_dir = os.path.abspath(exports_dir)
    if not os.path.isdir(exports_dir):
        os.makedirs(exports_dir)
    with store.tx() as conn:
        revision = store.bump_domain_revision(conn)  # 导出序号沿用资料域版本，严格递增
        payload = _registry_payload(store)
    snapshot_name = 'export-%d-%s' % (revision, ids.new_id()[:8])
    snapshot_dir = os.path.join(exports_dir, snapshot_name)
    current_path = os.path.join(exports_dir, 'current')
    if os.path.isdir(current_path) or os.path.isfile(current_path):
        try:
            with open(os.path.join(current_path, 'manifest.json'), 'r', encoding='utf-8') as f:
                prev = json.load(f)
            if prev.get('source_revision') >= revision:
                return {'reused': True, 'manifest': prev}
        except (OSError, ValueError):
            pass
    os.makedirs(snapshot_dir)
    ent_doc, area_doc = payload
    files = {}
    for name, doc in (('entities.json', ent_doc), ('areas.json', area_doc)):
        # _write_json 内部已做 flush+fsync（Windows 上只读句柄不可 fsync，故不二次刷）
        _write_json(os.path.join(snapshot_dir, name), doc)
        with open(os.path.join(snapshot_dir, name), 'rb') as f:
            files[name] = ids.digest_of(f.read())
    manifest = {'store_id': store.meta('store_id'),
                'source_revision': revision,
                'export_schema': EXPORT_SCHEMA,
                'generated_at': ids.now_iso(),
                'snapshot': snapshot_name,
                'files': files}
    _write_json(os.path.join(snapshot_dir, 'manifest.json'), manifest)
    _fsync_dir(snapshot_dir)
    # current 指针：先写 tmp 再 replace，目录永不半态
    pointer_tmp = current_path + '.tmp-' + ids.new_id()[:8]
    with open(pointer_tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(snapshot_name + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(pointer_tmp, current_path)
    _fsync_dir(exports_dir)
    return {'reused': False, 'manifest': manifest, 'directory': snapshot_dir}


def read_current(exports_dir):
    """读取 current 导出；不存在或 manifest 缺失返回 None（不猜）。"""
    current_path = os.path.join(os.path.abspath(exports_dir), 'current')
    try:
        with open(current_path, 'r', encoding='utf-8') as f:
            snapshot = f.read().strip()
        with open(os.path.join(os.path.abspath(exports_dir), snapshot, 'manifest.json'),
                  'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def deliver(store, consumer, kind, revision):
    """消费游标：旧 revision 拒绝（消费端不会倒退），同 revision 幂等。"""
    now = ids.now_iso()
    with store.tx() as conn:
        row = conn.execute('SELECT * FROM consumer_offsets WHERE consumer=?', (consumer,)).fetchone()
        if row is not None and (row['kind'] == kind and int(revision) < int(row['last_revision'])):
            raise DomainError('stale_delivery', '消费游标倒退（当前 %s，收到 %s）'
                              % (row['last_revision'], revision))
        if row is None:
            conn.execute('INSERT INTO consumer_offsets(consumer, kind, last_revision,'
                         ' updated_at) VALUES(?,?,?,?)', (consumer, kind, int(revision), now))
        else:
            conn.execute('UPDATE consumer_offsets SET kind=?, last_revision=?, updated_at=?'
                         ' WHERE consumer=?', (kind, int(revision), now, consumer))
    return {'consumer': consumer, 'kind': kind, 'last_revision': int(revision)}
