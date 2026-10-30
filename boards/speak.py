#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""speak.py — 把执行结果通过 Wyoming TTS(10302) 播报出去。

板上没有 /dev/snd（实测 aplay -l 无卡），所以这里做两件事：
  1. 生成 wav 落盘（给外面接喇叭/HDMI/网络播放用）
  2. 可选：POST 到外部播放器（如果以后接了 USB 声卡或网络音箱）

用法（由 klar_executor 调用）
    from speak import speak
    speak("客厅的灯开了")            # 合成并落盘，返回 wav 路径
    speak("...", play=True)          # 额外尝试本地播放（无设备则跳过）

Wyoming TTS 协议：Synthesize -> AudioStart/chunks/AudioStop -> SynthesizeStop
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_kit import load_config  # noqa: E402

LOG = logging.getLogger("speak")
OUTDIR = os.environ.get("KLAR_SPEECH_DIR", "/mnt/data/klar/speech")


def _client():
    from wyoming.client import AsyncTcpClient
    return AsyncTcpClient


def synthesize(text, uri=None, out=None, timeout=120):
    """调用 Wyoming TTS 合成，返回 (wav_path, duration_s)。失败抛异常。"""
    import asyncio
    import wave
    from wyoming.audio import AudioChunk, AudioStop
    from wyoming.tts import Synthesize, SynthesizeStop

    cfg = load_config()
    uri = uri or "tcp://127.0.0.1:%d" % int(cfg["server"].get("tts_port", 10302))
    os.makedirs(OUTDIR, exist_ok=True)
    if out is None:
        safe = "".join(c for c in text if c.isalnum() or c in " ，。！？、")[:20].strip()
        out = os.path.join(OUTDIR, "%s-%d.wav" % (safe or "say", int(time.time() * 1000)))

    async def _run():
        audio, rate = [], 22050
        client = _client()
        async with client.from_uri(uri) as c:
            await c.write_event(Synthesize(text=text).event())
            while True:
                ev = await asyncio.wait_for(c.read_event(), timeout=timeout)
                if ev is None:
                    break
                if AudioChunk.is_type(ev.type):
                    ch = AudioChunk.from_event(ev)
                    rate = ch.rate
                    audio.append(ch.audio)
                elif SynthesizeStop.is_type(ev.type):
                    break
                elif AudioStop.is_type(ev.type):
                    continue
        return b"".join(audio), rate

    loop = asyncio.new_event_loop()
    try:
        data, rate = loop.run_until_complete(_run())
    finally:
        loop.close()

    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    dur = len(data) / 2.0 / rate
    LOG.info("播报 %r -> %s (%.2fs)", text, out, dur)
    return out, dur


def has_soundcard():
    """真正有播放能力的判断。

    坑：本板 /dev/snd 目录**存在**但里面只有 `timer`，没有 pcm/control，
       所以 `os.path.exists("/dev/snd")` 会是 True 却根本放不出声
        （实测 aplay: Unknown PCM default / no soundcards found）。
       判定必须是「存在 pcm 或 control 设备节点」。
    """
    d = "/dev/snd"
    if not os.path.isdir(d):
        return False
    try:
        entries = os.listdir(d)
    except OSError:
        return False
    return any(e.startswith(("pcm", "control")) for e in entries)


def play_file(path):
    """本地播放。本板没有声卡，会直接返回 False。"""
    if not has_soundcard():
        LOG.debug("no soundcard (/dev/snd has no pcm/control node); skip playback")
        return False
    import shutil
    import subprocess
    player = shutil.which("aplay")
    if not player:
        return False
    try:
        subprocess.run([player, "-q", path], timeout=120, check=False)
        return True
    except Exception as e:                       # noqa: BLE001
        LOG.warning("play failed: %s", e)
        return False


def speak(text, play=None, return_path=False):
    """播报一句。play=None 时自动判断（有 /dev/snd 才播）。返回 (ok, info)。"""
    try:
        path, dur = synthesize(text)
    except Exception as e:                       # noqa: BLE001
        LOG.warning("TTS 失败: %s", e)
        return (False, str(e)) if not return_path else (False, str(e), None)

    do_play = has_soundcard() if play is None else play
    played = play_file(path) if do_play else False
    info = {"wav": path, "duration_s": dur, "played_locally": played}
    if return_path:
        return True, info, path
    return True, info


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--play", action="store_true")
    ap.add_argument("--uri")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ok, info = speak(a.text, play=(True if a.play else None))
    print(ok, info)
