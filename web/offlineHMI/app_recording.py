#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_recording.py -- 8093 录像页的原生 HMI 复现。

功能对齐 recording_panel（8093）：
  实时预览(单帧) · 开始/停止录像 · 录像列表 · 回放(at=秒) · 下载 · 删除/恢复 · 存储用量

用法（校机 / 102，无板也能跑）:
  python app_recording.py --backend png:out --mouse mock:[[200,900,1],[1600,900,1]] --mock
  python app_recording.py --backend png:out --mouse win32 --server http://192.168.1.113:8093
板上（最后一步）:
  python app_recording.py --backend udmabuf --mouse evdev --server http://127.0.0.1:8093
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmi import (App, Canvas, Theme, Button, Frame, make_backend, make_mouse,
                 Board, MockServer, SCREEN_W, SCREEN_H)
from hmi.ui import PANEL, MUTED

BTN_W, BTN_H = 260, 72
TOP = 96
PREVIEW_BOX = (24, TOP, 1280, 720)          # x,y,w,h
RIGHT_X = 1340
LIST_TOP = TOP


class RecordingApp(App):
    title = "录像"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.mode = "off"                    # off | rec
        self.recs = []
        self.sel = None                      # 选中的录像 id
        self.play_at = 0.0
        self.trash_view = False
        self.note = ""
        self.storage = {}
        self._refresh_list()

    # ---- 数据 ----
    def _refresh_list(self):
        try:
            data = self.board.recordings(trash=self.trash_view)
            self.recs = data.get("recordings", [])
        except Exception as e:
            self.note = "列表失败: %s" % e
            self.recs = []
        try:
            self.storage = self.board.storage() or {}
        except Exception:
            self.storage = {}

    def on_click(self, key: str):
        try:
            if key == "toggle":
                self.mode = self.board.recorder_mode("rec" if self.mode != "rec" else "off").get("mode", self.mode)
                self.note = ""
            elif key == "refresh":
                self._refresh_list()
            elif key == "trash_view":
                self.trash_view = not self.trash_view
                self._refresh_list()
            elif key.startswith("sel:"):
                self.sel = key[4:]
                self.play_at = 0.0
            elif key == "play_back":
                self.play_at = max(0.0, self.play_at - 5)
            elif key == "play_fwd":
                self.play_at += 5
            elif key == "download":
                if self.sel:
                    data = self.board.recording_download(self.sel, "mjpeg")
                    out = os.path.join(os.environ.get("OFFLINEHMI_OUT", "."), self.sel + ".mjpeg")
                    open(out, "wb").write(data)
                    self.note = "已下载 %d B -> %s" % (len(data), out)
            elif key == "del":
                if self.sel:
                    self.board.trash(self.sel)
                    self.note = "已移入回收站: " + self.sel
                    self._refresh_list()
            elif key == "restore":
                if self.sel:
                    self.board.restore(self.sel)
                    self.note = "已恢复: " + self.sel
                    self._refresh_list()
            elif key == "purge":
                if self.sel:
                    self.board.purge(self.sel)
                    self.note = "已彻底删除: " + self.sel
                    self._refresh_list()
        except Exception as e:
            self.note = "操作失败: %s" % e

    def on_tick(self, state):
        super().on_tick(state)
        if self.mode == "rec":
            try:
                st = self.board.recorder_status()
                self.note = "录制中: %s" % st
            except Exception:
                pass

    # ---- 界面 ----
    def build(self, c: Canvas, state):
        Frame.header(c, state)
        btns: list[Button] = []
        y = TOP

        # 左：预览窗
        c.rect(PREVIEW_BOX, fill=(8, 12, 16), outline=(60, 70, 80))
        try:
            jpeg = self.board.camera_preview_jpeg()
            c.jpeg_to_rgb(jpeg, PREVIEW_BOX)
        except Exception as e:
            c.text((PREVIEW_BOX[0] + 20, PREVIEW_BOX[1] + 20), "预览不可用: %s" % str(e)[:80],
                   fill=(150, 150, 150))

        # 右：控制列
        x = RIGHT_X
        label = "停止录制" if self.mode == "rec" else "开始录制"
        kind = "danger" if self.mode == "rec" else "accent"
        b = Button("toggle", (x, y, BTN_W, BTN_H), label, kind); btns.append(b); y += BTN_H + 16
        for key, lab in (("refresh", "刷新列表"), ("trash_view", "回收站" if not self.trash_view else "返回列表")):
            b = Button(key, (x, y, BTN_W, BTN_H), lab); btns.append(b); y += BTN_H + 16
        for key, lab in (("play_back", "回退5s"), ("play_fwd", "前进5s"),
                         ("download", "下载选中"), ("del", "删除"), ("restore", "恢复"), ("purge", "彻底删除")):
            b = Button(key, (x, y, BTN_W, BTN_H), lab); btns.append(b); y += BTN_H + 16
        y += 8
        if self.storage:
            c.text((x, y), "存储: %s" % self.storage, fill=(150, 158, 166)); y += 34

        # 下方：录像列表
        ly = TOP + PREVIEW_BOX[3] + 24
        c.text((24, ly), ("回收站" if self.trash_view else "录像") + "（点击选中）",
               fill=(150, 158, 166)); ly += 34
        for i, r in enumerate(self.recs[:8]):
            rid = r.get("id", "?")
            box = (24, ly, 1280, 52)
            hot = self.sel == rid
            c.rect(box, fill=(36, 50, 64) if hot else PANEL,
                   outline=(255, 255, 255) if hot else None, width=2)
            c.text((36, ly + 26), "%s   %ss  %s帧" % (rid, r.get("duration", "?"), r.get("frames", "?")),
                   anchor="lm")
            btns.append(Button("sel:" + rid, box, ""))
            ly += 60
        if self.note:
            c.text((24, SCREEN_H - 40), self.note, fill=(240, 200, 120))
        return btns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="png:out", help="png:<dir> | udmabuf[:off]")
    ap.add_argument("--mouse", default="mock:[[[300,200,0],[300,900,0],[300,900,1],[300,300,0]]]")
    ap.add_argument("--server", default=None, help="http://<板IP>:8093；不给则用内置 mock")
    ap.add_argument("--font", default=None, help="TTF 路径（中文）")
    ap.add_argument("--frames", type=int, default=None, help="最多重画几帧退出（预演/CI 用）")
    args = ap.parse_args()

    board = Board(args.server) if args.server else MockServer()
    app = RecordingApp(make_backend(args.backend), make_mouse(args.mouse), board,
                       theme=Theme(args.font))
    n = app.run(max_frames=args.frames)
    print("presented %d frames" % n)


if __name__ == "__main__":
    main()
