#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fsm.py —— 对话状态机（turn-taking）**骨架**：纯逻辑、无 I/O、可单测。

为什么要有它：现在的链路是"一次请求跑完整条链"，没有"轮次"的概念。真语音助手要管：
  空闲(idle) --唤醒--> 收音(listening) --说完了--> 思考(thinking) --想好了--> 播报(speaking) --播完--> 空闲
外加：播报中被**打断(barge_in)**回到收音；各状态的**超时**；出错进 error 再恢复。

本文件只负责"该处在什么状态、下一步该做什么（action）"，**不碰音频/模型**。
action 字符串是给上层（`core.Chain` / `wyoming_gateway.py`）的约定：
  start_listen       进收音，开始喂音频
  run_asr            收音结束，跑 ASR
  run_llm            ASR 出文本，跑 LLM（thinking 内自环）
  start_tts          LLM 出回复，跑 TTS 播报
  stop_tts_and_listen 打断：停播报、回到收音
  idle               回空闲
  recover            从 error 恢复
  None               忽略（非法事件，状态不变）

板上/服务侧把它接起来时：拿 `feed()` 返回的 action 去调对应组件即可；接 mic 与扬声器是
另一层的事（板子无麦无声卡，见 PITFALLS）。
"""

import time

STATES = ("idle", "listening", "thinking", "speaking", "error")

# (当前状态, 事件) -> (新状态, action)。表驱动，易扩展。
TRANSITIONS = {
    ("idle", "wake"): ("listening", "start_listen"),
    ("listening", "speech_end"): ("thinking", "run_asr"),
    ("listening", "timeout"): ("idle", "idle"),
    ("thinking", "asr_done"): ("thinking", "run_llm"),   # 自环：状态不变，但建议跑 LLM
    ("thinking", "llm_done"): ("speaking", "start_tts"),
    ("thinking", "timeout"): ("error", "recover"),
    ("speaking", "tts_done"): ("idle", "idle"),
    ("speaking", "barge_in"): ("listening", "stop_tts_and_listen"),
    ("speaking", "timeout"): ("idle", "idle"),
}

DEFAULT_TIMEOUT_MS = {
    "idle": None,
    "listening": 8000,
    "thinking": 15000,
    "speaking": 60000,
    "error": None,
}


class DialogueFSM(object):
    def __init__(self, clock=None, timeout_ms=None):
        self._clock = clock or time.monotonic
        self.state = "idle"
        self.timeout_ms = dict(DEFAULT_TIMEOUT_MS)
        if timeout_ms:
            self.timeout_ms.update(timeout_ms)
        self._entered = self._clock()
        self.history = []                       # [(t, event, from, to, action)]

    def _now(self, now):
        return self._clock() if now is None else now

    def _enter(self, new_state, now):
        self.state = new_state
        self._entered = now

    def feed(self, event, now=None):
        """喂一个事件，返回 (new_state, action)。非法事件不迁移、action=None。"""
        now = self._now(now)
        src = self.state

        if event == "reset":                    # 任意状态都能复位
            self._enter("idle", now)
            self.history.append((now, event, src, "idle", "recover"))
            return "idle", "recover"

        if event == "error":                    # 任意状态都能进 error
            self._enter("error", now)
            self.history.append((now, event, src, "error", "recover"))
            return "error", "recover"

        key = (src, event)
        if key not in TRANSITIONS:
            self.history.append((now, event, src, src, None))
            return src, None

        dst, action = TRANSITIONS[key]
        same = (dst == src)
        if not same:
            self._enter(dst, now)
        else:
            # 自环也应刷新停留计时（例如 thinking 里 asr_done→run_llm 后重新计时）
            self._entered = now
        self.history.append((now, event, src, dst, action))
        return dst, action

    def tick(self, now=None):
        """按当前状态的超时判是否该喂 timeout。返回 feed 的结果；没超时返回 (state, None)。"""
        now = self._now(now)
        ms = self.timeout_ms.get(self.state)
        if ms is not None and (now - self._entered) * 1000.0 >= ms:
            return self.feed("timeout", now=now)
        return self.state, None

    @property
    def elapsed_ms(self):
        return int((self._clock() - self._entered) * 1000)
