# -*- coding: utf-8 -*-
"""数据盘持久化与掉盘策略（H02）：挂载身份核对 + 同设备校验。

规则（规划 §数据盘）：同一可靠文件系统；核对挂载 UUID 与 fstype；挂载丢失拒绝写、
不另建空库；DB/WAL/SHM 必须在同一设备。纯函数，便于无盘单测。
"""
import os


def parse_mountinfo(text):
    """解析 /proc/self/mountinfo 文本 → [{mountpoint, fstype, source}]。"""
    rows = []
    for line in str(text).splitlines():
        parts = line.split()
        if len(parts) < 10 or '-' not in parts:
            continue
        sep = parts.index('-')
        if sep + 2 >= len(parts):
            continue
        rows.append({'mountpoint': parts[4], 'fstype': parts[sep + 1],
                     'source': parts[sep + 2]})
    return rows


def _norm(path):
    return os.path.normpath(str(path)).replace('\\', '/')


def check_mount(mountinfo_text, target, expected_uuid=None, fstype='ext4'):
    """确认 target 确实挂载、fstype 正确、UUID 匹配；否则返回 ok=False 及原因。

    挂载丢失 → not_mounted：调用方应拒绝写入，绝不另建空库。
    """
    rows = parse_mountinfo(mountinfo_text)
    match = [r for r in rows if _norm(r['mountpoint']) == _norm(target)]
    if not match:
        return {'ok': False, 'reason': 'not_mounted', 'target': _norm(target)}
    m = match[0]
    if fstype and m['fstype'] != fstype:
        return {'ok': False, 'reason': 'fstype_mismatch', 'got': m['fstype'], 'want': fstype}
    if expected_uuid:
        uid = expected_uuid.lower().replace('-', '')
        src = m['source'].lower().replace('-', '')
        if uid not in src:
            return {'ok': False, 'reason': 'uuid_mismatch', 'source': m['source']}
    return {'ok': True, 'mount': m}


def require_mount(mountinfo_text, target, expected_uuid=None, fstype='ext4'):
    info = check_mount(mountinfo_text, target, expected_uuid=expected_uuid, fstype=fstype)
    if not info['ok']:
        raise RuntimeError('数据盘门禁未通过: %s（target=%s）'
                           % (info['reason'], info.get('target', target)))
    return info


def same_device(paths):
    """确认一组路径（DB/WAL/SHM/媒体）在同一设备；失败即拒绝（H02）。"""
    devs = set()
    for p in paths:
        try:
            devs.add(os.stat(p).st_dev)
        except OSError as exc:
            return {'ok': False, 'reason': 'stat_failed', 'path': p, 'error': str(exc)}
    return {'ok': len(devs) <= 1, 'devices': sorted(str(d) for d in devs)}
