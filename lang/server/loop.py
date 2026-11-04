#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loop.py —— 把 fsm.py 接进**真实音频循环**：一麦一喇叭，自动匹配默认设备。

状态流转全走 `fsm.DialogueFSM`（表驱动，本文件不自己造轮次逻辑）：

    idle --有人声(wake)--> listening --说完(speech_end)--> thinking
         --跑完链(asr_done/llm_done)--> speaking --放完(tts_done)--> idle
    speaking 中检测到大声 → barge_in → 掐掉播报回 listening
    各状态超时由 fsm.tick() 兜底（listening 8s / thinking 15s / speaking 60s）

设备**自动匹配**（她 2026-11-04 定：Linux 上就一个麦克风一个喇叭）：
  采集 = `arecord -q -t raw -f S16_LE -r <audio.in_sample_rate> -c 1`
  播放 = `aplay -q <wav>`（子进程，可被 barge-in 掐掉）
  `arecord -l`/`aplay -l` 数声卡：有 → 用 default；**没有 → 自动降级**
  （--run 不退出、每 10 s 重探，麦一插上即自动开麦；播报能力每轮现查，
   无声卡时回复自动落盘 `/tmp/loop_reply.wav`——喇叭到位即改走 aplay）。

端点检测（loop 自带的能量门，不依赖 chain 的 vad 档——板上现在 vad=none）：
  帧 30 ms；RMS ≥ start_th 视作起音；连续 end_silence_ms 低于 start_th 视作说完；
  语音总时长 < min_speech_ms 丢弃。阈值可环境变量覆盖：
  LOOP_START_TH / LOOP_END_SILENCE_MS / LOOP_MIN_SPEECH_MS / LOOP_BARGE_FRAMES

用法：
    python3 loop.py --check                      # 设备/档位/ fsm 摘要，不跑
    python3 loop.py --simulate some.wav          # 文件当麦克风，验证 fsm＋全链（板上判据）
    python3 loop.py --oneshot some.wav           # 单轮：跑链＋有喇叭就播，没有就落盘
    python3 loop.py --run                        # 常驻真实循环（需麦；无声卡自动落盘）
