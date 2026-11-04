#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""router.py —— ASR 之后的意图路由器：把识别文本交给 klar 的 NLP，决定接下来怎么走。

契约：`run((text, session)) -> {"action": "llm"|"fixed"|"verify", "reply": "...", "intent": "...", "slots": {}}`

三档：
  `none`（`chain.router: none`，本文件不注册）——本级跳过，等价 llm；
  `klar`  —— 调 klar NLU（HTTP），把 intent/action 映射成三选一；**任何失败一律回落 llm**（不炸链）；
  `echo`  —— 自检档：恒 `action=llm`。

判定顺序（`klar` 档）：
  1. 响应里已有 `action ∈ {llm,fixed,verify}` ⇒ 直接采用；
  2. 否则看 `intent`：先比 `router_profiles.klar.actions` 表（intent → action），
     再按子串兜底（含 `verify`→verify、含 `fixed`/`reply`→fixed），默认 llm；
  3. `reply` 取响应里的 `reply` 或 `text`。

`verify` 分支**还没实装**：`core.Chain` 见到 `action=verify` 会留空并跳过（她 2026-11-04 定）。

配置（`lang/config/klar.yaml`）：
  klar.nlu.http_port / klar.nlu.path      klar NLU 的地址（默认 127.0.0.1:10520 + /parse）
  router_profiles.klar.url/timeout/actions 可选覆盖
"""

import json
import os
import time
import urllib.request

from core import Stage


def _env(k):
    return os.environ.get(k) or None


class KlarRouter(Stage):
    name = "klar"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = self.profile or {}
        nlu = self.cfg.section("klar").get("nlu", {}) or {}
        port = nlu.get("http_port", "10520")
        self.url = (_env("KLAR_NLU_URL") or p.get("url")
                    or "http://127.0.0.1:%s" % port).rstrip("/")
        if not self.url.endswith(p.get("path", "/parse") or "/parse"):
            self.url += p.get("path", "/parse") or "/parse"
        self.timeout = float(p.get("timeout", 3) or 3)
        self.actions = p.get("actions") or {}       # intent -> llm|fixed|verify
        self.session_field = p.get("session_field", "session")

    # ---- 映射 --------------------------------------------------------------
    def _map(self, obj):
        act = (obj.get("action") or "").strip().lower()
        if act in ("llm", "fixed", "verify"):
            return act
        intent = (obj.get("intent") or "").strip().lower()
        for want, target in (self.actions or {}).items():
            if isinstance(target, list):
                if intent in [str(x).lower() for x in target]:
                    return want
            elif intent == str(target).lower():
                return want
        if "verify" in intent:
            return "verify"
        if "fixed" in intent or "reply" in intent:
            return "fixed"
        return "llm"

    def run(self, args):
        """args = (text, session)。失败回落 llm（不抛）。"""
        text, session = (args + ("", None))[:2]
        payload = {"text": text}
        if session:
            payload[self.session_field] = session
        try:
            req = urllib.request.Request(
                self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except Exception as exc:                    # noqa: BLE001 —— 路由失败不能炸链
            return {"action": "llm", "fallback": "%s: %s" % (type(exc).__name__, exc)[:160]}
        if not isinstance(obj, dict):
            return {"action": "llm", "fallback": "klar 返回不是对象"}
        out = {"action": self._map(obj)}
        if obj.get("reply") or obj.get("text"):
            out["reply"] = (obj.get("reply") or obj.get("text") or "").strip()
        if obj.get("intent"):
            out["intent"] = obj["intent"]
        if obj.get("slots"):
            out["slots"] = obj["slots"]
        return out


class EchoRouter(Stage):
    """自检档：恒走 llm；文本以 `固定:` / `verify:` 开头时给对应分支，便于测三条路。"""
    name = "echo"

    def run(self, args):
        text, _session = (args + ("", None))[:2]
        text = (text or "").strip()
        if text.startswith("固定:") or text.startswith("固定："):
            return {"action": "fixed", "reply": text.split(":", 1)[-1].split("：", 1)[-1].strip()}
        if text.startswith("verify:"):
            return {"action": "verify"}
        return {"action": "llm"}