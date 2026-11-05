# -*- coding: utf-8 -*-
"""受控媒体上传与读取（V9）：白名单类型、大小上限、原子落盘、防路径逃逸。

规划 §视频域适配与媒体 / §拟议服务API：
  * 上传 → media_assets 行（storage_root+relative_path+sha256），本体永不入库
  * 读取只按 media_id 从业务库白名单解析路径；realpath+commonpath 防符号链接逃逸
  * 已删除/撤销的媒体不提供服务；未知 media_id 404，不猜
"""
import hashlib
import os
import tempfile

from . import ids
from .errors import ConflictError, DomainError, NotFoundError

ALLOWED = {'image/jpeg': '.jpg', 'image/png': '.png',
           'audio/wav': '.wav', 'audio/x-wav': '.wav',
           'video/mp4': '.mp4'}
MAX_UPLOAD = 32 * 1024 * 1024


def _atomic_write(full_path, data):
    directory = os.path.dirname(full_path)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.media-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, full_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # Windows 无目录句柄 fsync：容忍（板上 Linux 生效）
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def save_upload(store, media_root, data, content_type, source_ref=None):
    """上传字节 → 原子落盘 → media_assets 行；返回行 dict。"""
    if content_type not in ALLOWED:
        raise ConflictError('unsupported_media_type',
                            '类型 %s 不在白名单（允许：%s）' % (content_type, '/'.join(sorted(ALLOWED))))
    if not data:
        raise DomainError('empty_upload', '上传内容为空')
    if len(data) > MAX_UPLOAD:
        raise ConflictError('upload_too_large',
                            '上传超过上限 %d 字节' % MAX_UPLOAD, {'limit': MAX_UPLOAD})
    if not media_root:
        raise DomainError('media_root_unconfigured', '服务未配置媒体根目录，拒绝落盘')
    media_id = ids.new_id()
    relative = 'uploads/%s%s' % (media_id, ALLOWED[content_type])
    full = os.path.join(media_root, 'uploads', media_id + ALLOWED[content_type])
    _atomic_write(full, data)
    now = ids.now_iso()
    with store.tx() as conn:
        conn.execute(
            'INSERT INTO media_assets(media_id, storage_root, relative_path, kind,'
            ' size_bytes, sha256, status, created_at) VALUES(?,?,?,?,?,?,?,?)',
            (media_id, media_root, relative, content_type, len(data),
             hashlib.sha256(data).hexdigest(), 'ready', now))
        store.bump_domain_revision(conn)
    return store.query_one('SELECT * FROM media_assets WHERE media_id=?', (media_id,))


def open_asset(store, media_id):
    """按 media_id 解析受控路径；返回 (绝对路径, 行)。防逃逸；不提供已删除媒体。"""
    row = store.query_one('SELECT * FROM media_assets WHERE media_id=?', (media_id,))
    if row is None:
        raise NotFoundError('media_not_found', '媒体不存在: %s' % media_id)
    if row['status'] in ('deleted', 'revoked'):
        raise ConflictError('media_unavailable', '媒体 %s 状态 %s，不提供读取' % (media_id, row['status']))
    base = os.path.realpath(row['storage_root'])
    full = os.path.realpath(os.path.join(row['storage_root'], row['relative_path']))
    if os.path.commonpath([base, full]) != base:
        raise ConflictError('media_path_escape', '媒体路径越界，拒绝读取')
    if not os.path.isfile(full):
        raise NotFoundError('media_file_missing', '媒体文件缺失: %s' % media_id)
    return full, row
