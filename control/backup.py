# -*- coding: utf-8 -*-
"""在线备份、校验与恢复（B05）。

规则（规划 §备份与恢复）：
  * 用 sqlite3 在线 backup API 生成一致性快照，**不直接拷主库**（WAL/写入中不安全）。
  * 备份附 `manifest.json`：db 的 sha256/字节数/schema_version/restore_epoch/时间，
    可带媒体清单（路径/字节/sha256）。
  * 恢复前必须校验：sha256、`integrity_check`、`foreign_key_check` 全过，不符即拒绝。
  * 恢复后**提升 restore_epoch**，使旧任务/回执/投影失效（A02 的世代栅栏）。

本模块只依赖标准库与 Store 的公开接口；不做任何外部动作。
"""
import hashlib
import json
import os
import sqlite3

from . import ids

MANIFEST = 'manifest.json'
CHUNK = 1 << 20


class BackupError(Exception):
    """备份/恢复的通用错误（IO、参数）。"""


class BackupInvalid(BackupError):
    """备份内容未通过校验（sha/integrity/fk/schema），拒绝恢复。"""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def _open_ro(path):
    """只读打开（不触发写、不建 -wal）。用 URI mode=ro。"""
    uri = 'file:' + os.path.abspath(path).replace('\\', '/').replace('?', '%3f')
    return sqlite3.connect(uri + '?mode=ro', uri=True)


def _scan_media(media_root):
    items = []
    if not media_root:
        return items
    for root, _dirs, files in os.walk(media_root):
        for name in files:
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
                items.append({
                    'path': os.path.relpath(full, media_root).replace('\\', '/'),
                    'bytes': size,
                    'sha256': _sha256_file(full),
                })
            except OSError:
                # 媒体在备份窗口内被移除：登记为不可读，不静默丢
                items.append({'path': os.path.relpath(full, media_root).replace('\\', '/'),
                              'bytes': None, 'sha256': None, 'unreadable': True})
    items.sort(key=lambda x: x['path'])
    return items


def backup(store, dest_dir, media_root=None, note=None):
    """在线一致性备份到 dest_dir，写 manifest，返回 manifest dict。

    用 store.conn.backup() —— 源库保持写入可用（在线），目的库是一致快照。
    """
    if not store.path or store.path == ':memory:':
        raise BackupError('内存库不能在线备份，请对文件库操作')
    os.makedirs(dest_dir, exist_ok=True)
    stamp = ids.now_iso().replace(':', '').replace('.', '').replace('+', '')
    db_name = 'business-%s.sqlite3' % stamp
    final_db = os.path.join(dest_dir, db_name)
    tmp_db = os.path.join(dest_dir, '.tmp-' + db_name)
    dst = sqlite3.connect(tmp_db)
    try:
        store.conn.backup(dst)
    finally:
        dst.close()
    # 校验快照本身可开且完整，再落最终名
    try:
        ro = _open_ro(tmp_db)
        try:
            integ = ro.execute('PRAGMA integrity_check').fetchone()[0]
            fk = ro.execute('PRAGMA foreign_key_check').fetchall()
        finally:
            ro.close()
    except sqlite3.Error as exc:
        os.remove(tmp_db)
        raise BackupError('备份快照无法打开: %r' % exc)
    if integ != 'ok' or fk:
        os.remove(tmp_db)
        raise BackupInvalid('备份快照自检失败: integrity=%r fk_rows=%d' % (integ, len(fk)))
    os.replace(tmp_db, final_db)
    manifest = {
        'kind': 'ha4fpai-business-backup',
        'created_at': ids.now_iso(),
        'db_file': db_name,
        'db_sha256': _sha256_file(final_db),
        'db_bytes': os.path.getsize(final_db),
        'schema_version': store.meta('schema_version'),
        'restore_epoch': store.restore_epoch(),
        'domain_revision': store.meta('domain_revision'),
        'store_id': store.meta('store_id'),
        'generation': store.meta('generation'),
        'sqlite_version': sqlite3.sqlite_version,
        'media': _scan_media(media_root),
        'note': note,
    }
    tmp_manifest = os.path.join(dest_dir, '.tmp-' + MANIFEST)
    with open(tmp_manifest, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_manifest, os.path.join(dest_dir, MANIFEST))
    return manifest


