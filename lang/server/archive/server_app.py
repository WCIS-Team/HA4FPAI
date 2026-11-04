# -*- coding: utf-8 -*-
"""FunASR Windows 常驻服务 —— 一个共享本地引擎, 双协议:

  POST /v1/audio/transcriptions   OpenAI 兼容 (multipart: file [+ model])
                                  → {"text": "..."}
  POST /api/v1/asr                FunASR runtime 兼容 (multipart: audio [+ 附加字段])
                                  → {"code":0, "text": "...", "asr_ms": n}
                                  板上链路默认就是打这个口 (ASR_REMOTE_PROTO=funasr)
  GET  /health                    健康检查(板上 remote_asr.health() 探的就是它)
  GET  /v1/models                 模型列表

约定对齐 funasrarm: code 非 0 视为失败; 文本剥 <|...|> 标签; WAV 16k 单声道进。
host/port/preset 从 config.yaml [server] 读, 环境变量 FUNASR_HOST/PORT/PRESET 可盖。
"""

import io
import sys
import threading
import time
import wave

from pathlib import Path

if __package__ in (None, ""):          # 直接运行: python asr\server_app.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from asr import engine as engine_mod
    from asr.wavio import read_wav_pcm16, to_target_rate
else:                                  # 包内: python -m asr.server_app
    from . import engine as engine_mod
    from .wavio import read_wav_pcm16, to_target_rate

TARGET_RATE = 16000

from fastapi import FastAPI, File, Form, HTTPException, UploadFile          # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                          # noqa: E402
from fastapi.responses import JSONResponse                                  # noqa: E402


def _payload_to_pcm16(raw: bytes, rate_hint=None):
    """上传体 → (16k mono pcm16, 原始采样率)。吃完整 wav; 解析不了就当裸 pcm16@16k。"""
    try:
        pcm, rate, _ch = read_wav_pcm16(raw)
    except Exception:
        if rate_hint:
            rate = int(rate_hint)
        else:
            rate = TARGET_RATE
        pcm = raw
    return to_target_rate(pcm, rate), rate


def create_app(preset=None, device="cpu", cors_origins=None):
    import os
    preset = preset or os.environ.get("FUNASR_PRESET") or "sensevoice"
    app = FastAPI(title="FunASR Server (win)", version=engine_mod.load_profiles().get("version", "1"))
    app.state.engine = None
    app.state.lock = threading.Lock()

    if cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(cors_origins),
            allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    def get_engine():
        with app.state.lock:
            if app.state.engine is None:
                t0 = time.time()
                app.state.engine = engine_mod.build(preset, device=device)
                print("[server] 引擎就绪: %s (%.1fs)" % (preset, time.time() - t0))
            return app.state.engine

    @app.get("/health")
    def health():
        return {"status": "ok", "model_ready": app.state.engine is not None,
                "preset": preset, "device": device}

    @app.get("/v1/models")
    def models():
        eng = app.state.engine
        return {"data": [{"id": preset, "object": "model",
                          "loaded": eng is not None,
                          "profile": getattr(eng, "profile_name", None)}]}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(file: UploadFile = File(...), model: str = Form(default=None)):
        raw = await file.read()
        pcm, _rate = _payload_to_pcm16(raw)
        if not pcm:
            raise HTTPException(400, "空音频")
        eng = get_engine()
        t0 = time.time()
        text, _ms = eng(pcm)
        return {"text": text}

    @app.post("/api/v1/asr")
    async def asr_runtime(audio: UploadFile = File(...), language: str = Form(default=None)):
        raw = await audio.read()
        pcm, rate = _payload_to_pcm16(raw, rate_hint=None)
        if not pcm:
            return JSONResponse({"code": 1, "msg": "空音频"})
        try:
            eng = get_engine()
            if language:
                eng.language = language
            t0 = time.time()
            text, ms = eng(pcm, rate)
        except Exception as exc:
            return JSONResponse({"code": 2, "msg": repr(exc)[:200]})
        return JSONResponse({"code": 0, "text": text, "asr_ms": int((time.time() - t0) * 1000)})

    return app


def main():
    import argparse
    import os

    import uvicorn

    ap = argparse.ArgumentParser(description="FunASR 常驻服务(双协议: openai + funasr)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--preset", default=None, help="sensevoice|paraformer|config.yaml 里的 profile 名")
    ap.add_argument("--device", default=None)
    ap.add_argument("--cors-origin", action="append", default=None)
    args = ap.parse_args()

    cfg = engine_mod.load_profiles() or {}
    srv = cfg.get("server") or {}
    host = args.host or os.environ.get("FUNASR_HOST") or srv.get("host", "127.0.0.1")
    port = args.port or int(os.environ.get("FUNASR_PORT", 0)) or int(srv.get("port", 8000))
    device = args.device or cfg.get("device", "cpu")
    cors = args.cors_origin or srv.get("cors_origins") or []

    app = create_app(preset=args.preset or srv.get("preset", "sensevoice"),
                     device=device, cors_origins=cors)
    print("FunASR server: http://%s:%d  (docs: /docs)" % (host, port))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
