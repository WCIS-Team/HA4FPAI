# -*- coding: utf-8 -*-
"""kiosk 层：单实例锁 · 首页/子页切换 · 角落热区回首页 · 空闲归位 · 管理员手势退出。

kiosk（自助终端）的定义（对齐她在 2026-11-04 的要求）：
  1. 开机就进、**独占全屏**——这块板没有 X/WM/桌面，程序独占 1920×1080 帧缓冲；
  2. 用户**只能在我们的界面里活动**——没有 shell、没有浏览器地址栏、没有别的进程能画屏；
  3. **出不去**——没有键盘退出路径；退出只留一个"管理员手势"（连点右上角 5 次）；
  4. **空闲自动归位**——无人操作 N 秒回首页（查询机/ATM 的标准行为）；
  5. **崩溃自动拉起 + 退出复位**——交给 systemd Restart=always；退出时把 0x54 写回复位默认。
"""
from __future__ import annotations

import time

from .app import App
from .ui import Button, Canvas
from .display import SCREEN_W, SCREEN_H
from .lockfile import SingleInstance  # noqa: F401  （对外导出，入口从这里取）

# 右上角＝回首页（单次点击）；右下角＝管理员退出（5 秒内连点 5 次，不可见）
HOT = (SCREEN_W - 140, 0, 140, 72)
HOT_ADMIN = (SCREEN_W - 96, SCREEN_H - 96, 96, 96)
ADMIN_TAPS = 5
ADMIN_WINDOW_S = 5.0


def draw_home_icon(c: "Canvas", cx: int, cy: int, size: int = 26,
                   fill=None):
    """画一个房子（不用字形，避免字体缺字变豆腐）。"""
    if fill is None:
        fill = c.theme.colors["icon"]
    half, roof = size // 2, size // 2
    x0, y0 = cx - half, cy
    # 屋顶三角
    c.d.polygon([(cx, cy - roof), (cx - half, cy), (cx + half, cy)], fill=fill)
    # 屋身
    c.d.rectangle((x0 + 4, cy, cx + half - 4, cy + size // 2), fill=fill)


class KioskShell(App):
    """首页 + 子页。子页就是已有的四个 app（它们本身是 App 子类）。

    registry: [(key, 标题, 工厂)]；工厂接收 (backend, mouse, board, theme) 返回 App。
    """

    title = "WCIS"

    def __init__(self, backend, mouse, board, registry, theme=None, cursor=True,
                 idle_reset_s=120.0, allow_admin_exit=True):
        super().__init__(backend, mouse, board, theme=theme, cursor=cursor,
                         idle_reset_s=idle_reset_s)
        self.registry = registry
        self.current = None            # None = 停在首页
        self.current_key = None
        self.allow_admin_exit = allow_admin_exit
        self._taps = []
        self.note = ""

    # -- 导航 ----------------------------------------------------------------
    def open(self, key: str):
        for k, _label, factory in self.registry:
            if k == key:
                child = factory(self.backend, self.mouse, self.board, self.theme)
                child.idle_reset_s = None        # 归位由 shell 统一管
                self.current = child
                self.current_key = key
                self.title = _label
                self.state["title"] = _label
                return
        self.note = "没有这一页: " + key

    def home(self):
        self.current = None
        self.current_key = None
        self.title = "WCIS"
        self.state["title"] = self.title

    def on_idle(self):
        if self.current is not None:
            self.home()
            self.note = "空闲归位"
            return True
        return False

    def on_shutdown(self):
        if self.current is not None:
            try:
                self.current.on_shutdown()
            except Exception:
                pass
        self.note = ""

    # -- 输入 ----------------------------------------------------------------
    def on_click(self, key: str):
        if key.startswith("open:"):
            self.open(key[5:])
        elif key == "home":
            self.home()
        elif key == "__admin":
            # 右下角热区连点 5 次＝管理员退出（kiosk 唯一的出口）
            if self.allow_admin_exit and self._admin_tapped():
                self.note = "管理员退出"
                self.stop()
        elif self.current is not None:
            self.current.on_click(key)

    def _hit_hot(self) -> bool:
        hx, hy, hw, hh = HOT_ADMIN
        return hx <= self.x < hx + hw and hy <= self.y < hy + hh

    def _admin_tapped(self) -> bool:
        """连点右上角 ADMIN_TAPS 次（ADMIN_WINDOW_S 内）→ 允许退出。"""
        now = time.time()
        self._taps = [t for t in self._taps if now - t <= ADMIN_WINDOW_S]
        self._taps.append(now)
        return len(self._taps) >= ADMIN_TAPS

    # -- 绘制 ----------------------------------------------------------------
    def build(self, c: Canvas, state):
        if self.current is not None:
            # 子页自己会画顶栏和时钟；这里先把它挡住，统一由 shell 画"时钟+房子"，
            # 免得房子图标压在时钟上。
            clock = state.get("clock", "")
            state["clock"] = ""
            btns = self.current.build(c, state) or []
            state["clock"] = clock
            c.text((SCREEN_W - 210, 34), clock, fill=c.theme.colors["muted"], anchor="rm")
            # 子页上叠一个回首页热区（kiosk 里这是"隐藏入口"，只画个房子）
            btns.append(Button("home", HOT, ""))
            btns.append(Button("__admin", HOT_ADMIN, ""))
            draw_home_icon(c, SCREEN_W - 110, 36)
            return btns

        # 首页：四宫格
        c.rect((0, 0, SCREEN_W, 96), fill=c.theme.colors["panel"])
        c.text((40, 48), "WCIS 端侧智能终端", anchor="lm", size=40)
        c.text((SCREEN_W - 40, 48), state.get("clock", ""), fill=c.theme.colors["muted"],
               anchor="rm", size=28)
        btns = []
        labels = dict((k, label) for k, label, _f in self.registry)
        cell_w, cell_h = 420, 220
        for i, (key, label, _factory) in enumerate(self.registry):
            row, col = divmod(i, 2)
            x = 96 + col * (cell_w + 48)
            y = 190 + row * (cell_h + 48)
            btns.append(Button("open:" + key, (x, y, cell_w, cell_h), label, "accent"))
        btns.append(Button("__admin", HOT_ADMIN, ""))
        if self.note:
            c.text((40, SCREEN_H - 48), self.note, fill=c.theme.colors["notice"])
        return btns