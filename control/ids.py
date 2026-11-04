# -*- coding: utf-8 -*-
"""ID、规范 JSON、摘要与时间工具（py3.8 兼容，纯标准库）。"""
import datetime
import hashlib
import json
import os
import time
import uuid


def new_id():
    """实体主键：不含姓名语义的 UUID（hex32，兼容旧视频库的 ID 形状）。"""
    return uuid.uuid4().hex


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest_of(value):
    """规范化摘要：幂等键比较、确认绑定都用它，避免序列化差异误判。"""
    if not isinstance(value, (str, bytes)):
        value = canonical_json(value)
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


_BOOT_ID = None


def boot_id():
    """同一设备同一 boot 的标识；拿不到 /proc 时退化为进程级伪 boot。

    事件关联只允许在同一 boot 内比较单调时钟；跨 boot 用各自的
    wall_time 与同步质量（规划 §时间与跨库关联）。
    """
    global _BOOT_ID
    if _BOOT_ID is None:
        path = '/proc/sys/kernel/random/boot_id'
        text = ''
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
            except OSError:
                text = ''
        _BOOT_ID = text or ('pseudo-boot-' + uuid.uuid4().hex)
    return _BOOT_ID


def monotonic_ns():
    return time.monotonic_ns()
