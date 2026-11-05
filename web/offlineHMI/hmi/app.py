# -*- coding: utf-8 -*-
"""共用 runloop：输入 → 命中/状态更新 → 重画 → 呈现。

静止时零重画（只等输入），符合 A53 算力约束。
kiosk 需要的那几件（空闲归位、SIGTERM 优雅退出并复位显示寄存器）也在这里，
由子类覆写 on_idle()/on_shutdown() 实现。
"""
from __future__ import annotations

import signal
import time

from .display import SCREEN_W, SCREEN_H
from .ui import Canvas, Theme, Cursor, Button

THEME_TOGGLE = (1400, 8, 180, 48)


class App:
    """子类实现 build(state) -> list[Button] 与 on_click(key)。"""

    title = "HMI"

    def __init__(self, backend, mouse, board, theme=None, cursor=True,
                 idle_reset_s: float | None = None):
        self.backend = backend
        self.mouse = mouse
        self.board = board
        self.theme = theme or Theme()
        self.cursor_on = cursor
        self.idle_reset_s = idle_reset_s
        self.x, self.y = SCREEN_W // 2, SCREEN_H // 2
        self.state = {"title": self.title,
                      "clock": time.strftime("%H:%M:%S"),
                      "x": self.x, "y": self.y}
        self.buttons: list = []
        self._last_clock = ""
        self.last_input = time.time()
        self._stop = False

    # -- 子类钩子 ------------------------------------------------------------
    def build(self, c: Canvas, state: dict):
        raise NotImplementedError

    def on_click(self, key: str):
        pass

    def handle_click(self, key: str):
        if key == "__theme":
            self.theme.toggle()
        else:
            self.on_click(key)

    def on_tick(self, state: dict):
        """每 ~1 s 唤醒一次（时钟/轮询）。默认只更新时钟。"""
        state["clock"] = time.strftime("%H:%M:%S")

    def on_idle(self):
        """空闲 idle_reset_s 秒后调用（kiosk 用它回首页）。默认什么都不做。"""

    def on_shutdown(self):
        """收到 SIGTERM/SIGINT 时调用（kiosk 用它复位显示寄存器）。"""

    # -- 信号 ----------------------------------------------------------------
    def _install_signals(self):
        def handler(signum, frame):
            self._stop = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):        # 非主线程等场景
                pass

    def stop(self):
        self._stop = True

    # -- 主循环 ----------------------------------------------------------------
    def run(self, poll_hz: float = 60, max_frames: int | None = None):
        self._install_signals()
        frame_no = 0
        last_tick = 0.0
        interval = 1.0 / poll_hz
        self._present(force=True)
        try:
            while not self._stop:
                ev = self.mouse.poll()
                dirty = False
                if ev.dx or ev.dy:
                    self.x = max(0, min(SCREEN_W - 1, self.x + ev.dx))
                    self.y = max(0, min(SCREEN_H - 1, self.y + ev.dy))
                    self.state["x"], self.state["y"] = self.x, self.y
                    self.last_input = time.time()
                    dirty = True
                if ev.click:
                    self.last_input = time.time()
                    for b in self.buttons:
                        if b.hit(self.x, self.y):
                            self.handle_click(b.key)
                            dirty = True
                            break
                now = time.time()
                if now - last_tick >= 1.0:
                    last_tick = now
                    self.on_tick(self.state)
                    if self.state.get("clock") != self._last_clock:
                        self._last_clock = self.state["clock"]
                        dirty = True
                    if self.idle_reset_s and now - self.last_input >= self.idle_reset_s:
                        self.last_input = now
                        if self.on_idle():
                            dirty = True
                if dirty or (max_frames is not None and frame_no == 0):
                    self._present(force=False)
                    frame_no += 1
                    if max_frames is not None and frame_no >= max_frames:
                        return frame_no
                time.sleep(interval)
            return frame_no
        finally:
            self._shutdown()

    def _shutdown(self):
        try:
            self.on_shutdown()
        except Exception:
            pass
        # udmabuf 后端在退出时把 0x54 复位（kiosk 拔掉/重启不留脏地址）
        close = getattr(self.backend, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass

    def _present(self, force: bool):
        c = Canvas(self.theme)
        self.buttons = self.build(c, self.state) or []
        label = "深色模式" if self.theme.scheme == "light" else "浅色模式"
        self.buttons.append(Button("__theme", THEME_TOGGLE, label))
        # 带标签的按钮由这里统一绘制（build 里只负责放位置）；
        # 空标签按钮是"热点区"（如列表行），由 build 自己画内容。
        for b in self.buttons:
            if b.label:
                b.draw(c, b.hit(self.x, self.y))
        rgb565 = c.to_rgb565()
        if self.cursor_on:
            Cursor.blit(rgb565, self.x, self.y)
        self.backend.present(rgb565)