#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kiosk.py -- offlineHMI 的自助终端入口（开机就进、独占全屏、出不去）。

形态（对齐她 2026-11-04 的要求）：
  首页四宫格 → 点进某一页 → 右上角「⌂」回首页
  空闲 N 秒自动回首页（查询机/ATM 的标准行为）
  退出只有一条路：**5 秒内连点右上角 5 次**（管理员手势；`--no-admin-exit` 可关死）
  收到 SIGTERM 时把 0x54 写回复位默认（udmabuf 后端），不留脏地址

配套（板上部署，属 OS 层改动，要登记 os.md）：
  见 README「kiosk 部署」一节 —— systemd 单元 Restart=always，或 rc.local 兜底。

用法：
  校机预演   python kiosk.py --backend png:out --mouse win32 --mock --frames 6
  板上真机   sudo python3 kiosk.py --backend udmabuf --mouse evdev --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmi import (Board, MockServer, Theme, make_backend, make_mouse,
                 SCREEN_W, SCREEN_H)
from hmi.kiosk import KioskShell, SingleInstance
from hmi.asr import MockAsr, RemoteAsr, ArecordSource, SynthSource

from app_preview import PreviewApp
from app_recording import RecordingApp
from app_photos import PhotosApp
from app_voice import VoiceApp


def build_registry(host: str, mock: bool, asr_spec: str, source_spec: str):
    """每页各自要的 board（8091/8093 端口不同），工厂闭包捕获。"""
    def mk_board(port):
        return MockServer() if mock else Board("http://%s:%d" % (host, port))

    def asr_obj():
        return MockAsr() if asr_spec == "mock" else RemoteAsr(asr_spec)

    def source_obj():
        if source_spec == "synth":
            return SynthSource()
        dev = source_spec.split(":", 1)[1] if ":" in source_spec else "hw:0,0"
        return ArecordSource(dev)

    def f_preview(backend, mouse, board, theme):
        return PreviewApp(backend, mouse, mk_board(8091), theme=theme)

    def f_recording(backend, mouse, board, theme):
        return RecordingApp(backend, mouse, mk_board(8093), theme=theme)

    def f_photos(backend, mouse, board, theme):
        return PhotosApp(backend, mouse, mk_board(8091), theme=theme)

    def f_voice(backend, mouse, board, theme):
        return VoiceApp(backend, mouse, None, theme=theme,
                        asr=asr_obj(), source=source_obj())

    return [
        ("preview", "实时预览", f_preview),
        ("recording", "录像", f_recording),
        ("photos", "人脸识别", f_photos),
        ("voice", "语音识别", f_voice),
    ]


def main():
    ap = argparse.ArgumentParser(description="offlineHMI kiosk")
    ap.add_argument("--backend", default="png:out", help="png:<dir> | udmabuf[:off]")
    ap.add_argument("--mouse", default="mock:[]", help="evdev | evdev:<dev> | win32 | mock:<pts>")
    ap.add_argument("--host", default="127.0.0.1", help="板端 REST 主机（8091/8093/18090）")
    ap.add_argument("--mock", action="store_true", help="全部走内置假数据，不连网")
    ap.add_argument("--asr", default="mock", help="mock | http://host:18090")
    ap.add_argument("--source", default="synth", help="synth | arecord[:dev]")
    ap.add_argument("--font", default=None, help="TTF 路径（默认自动找 CJK 字体）")
    ap.add_argument("--idle", type=float, default=120.0, help="空闲归位秒数（0=关）")
    ap.add_argument("--no-admin-exit", action="store_true", help="关掉管理员手势退出")
    ap.add_argument("--no-single-instance", action="store_true", help="跳过单实例锁（调试用）")
    ap.add_argument("--frames", type=int, default=None, help="预演：最多画几帧后退出")
    args = ap.parse_args()

    lock = SingleInstance()
    if not args.no_single_instance and not lock.acquire():
        print("另一个 kiosk 已在运行（%s 被锁）" % lock.path, file=sys.stderr)
        return 3

    backend = make_backend(args.backend)
    mouse = make_mouse(args.mouse)
    shell = KioskShell(backend, mouse, None,
                       build_registry(args.host, args.mock, args.asr, args.source),
                       theme=Theme(args.font),
                       idle_reset_s=(args.idle or None),
                       allow_admin_exit=not args.no_admin_exit)
    try:
        n = shell.run(max_frames=args.frames)
        print("presented %d frames" % n)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())