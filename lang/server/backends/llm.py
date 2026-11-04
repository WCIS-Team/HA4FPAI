#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm.py —— LLM 档。契约：run(text, session) -> (reply, meta)；失败抛给链降级回 asr 文本。

openai   云 API（OpenAI 兼容 chat/completions）。调用形状按 mimo_bridge 板上验证过的来：
         关思考链（reasoning_effort=none，不关白吃 1~8 s）、max_completion_tokens、
         多轮上下文按 session 分桶。配置沿用 MIMO_* 环境变量与 lang/config/klar.yaml 的 mimo 节。
npuserv  板上 NPU 槽位（`lang/npuserv/`，2026-11-04 从 lang/llm/ 移入，本次移到 lang/npuserv，仍 server 管辖）。**只能 HTTP 编排**：CMA 池随 worker 进程
         退出才归还、一次一模型、apply 50-132 s——这个档不装载任何模型，只调 /generate。
         ids-in/ids-out 的件（ERNIE）需要分词器桥，见 README「缺什么」——没有就是明确报错。
echo     自检档：固定回复，不外发。
"""

import json
import os
import threading
import time
import urllib.request

from core import Stage


def _pick(profile, key, env, default=None):
    v = os.environ.get(env)
    if v not in (None, ""):
        return v
    v = profile.get(key)
    return v if v not in (None, "") else default


DEFAULT_SYSTEM = (
    "你是一个中文语音助手，你的回答会被直接转成语音念给用户听。"
    "要求：口语化，一般 1 到 2 句话，不超过 80 个字；"
    "不要用 Markdown、列表、表情符号、括号注释、序号；"
    "不要复述用户的问题；不要解释你在做什么，直接给答案；"
    "拿不准就直说不知道。"
)


class OpenAI(Stage):
    name = "openai"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = dict(self.cfg.online("llm"))          # 在线 LLM 统一在 lang/config/online.yaml
        p.update(self.profile or {})
        self.base = (_pick(p, "base_url", "MIMO_BASE_URL",
                           "https://api.xiaomimimo.com/v1") or "").rstrip("/")
        self.model = _pick(p, "model", "MIMO_LLM_MODEL", "mimo-v2.6-flash")
        self.key_env = p.get("api_key_env", "MIMO_API_KEY")
        self.api_key = (p.get("api_key") or "").strip()   # 兼容把密钥直接写进配置（⚠️ 优先用 env）
        self.keyfile = _pick(p, "keyfile", "KEYFILE", "")
        self.system = os.environ.get("SYSTEM_PROMPT") or p.get("system_prompt", "") or DEFAULT_SYSTEM
        self.history_turns = int(_pick(p, "history_turns", "HISTORY_TURNS", 2) or 2)
        self.max_tokens = int(_pick(p, "max_tokens", "LLM_MAX_TOKENS", 128) or 128)
        self.reasoning = _pick(p, "reasoning", "LLM_REASONING", "none")  # "" = 完全不传
        self.temperature = float(_pick(p, "temperature", "LLM_TEMPERATURE", 0.6) or 0.6)
        self.timeout = float(_pick(p, "timeout", "LLM_TIMEOUT", 60) or 60)
        self._hist = {}                                  # session -> [msgs]
        self._hlock = threading.Lock()

    def _key(self):
        k = os.environ.get(self.key_env) or ""
        if not k and self.api_key:                        # 配置里直接写的（⚠️ 应尽量换成 env）
            k = self.api_key
        if k:
            return k.strip()
        if self.keyfile and os.path.isfile(self.keyfile):  # 密钥文件（板上 /root/funasr/.mimo-key 姿势）
            with open(self.keyfile, encoding="utf-8") as f:
                return f.read().strip()
        raise RuntimeError("缺 API key：环境变量 %s / 配置 api_key / keyfile（密钥不进 config，D-27）" % self.key_env)

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + self._key()})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        ms = int((time.time() - t0) * 1000)
        try:
            txt = obj["choices"][0]["message"]["content"]
            if isinstance(txt, list):
                txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
        except Exception:
            txt = ""
        txt = (txt or "").strip()
        if not txt:
            raise RuntimeError("LLM 返回空内容：%s" % json.dumps(obj, ensure_ascii=False)[:160])
        return txt, ms

    def run(self, text, session=None):
        sid = session or "default"
        with self._hlock:
            hist = self._hist.setdefault(sid, [])
            msgs = [{"role": "system", "content": self.system}]
            if self.history_turns > 0:
                msgs += hist[-(self.history_turns * 2):]
            msgs.append({"role": "user", "content": text})
        payload = {"model": self.model, "messages": msgs, "stream": False,
                   "temperature": self.temperature, "top_p": 0.95,
                   "max_completion_tokens": self.max_tokens,
                   "frequency_penalty": 0, "presence_penalty": 0}
        if self.reasoning:                                # none＝关思考链（板上实测必须关）
            payload["reasoning_effort"] = self.reasoning
        reply, ms = self._post(payload)
        with self._hlock:
            hist.extend([{"role": "user", "content": text},
                         {"role": "assistant", "content": reply}])
            keep = self.history_turns * 2
            if len(hist) > keep > 0:
                del hist[:len(hist) - keep]
        return reply, {"llm_ms": ms, "llm_model": self.model, "session": sid}

    def reset(self, session=None):
        with self._hlock:
            if session:
                self._hist.pop(session, None)
            else:
                self._hist.clear()


class NpuServ(Stage):
    """npuserv 槽位编排档：/status 探就绪 → /generate。不 load、不 unload（那是运维动作）。"""
    name = "npuserv"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = self.profile
        self.base = (_pick(p, "base_url", "NPUSERV_URL", "http://127.0.0.1:18080") or "").rstrip("/")
        self.model = _pick(p, "model", "NPUSERV_MODEL", "")
        if not self.model:
            raise ValueError("npuserv 档要给 model（llm_profiles.npuserv.model 或 NPUSERV_MODEL）")
        self.max_new = int(_pick(p, "max_new", "NPUSERV_MAX_NEW", 128) or 128)
        self.timeout = float(_pick(p, "timeout", "NPUSERV_TIMEOUT", 900) or 900)
        self.wait_s = int(_pick(p, "wait_s", "NPUSERV_WAIT_S", 10) or 10)

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def run(self, text, session=None):
        models = self._get("/models")
        entry = next((m for m in models.get("models", []) if m.get("id") == self.model), None)
        if entry is None:
            raise RuntimeError("npuserv 没有 %r 这个件：%s"
                               % (self.model, [m.get("id") for m in models.get("models", [])]))
        caps = entry.get("capabilities") or {}
        if not caps.get("text_in", False):
            raise RuntimeError(
                "%r 是 ids-in/ids-out 件（capabilities.text_in=false）：需要先接分词器桥"
                "（tokenizer 进 /generate 的 ids）——这是登记在案的缺口，见 README" % self.model)
        st = self._get("/status")
        if st.get("state") != "ready" or (st.get("info") or {}).get("model") not in (None, self.model):
            raise RuntimeError("npuserv 未就绪（state=%s）：先 POST /load %r"
                               % (st.get("state"), self.model))
        t0 = time.time()
        obj = self._post("/generate", {"model": self.model, "text": text,
                                       "max_new": self.max_new, "stream": False,
                                       "wait_s": self.wait_s})
        end = obj.get("end") or obj                       # 兼容直接回 end object 的形状
        reply = None
        for k in ("text", "reply", "content", "gen_text"):
            v = end.get(k) if isinstance(end, dict) else None
            if isinstance(v, str) and v.strip():
                reply = v.strip()
                break
        if reply is None:
            keys = sorted(end.keys()) if isinstance(end, dict) else type(end).__name__
            raise RuntimeError("npuserv 回包里抠不到文本（end=%s）：ids-out 件要接分词器桥，见 README"
                               % keys)
        return reply, {"llm_ms": int((time.time() - t0) * 1000),
                       "llm_model": self.model, "npuserv": True}

    def reset(self, session=None):
        pass                                              # 多轮是 adapter 层的事，这里无状态


class Echo(Stage):
    """自检档：固定回复（多句，给分词器留活）。"""
    name = "echo"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        self.reply = self.profile.get("reply") or (
            "好的，这是自检回复。第二句用来切分。"
            "最后来一句长一点的，看看分词器会不会把它切开，给 TTS 留几个小块。")

    def run(self, text, session=None):
        return self.reply, {"llm_ms": 0, "llm_model": "echo", "session": session or "default"}

    def reset(self, session=None):
        pass
