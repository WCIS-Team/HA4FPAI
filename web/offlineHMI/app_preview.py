#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_preview.py -- 8092 实时预览页的原生 HMI 复现。

8092 的浏览器页用 MSE+H.264（为局域网远端省带宽）；本地直显不需要那条链：
走 8091 的 /api/camera/preview.jpg 单帧 JPEG（零编解码，方案页 §九）。
本 app 轮询单帧 + 拍照留档。

用法:
  python app_preview.py --backend png:out --mouse mock:[[[300,300,0],[900,900,1]]] --mock
  python app_preview.py --backend png:out --mouse win32 --server http://192.168.1.113:8091
板上:
  python app_preview.py --backend udmabuf --mouse evdev --server http://127.0.0.1:8091
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmi import (App, Canvas, Theme, Button, Frame, make_backend, make_mouse,
                 Board, MockServer, SCREEN_W, SCREEN_H)

BTN_W, BTN_H = 260, 72
PREVIEW_BOX = (24, 96, 1280, 720)
RIGHT_X = 1340


class PreviewApp(App):
    title = "实时预览"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.note = ""
        self.shots = []
        self.outdir = os.environ.get("OFFLINEHMI_OUT", ".")
        self.status = {}
        self._stale = 0

    def on_click(self, key: str):
        try:
            if key == "shot":
                jpeg = self.board.camera_preview_jpeg()
                name = "shot-%s.jpg" % time.strftime("%Y%m%d-%H%M%S")
                path = os.path.join(self.outdir, name)
                open(path, "wb").write(jpeg)
                self.shots.insert(0, name)
                self.shots = self.shots[:5]
                self.note = "已存 %s (%d B)" % (name, len(jpeg))
            elif key == "refresh":
                self.note = ""
        except Exception as e:
            self.note = "失败: %s" % e

    def on_tick(self, state):
        super().on_tick(state)
        try:
            self.status = self.board.camera_status()
        except Exception:
            self.status = {}

    def build(self, c: Canvas, state):
        Frame.header(c, state)
        btns: list[Button] = []
        # 预览窗（每 tick 换一帧 → 直显下就是 1 fps；可改 --pollhz 提到更高）
        c.rect(PREVIEW_BOX, fill=c.theme.colors["well"], outline=c.theme.colors["border"])
        try:
            jpeg = self.board.camera_preview_jpeg()
            c.jpeg_to_rgb(jpeg, PREVIEW_BOX)
            self._stale = 0
        except Exception as e:
            self._stale += 1
            c.text((PREVIEW_BOX[0] + 20, PREVIEW_BOX[1] + 20),
                   "预览不可用(%ds): %s" % (self._stale, str(e)[:70]),
                   fill=c.theme.colors["muted"])
        x, y = RIGHT_X, 96
        b = Button("shot", (x, y, BTN_W, BTN_H), "拍照", "accent"); btns.append(b); y += BTN_H + 16
        b = Button("refresh", (x, y, BTN_W, BTN_H), "清除提示"); btns.append(b); y += BTN_H + 16
        if self.status:
            c.text((x, y), "%sx%s %sfps" % (self.status.get("width", "?"),
                                            self.status.get("height", "?"),
                                            self.status.get("fps", "?")),
                   fill=c.theme.colors["muted"]); y += 34
        for name in self.shots:
            c.text((x, y), name, fill=c.theme.colors["muted"]); y += 30
        if self.note:
            c.text((24, SCREEN_H - 40), self.note, fill=c.theme.colors["notice"])
        return btns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="png:out")
    ap.add_argument("--mouse", default="mock:[[[300,300,0],[900,900,0],[900,900,1]]]")
    ap.add_argument("--server", default=None, help="http://<板IP>:8091")
    ap.add_argument("--font", default=None)
    ap.add_argument("--theme", choices=("dark", "light"), default="dark",
                    help="Color theme (default: dark)")
    ap.add_argument("--frames", type=int, default=None)
    args = ap.parse_args()

    board = Board(args.server) if args.server else MockServer()
    os.environ.setdefault("OFFLINEHMI_OUT", ".")
    app = PreviewApp(make_backend(args.backend), make_mouse(args.mouse), board,
                     theme=Theme(args.font, scheme=args.theme))
    n = app.run(max_frames=args.frames)
    print("presented %d frames" % n)


if __name__ == "__main__":
    main()