def verify(backup_dir, expected_sha256=None):
    """校验备份目录；通过返回 manifest，否则抛 BackupInvalid。只读，不改备份。"""
    manifest_path = os.path.join(backup_dir, MANIFEST)
    if not os.path.exists(manifest_path):
        raise BackupInvalid('缺少 %s' % MANIFEST)
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    db_path = os.path.join(backup_dir, manifest['db_file'])
    if not os.path.exists(db_path):
        raise BackupInvalid('备份库文件缺失: %s' % manifest['db_file'])
    actual = _sha256_file(db_path)
    if actual != manifest.get('db_sha256'):
        raise BackupInvalid('sha256 不符（manifest=%s 实际=%s）'
                            % (manifest.get('db_sha256'), actual))
    if expected_sha256 and actual != expected_sha256:
        raise BackupInvalid('sha256 与预期不符')
    ro = _open_ro(db_path)
    try:
        try:
            integ = ro.execute('PRAGMA integrity_check').fetchone()[0]
            fk = ro.execute('PRAGMA foreign_key_check').fetchall()
            has_mig = ro.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                 " AND name='schema_migrations'").fetchone()
            versions = [r[0] for r in ro.execute(
                'SELECT version FROM schema_migrations ORDER BY version')] if has_mig else []
        except sqlite3.Error as exc:
            raise BackupInvalid('备份库无法读取: %r' % exc)
    finally:
        ro.close()
    if integ != 'ok':
        raise BackupInvalid('integrity_check 未通过: %r' % integ)
    if fk:
        raise BackupInvalid('foreign_key_check 有 %d 行悬空外键' % len(fk))
    manifest['_verified_sha256'] = actual
    manifest['_schema_migrations'] = versions
    return manifest


def restore(store_path, backup_dir, expected_sha256=None):
    """把备份恢复到 store_path（调用方须保证此时无进程持有该库）。

    步骤：校验 → 落临时文件 → 原子替换 → 清旧 -wal/-shm → 打开并提升 restore_epoch。
    返回 {restored, from_sha256, epoch_before, epoch_after}。
    """
    manifest = verify(backup_dir, expected_sha256=expected_sha256)
    src_db = os.path.join(backup_dir, manifest['db_file'])
    target = os.path.abspath(store_path)
    dirn = os.path.dirname(target) or '.'
    os.makedirs(dirn, exist_ok=True)
    tmp_target = os.path.join(dirn, '.restore-tmp.sqlite3')
    with open(src_db, 'rb') as fsrc, open(tmp_target, 'wb') as fdst:
        for block in iter(lambda: fsrc.read(CHUNK), b''):
            fdst.write(block)
        fdst.flush()
        os.fsync(fdst.fileno())

    from .store import Store
    # 记录恢复前世代（若目标存在且可读）
    epoch_before = None
    if os.path.exists(target):
        try:
            old = Store(target)
            epoch_before = old.restore_epoch()
            old.conn.close()
        except Exception:
            epoch_before = None
    os.replace(tmp_target, target)
    for suffix in ('-wal', '-shm'):
        p = target + suffix
        if os.path.exists(p):
            os.remove(p)
    store = Store(target)
    epoch_after = store.bump_restore_epoch()
    store.conn.close()
    return {'restored': True, 'db_file': manifest['db_file'],
            'from_sha256': manifest['_verified_sha256'],
            'schema_version': manifest.get('schema_version'),
            'epoch_before': epoch_before, 'epoch_after': epoch_after}
