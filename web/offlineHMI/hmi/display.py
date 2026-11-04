# -*- coding: utf-8 -*-
"""显示后端抽象：png（校机预演）/ udmabuf（板上真机）双实现。

屏幕几何与寄存器常量来自板上取证（A 级，2026-11-03 只读）：
  udmabuf0  物理 0x30100000  128 MiB   /sys/class/u-dma-buf/udmabuf0/
  一帧      1920*1080*2 = 4,147,200 B (RGB565)
  0x40080054 = 物理地址 >> 3（frame_ctrl.v slv_reg21，hdmi_display.hpp 同式）
  0x40080078 bit0: 0=显示开(RGB565) 1=显示关 —— 本模块从不写 1。

png 后端：每帧落 <outdir>/frame-NNNN.png，供校机/102 无板验证。
udmabuf 后端：mmap /dev/udmabuf0，双缓冲（A/B 区），写 0x54 换址。
"""
from __future__ import annotations

import ctypes
import mmap
import os

import numpy as np
from PIL import Image

SCREEN_W, SCREEN_H = 1920, 1080
FRAME_BYTES = SCREEN_W * SCREEN_H * 2

UDMABUF_DEV = "/dev/udmabuf0"
UDMABUF0_PHYS = 0x30100000
UDMABUF0_SIZE = 134217728
DEFAULT_OFFSET = 0x07000000          # 尾部，避开 icraft 池
REG_DISPLAY_ADDR = 0x40080054
REG_DEFAULT_0x54 = 0x07E00000        # 复位默认（取证读回值）
PAGE_SIZE = 0x1000


class PngBackend:
    """无板预演：把每帧写 成 PNG。outdir 必填。"""

    def __init__(self, outdir: str):
        self.outdir = outdir
        os.makedirs(outdir, exist_ok=True)
        self._n = 0
        self._mine: list[str] = []       # 只清理本次会话写的帧

    def present(self, rgb565: "np.ndarray") -> str:
        """rgb565: uint8[1080,1920,2]。落盘并返回文件名。"""
        if rgb565.shape != (SCREEN_H, SCREEN_W, 2):
            raise ValueError("expect (1080,1920,2) uint8, got %r" % (rgb565.shape,))
        self._n += 1
        name = "frame-%05d.png" % self._n
        Image.frombytes("RGB", (SCREEN_W, SCREEN_H), rgb565.tobytes(),
                        "raw", "BGR;16").save(os.path.join(self.outdir, name))
        # 只保留本次会话最近 3 帧，防预演目录爆盘；不碰别的运行留下的
        self._mine.append(name)
        while len(self._mine) > 3:
            old = self._mine.pop(0)
            try:
                os.unlink(os.path.join(self.outdir, old))
            except OSError:
                pass
        return name


def rgb_to_rgb565(rgb: "np.ndarray") -> "np.ndarray":
    """rgb: uint8[H,W,3] -> uint8[H,W,2]（RGB565，小端字节序）。"""
    h, w = rgb.shape[:2]
    r = (rgb[:, :, 0].astype(np.uint16) >> 3) << 11
    g = (rgb[:, :, 1].astype(np.uint16) >> 2) << 5
    b = (rgb[:, :, 2].astype(np.uint16) >> 3)
    packed = (r | g | b).astype(np.uint16)
    return packed.view(np.uint8).reshape(h, w, 2)


class UdmabufBackend:
    """板上真机：双缓冲 + 写 0x54 换址。需要 root。"""

    def __init__(self, offset: int = DEFAULT_OFFSET):
        if offset + 2 * FRAME_BYTES > UDMABUF0_SIZE:
            raise ValueError("offset 0x%X needs two frames beyond udmabuf0" % offset)
        self.offset = offset
        self._fd = os.open(UDMABUF_DEV, os.O_RDWR | os.O_SYNC)
        self._mm = mmap.mmap(self._fd, UDMABUF0_SIZE)
        self._front = 0
        self._mem = None  # /dev/mem 映射，懒建立

    # -- /dev/mem 寄存器访问 ------------------------------------------------
    def _open_devmem(self, rw: bool):
        fd = os.open("/dev/mem", (os.O_RDWR if rw else os.O_RDONLY) | os.O_SYNC)
        return fd

    def _reg_write(self, addr: int, value: int):
        page, off = addr & ~0xFFF, addr & 0xFFF
        fd = self._open_devmem(True)
        try:
            m = mmap.mmap(fd, PAGE_SIZE, offset=page)
            m[off:off + 4] = int(value).to_bytes(4, "little")
            m.close()
        finally:
            os.close(fd)

    def reg_read(self, addr: int) -> int:
        page, off = addr & ~0xFFF, addr & 0xFFF
        fd = self._open_devmem(False)
        try:
            m = mmap.mmap(fd, PAGE_SIZE, offset=page)
            v = int.from_bytes(m[off:off + 4], "little")
            m.close()
        finally:
            os.close(fd)
        return v

    # -- 帧提交 -------------------------------------------------------------
    def present(self, rgb565: "np.ndarray"):
        """把一帧拷进后备缓冲，再写 0x54 切到它。rgb565: uint8[1080,1920,2]。"""
        if rgb565.shape != (SCREEN_H, SCREEN_W, 2):
            raise ValueError("expect (1080,1920,2) uint8")
        back = 1 - self._front
        base = self.offset + back * FRAME_BYTES
        self._mm[base:base + FRAME_BYTES] = rgb565.tobytes()
        phys = UDMABUF0_PHYS + base
        self._reg_write(REG_DISPLAY_ADDR, (phys >> 3) & 0xFFFFFFFF)
        self._front = back

    def phys_front(self) -> int:
        return UDMABUF0_PHYS + self.offset + self._front * FRAME_BYTES

    def close(self, restore: bool = True):
        if restore:
            self._reg_write(REG_DISPLAY_ADDR, REG_DEFAULT_0x54)
        self._mm.close()
        os.close(self._fd)


def make_backend(spec: str):
    """'png:<outdir>' 或 'udmabuf[:offset]'。"""
    if spec.startswith("png:"):
        return PngBackend(spec[4:])
    if spec == "udmabuf" or spec.startswith("udmabuf:"):
        off = int(spec.split(":", 1)[1], 0) if ":" in spec else DEFAULT_OFFSET
        return UdmabufBackend(off)
    raise ValueError("backend must be png:<dir> or udmabuf[:offset]")
