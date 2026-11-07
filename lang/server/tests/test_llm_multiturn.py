#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_llm_multiturn.py —— npuserv 档多轮上下文单测（纯标准库，不连板、不发网络）。

覆盖：prompt 拼接形状、session 分桶隔离、历史窗口裁剪（history_turns）、
reset(session) 清桶、history_turns=0 时退化为单轮、meta.turns 计数。

跑法（仓库根）：python -B -X utf8 lang/server/tests/test_llm_multiturn.py
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))        # lang/server
sys.path.insert(0, str(HERE.parent.parent))  # lang（core 在 server 目录里 import core）

PASS = []


def ok(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print("[%s] %s %s" % ("ok" if cond else "FAIL", name, detail))


def make_backend(**profile):
    """构造 NpuServ 但把网络层换成桩：/models、/status、/generate 都本地回。"""
    from backends import llm as L

    class _Stage(object):
        def __init__(self, *a, **kw):
            pass

    # 直接实例化，绕过 Stage.__init__ 的配置装配
    orig_stage = L.Stage
    L.Stage = _Stage
    try:
        b = L.NpuServ.__new__(L.NpuServ)
        b.name = "npuserv"
        b.profile = dict(profile)
        b.base = "http://stub"
        b.model = profile.get("model", "ernie-4.5-0.3b")
        b.max_new = 8
        b.timeout = 5
        b.wait_s = 1
        b.history_turns = int(profile.get("history_turns", 2))
        b.system = profile.get("system_prompt", "SYS")
        b._hist = {}
        import threading
        b._hlock = threading.Lock()
        b.sent = []

        def _get(path):
            if path == "/models":
                return {"models": [{"id": b.model, "capabilities": {"text_in": True}}]}
            if path == "/status":
                return {"state": "ready", "info": {"model": b.model}}
            return {}

        def _post(path, payload):
            b.sent.append(payload)
            return {"end": True, "text": "回复%d" % len(b.sent)}

        b._get = _get
        b._post = _post
    finally:
        L.Stage = orig_stage
    return b


def main():
    b = make_backend(history_turns=2, system_prompt="SYS")

    r1, m1 = b.run("第一问", session="s1")
    ok("首轮回复", r1 == "回复1", r1)
    ok("首轮 meta.turns=1", m1.get("turns") == 1, m1)
    p1 = b.sent[0]["text"]
    ok("首轮 prompt 含 system", p1.startswith("SYS"), p1[:20])
    ok("首轮 prompt 只含当前问", p1.count("用户：") == 1, p1)
    ok("首轮 prompt 以助手引导结尾", p1.rstrip().endswith("助手："), p1[-10:])

    r2, m2 = b.run("第二问", session="s1")
    p2 = b.sent[1]["text"]
    ok("次轮 prompt 带上历史用户提问", "用户：第一问" in p2, p2)
    ok("次轮 prompt 带上历史助手回复", "助手：回复1" in p2, p2)
    ok("次轮 prompt 含当前问", "用户：第二问" in p2, p2)
    ok("次轮 meta.turns=2", m2.get("turns") == 2, m2)

    # 第三个 session 不应看到 s1 的历史
    b.run("别的问", session="s2")
    p3 = b.sent[2]["text"]
    ok("session 分桶隔离", "第一问" not in p3, p3)

    # 窗口裁剪：history_turns=2，第 4 次问时历史里只剩最近 2 轮
    b.run("第三问", session="s1")
    b.run("第四问", session="s1")
    p5 = b.sent[4]["text"]
    ok("历史窗口裁剪（只留最近 2 轮）", "第一问" not in p5 and "第三问" in p5,
       [ln for ln in p5.split("\n") if ln.startswith("用户：")])

    # reset 清桶
    b.reset("s1")
    b.run("重置后", session="s1")
    p6 = b.sent[5]["text"]
    ok("reset(session) 清历史", "第三问" not in p6, p6)

    # history_turns=0 → 单轮
    b0 = make_backend(history_turns=0, system_prompt="SYS")
    b0.run("A", session="x")
    b0.run("B", session="x")
    ok("history_turns=0 退化为单轮", "用户：A" not in b0.sent[1]["text"], b0.sent[1]["text"])

    passed = sum(1 for _, c in PASS if c)
    print("\ntest_llm_multiturn: %d/%d ok" % (passed, len(PASS)))
    return 0 if passed == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())