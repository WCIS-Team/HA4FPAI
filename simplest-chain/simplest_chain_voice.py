#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""simplest_chain_voice.py —— 板端最小语音回环：录音 → SiliconFlow ASR+LLM → ekho 念出来。

一句话：按一下（或常驻），对着麦克风说一句，脚本自己断句，把 wav POST 给
`simplest_chain_bridge.py` 的 `/api/v1/asr`，拿回 LLM 回复文本，再 POST 给
`ekho-server` 的 `/tts?wait=1`，有喇叭就播、没有就落盘。

用法：
  python3 simplest_chain_voice.py                         # 常驻：说一句、答一句
  python3 simplest_chain_voice.py --once                  # 只跑一轮
  python3 simplest_chain_voice.py --input ask.wav --tts none   # 不录音，只测上传与回答
  python3 simplest_chain_voice.py --check                 # 只看 bridge / ekho 健康

环境变量：
  SIMPLEST_CHAIN_BRIDGE_URL   默认 http://127.0.0.1:18090/api/v1/asr
  TTS_EKHO_URL      默认 http://127.0.0.1:2046
  DEV               默认 plughw:MCP01,0
"""
import argparse
import array
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
import wave

HERE = os.path.dirname(os.path.abspath(__file__))


def _log(msg):
    print(msg, flush=True)


def _init_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _rms(pcm):
    """s16le 单声道字节流的 RMS，归一到 0..1。"""
    n = len(pcm) // 2
    if n <= 0:
        return 0.0
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    if not a:
        return 0.0
    acc = 0.0
    for v in a:
        acc += float(v) * float(v)
    return (acc / len(a)) ** 0.5 / 32768.0


def _pcm_to_wav(pcm, rate, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


def record_turn(device, rate=16000, frame_ms=100, start_rms=0.012,
                silence_ms=700, min_speech_ms=300, max_s=20.0,
                wait_speech_s=30.0):
    """从 arecord 录一轮，能量 VAD 自动断句。返回 (pcm_bytes, err)。"""
    frame_bytes = int(rate * frame_ms / 1000) * 2
    cmd = ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE",
           "-r", str(rate), "-c", "1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        return None, "找不到 arecord（板端请装 alsa-utils）"
    except Exception as exc:                       # noqa: BLE001
        return None, "arecord 起不来：%r" % (exc,)

    buf = bytearray()
    voiced_ms = 0
    silence_acc = 0
    started = False
    t0 = time.time()
    try:
        while True:
            frame = proc.stdout.read(frame_bytes)
            if not frame or len(frame) < frame_bytes:
                break
            e = _rms(frame)
            if not started:
                if e >= start_rms:
                    started = True
                    buf += frame
                    voiced_ms = frame_ms
                elif time.time() - t0 >= wait_speech_s:
                    return None, "等说话超时（%.0f s）" % wait_speech_s
            else:
                buf += frame
                if e >= start_rms:
                    voiced_ms += frame_ms
                    silence_acc = 0
                else:
                    silence_acc += frame_ms
                if silence_acc >= silence_ms and voiced_ms >= min_speech_ms:
                    break
                if len(buf) >= int(max_s * rate * 2):
                    break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:                          # noqa: BLE001
            try:
                proc.kill()
            except Exception:                      # noqa: BLE001
                pass

    if not started or voiced_ms < min_speech_ms:
        err = proc.stderr.read().decode("utf-8", "replace")[-800:] if proc.stderr else ""
        return None, "没有听到有效语音" + (("；arecord: " + err) if err.strip() else "")
    return bytes(buf), None


def post_audio(bridge_url, wav_bytes, timeout=120.0):
    """multipart POST 到 simplest_chain_bridge /api/v1/asr，返回解析后的 JSON。"""
    boundary = "----simplestchain" + uuid.uuid4().hex
    body = (
        ("--%s\r\n" % boundary).encode()
        + b'Content-Disposition: form-data; name="audio"; filename="seg.wav"\r\n'
        + b"Content-Type: audio/wav\r\n\r\n"
        + wav_bytes
        + ("\r\n--%s--\r\n" % boundary).encode()
    )
    req = urllib.request.Request(
        bridge_url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:                        # noqa: BLE001
        raise RuntimeError("bridge 回包不是 JSON：%r（前 300 字节：%r）"
                           % (exc, raw[:300]))
    if obj.get("code") not in (0, None):
        raise RuntimeError("bridge 返回错误：%s" % (obj.get("msg") or obj.get("error") or obj))
    return obj


def ekho_tts(ekho_url, text, out_dir, voice="", timeout=120.0):
    """POST 文本给 ekho-server，返回 (wav_path, wav_bytes)。"""
    q = "/tts?wait=1"
    if voice:
        q += "&voice=" + urllib.parse.quote(voice)
    req = urllib.request.Request(
        ekho_url.rstrip("/") + q, data=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError("ekho 返回空音频")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "reply_%s.wav" % time.strftime("%Y%m%dT%H%M%S"))
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path, data


def has_soundcard():
    """/dev/snd 里有 pcm/control 才算有播放设备（只有 timer 不算）。"""
    d = "/dev/snd"
    if not os.path.isdir(d):
        return False
    try:
        return any(x.startswith(("pcm", "control")) for x in os.listdir(d))
    except OSError:
        return False


def play_wav(path):
    player = shutil.which("aplay")
    if not player or not has_soundcard():
        return False
    try:
        subprocess.run([player, "-q", path], timeout=180, check=False)
        return True
    except Exception:                                # noqa: BLE001
        return False


def check_health(bridge_url, ekho_url):
    for name, url in (("bridge", bridge_url.rsplit("/api/", 1)[0] + "/health"),
                      ("ekho", ekho_url.rstrip("/") + "/health")):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = r.read(400).decode("utf-8", "replace")
            _log("[health] %-6s OK  %s  %s" % (name, url, data[:200]))
        except Exception as exc:                    # noqa: BLE001
            _log("[health] %-6s FAIL %s  %r" % (name, url, exc))


def main(argv=None):
    _init_stdout()
    ap = argparse.ArgumentParser(description="SiliconFlow ASR+LLM + ekho 最小语音回环")
    ap.add_argument("--bridge-url", default=os.environ.get(
        "SIMPLEST_CHAIN_BRIDGE_URL", "http://127.0.0.1:18090/api/v1/asr"))
    ap.add_argument("--ekho-url", default=os.environ.get(
        "TTS_EKHO_URL", "http://127.0.0.1:2046"))
    ap.add_argument("--device", default=os.environ.get("DEV", "plughw:MCP01,0"))
    ap.add_argument("--rate", type=int, default=16000)
    ap.add_argument("--input", help="用现成 wav 代替麦克风（跑一轮）")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--tts", choices=["ekho", "none"], default="ekho")
    ap.add_argument("--voice", default="", help="ekho 音色，空=服务端默认")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "tts"))
    ap.add_argument("--no-play", action="store_true", help="只合成不播放")
    ap.add_argument("--check", action="store_true", help="检查 bridge / ekho 健康")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--start-rms", type=float, default=0.012)
    ap.add_argument("--silence-ms", type=int, default=700)
    ap.add_argument("--min-speech-ms", type=int, default=300)
    ap.add_argument("--max-s", type=float, default=20.0)
    a = ap.parse_args(argv)

    bridge_url = a.bridge_url
    if "/api/v1/asr" not in bridge_url and "/v1/audio" not in bridge_url:
        bridge_url = bridge_url.rstrip("/") + "/api/v1/asr"

    if a.check:
        check_health(bridge_url, a.ekho_url)
        return 0

    if a.input:
        with open(a.input, "rb") as f:
            turns = [f.read()]
    else:
        turns = None

    n = 0
    while True:
        if turns is not None:
            wav_bytes = turns[0]
        else:
            _log("[mic] 听音中（%.0fs 内没说话就退出）……" % 30.0)
            pcm, err = record_turn(a.device, a.rate, start_rms=a.start_rms,
                                   silence_ms=a.silence_ms,
                                   min_speech_ms=a.min_speech_ms,
                                   max_s=a.max_s)
            if pcm is None:
                _log("[mic] %s" % err)
                return 3
            wav_bytes = _pcm_to_wav(pcm, a.rate)
        n += 1
        try:
            obj = post_audio(bridge_url, wav_bytes, timeout=a.timeout)
        except Exception as exc:                    # noqa: BLE001
            _log("[bridge] 请求失败：%r" % (exc,))
            return 4
        asr_text = (obj.get("asr_text") or "").strip()
        reply = (obj.get("text") or asr_text).strip()
        _log("[%d] 识别: %s" % (n, asr_text or "(空)"))
        _log("[%d] 回答: %s" % (n, reply or "(空)"))
        if a.tts == "ekho" and reply:
            try:
                path, _data = ekho_tts(a.ekho_url, reply, a.out_dir,
                                       voice=a.voice, timeout=a.timeout)
                _log("[tts] %s" % path)
                if not a.no_play:
                    _log("[tts] aplay: %s" % ("已播" if play_wav(path) else "无播放设备，已落盘"))
            except Exception as exc:                # noqa: BLE001
                _log("[tts] 合成失败：%r" % (exc,))
        if a.input or a.once:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
