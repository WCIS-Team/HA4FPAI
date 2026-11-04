#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fsm.py —— DialogueFSM 单测（纯标准库，python -X utf8 tests/test_fsm.py）。"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from fsm import DialogueFSM   # noqa: E402

PASS = []


def ok(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print("[%s] %s %s" % ("ok" if cond else "FAIL", name, detail))


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def adv(self, s):
        self.t += s


def t_basic_flow():
    f = DialogueFSM()
    s, a = f.feed("wake")
    ok("wake→listening", s == "listening" and a == "start_listen")
    s, a = f.feed("speech_end")
    ok("speech_end→thinking/run_asr", s == "thinking" and a == "run_asr")
    s, a = f.feed("asr_done")
    ok("asr_done 自环且 run_llm", s == "thinking" and a == "run_llm")
    s, a = f.feed("llm_done")
    ok("llm_done→speaking/start_tts", s == "speaking" and a == "start_tts")
    s, a = f.feed("tts_done")
    ok("tts_done→idle", s == "idle" and a == "idle")


def t_barge_in():
    f = DialogueFSM()
    for e in ("wake", "speech_end", "asr_done", "llm_done"):
        f.feed(e)
    ok("已到 speaking", f.state == "speaking")
    s, a = f.feed("barge_in")
    ok("barge_in→listening/stop_tts_and_listen",
       s == "listening" and a == "stop_tts_and_listen")


def t_illegal():
    f = DialogueFSM()
    s, a = f.feed("llm_done")          # idle 下非法
    ok("非法事件不迁移", s == "idle" and a is None)
    f.feed("wake")
    s, a = f.feed("barge_in")          # listening 下非法
    ok("listening 下 barge_in 忽略", s == "listening" and a is None)


def t_error_reset():
    f = DialogueFSM()
    f.feed("wake")
    s, a = f.feed("error")
    ok("任意状态 error→error", s == "error" and a == "recover")
    s, a = f.feed("reset")
    ok("error reset→idle", s == "idle" and a == "recover")
    f.feed("wake")
    f.feed("speech_end")
    s, a = f.feed("reset")
    ok("thinking 下 reset→idle", s == "idle")


def t_timeout():
    clk = Clock()
    f = DialogueFSM(clock=clk, timeout_ms={"listening": 1000})
    f.feed("wake")                     # 进 listening，_entered=1000
    s, a = f.tick()                    # 未超时
    ok("listening 未超时", s == "listening" and a is None)
    clk.adv(1.2)                       # 过 1200ms
    s, a = f.tick()
    ok("listening 超时→idle", s == "idle" and a == "idle")
    # thinking 超时→error
    clk2 = Clock()
    g = DialogueFSM(clock=clk2, timeout_ms={"thinking": 500})
    g.feed("wake")
    g.feed("speech_end")               # thinking
    clk2.adv(0.6)
    s, a = g.tick()
    ok("thinking 超时→error", s == "error" and a == "recover")


def t_history():
    f = DialogueFSM()
    f.feed("wake")
    f.feed("speech_end")
    ok("history 记录迁移", len(f.history) == 2 and f.history[-1][3] == "thinking")


def main():
    print("=" * 60)
    print("DialogueFSM 单测 —— python %s" % sys.version.split()[0])
    print("=" * 60)
    t_basic_flow()
    t_barge_in()
    t_illegal()
    t_error_reset()
    t_timeout()
    t_history()
    fails = [n for n, c in PASS if not c]
    print("-" * 60)
    print("通过 %d / %d" % (len(PASS) - len(fails), len(PASS)))
    if fails:
        for n in fails:
            print("  FAIL:", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
