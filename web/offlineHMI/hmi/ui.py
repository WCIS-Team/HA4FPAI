# -*- coding: utf-8 -*-
"""极简控件层：矩形按钮、标签、图标按钮、软件光标——全部画进一帧 RGB565。

设计约束（方案页 §九）：
- 界面是"事件驱动整帧重画"：鼠标动/点才重画，静止零 CPU；
- 画布就是 1920x1080x2 的 numpy 数组，最后由 display 后端呈现；
- 中文标签需要 TTF 字体（板上 fonts-wqy-zenhei 或任意 .ttf 路径），
  没有字体时自动退化为 ASCII 文本。
"""
from __future__ import annotations

import ctypes
import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .display import SCREEN_W, SCREEN_H, rgb_to_rgb565

# ---- 调色板（1080p 深底，与 8092 页面同色系） --------------------------------
BG = (16, 24, 32)
PANEL = (26, 36, 48)
ACCENT = (64, 180, 120)
DANGER = (214, 84, 80)
TEXT = (235, 235, 235)
MUTED = (130, 140, 150)


class Theme:
    """字体：优先找一个能显示中文的 TTF；找不到退化到 PIL 位图字体（仅 ASCII）。"""

    CJK_CANDIDATES = (
        # Windows（校机/本机预演）
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        # 板端（apt install fonts-wqy-zenhei）
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        # 102
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )

    def __init__(self, ttf_path: str | None = None, size: int = 28):
        self.ttf = ttf_path or self._find()
        self.size = size
        try:
            self.font = ImageFont.truetype(self.ttf, size) if self.ttf else None
        except OSError:
            self.font = None
        self._ascii_only = self.font is None
        if self.font is None:
            self.font = ImageFont.load_default()

    @classmethod
    def _find(cls):
        import os
        for p in cls.CJK_CANDIDATES:
            if os.path.exists(p):
                return p
        return None

    def can(self, text: str) -> bool:
        return not self._ascii_only or all(ord(c) < 128 for c in text)

    def renderable(self, s: str) -> str:
        """PIL 位图字体（load_default）只能画 latin-1；CJK 字符会抛
        UnicodeEncodeError（Pillow 7/9 都会）。没有 TTF 时把非 latin-1 字符
        替换成 '?'，保证不崩——中文标签退化成问号而不是整个画面挂掉。"""
        if self._ascii_only:
            return "".join(c if ord(c) < 256 else "?" for c in s)
        return s


class Canvas:
    """一帧的绘制面。用 PIL 画，最后转 RGB565 numpy。"""

    def __init__(self, theme: Theme):
        self.theme = theme
        self.img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG)
        self.d = ImageDraw.Draw(self.img)

    def to_rgb565(self) -> "np.ndarray":
        rgb = np.asarray(self.img, dtype=np.uint8)
        return rgb_to_rgb565(rgb)

    def jpeg_to_rgb(self, jpeg: bytes, box: tuple[int, int, int, int]) -> None:
        """把 JPEG 等比缩放贴进 box=(x,y,w,h) 区域内，居中。"""
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        x, y, w, h = box
        scale = min(w / im.width, h / im.height)
        nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        im = im.resize((nw, nh))
        self.img.paste(im, (x + (w - nw) // 2, y + (h - nh) // 2))

    # ---- 图元 ----
    def rect(self, box, fill=None, outline=None, width=2):
        """box 用 (x, y, w, h)；PIL 要 (x0, y0, x1, y1)，这里统一换算。"""
        x, y, w, h = box
        if w <= 0 or h <= 0:
            return
        self.d.rectangle((x, y, x + w - 1, y + h - 1), fill=fill,
                         outline=outline, width=width)

    def text(self, xy, s, fill=TEXT, anchor=None, size=None):
        f = self.theme.font
        if size and not self.theme._ascii_only and self.theme.ttf:
            if size != self.theme.size:
                f = ImageFont.truetype(self.theme.ttf, size)
        s = self.theme.renderable(s)
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)


class Button:
    """矩形按钮：命中测试 + 绘制。"""

    def __init__(self, key: str, box: tuple[int, int, int, int], label: str,
                 kind: str = "normal"):  # normal | accent | danger
        self.key, self.box, self.label, self.kind = key, box, label, kind
        self.enabled = True

    def hit(self, x: int, y: int) -> bool:
        bx, by, bw, bh = self.box
        return self.enabled and bx <= x < bx + bw and by <= y < by + bh

    def draw(self, c: Canvas, hot: bool):
        fill = {"normal": PANEL, "accent": ACCENT, "danger": DANGER}[self.kind]
        outline = (255, 255, 255) if hot else None
        c.rect(self.box, fill=fill, outline=outline, width=3)
        bx, by, bw, bh = self.box
        c.text((bx + bw // 2, by + bh // 2), self.label,
               fill=(20, 26, 32) if self.kind != "normal" else TEXT,
               anchor="mm")


class Cursor:
    """软件光标：16x16 白箭头 + 黑描边，贴在整帧之上。"""

    ARROW = [  # 16x16, '.' 透明 '*' 白 '#' 黑
        "*...............",
        "**..............",
        "#*..............",
        "#**.............",
        "#***............",
        "#****...........",
        "#*****..........",
        "#******.........",
        "#*******........",
        "#********.......",
        "#*****####......",
        "#**#**..........",
        "#*#.*#..........",
        "##..#*..........",
        ".....#*.........",
        "................",
    ]

    @classmethod
    def blit(cls, rgb565: "np.ndarray", x: int, y: int):
        h, w = len(cls.ARROW), len(cls.ARROW[0])
        x0, y0 = max(0, x), max(0, y)
        for j in range(h):
            yy = y0 + j
            if yy >= SCREEN_H:
                break
            row = cls.ARROW[j]
            for i in range(w):
                xx = x0 + i
                if xx >= SCREEN_W:
                    break
                ch = row[i]
                if ch == ".":
                    continue
                px = 0xFFFF if ch == "*" else 0x0000
                rgb565[yy, xx, 0] = px & 0xFF
                rgb565[yy, xx, 1] = px >> 8


class Frame:
    """一屏：底图 + 控件集。runloop 每次输入事件后重画。"""

    def __init__(self, title: str):
        self.title = title

    def build(self, c: Canvas, state: dict) -> list[Button]:
        raise NotImplementedError

    @staticmethod
    def header(c: Canvas, state: dict):
        c.rect((0, 0, SCREEN_W, 64), fill=PANEL)
        c.text((24, 32), state.get("title", ""), anchor="lm", size=36)
        if state.get("clock"):
            c.text((SCREEN_W - 24, 32), state["clock"], fill=MUTED, anchor="rm")