"""
import argparse
import array
import logging
import os
import select
import subprocess
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cfg import Config                          # noqa: E402
from core import Chain                           # noqa: E402
from fsm import DialogueFSM                      # noqa: E402

LOG = logging.getLogger("loop")

FRAME_MS = 30
START_TH = float(os.environ.get("LOOP_START_TH", "0.012"))
END_SILENCE_MS = int(os.environ.get("LOOP_END_SILENCE_MS", "700"))
MIN_SPEECH_MS = int(os.environ.get("LOOP_MIN_SPEECH_MS", "250"))
BARGE_FRAMES = int(os.environ.get("LOOP_BARGE_FRAMES", "2"))
OUT_FALLBACK = os.environ.get("LOOP_OUT_WAV", "/tmp/loop_reply.wav")


# ---------------------------------------------------------------------------
# 设备自动匹配
# ---------------------------------------------------------------------------
def _card_count(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        lines = [l for l in p.stdout.decode("utf-8", "ignore").splitlines()
                 if l.strip().startswith("card ")]
        return len(lines)
    except Exception:                                # noqa: BLE001
        return -1                                    # 命令本身不存在


def devices():
    cap = _card_count(["arecord", "-l"])
    play = _card_count(["aplay", "-l"])
    return {"capture_cards": cap, "playback_cards": play,
            "can_capture": cap > 0, "can_play": play > 0}


# ---------------------------------------------------------------------------
# PCM 帧与能量
# ---------------------------------------------------------------------------
def frame_bytes(rate):
    return int(rate * FRAME_MS / 1000) * 2           # s16le 单声道


def rms_of(pcm):
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not a:
        return 0.0
    s = 0.0
    for v in a:
        s += v * v
    return (s / len(a)) ** 0.5 / 32768.0


def pcm_to_wav(pcm, rate):
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


def drain(pipe, nbytes):
    """丢掉 thinking/speaking 期间积压在管子里的旧帧（否则下一轮开头吃到上轮的尾音）。"""
    while True:
        try:
            r, _, _ = select.select([pipe], [], [], 0)
        except (ValueError, OSError):
            return
        if not r:
            return
        data = pipe.read(nbytes)
        if not data:
            return


# ---------------------------------------------------------------------------
# 命令抢占：识别文本命中 HA4FPAI 执行器通道 B（DIRECT_COMMANDS）就走动作，不走 LLM
# ---------------------------------------------------------------------------
_exec_mod = None


def _load_exec():
    """执行器真值在 HA4FPAI/klar/klar_executor.py；板上部署在 server/upper/。"""
    global _exec_mod
    if _exec_mod is not None:
        return _exec_mod or None
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(here, "upper"),
              os.path.normpath(os.path.join(here, "..", "upper")),
              os.path.normpath(os.path.join(here, "..", "..", "..",
                                            "repos", "HA4FPAI", "klar"))):
        if os.path.isfile(os.path.join(d, "klar_executor.py")):
            sys.path.insert(0, d)
            import klar_executor as ex               # noqa: E402
            _exec_mod = ex
            LOG.info("命令表就绪：%s（短语 %d 组）", d, len(ex.DIRECT_COMMANDS))
            return ex
    _exec_mod = False
    LOG.info("未找到执行器（klar_executor.py），命令抢占关闭，全部走 LLM")
    return None


def try_command(text):
    ex = _load_exec()
    if ex is None:
        return None
    hit = ex.match_direct(text)
    if not hit:
        return None
    fn, slots, phrase = hit
    slots.setdefault("text", text)
    try:
        ok, msg, detail = fn(slots)
    except Exception as exc:                         # noqa: BLE001
        return {"ok": False, "matched": phrase,
                "message": "命令执行异常：%s" % exc, "detail": None}
    return {"ok": bool(ok), "matched": phrase,
            "message": msg or ("执行完成" if ok else "执行失败"), "detail": detail}


# ---------------------------------------------------------------------------
# 跑一轮（thinking 段）：文件音频 → Chain.run_audio → 播报/落盘
# ---------------------------------------------------------------------------
def run_turn(chain, wav_bytes, session):
    """ASR 先行 → 命令抢占 → 其余才进 LLM；tts 一律走链尾（返回带 _wav）。"""
    out = chain.run_audio(wav_bytes, opts={"llm": False, "tts": False,
                                           "session": session})
    text = (out.get("asr_text") or "").strip()
    if not text:
        return out
    cmd = try_command(text)
    if cmd is not None:
        LOG.info("命令命中：%r -> %r", text, cmd["message"])
        out["command"] = cmd
        nxt = chain.run_text(cmd["message"], opts={"llm": False, "tts": True,
                                                   "session": session,
                                                   "format": "wav"})
    else:
        nxt = chain.run_text(text, opts={"llm": True, "tts": True,
                                         "session": session, "format": "wav"})
    for k in ("text", "chunks", "_wav", "audio_rate", "audio_ms"):
        if k in nxt:
            out[k] = nxt[k]
    for k in ("stages_ms", "stages_error"):
        merged = dict(out.get(k) or {})
        merged.update(nxt.get(k) or {})
        out[k] = merged
    out["total_ms"] = out.get("total_ms") or nxt.get("total_ms")
    return out


def speak(out, can_play, fallback=OUT_FALLBACK):
    """把链尾 wav 播出去（有卡）或落盘（无声卡）。返回 (方式, 路径/None, 时长ms)。"""
    data = out.get("_wav")
    dur = out.get("audio_ms", 0)
    if not data:
        return ("none", None, 0)
    with open(fallback, "wb") as f:
        f.write(data)
    if not can_play:
        return ("file", fallback, dur)
    p = subprocess.Popen(["aplay", "-q", fallback],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ("aplay", p, dur)


# ---------------------------------------------------------------------------
# fsm 驱动：拆成"跑链→起播→轮询"三步，外层帧循环才能在播报中检测 barge-in
# ---------------------------------------------------------------------------
def turn_prepare(fsm, chain, seg_pcm, in_rate, session):
    """listening→thinking：整链跑完，fsm 推到 speaking；返回 out（None＝空识别，已回 idle）。"""
    wav_bytes = pcm_to_wav(seg_pcm, in_rate)
    fsm.feed("speech_end")                          # -> thinking, run_asr
    try:
        out = run_turn(chain, wav_bytes, session)
    except Exception as exc:                         # noqa: BLE001
        LOG.error("chain 异常：%s", exc)
        fsm.feed("error")
        fsm.feed("reset")
        return None
    fsm.feed("asr_done")
    if not (out.get("asr_text") or "").strip():
        LOG.info("识别为空，回 idle")
        fsm.feed("reset")
        return None
    fsm.feed("llm_done")                            # -> speaking, start_tts
    return out


def begin_speaking(fsm, out, can_play):
    """起播。返回 aplay 句柄；无声卡/无音频＝直接 tts_done 回 idle，返回 None。"""
    data = out.get("_wav")
    if not data:
        fsm.feed("tts_done")
        return None
    with open(OUT_FALLBACK, "wb") as f:
        f.write(data)
    if not can_play:
        LOG.info("无声卡：回复落盘 %s（%d ms）", OUT_FALLBACK, out.get("audio_ms", 0))
        fsm.feed("tts_done")
        return None
    return subprocess.Popen(["aplay", "-q", OUT_FALLBACK],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# --simulate：文件当麦克风，走完整套 fsm 事件，打印迁移史（板上验收判据）
# ---------------------------------------------------------------------------
def simulate(path, chain, dev, session="loop-sim"):
    fsm = DialogueFSM()
    with open(path, "rb") as f:
        payload = f.read()
    # 直接当整段语音（不做能量切分——文件本身就是一轮话）
    print("起点状态:", fsm.state)
    out = drive_after_speech_from_file(fsm, chain, payload, dev["can_play"], session)
    print("---- fsm 迁移史 ----")
    for (t, ev, src, dst, act) in fsm.history:
        print("  %-10s %s -> %s   action=%s" % (ev, src, dst, act))
    print("终点状态:", fsm.state)
    if out:
        print("asr_text :", out.get("asr_text"))
        if out.get("command"):
            print("command  :", out["command"])
        print("llm_text :", out.get("text"))
        print("stages_ms:", out.get("stages_ms"))
        print("stages_error:", out.get("stages_error") or "{}")
    return out


def drive_after_speech_from_file(fsm, chain, wav_payload, can_play, session):
    fsm.feed("wake")                                # idle -> listening：文件当"被唤醒后收的一轮话"
    state, act = fsm.feed("speech_end")             # listening -> thinking, run_asr
    if act != "run_asr":
        raise RuntimeError("fsm 未走通：speech_end 在 %s 态给出 %s" % (state, act))
    out = run_turn(chain, wav_payload, session)
    fsm.feed("asr_done")
    if not (out.get("asr_text") or "").strip():
        LOG.info("识别为空，回 idle")
        fsm.feed("reset")
        return out
    fsm.feed("llm_done")                            # -> speaking, start_tts
    how, obj, dur = speak(out, can_play)
    if how == "aplay":
        while obj.poll() is None:
            time.sleep(0.05)
    fsm.feed("tts_done")                            # -> idle
    return out


# ---------------------------------------------------------------------------
# --run：真实麦克风常驻循环
# ---------------------------------------------------------------------------
def run_live(chain, cfg, dev):
    in_rate = int(cfg.get("audio", "in_sample_rate", "48000") or 48000)
    fb = frame_bytes(in_rate)
    # 没麦克风不退出：常驻等硬件（ systemd 单元可以先 enable，麦到位即工作）
    while True:
        dev = devices()
        if dev["can_capture"]:
            break
        LOG.info("暂无采集声卡（arecord -l 为空），10s 后重探……硬件一到即自动开麦")
        time.sleep(10)
    mic = subprocess.Popen(
        ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(in_rate), "-c", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if mic.poll() is not None:
        LOG.error("arecord 起不来，15s 后重试")
        return 3
    fsm = DialogueFSM()
    LOG.info("真实循环起来：capture=%s rate=%d frame=%dB（自动匹配 default）",
             "default", in_rate)
    buf = bytearray()
    voiced_ms = 0
    silence_ms = 0
    barge_hit = 0
    play_proc = None
    try:
        while True:
            frame = mic.stdout.read(fb)
            if not frame or len(frame) < fb:
                break
            e = rms_of(frame)
            voiced = e >= START_TH
            ms, act = fsm.tick()
            if act == "idle":
                LOG.info("超时：%s -> idle", ms)
                buf = bytearray()
            if act == "recover":
                fsm.feed("reset")
                buf = bytearray()

            if fsm.state == "idle":
                if voiced:                          # 能量即唤醒（kws passthrough 口径）
                    fsm.feed("wake")
                    buf = bytearray(frame)
                    voiced_ms, silence_ms = FRAME_MS, 0
            elif fsm.state == "listening":
                buf += frame
                if voiced:
                    voiced_ms += FRAME_MS
                    silence_ms = 0
                else:
                    silence_ms += FRAME_MS
                    if voiced_ms >= MIN_SPEECH_MS and silence_ms >= END_SILENCE_MS:
                        seg = bytes(buf)
                        buf = bytearray()
                        voiced_ms = silence_ms = 0
                        out = turn_prepare(fsm, chain, seg, in_rate, "loop")
                        play_proc = (begin_speaking(fsm, out, devices()["can_play"])
                                     if out is not None else None)
                        barge_hit = 0
            elif fsm.state == "speaking":
                # 播报期间的 barge-in：连续大声
                if voiced:
                    barge_hit += 1
                    if barge_hit >= BARGE_FRAMES:
                        LOG.info("barge-in：掐播报回收音")
                        if play_proc is not None:
                            play_proc.kill()
                            play_proc = None
                        fsm.feed("barge_in")
                        buf = bytearray(frame)
                        voiced_ms, silence_ms = FRAME_MS, 0
                        drain(mic.stdout, fb)
                else:
                    barge_hit = 0
                if play_proc is not None and play_proc.poll() is not None:
                    play_proc = None                # 自然播完
                    fsm.feed("tts_done")
                    LOG.info("播报完成 -> idle")
                    drain(mic.stdout, fb)
    except KeyboardInterrupt:
        pass
    finally:
        mic.terminate()
    return 0


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="设备/档位/摘要")
    ap.add_argument("--simulate", metavar="WAV", help="拿 wav 当麦克风跑一轮 fsm 全链")
    ap.add_argument("--oneshot", metavar="WAV", help="单轮：跑链＋有喇叭就播")
    ap.add_argument("--run", action="store_true", help="真实麦克风常驻循环")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-warm", action="store_true", help="Skip runtime/model preload")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    dev = devices()
    if a.check:
        chain = Chain(cfg)
        print("设备自动匹配:", dev)
        for s in ("sar", "vad", "kws", "asr", "llm", "tokenizer", "tts"):
            print("  chain.%-9s = %s" % (s, chain.active(s)))
        print("能量门: start_th=%.3f end_silence=%dms min_speech=%dms barge=%d帧"
              % (START_TH, END_SILENCE_MS, MIN_SPEECH_MS, BARGE_FRAMES))
        print("fsm: idle/listening/thinking/speaking/error，超时 listening=8s thinking=15s speaking=60s")
        return 0
    chain = Chain(cfg)
    if not a.no_warm:
        info = chain.warm()
        LOG.info("warm: %s", info.get("stages"))
    if a.simulate:
        simulate(a.simulate, chain, dev)
        return 0
    if a.oneshot:
        fsm = DialogueFSM()
        out = drive_after_speech_from_file(fsm, chain, open(a.oneshot, "rb").read(),
                                           dev["can_play"], "oneshot")
        print("asr:", out.get("asr_text"))
        print("llm:", out.get("text"))
        print("输出:", OUT_FALLBACK, "（无声卡时落盘）")
        return 0
    if a.run:
        return run_live(chain, cfg, dev)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
