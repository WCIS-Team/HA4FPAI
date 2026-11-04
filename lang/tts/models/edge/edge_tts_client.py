#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_tts_client.py —— 调 Microsoft Edge「大声朗读」在线语音（中文）→ wav。

链路里的用途：`lang/server` 的 `chain.tts: edge` 档调这里，把文本变成单声道 PCM。

## 为什么不能只靠 edge-tts 自己
`edge-tts` 从微软端点拿到的音频格式**固定是 mp3**（`audio-24khz-48kbitrate-mono-mp3`），
不是 PCM/wav。而本服务的 TTS 契约要的是**采样**（`run(chunk) -> (samples, rate)`），所以必须
把 mp3 解成 PCM。

## mp3 解码：可插拔，优先挑小的（边缘设备不要为了转码背 200 MB 的 ffmpeg）
`decoder` 顺序（auto 时）：**miniaudio → soundfile → mpg123 → ffmpeg**，谁在就用谁。
  miniaudio  pip 轮子 ~1 MB，自带 minimp3（单头 C），无系统依赖 —— 边缘首选
  soundfile  pip 轮子 ~1 MB，libsndfile≥1.1 支持 mp3
  mpg123     apt 系统包 ~0.1–0.2 MB（libmpg123 是很小的 mp3 解码库），CLI 出裸 PCM
  ffmpeg     兜底；完整版 200 MB+（本机 211 MB），最不划算
可用 `online.edge.decoder` 指定（auto|miniaudio|soundfile|mpg123|ffmpeg）。

## 不需要 key
Edge 朗读用的是公开端点，无需 API key / 账号；但要**联网**（必要时 `proxy=`）。

## 中文音色（部分）
  zh-CN-XiaoxiaoNeural（女，默认） zh-CN-XiaoyiNeural（女）
  zh-CN-YunxiNeural（男）         zh-CN-YunjianNeural（男，解说）
  zh-CN-YunyangNeural（男，新闻）  zh-CN-liaoning-XiaobeiNeural（东北女）
  zh-CN-shaanxi-XiaoniNeural（陕西女） zh-HK-HiuMaanNeural（粤语） zh-TW-HsiaoChenNeural（台湾）
"""

import array
import io
import shutil
import subprocess
import wave

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DECODER_ORDER = ("miniaudio", "soundfile", "mpg123", "ffmpeg")
LAST_DECODER = None          # 最近一次实际用的解码器（给 /health 看）


def _synth_mp3(text, voice, rate, volume, pitch, proxy):
    """edge-tts 流式拿 mp3 字节。edge_tts 是异步库，这里用 asyncio.run 包一层同步接口。"""
    import asyncio

    import edge_tts                                   # 惰性：没装就抛清晰错误

    async def _go():
        kw = {"rate": rate, "volume": volume, "pitch": pitch}
        if proxy:
            kw["proxy"] = proxy
        comm = edge_tts.Communicate(text, voice, **kw)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf.extend(chunk["data"])
        return bytes(buf)

    try:                                        # 已在事件循环里（Wyoming/asyncio 调用方）？
        asyncio.get_running_loop()
        nested = True
    except RuntimeError:
        nested = False
    if nested:                                  # asyncio.run 不能嵌套：丢独立线程跑一个干净循环
        import threading
        box = {}

        def _thread():
            try:
                box["data"] = asyncio.run(_go())
            except BaseException as exc:        # noqa: BLE001 - 原样抛回调用线程
                box["err"] = exc

        t = threading.Thread(target=_thread)
        t.start()
        t.join()
        if "err" in box:
            raise box["err"]
        data = box.get("data")
    else:
        data = asyncio.run(_go())
    if not data:
        raise RuntimeError("edge-tts 没返回音频（网络/文本为空/被限流？）")
    return data


def _pcm_wav(pcm, rate):
    """裸 pcm_s16le 单声道 → wav 字节。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


def _decode_miniaudio(mp3, sample_rate, ffmpeg_bin):
    import miniaudio
    d = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                         nchannels=1, sample_rate=int(sample_rate))
    return d.samples.tobytes(), int(d.sample_rate)


def _decode_soundfile(mp3, sample_rate, ffmpeg_bin):
    import soundfile as sf
    data, sr = sf.read(io.BytesIO(mp3), dtype="int16", always_2d=True)
    n = data.shape[1]
    if n > 1:                                          # 多声道取均值 → 单声道
        mono = array.array("h", (int(sum(row) / n) for row in data))
    else:
        mono = array.array("h", (int(v) for v in data[:, 0]))
    return mono.tobytes(), int(sr)


def _decode_mpg123(mp3, sample_rate, ffmpeg_bin):
    exe = shutil.which("mpg123")
    if not exe:
        raise RuntimeError("mpg123 不在 PATH")
    p = subprocess.run([exe, "-q", "-s", "-r", str(int(sample_rate)), "-m", "-"],
                       input=mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("mpg123 失败: " + p.stderr.decode("utf-8", "replace")[:160])
    return p.stdout, int(sample_rate)


def _decode_ffmpeg(mp3, sample_rate, ffmpeg_bin):
    exe = ffmpeg_bin or shutil.which("ffmpeg") or ""
    if not exe:
        raise RuntimeError("ffmpeg 不在 PATH")
    p = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(int(sample_rate)), "pipe:1"],
        input=mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 失败: " + p.stderr.decode("utf-8", "replace")[:160])
    return p.stdout, int(sample_rate)


_DECODERS = {"miniaudio": _decode_miniaudio, "soundfile": _decode_soundfile,
             "mpg123": _decode_mpg123, "ffmpeg": _decode_ffmpeg}


def mp3_to_wav(mp3, ffmpeg_bin="", sample_rate=24000, decoder="auto"):
    """mp3 字节 → wav 字节。decoder=auto 时按 DECODER_ORDER 依次试（小解码器优先）。"""
    global LAST_DECODER
    order = list(DECODER_ORDER) if decoder in ("", "auto", None) else [decoder]
    errs = []
    for name in order:
        fn = _DECODERS.get(name)
        if fn is None:
            errs.append("%s:未知" % name)
            continue
        try:
            pcm, rate = fn(mp3, sample_rate, ffmpeg_bin)
            if not pcm:
                raise RuntimeError("空输出")
            LAST_DECODER = name
            return _pcm_wav(pcm, rate)
        except Exception as exc:                        # 换下一个解码器
            errs.append("%s:%s" % (name, str(exc)[:80]))
    raise RuntimeError("mp3 解码失败，试过 %s；建议 pip install miniaudio 或装 mpg123"
                       % "; ".join(errs))


def synthesize_media(text, voice=DEFAULT_VOICE, rate="+0%", volume="+0%", pitch="+0Hz",
                     proxy="", ffmpeg_bin="", fmt="wav", sample_rate=24000, decoder="miniaudio"):
    """文本 → 音频字节。fmt='wav'（默认，需某个 mp3 解码器）或 'mp3'（原样）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    mp3 = _synth_mp3(text, voice, rate, volume, pitch, proxy)
    if fmt == "mp3":
        return mp3
    return mp3_to_wav(mp3, ffmpeg_bin=ffmpeg_bin, sample_rate=sample_rate, decoder=decoder)


def synthesize_wav(text, **kw):
    return synthesize_media(text, fmt="wav", **kw)
