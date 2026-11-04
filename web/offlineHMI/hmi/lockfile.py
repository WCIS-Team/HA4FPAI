# -*- coding: utf-8 -*-
"""跨平台单实例锁：POSIX 用 fcntl.flock，Windows 用 msvcrt.locking。

kiosk 只允许一个实例——两个进程同时写帧缓冲会互相覆盖。
锁文件位置：POSIX 优先 /run（tmpfs，重启自清），退 /tmp；
Windows 落临时目录（校机预演用）。
"""
from __future__ import annotations

import os
import tempfile

try:
    import fcntl
except ImportError:                                  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None


def default_lock_path() -> str:
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), "offlinehmi.lock")
    if os.path.isdir("/run"):
        return "/run/offlinehmi.lock"
    return "/tmp/offlinehmi.lock"


class SingleInstance:
    def __init__(self, path: str | None = None):
        self.path = path or default_lock_path()
        self._fd = None

    def acquire(self) -> bool:
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            self.path = os.path.join(tempfile.gettempdir(), "offlinehmi.lock")
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                return True                          # 无锁原语：放行
        except OSError:
            os.close(self._fd)
            self._fd = None
            return False
        try:
            os.write(self._fd, ("%d\n" % os.getpid()).encode())
        except OSError:
            pass
        return True

    def release(self):
        if self._fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        except OSError:
            pass
        try:
            os.close(self._fd)
        finally:
            self._fd = None