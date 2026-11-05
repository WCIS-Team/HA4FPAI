#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backends —— 档位注册表（组件可替换的落点）。

加一个后端＝三步：
  1. 在对应模块里写一个 Stage 子类，实现与该级契约一致的 run()（契约见 core.py 头注）；
  2. 在下面 REGISTRY 的那一级登记一个名字；
  3. lang/config/server.yaml 的 chain 节把 active 档指过去（<stage>_profiles.<名字> 给参数）。

共用 env：需要 onnxruntime 的档拿 shared.ort() / shared.ort_opts()（进程内一份）；
NPU 槽位、独立 conda env 的 piper、ekho 这类**独占资源**不许进本进程，走 HTTP 档。
"""

from backends.asr import Echo as AsrEcho, RemoteApi, SherpaAsr
from backends.front import (KwsPassthrough, KwsSherpa, SarAuto, SarDecimate3,
                            SarLinear, VadRms, VadSherpa, VadSilero, VadWebrtc)
from backends.llm import Echo as LlmEcho, NpuServ, OpenAI
from backends.kws_route import KwsRoute
from backends.rms import RmsEnergy
from backends.router import EchoRouter, KlarRouter
from backends.spk import SpkSherpa
from backends.text import Punct, TtsSplit, Window
from backends.tts import EdgeTts, Http as TtsHttp, SherpaTts, Sine as TtsSine

REGISTRY = {
    "sar": {"auto": SarAuto, "linear": SarLinear, "decimate3": SarDecimate3},
    "vad": {"sherpa": VadSherpa,               # 板端现役档（sherpa_onnx.Vad）
            "webrtc": VadWebrtc,               # 复用 lang/vad/webrtc（带板上 .so）
            "silero": VadSilero,               # 复用 lang/vad/models/silero_vad_v5.onnx（raw-ORT）
            "rms": VadRms},                    # 复用 lang/vad/rms.py（纯标准库）
    "kws": {"passthrough": KwsPassthrough,     # 留空占位（她定 2026-11-04）
            "sherpa": KwsSherpa},              # 复用 lang/kws/models（int8 副本本机在盘）
    "kws_route": {"default": KwsRoute},        # KWS 之后的路由（实现写在 lang/kws/router.py）
    "rms": {"energy": RmsEnergy},              # sar 之后的能量门/电平（KWS 之前）
    "asr": {"sherpa": SherpaAsr,               # 本地流式 zipformer（板路径）
            "remote-api": RemoteApi,           # **外部 ASR API**（multipart/openai/json）
            "echo": AsrEcho},
    "router": {"klar": KlarRouter,             # ASR 后交给 klar 的 NLP 定去向（llm/fixed/verify）
               "echo": EchoRouter},            # 自检档：恒 llm
    "spk": {"sherpa": SpkSherpa},              # 声纹（CAM++）：**默认不入链**，被路由点名才装载
                                               #   ——闲置 idle_unload_s 秒自动卸载；参数在 vertify.yaml
    "llm": {"openai": OpenAI,
            "npuserv": NpuServ,                # 板上 NPU 槽位（留空，不接板端推理）
            "echo": LlmEcho},
    "tokenizer": {"tts_split": TtsSplit,       # 默认档（她 2026-11-04 定）：标点优先＋严格字数上限
                  "punct": Punct,              # 原来的标点档（行为未改，块长会超上限）
                  "window": Window},           # 固定字数硬切（不认标点，压峰值最狠）
    "tts": {"sherpa": SherpaTts,               # 本地 matcha/vits（板路径）
            "http": TtsHttp,                   # **外部 TTS 服务**（piper/ekho/json）
            "edge": EdgeTts,                   # Edge 在线中文 TTS（lang/tts/models/edge）
            "sine": TtsSine},
}
