# -*- coding: utf-8 -*-
"""WAL 与磁盘运维（B06）：checkpoint 策略、空间/余量阈值、SQLite 错误映射。

规则（规划 §WAL与磁盘故障运维）：
  * 常规用 PASSIVE checkpoint 观测，维护窗口才用受控 TRUNCATE；不无脑 TRUNCATE。
  * 设磁盘余量与 WAL 体积阈值；越阈要能被观察到（healthy 返回 warnings）。
  * 把 SQLITE_BUSY/FULL/IOERR/READONLY 映射成稳定业务码，不让假成功。
"""
import os
import shutil
import sqlite3

# 默认阈值：磁盘余量下限 128 MiB；WAL 超过 64 MiB 视为需关注
DEFAULT_THRESHOLDS = {
    'min_free_bytes': 128 * 1024 * 1024,
    'wal_warn_bytes': 64 * 1024 * 1024,
}

_MODES = ('PASSIVE', 'FULL', 'RESTART', 'TRUNCATE')

# Python 3.11+ 才有 sqlite_errorcode；老版本按消息兜底
_ERRNO_MAP = {
    5: 'db_busy',       # SQLITE_BUSY
    6: 'db_locked',     # SQLITE_LOCKED
    8: 'db_readonly',   # SQLITE_READONLY
    10: 'db_io',        # SQLITE_IOERR
    13: 'db_full',      # SQLITE_FULL
}


def _wal_path(store):
    return (store.path or '') + '-wal'


def wal_size(store):
    p = _wal_path(store)
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def checkpoint(store, mode='PASSIVE'):
    """执行一次 WAL checkpoint，返回 {mode, busy, log_pages, checkpointed, wal_bytes}。

    PRAGMA wal_checkpoint(X) 返回 (busy, log, checkpointed)；busy=1 表示有读者/写者
    妨碍（PASSIVE 不阻塞，属正常观测）。维护窗口用 TRUNCATE 才回收文件尾部。
    """
    m = str(mode).upper()
    if m not in _MODES:
        raise ValueError('mode 只能是 %s' % '/'.join(_MODES))
    row = store.conn.execute('PRAGMA wal_checkpoint(%s)' % m).fetchone()
    busy, log_pages, done = (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
    return {'mode': m, 'busy': busy, 'log_pages': log_pages,
            'checkpointed': done, 'wal_bytes': wal_size(store)}


def disk_stat(path):
    """返回磁盘容量与余量；path 可以是目录或库文件。"""
    target = path
    if not os.path.isdir(target):
        target = os.path.dirname(os.path.abspath(path)) or '.'
    usage = shutil.disk_usage(target)
    return {'path': target, 'total_bytes': usage.total,
            'used_bytes': usage.used, 'free_bytes': usage.free}


def map_sqlite_error(exc):
    """把 sqlite3 异常映射成稳定业务码。"""
    code = getattr(exc, 'sqlite_errorcode', None)
    if code is not None:
        primary = code & 0xFF
        if primary in _ERRNO_MAP:
            return _ERRNO_MAP[primary]
    msg = str(exc).lower()
    if 'database is locked' in msg:
        return 'db_locked'
    if 'database is busy' in msg or 'database table is locked' in msg:
        return 'db_busy'
    if 'readonly' in msg or 'read-only' in msg:
        return 'db_readonly'
    if 'disk is full' in msg or 'database or disk is full' in msg:
        return 'db_full'
    if 'disk i/o error' in msg or 'io error' in msg:
        return 'db_io'
    return 'db_error'


def healthy(store, thresholds=None, path=None):
    """综合健康快照：WAL 体积、磁盘余量、是否越阈。不改库（只读取 wal 大小与磁盘）。"""
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    db_path = path or store.path
    d = disk_stat(db_path)
    wal_bytes = wal_size(store)
    warnings = []
    if d['free_bytes'] < th['min_free_bytes']:
        warnings.append('disk_free_below_min:%d<%d' % (d['free_bytes'], th['min_free_bytes']))
    if wal_bytes > th['wal_warn_bytes']:
        warnings.append('wal_above_warn:%d>%d' % (wal_bytes, th['wal_warn_bytes']))
    return {'ok': not warnings, 'warnings': warnings, 'wal_bytes': wal_bytes,
            'disk': d, 'thresholds': th}
