# -*- coding: utf-8 -*-
"""鼠标输入：Linux evdev（板上）与 Windows/win32（校机）双实现。

板上: /dev/input/eventN, input_event 在 aarch64 = 24 B
      (struct timeval 16B + u16 type + u16 code + s32 value)
      EV_REL/REL_X|REL_Y 累计位移, EV_KEY/BTN_LEFT 1=按下 0=松开
校机: user32.GetCursorPos + GetAsyncKeyState(VK_LBUTTON)
"""
from __future__ import annotations

import ctypes
import os
import struct
import time

SCREEN_W, SCREEN_H = 1920, 1080


class MouseEvent:
    __slots__ = ("dx", "dy", "click")

    def __init__(self, dx=0, dy=0, click=False):
        self.dx, self.dy, self.click = dx, dy, click


class EvdevMouse:
    """读一个 /dev/input/eventN。热插拔由外层 reopen() 处理。"""

    def __init__(self, path: str):
        self.path = path
        self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        # aarch64 timeval = 2 x i64
        self._fmt = "llHHi"
        self._size = struct.calcsize(self._fmt)
        assert self._size == 24, self._size

    def poll(self) -> MouseEvent:
        ev = MouseEvent()
        try:
            data = os.read(self._fd, self._size * 32)
        except BlockingIOError:
            return ev
        for i in range(len(data) // self._size):
            _, _, etype, code, value = struct.unpack_from(self._fmt, data, i * self._size)
            if etype == 2:      # EV_REL
                if code == 0:
                    ev.dx += value
                elif code == 1:
                    ev.dy += value
            elif etype == 1:    # EV_KEY
                if code == 0x110 and value == 1:   # BTN_LEFT press
                    ev.click = True
        return ev

    def reopen(self):
        try:
            os.close(self._fd)
        except OSError:
            pass
        time.sleep(0.5)
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)


class Win32Mouse:
    """校机预演用：读真实系统光标。"""

    def __init__(self):
        self._x, self._y = SCREEN_W // 2, SCREEN_H // 2
        self._down = False
        self._user32 = ctypes.windll.user32

    def poll(self) -> MouseEvent:
        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = PT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        # 窗口坐标 → 1080p 画布坐标（简单居中映射；预演够用）
        dx = pt.x - self._x
        dy = pt.y - self._y
        self._x, self._y = pt.x, pt.y
        down = bool(self._user32.GetAsyncKeyState(0x01) & 0x8000)
        click = down and not self._down
        self._down = down
        return MouseEvent(dx, dy, click)


class MockMouse:
    """完全离线：按脚本路径移动并点击（无板无屏 CI 用）。"""

    def __init__(self, script):
        """script: list of (x, y, click) 绝对坐标步进。"""
        self.script = list(script)
        self.x, self.y = 960, 540

    def poll(self) -> MouseEvent:
        if not self.script:
            return MouseEvent()
        x, y, click = self.script.pop(0)
        dx, dy = x - self.x, y - self.y
        self.x, self.y = x, y
        return MouseEvent(dx, dy, click)


def find_mouse_device():
    """扫描 /proc/bus/input/devices，找第一块带 mouse 能力的设备，返回 /dev/input/eventN。

    设备号会随 USB 重枚举变（板上 MCP01 的 event0/event1 就是这样），
    所以运行时现场查，不要把 eventN 写死。
    """
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8", errors="replace") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return None
    for blk in blocks:
        if "mouse" not in blk.lower():
            continue
        for line in blk.splitlines():
            line = line.strip()
            if line.startswith("H: Handlers="):
                for tok in line.split("=", 1)[1].split():
                    if tok.startswith("event"):
                        return "/dev/input/" + tok
    return None


def make_mouse(spec: str):
    """'evdev' | 'evdev:/dev/input/eventN' | 'win32' | 'mock:<pts>'"""
    if spec == "evdev":
        dev = find_mouse_device()
        if not dev:
            raise RuntimeError("no mouse device found in /proc/bus/input/devices")
        return EvdevMouse(dev)
    if spec.startswith("evdev:"):
        return EvdevMouse(spec[6:])
    if spec == "win32":
        return Win32Mouse()
    if spec.startswith("mock:"):
        import ast
        arg = spec[5:]
        pts = ast.literal_eval(open(arg).read() if os.path.exists(arg) else arg)
        # 允许两种写法：[[x,y,c],...] 与 [[[x,y,c],...]]（多套一层）
        if (len(pts) == 1 and isinstance(pts[0], (list, tuple))
                and pts[0] and isinstance(pts[0][0], (list, tuple))):
            pts = pts[0]
        return MockMouse(pts)
    raise ValueError("mouse must be evdev[:/dev/input/eventN] | win32 | mock:<pts>")
