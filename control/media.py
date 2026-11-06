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
    """上传字节 → 校验真实编码 → 原子落盘 → media_assets 行；返回行 dict。"""
    if content_type not in ALLOWED:
        raise ConflictError('unsupported_media_type',
                            '类型 %s 不在白名单（允许：%s）' % (content_type, '/'.join(sorted(ALLOWED))))
    if not data:
        raise DomainError('empty_upload', '上传内容为空')
    if len(data) > MAX_UPLOAD:
        raise ConflictError('upload_too_large',
                            '上传超过上限 %d 字节' % MAX_UPLOAD, {'limit': MAX_UPLOAD})
    validate_payload(data, content_type)        # F05：真实编码/尺寸/炸弹校验
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


# ---- F05：真实编码 / 尺寸 / 解码预算校验（不信任声明的 MIME）----
MAX_PIXELS = 40 * 1000 * 1000      # 解码预算：40MP
MAX_DURATION_S = 600               # 音频/视频时长预算


def sniff(data):
    """按魔数判定真实格式。返回 'png'/'jpeg'/'webp'/'wav'/'mp4' 或 None。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'wav'
    if len(data) >= 12 and data[4:8] == b'ftyp':
        return 'mp4'
    return None


_MIME_FMT = {'image/png': 'png', 'image/jpeg': 'jpeg', 'audio/wav': 'wav',
             'audio/x-wav': 'wav', 'video/mp4': 'mp4'}


def _png_size(data):
    if len(data) < 24:
        raise ConflictError('truncated_media', 'PNG 头部不完整')
    import struct
    width, height = struct.unpack('>II', data[16:24])
    return width, height


def _jpeg_size(data):
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            import struct
            height, width = struct.unpack('>HH', data[i + 5:i + 9])
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], 'big')
        i += 2 + seg_len
    raise ConflictError('truncated_media', 'JPEG 未找到尺寸')


def validate_payload(data, content_type, max_bytes=MAX_UPLOAD):
    """真实编码与声明一致、尺寸/时长在预算内；不符抛 ConflictError（F05）。"""
    if len(data) > max_bytes:
        raise ConflictError('upload_too_large', '超过上限 %d 字节' % max_bytes)
    fmt = sniff(data)
    if fmt is None:
        raise ConflictError('bad_media', '无法识别的真实编码（可能是伪 %s）' % content_type)
    want = _MIME_FMT.get(content_type)
    if want is not None and fmt != want:
        raise ConflictError('media_type_mismatch',
                            '声明 %s 实际是 %s' % (content_type, fmt))
    meta = {'format': fmt, 'bytes': len(data)}
    if fmt == 'png':
        w, h = _png_size(data)
        if w * h > MAX_PIXELS:
            raise ConflictError('decode_budget', 'PNG 像素 %dx%d 超预算' % (w, h))
        meta.update(width=w, height=h)
    elif fmt == 'jpeg':
        w, h = _jpeg_size(data)
        if w * h > MAX_PIXELS:
            raise ConflictError('decode_budget', 'JPEG 像素 %dx%d 超预算' % (w, h))
        meta.update(width=w, height=h)
    elif fmt == 'wav':
        if len(data) >= 32:
            byte_rate = int.from_bytes(data[28:32], 'little') or 1
            duration = max(0, len(data) - 44) / float(byte_rate)
            if duration > MAX_DURATION_S:
                raise ConflictError('decode_budget', 'WAV 时长 %.1fs 超预算' % duration)
            meta['duration_s'] = round(duration, 2)
    return meta


# ---- F06：Range / 鉴权 / 流式读取（不全量缓冲）----
def parse_range(range_header, size):
    """解析 Range 头。返回 {'start','end','suffix'} 或 {'unsatisfiable':True} 或 None(无头)。"""
    if not range_header:
        return None
    if size < 0:
        return {'unsatisfiable': True}
    text = str(range_header).strip()
    if not text.lower().startswith('bytes='):
        return {'unsatisfiable': True}
    spec = text[6:].strip()
    if ',' in spec:
        return {'unsatisfiable': True}          # 不做多段
    try:
        if spec.startswith('-'):
            n = int(spec[1:])
            if n <= 0:
                return {'unsatisfiable': True}
            start = max(0, size - n)
            return {'start': start, 'end': size - 1, 'suffix': True}
        if '-' not in spec:
            return {'unsatisfiable': True}
        a, b = spec.split('-', 1)
        start = int(a)
        if start >= size:
            return {'unsatisfiable': True}
        end = int(b) if b else size - 1
        end = min(end, size - 1)
        if end < start:
            return {'unsatisfiable': True}
        return {'start': start, 'end': end, 'suffix': False}
    except ValueError:
        return {'unsatisfiable': True}


def read_range(path, start, end):
    """按需 seek/read 指定区间，不把整文件读进内存。"""
    length = end - start + 1
    with open(path, 'rb') as f:
        f.seek(start)
        return f.read(length)


def check_read_access(store, token_hash, media_id):
    """校验会话有效（未撤销/未过期）后才允许读取；返回 principal_id。"""
    if not token_hash:
        raise ConflictError('unauthenticated', '缺少会话令牌')
    row = store.query_one('SELECT * FROM sessions WHERE token_hash=?', (token_hash,))
    if row is None:
        raise ConflictError('unauthenticated', '未知会话')
    if int(row['revoked']):
        raise ConflictError('session_revoked', '会话已撤销')
    if row['expires_at'] is not None and ids.now_iso() > row['expires_at']:
        raise ConflictError('session_expired', '会话已过期')
    return row['principal_id']


def reconcile(store, media_root):
    """媒体引用对账（F05）：DB 记录 vs 磁盘文件；只报告，不删未知文件。"""
    rows = [dict(r) for r in store.query('SELECT * FROM media_assets')]
    missing, size_mismatch, unsafe = [], [], []
    known = set()
    for row in rows:
        full = os.path.join(row['storage_root'], row['relative_path'])
        known.add(os.path.realpath(full))
        if os.path.islink(full):
            unsafe.append(row['media_id'])
            continue
        if not os.path.isfile(full):
            missing.append(row['media_id'])
            continue
        if row['size_bytes'] is not None and os.path.getsize(full) != row['size_bytes']:
            size_mismatch.append(row['media_id'])
    orphans = []
    if media_root and os.path.isdir(media_root):
        for root, _d, files in os.walk(media_root):
            for name in files:
                full = os.path.realpath(os.path.join(root, name))
                if full not in known:
                    orphans.append(os.path.relpath(full, media_root).replace('\\', '/'))
    return {'missing_files': sorted(missing), 'orphans': sorted(orphans),
            'size_mismatch': sorted(size_mismatch), 'unsafe_symlink': sorted(unsafe)}


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
