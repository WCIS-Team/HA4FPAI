#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_voice.py -- webui.py（FunASR 两段式语音识别）的原生 HMI 复现。

原版（repos/HA4FPAI/lang/server/archive/webui.py）是 Gradio 网页，依赖 gradio + 本地
funasr 引擎（asr/engine.py 已删，跑不起来）。本复现改走**板上现役远程 ASR**
（mimo_bridge :18090 的 /api/v1/asr，"只 ASR" 加 ?llm=0），界面直接画进帧缓冲：

  Tab1 录音/上传识别：整段录完（容忍停顿）→ 细切分 → 逐段 ASR → 拼接
  Tab2 流式自动断句：边说边传 → 粗段收口（停顿容忍 2s）即细切 → ASR → 文本追加

两段式设计、dBFS 门限、参数默认值全部对齐原版（见 hmi/asr.py 注释）。

用法（校机/102 预演，无麦克风、无板）:
  python app_voice.py --backend png:out --mouse win32 --asr mock --source synth --frames 8
  python app_voice.py --backend png:out --mouse win32 --asr http://192.168.1.113:18090 --source synth
板上:
  python app_voice.py --backend udmabuf --mouse evdev \
      --asr http://127.0.0.1:18090 --source arecord
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmi import (App, Canvas, Theme, Button, Frame, make_backend, make_mouse,
                 SCREEN_W, SCREEN_H)
from hmi.asr import (RemoteAsr, MockAsr, ArecordSource, SynthSource,
                     StreamWorker, read_wav, to_target_rate, fine_split,
                     iter_frames, TARGET, write_wav)

BTN_W, BTN_H = 300, 72
RIGHT_X = 1400
PREVIEW_BOX = (24, 96, 1280, 720)


def make_asr(spec: str):
    if spec == "mock":
        return MockAsr()
    return RemoteAsr(spec)


def make_source(spec: str):
    if spec == "synth":
        return SynthSource()
    if spec.startswith("arecord"):
        dev = spec.split(":", 1)[1] if ":" in spec else "hw:0,0"
        return ArecordSource(dev)
    raise ValueError("source must be synth | arecord[:dev]")


