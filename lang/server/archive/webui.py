#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FunASR Web UI (Gradio) —— 家居场景两段式: 粗采集(容忍停顿) → 送 ASR 前再细切分一次。

为什么两段式: 家居里人说话常带"想了想"的停顿, 分段器若按 600ms 静音收口会把一句话切散。
做法是把"停顿容忍"和"切分"拆开:
  粗段(收口 = 停顿容忍, 默认 2s)  → 决定"这一轮说到哪算完", 宁大勿小, 保整句;
  细段(收口 = 600ms 正常 VAD 口径) → 只在粗段内部切, 决定"给 ASR 的粒度", 逐段识别再拼接。

两种模式:
  A 录音/上传识别   整段录完(浏览器不掐停顿) → 细切分 → 逐段 ASR
  B 流式自动断句    边说边传 → 粗分段只认"停顿容忍"收口(=整轮) → 收口即细切 → ASR → 文本实时追加

识别走共享本地引擎(懒加载, 与 funasr-server 同一套 be(pcm16)->(text,ms) 合同)。
启动: funasr-webui.bat   (默认 http://127.0.0.1:7860, --no-browser 适合后台/调试)
"""

import io
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

if __package__ in (None, ""):          # 直接运行: python asr\webui.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from asr import engine as engine_mod
    from asr.wavio import read_wav_pcm16, to_target_rate, pcm_to_wav_bytes, peak_dbfs
else:
    from . import engine as engine_mod
    from .wavio import read_wav_pcm16, to_target_rate, pcm_to_wav_bytes, peak_dbfs

from asr_chain import RmsSegmenter, iter_frames        # noqa: E402  与链路同一套分段器

TARGET = 16000


# ---------------------------------------------------------------- 引擎(共享懒加载)

class SharedEngine(object):
    def __init__(self, preset, device="cpu"):
        self.preset, self.device = preset, device
        self._eng = None
        self._lock = threading.Lock()
        self.load_s = None

    def _get(self):
        if self._eng is None:
            t0 = time.time()
            self._eng = engine_mod.build("funasr-" + self.preset if self.preset in
                                         ("sensevoice", "paraformer") else self.preset,
                                         device=self.device)
            self.load_s = round(time.time() - t0, 1)
            print("[webui] 引擎就绪: %s (%.1fs)" % (self.preset, self.load_s))
        return self._eng

    def asr(self, pcm16):
        """串行化识别(AutoModel 非并发安全)。返回 (text, ms)。"""
        with self._lock:
            eng = self._get()
            t0 = time.time()
            text, ms = eng(pcm16)
            return text, int((time.time() - t0) * 1000)


# ---------------------------------------------------------------- 音频工具

def np_to_pcm16(data, sr):
    """浏览器/上传来的浮点(或整型)numpy → 单声道 pcm16 bytes。"""
    arr = np.asarray(data)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    if arr.dtype.kind in "iu":
        arr = arr.astype("float32") / float(np.iinfo(arr.dtype).max if arr.dtype.kind == "i" else 65535)
    arr = np.clip(arr.astype("float32"), -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def load_file_pcm16(path):
    """上传/录音的 wav 文件(任意位宽)→ 16k 单声道 pcm16。"""
    try:
        import soundfile as sf
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        pcm = np_to_pcm16(data, sr)
        return to_target_rate(pcm, int(sr))
    except Exception:
        pcm, sr, _ch = read_wav_pcm16(path)
        return to_target_rate(pcm, sr)


def chunk_to_pcm16k(chunk):
    """流式 chunk (sr, np) → 16k pcm16。"""
    sr, data = chunk
    pcm = np_to_pcm16(data, sr)
    return to_target_rate(pcm, int(sr))


# ---------------------------------------------------------------- 两段式流水线

def fine_split(pcm16k, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms,
               frame_ms=100):
    """第二段(细切): 在粗段内部按正常 VAD 口径切段, 返回 pcm16 段列表。"""
    segger = RmsSegmenter(frame_ms=frame_ms, open_dbfs=open_dbfs, close_dbfs=close_dbfs,
                          sil_stop_ms=sil_stop_ms, min_seg_ms=min_seg_ms,
                          max_seg_s=max_seg_s, lead_ms=lead_ms)
    pieces = []
    for chunk, dbfs in iter_frames(pcm16k, frame_ms, TARGET):
        piece = segger.feed(chunk, dbfs)
        if piece is not None:
            pieces.append(piece)
    piece = segger.flush()
    if piece is not None:
        pieces.append(piece)
    return pieces or ([pcm16k] if pcm16k else [])


def asr_pieces(pieces, eng):
    """逐段识别 → (拼接文本, 明细行)。"""
    rows, texts, total_ms = [], [], 0
    for i, piece in enumerate(pieces):
        dur = len(piece) / 2.0 / TARGET
        text, ms = eng.asr(piece)
        total_ms += ms
        texts.append(text or "")
        rows.append("| %d | %.2f | %.1f | %d | %.2f | %s |"
                    % (i + 1, dur, peak_dbfs(piece), ms, ms / 1000.0 / dur if dur else 0, text or ""))
    return "\n".join(t for t in texts if t), rows, total_ms


def stats_md(rows, total_ms, load_s, note=""):
    head = ("| # | 时长s | 峰值dBFS | asr_ms | rtf | text |\n|---|---|---|---|---|---|\n")
    extra = "\n\n引擎加载 %.1fs, 识别合计 %d ms%s" % (load_s or 0, total_ms, ("，" + note) if note else "")
    return head + "\n".join(rows) + extra if rows else "（没有切出语音段——全静音？调低 open/close 门限试试）" + extra


# ---------------------------------------------------------------- 模式 A: 录音/上传

def ui_transcribe(audio_path, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms):
    if not audio_path:
        return "（先录音或上传 wav）", ""
    t0 = time.time()
    pcm = load_file_pcm16(audio_path)
    pieces = fine_split(pcm, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms)
    text, rows, total_ms = asr_pieces(pieces, ENG)
    note = "（细切 %d 段 / 总输入 %.1fs / 处理 %.1fs）" % (len(pieces), len(pcm) / 2.0 / TARGET, time.time() - t0)
    return text, stats_md(rows, total_ms, ENG.load_s, note)


# ---------------------------------------------------------------- 模式 B: 流式自动断句

class StreamState(object):
    """一轮 = 粗分段器的一个收口段;texts 存本次会话已识别的整轮文本。"""
    def __init__(self, turn_sil_ms, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms):
        self.turn = RmsSegmenter(frame_ms=100, open_dbfs=open_dbfs, close_dbfs=close_dbfs,
                                 sil_stop_ms=turn_sil_ms, min_seg_ms=min_seg_ms,
                                 max_seg_s=max_seg_s, lead_ms=lead_ms)
        self.texts = []
        self.turns = 0
        self.fine = dict(open_dbfs=open_dbfs, close_dbfs=close_dbfs,
                         sil_stop_ms=sil_stop_ms, min_seg_ms=min_seg_ms,
                         max_seg_s=max_seg_s, lead_ms=lead_ms)


def _asr_turn(pcm_turn, st):
    pieces = fine_split(pcm_turn, st.fine["open_dbfs"], st.fine["close_dbfs"],
                        st.fine["sil_stop_ms"], st.fine["min_seg_ms"],
                        st.fine["max_seg_s"], st.fine["lead_ms"])
    text, rows, total_ms = asr_pieces(pieces, ENG)
    st.turns += 1
    return text, rows, total_ms


def ui_stream(chunk, st, turn_sil_ms, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms):
    if st is None:
        st = StreamState(turn_sil_ms, open_dbfs, close_dbfs, sil_stop_ms, min_seg_ms, max_seg_s, lead_ms)
    st.fine.update(dict(open_dbfs=open_dbfs, close_dbfs=close_dbfs, sil_stop_ms=sil_stop_ms,
                        min_seg_ms=min_seg_ms, max_seg_s=max_seg_s, lead_ms=lead_ms))
    if chunk is not None:
        pcm = chunk_to_pcm16k(chunk)
        for frame, dbfs in iter_frames(pcm, 100, TARGET):
            piece = st.turn.feed(frame, dbfs)
            if piece is not None:                     # 一轮收口(停顿容忍到点) → 立刻识别
                text, rows, ms = _asr_turn(piece, st)
                if text:
                    st.texts.append(text)
    full = ("\n".join("[%d] %s" % (i + 1, t) for i, t in enumerate(st.texts))
            if st.texts else "（说几句话试试——停顿 %.1f 秒算一轮说完）" % (turn_sil_ms / 1000.0))
    return full, st


def ui_stream_flush(st):
    """点停止后把没等到停顿的尾巴也识别掉。"""
    if st is None:
        return "（本轮没有待收尾的输入）", st
    piece = st.turn.flush()
    if piece is not None:
        text, rows, ms = _asr_turn(piece, st)
        if text:
            st.texts.append(text)
    full = "\n".join("[%d] %s" % (i + 1, t) for i, t in enumerate(st.texts)) if st.texts else "（没有内容）"
    return full, st


def ui_stream_reset(_st):
    return "（已清空，重新说话）", None


# ---------------------------------------------------------------- 界面

def build_ui():
    import gradio as gr

    with gr.Blocks(title="FunASR Web UI") as demo:
        gr.Markdown(
            "## FunASR 家居语音识别（两段式：整轮采集 → ASR 前细切分）\n"
            "**粗段**只管“这轮说到哪算完”（停顿容忍，默认 2s，宁大勿小保整句）；"
            "**细段**只在粗段内部按正常 VAD 口径切（默认 600ms），逐段识别再拼。"
            "同一套引擎/分段器与命令行链路（asr_chain.py）完全一致。")
        with gr.Tab("录音 / 上传识别"):
            with gr.Row():
                with gr.Column():
                    audio_in = gr.Audio(sources=["microphone", "upload"], type="filepath",
                                        label="说一句话（可带停顿，录完自动识别）")
                    go = gr.Button("开始识别", variant="primary")
                with gr.Column():
                    txt = gr.Textbox(label="识别文本", lines=4)
                    det = gr.Markdown(label="分段明细")
            with gr.Accordion("细切分参数（ASR 前的分段）", open=False):
                with gr.Row():
                    p_sil = gr.Slider(200, 2000, 600, 100, label="静音收口 sil-stop (ms)")
                    p_min = gr.Slider(100, 1000, 300, 100, label="最短段 min-seg (ms)")
                    p_max = gr.Slider(5, 60, 20, 1, label="最长段 max-seg (s)")
                with gr.Row():
                    p_open = gr.Slider(-70, -20, -45, 1, label="开口门限 open (dBFS)")
                    p_close = gr.Slider(-80, -30, -55, 1, label="收口门限 close (dBFS)")
                    p_lead = gr.Slider(0, 500, 200, 50, label="开口预卷 lead (ms)——防吃轻声字头")
            go.click(ui_transcribe, [audio_in, p_open, p_close, p_sil, p_min, p_max, p_lead],
                     [txt, det])
        with gr.Tab("流式（自动断句）"):
            with gr.Row():
                with gr.Column():
                    mic = gr.Audio(sources=["microphone"], streaming=True, label="点开始后一直说")
                    t_sil = gr.Slider(500, 5000, 2000, 100, label="停顿容忍 turn-sil (ms)——说完停这么久才算一轮")
                    flush = gr.Button("收尾识别（停止录音后点）")
                    reset = gr.Button("清空")
                with gr.Column():
                    st_txt = gr.Textbox(label="识别文本（按轮编号）", lines=12)
                    state = gr.State(None)
            mic.stream(ui_stream, [mic, state, t_sil, p_open, p_close, p_sil, p_min, p_max, p_lead],
                       [st_txt, state])
            flush.click(ui_stream_flush, [state], [st_txt, state])
            reset.click(ui_stream_reset, [state], [st_txt, state])
    return demo


def main():
    import argparse
    import uvicorn  # noqa: F401  确保依赖齐

    ap = argparse.ArgumentParser(description="FunASR Web UI (Gradio)")
    ap.add_argument("--host", default=os.environ.get("FUNASR_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FUNASR_PORT", 7860)))
    ap.add_argument("--preset", default=None, help="sensevoice|paraformer|profile 名")
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    args = ap.parse_args()

    cfg = engine_mod.load_profiles() or {}
    srv = cfg.get("server") or {}
    preset = args.preset or srv.get("preset", "sensevoice")
    device = args.device or cfg.get("device", "cpu")

    global ENG
    ENG = SharedEngine(preset, device)
    print("[webui] preset=%s device=%s, 首次识别时懒加载引擎" % (preset, device))

    demo = build_ui()
    demo.queue().launch(server_name=args.host, server_port=args.port,
                        inbrowser=not args.no_browser, show_error=True)


if __name__ == "__main__":
    main()
