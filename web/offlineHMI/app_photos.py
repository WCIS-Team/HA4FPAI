#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_photos.py -- 8091 人脸识别页的原生 HMI 复现。

功能对齐 serve_photos 页面：相机识别（recognize-camera 空体）、上传识别
（recognize，JPEG 直传）、录入（enroll，X-Person-Name）、图库浏览（gallery）。
识别结果里若带画好框的 JPEG 则直接贴显示（后端已画框，前端零绘制）。

用法:
  python app_photos.py --backend png:out --mouse mock:[[[300,300,0],[300,900,1]]] --mock
  python app_photos.py --backend png:out --mouse win32 --server http://192.168.1.113:8091
板上:
  python app_photos.py --backend udmabuf --mouse evdev --server http://127.0.0.1:8091
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


class PhotosApp(App):
    title = "人脸识别"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.note = ""
        self.result_jpeg = None          # 后端画好框的 JPEG（若有）
        self.result_text = []
        self.person_input = "person1"    # 录入名单（简化：轮换 personN）
        self.person_seq = 1
        self.gallery_ids = []

    def on_click(self, key: str):
        try:
            if key == "recognize":
                r = self.board.recognize_camera()
                self._absorb(r, "相机识别")
            elif key == "enroll":
                r = self.board.enroll_bytes(self.board.camera_preview_jpeg(),
                                            self.person_input)
                self._absorb(r, "录入 %s" % self.person_input)
                self.person_seq += 1
                self.person_input = "person%d" % self.person_seq
            elif key == "gallery":
                g = self.board.gallery()
                self.gallery_ids = [p.get("id") for p in g.get("photos", [])][:8]
                self.note = "图库 %d 项" % len(self.gallery_ids)
            elif key.startswith("gal:"):
                jpeg = self.board.gallery_media(key[4:])
                self.result_jpeg = jpeg
                self.result_text = ["图库照片 " + key[4:]]
        except Exception as e:
            self.note = "失败: %s" % e

    def _absorb(self, r: dict, what: str):
        self.result_text = []
        if isinstance(r, dict):
            for k in ("status", "elapsed_ms", "faces", "identity", "name", "error"):
                if k in r:
                    self.result_text.append("%s: %s" % (k, r[k]))
            img = r.get("image") or r.get("jpeg") or r.get("annotated")
            if isinstance(img, (bytes, bytearray)):
                self.result_jpeg = bytes(img)
            elif isinstance(r.get("data"), (bytes, bytearray)):
                self.result_jpeg = bytes(r["data"])
        self.note = what + " 完成"

    def build(self, c: Canvas, state):
        Frame.header(c, state)
        btns: list[Button] = []
        box = PREVIEW_BOX
        c.rect(box, fill=c.theme.colors["well"], outline=c.theme.colors["border"])
        if self.result_jpeg:
            try:
                c.jpeg_to_rgb(self.result_jpeg, box)
            except Exception:
                c.text((box[0] + 20, box[1] + 20), "结果图不可显示", fill=c.theme.colors["muted"])
        else:
            c.text((box[0] + 20, box[1] + 20), "点击右侧操作开始", fill=c.theme.colors["muted"])
        x, y = RIGHT_X, 96
        b = Button("recognize", (x, y, BTN_W, BTN_H), "相机识别", "accent"); btns.append(b); y += BTN_H + 16
        b = Button("enroll", (x, y, BTN_W, BTN_H), "录入:" + self.person_input); btns.append(b); y += BTN_H + 16
        b = Button("gallery", (x, y, BTN_W, BTN_H), "图库"); btns.append(b); y += BTN_H + 16
        for t in self.result_text[:8]:
            c.text((x, y), t[:40], fill=c.theme.colors["muted"]); y += 30
        for pid in self.gallery_ids:
            c.text((x, y), pid, fill=c.theme.colors["muted"]); y += 28
        if self.note:
            c.text((24, SCREEN_H - 40), self.note, fill=c.theme.colors["notice"])
        return btns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="png:out")
    ap.add_argument("--mouse", default="mock:[[[300,300,0],[300,900,0],[300,900,1]]]")
    ap.add_argument("--server", default=None, help="http://<板IP>:8091")
    ap.add_argument("--font", default=None)
    ap.add_argument("--theme", choices=("dark", "light"), default="dark",
                    help="Color theme (default: dark)")
    ap.add_argument("--frames", type=int, default=None)
    args = ap.parse_args()

    board = Board(args.server) if args.server else MockServer()
    app = PhotosApp(make_backend(args.backend), make_mouse(args.mouse), board,
                    theme=Theme(args.font, scheme=args.theme))
    n = app.run(max_frames=args.frames)
    print("presented %d frames" % n)


if __name__ == "__main__":
    main()