class VoiceApp(App):
    title = "语音识别"

    def __init__(self, *a, asr=None, source=None, **k):
        super().__init__(*a, **k)
        self.asr = asr
        self.source = source
        self.tab = 1                       # 1=整段识别 2=流式
        self.note = ""
        self.text = ""
        self.rows = []
        self.recording = False
        self._rec_pcm = bytearray()
        self.worker: StreamWorker | None = None
        # 细切分参数（默认值=原版 webui 滑条中值）
        self.fine = dict(open_dbfs=-45.0, close_dbfs=-55.0, sil_stop_ms=600,
                         min_seg_ms=300, max_seg_s=20.0, lead_ms=200)

    # ---- 操作 ----
    def on_click(self, key: str):
        try:
            if key == "tab1":
                self.tab = 1
            elif key == "tab2":
                self.tab = 2
            elif key == "rec_toggle":
                self._toggle_record()
            elif key == "transcribe":
                self._transcribe()
            elif key == "stream_toggle":
                self._stream_toggle()
            elif key == "flush":
                if self.worker:
                    self.worker.flush()
                    self.note = "已收尾"
            elif key == "reset":
                if self.worker:
                    self.worker.stop()
                self.worker = None
                self.text = ""
                self.rows = []
                self.note = "已清空"
        except Exception as e:
            self.note = "失败: %s" % str(e)[:100]

    def _toggle_record(self):
        if not self.recording:
            self._rec_pcm = bytearray()
            self.source.start()
            self.recording = True
            self.note = "录音中…（说几句话，停顿没关系）"
        else:
            # 收尾：把残留读出来
            tail = self.source.read(48000 * 2)     # ≤3 s 尾巴
            self._rec_pcm.extend(tail)
            self.source.stop()
            self.recording = False
            self.note = "录了 %.1fs，点「开始识别」" % (len(self._rec_pcm) / 2.0 / TARGET)

    def _transcribe(self):
        if not self._rec_pcm:
            self.note = "先录音"
            return
        t0 = time.time()
        pcm = to_target_rate(bytes(self._rec_pcm), TARGET)
        pieces = fine_split(pcm, **self.fine)
        texts, rows, total = [], [], 0
        for i, p in enumerate(pieces):
            try:
                tx, ms = self.asr.asr(p)
            except Exception as e:
                self.note = "ASR 失败: %s" % str(e)[:90]
                return
            total += ms
            texts.append(tx or "")
            rows.append("#%d %.1fs %dms %s" % (i + 1, len(p) / 2.0 / TARGET, ms, tx))
        self.text = "".join(t for t in texts if t) or "（没有切出语音段——全静音？）"
        self.rows = rows
        self.note = "细切 %d 段 / 输入 %.1fs / 处理 %.1fs / ASR 合计 %dms" % (
            len(pieces), len(pcm) / 2.0 / TARGET, time.time() - t0, total)

    def _stream_toggle(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.worker.flush()
            self.worker = None
            self.note = "流式已停"
        else:
            self.worker = StreamWorker(self.source, self.asr,
                                       turn_sil_ms=2000, fine=self.fine)
            self.worker.start()
            self.note = "流式识别中…（停顿 2s 算一轮）"

    def on_tick(self, state):
        super().on_tick(state)
        if self.recording:
            self._rec_pcm.extend(self.source.read(3200))   # ≈100 ms/tick
        if self.worker:
            self.worker.join(timeout=0.01)

    # ---- 界面 ----
    def build(self, c: Canvas, state):
        Frame.header(c, state)
        btns: list[Button] = []
        # Tab 切换
        b = Button("tab1", (24, 76, 200, 48), "整段识别",
                   "accent" if self.tab == 1 else "normal"); btns.append(b)
        b = Button("tab2", (240, 76, 200, 48), "流式断句",
                   "accent" if self.tab == 2 else "normal"); btns.append(b)

        # 文本区
        c.rect((24, 140, 1280, 620), fill=c.theme.colors["well"],
               outline=c.theme.colors["border"])
        if self.tab == 1:
            src = self.text or ("（录完点开始识别）" if not self.recording
                                else "录音中 %.1fs…" % (len(self._rec_pcm) / 2.0 / TARGET))
        else:
            if self.worker:
                src = "\n".join("[%d] %s" % (i + 1, t)
                                for i, t in enumerate(self.worker.texts)) or "（说几句话试试）"
            else:
                src = "（点「开始流式」后一直说，停顿 2s 算一轮）"
        y = 160
        for line in str(src).split("\n"):
            c.text((40, y), line[:72]); y += 36
            if y > 720:
                break
        # 明细行（tab1）
        if self.tab == 1:
            y = 780
            for r in self.rows[-8:]:
                c.text((40, y), r[:100], fill=c.theme.colors["muted"]); y += 28

        # 右侧按钮列
        x = RIGHT_X
        y = 140
        if self.tab == 1:
            lab = "停止录音" if self.recording else "开始录音"
            b = Button("rec_toggle", (x, y, BTN_W, BTN_H), lab,
                       "danger" if self.recording else "normal"); btns.append(b); y += BTN_H + 16
            b = Button("transcribe", (x, y, BTN_W, BTN_H), "开始识别", "accent")
            b.enabled = not self.recording and bool(self._rec_pcm)
            btns.append(b); y += BTN_H + 16
        else:
            live = self.worker and self.worker.is_alive()
            b = Button("stream_toggle", (x, y, BTN_W, BTN_H),
                       "停止流式" if live else "开始流式",
                       "danger" if live else "accent"); btns.append(b); y += BTN_H + 16
            b = Button("flush", (x, y, BTN_W, BTN_H), "收尾识别"); btns.append(b); y += BTN_H + 16
        b = Button("reset", (x, y, BTN_W, BTN_H), "清空"); btns.append(b); y += BTN_H + 16

        if self.note:
            c.text((24, SCREEN_H - 40), self.note[:110], fill=c.theme.colors["notice"])
        if self.worker and self.worker.error:
            c.text((24, SCREEN_H - 76), self.worker.error[:110], fill=c.theme.colors["error"])
        return btns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="png:out")
    ap.add_argument("--mouse", default="mock:[[300,300,0],[1500,200,1],[1500,300,1]]")
    ap.add_argument("--asr", default="mock", help="mock | http://<板IP>:18090")
    ap.add_argument("--source", default="synth", help="synth | arecord[:device]")
    ap.add_argument("--font", default=None)
    ap.add_argument("--theme", choices=("dark", "light"), default="dark",
                    help="Color theme (default: dark)")
    ap.add_argument("--frames", type=int, default=None)
    args = ap.parse_args()

    app = VoiceApp(make_backend(args.backend), make_mouse(args.mouse), None,
                   asr=make_asr(args.asr), source=make_source(args.source),
                   theme=Theme(args.font, scheme=args.theme))
    n = app.run(max_frames=args.frames)
    print("presented %d frames" % n)


if __name__ == "__main__":
    main()
