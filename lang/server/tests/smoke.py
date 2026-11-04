#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke.py —— lang/server 的无依赖自检（纯标准库，不外发、不装载模型）。

python -X utf8 tests/smoke.py            # 从 server/ 目录用相对路径跑，或任意 cwd

覆盖：config 解析 → audioio 单元（重采样/拼接/wav 进出）→ 分词器切句 →
全链 run_audio（echo asr/llm + sine tts + punct，含 kws 门控短路）→
HTTP 层 8 条端点（本进程起一个临时端口）。

**档位是自检自己钉住的**：`pin_selftest_config()` 把真配置目录复制到临时目录并追加
`zz-selftest-overlay.yaml`（chain → asr:echo / llm:echo / tts:sine）。生产默认 `chain.asr`
已改 `sherpa`（本地真模型），本条自检不跟着走——它必须能在一台没模型、没网的机器上跑通。
"""

import io
import json
import sys
import threading
import urllib.error
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent        # server/
sys.path.insert(0, str(HERE))

import cfg as cfg_mod                                # noqa: E402
from cfg import Config                               # noqa: E402
from core import Chain, WORK_RATE                    # noqa: E402
from backends import audioio                         # noqa: E402
from backends.text import Punct, TtsSplit            # noqa: E402
import app as app_mod                                # noqa: E402

PASS = []


def pin_selftest_config():
    """把自检钉在 echo/sine 档上，**不依赖生产默认**（2026-11-04）。

    为什么：`lang/config/server.yaml` 的 `chain.asr` 生产默认已改 `sherpa`（本地真模型，
    装载 ~8 s、要 160 MB 权重）。smoke 的定位是"纯标准库、不外发、不装载模型"，
    所以它必须**自己**把 chain 钉回自检档，否则一旦生产档换成真模型，这条自检就变成
    "有模型才能跑"，失去无依赖的性质。

    做法：真配置目录整份复制到临时目录，最后追加一个按文件名排序最靠后的覆盖文件
    （`cfg.Config` 按文件名升序深合并 ⇒ `zz-*` 必赢），再把 `LANG_CONFIG_DIR` 指过去。
    """
    import os
    import shutil
    import tempfile

    src = cfg_mod.find_config_dir()
    if not src:
        return None
    tmp = tempfile.mkdtemp(prefix="smoke-cfg-")
    for fn in sorted(os.listdir(src)):
        if fn.endswith((".yaml", ".yml")):
            shutil.copyfile(os.path.join(src, fn), os.path.join(tmp, fn))
    with io.open(os.path.join(tmp, "zz-selftest-overlay.yaml"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(
            "# smoke 专用覆盖：自检不依赖生产默认档（不装载模型、不外发）\n"
            "chain:\n"
            "  asr: echo\n"
            "  llm: echo\n"
            "  tts: sine\n")
    os.environ["LANG_CONFIG_DIR"] = tmp
    print("[note] 自检配置：%s（覆盖 chain → echo/echo/sine）" % tmp)
    return tmp


def ok(name, cond, detail=""):
    PASS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("ok" if cond else "FAIL", name, detail))


# ---------------------------------------------------------------- 1. config
def t_config():
    c = Config()
    import os as _os
    ok("config 定位（目录或单文件）",
       bool(c.path) and (_os.path.isdir(c.path) or c.path.endswith(".yaml")), str(c.path))
    ok("config 多源(目录)", len(c.sources) >= 9, "sources=%d" % len(c.sources))
    ok("config online.mimo 保留", c.online("mimo").get("llm_reasoning", "") == "none")
    ok("config online.asr/llm/tts", all(c.online(k) for k in ("asr", "llm", "tts")),
       "asr/llm/tts")
    ok("config server 节", c.get("server", "port", "") == "18091")
    ok("config chain.asr（自检固定 echo，非生产默认）", c.get("chain", "asr", "") == "echo",
       "生产默认见 server.yaml；本进程被 zz-selftest-overlay.yaml 钉在 echo")


# ---------------------------------------------------------------- 2. audioio
def t_audioio():
    wav = audioio.encode_wav(audioio.sine(16000, 1.0), 16000)
    smp, rate = audioio.decode_payload(wav)
    ok("wav 进出", rate == 16000 and len(smp) == 16000, "rate=%d n=%d" % (rate, len(smp)))

    s48 = audioio.sine(48000, 1.0)
    d3, r = audioio.to_work_rate(s48, 48000, WORK_RATE)
    ok("48k→16k decimate3", r == 16000 and 15000 < len(d3) <= 16000, "n=%d" % len(d3))
    lin = audioio.resample_linear(s48, 48000, 16000)
    ok("linear 长度", len(lin) == 16000, "n=%d" % len(lin))

    a = audioio.sine(22050, 0.5)
    b = audioio.sine(16000, 0.5)
    m, mr = audioio.concat([(a, 22050), (b, 16000)], gap_s=0.08, out_rate=48000)
    ok("拼接+SAR 输出重采样", mr == 48000 and len(m) > 40000, "rate=%d n=%d" % (mr, len(m)))

    # 多声道混缩
    import array
    import struct
    buf = io.BytesIO()
    import wave
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", [16384, -16384] * 100).tobytes())
    smp, rate = audioio.decode_payload(buf.getvalue())
    ok("立体声混缩", rate == 16000 and len(smp) == 100 and abs(smp[0]) < 1e-6,
       "n=%d smp0=%.4f" % (len(smp), smp[0]))


# ---------------------------------------------------------------- 3. tokenizer
def t_tokenizer():
    text = ("第一句话到这里。第二句也完了！最后来一句特别长的，中间有逗号，还有内容，"
            "一直长到超过二十个字符上限看看会怎样处理掉")

    # 原来的 punct 档（已搬到 lang/split/legacy_tiers.py，行为未改）
    p = Punct(None, {"max_chars": 20}, None)
    chunks = p.run(text)
    ok("切句：句末符", len(chunks) >= 4 and chunks[0] == "第一句话到这里。", str(chunks[:2]))
    ok("切句：块长上限", all(len(c) <= 21 for c in chunks),
       "max=%d" % max(len(c) for c in chunks))

    # 新默认档 tts_split（2026-11-04 她定：超过 20 字强制切，且绝不超）
    n = TtsSplit(None, {"max_chars": "20", "strict": "1"}, None)
    nchunks = n.run(text)
    ok("切句新档：严格 <= 20", all(len(c) <= 20 for c in nchunks), str(nchunks))
    ok("切句新档：内容守恒", "".join(nchunks) == text, str(nchunks))

    from backends import REGISTRY
    ok("分词器三档已登记", {"tts_split", "punct", "window"} <= set(REGISTRY["tokenizer"]),
       str(sorted(REGISTRY["tokenizer"])))
    cfg = Config()
    ok("chain.tokenizer 默认 tts_split", cfg.get("chain", "tokenizer", "") == "tts_split",
       cfg.get("chain", "tokenizer", ""))
    ok("split.yaml 的 max_chars 生效",
       cfg.profile("tokenizer", "tts_split").get("max_chars") == "20",
       str(cfg.profile("tokenizer", "tts_split")))


# ---------------------------------------------------------------- 4. 全链
def t_chain():
    c = Config()
    chain = Chain(c)
    wav_in = audioio.encode_wav(audioio.sine(48000, 1.0, 220.0), 48000)

    out = chain.run_audio(wav_in)
    ok("全链：wake", out.get("wake") is True)
    ok("全链：asr 文本", "自检" in (out.get("asr_text") or ""), out.get("asr_text", "")[:20])
    ok("全链：llm 走了", out.get("llm_ok") is True and "自检回复" in (out.get("text") or ""))
    ok("全链：切了多块", len(out.get("chunks") or []) >= 3, str(out.get("chunks"))[:60])
    ok("全链：出音频 48k", out.get("audio_rate") == 48000 and len(out.get("audio_b64") or "") > 1000,
       "rate=%s ms=%s" % (out.get("audio_rate"), out.get("audio_ms")))
    ok("全链：各段计时", all(k in out.get("stages_ms") for k in ("sar", "asr", "llm")),
       str(out.get("stages_ms")))
    ok("全链：无错误", not out.get("stages_error"), str(out.get("stages_error")))

    # 48k 输入经 sar 降采样：segments 计时存在且工作率对
    ok("全链：sar 生效", out["stages_ms"].get("sar", 999) < 999 and out.get("segments") == 1)

    # llm=0：LLM 跳过，text 直接落 asr 文本
    out2 = chain.run_audio(wav_in, opts={"llm": False})
    ok("llm=0 降级", out2.get("llm_ok") is None and "自检" in (out2.get("text") or ""))

    # kws 门控：假 KWS 恒不放行 → 短路，无 asr 文本
    class FakeKws(object):
        lock = threading.Lock()

        def run(self, segs):
            return False, "nohit"

    chain._stages[("kws", chain.active("kws"))] = FakeKws()
    out3 = chain.run_audio(wav_in)
    ok("kws 门控短路", out3.get("wake") is False and not out3.get("asr_text"))
    del chain._stages[("kws", chain.active("kws"))]

    # 顺序：kws 先于 vad（她 2026-11-04 定）——注掉真 rms，注入探针记录调用序
    c2 = Config()
    c2.data["chain"]["vad"] = "rms"
    ch2 = Chain(c2)
    calls = []

    class Probe(object):
        lock = threading.Lock()

        def __init__(self, name):
            self.name = name

        def run(self, *a):
            calls.append(self.name)
            return (True, "ok") if self.name == "kws" else [a[0]]

    ch2._stages[("kws", ch2.active("kws"))] = Probe("kws")
    ch2._stages[("vad", ch2.active("vad"))] = Probe("vad")
    try:
        ch2.run_audio(wav_in)
    finally:
        del ch2._stages[("kws", ch2.active("kws"))]
        del ch2._stages[("vad", ch2.active("vad"))]
    ok("顺序: kws 先于 vad", calls[:2] == ["kws", "vad"], str(calls))

    # router 接线：fixed 跳过 LLM 用 klar 的 reply 走分词+TTS；verify 留空跳过
    c3 = Config()
    c3.data["chain"].update(router="echo", asr="echo", tts="sine")

    class _FakeAsr(object):
        def __init__(self, text):
            self.text = text

        def run(self, segs):
            return self.text, []

    ch3 = Chain(c3)
    ch3._stages[("asr", "echo")] = _FakeAsr("固定:现在几点")
    o5 = ch3.run_audio(wav_in)
    ok("router fixed: 用固定回应且跳过 LLM",
       o5.get("route") == "fixed" and o5.get("text") == "现在几点" and o5.get("llm_ok") is None,
       str(o5.get("route")))
    ch3._stages[("asr", "echo")] = _FakeAsr("verify:开门")
    o6 = ch3.run_audio(wav_in)
    ok("router verify: 留空跳过", o6.get("route") == "verify" and o6.get("verify_skipped") is True)

    # 空音频
    out4 = chain.run_audio(b"")
    ok("空音频拒绝", out4.get("code") == 1)


# ---------------------------------------------------------------- 5. HTTP 层
def t_http():
    c = Config()
    app_mod._CHAIN = Chain(c)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app_mod.Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % port
    wav = audioio.encode_wav(audioio.sine(16000, 0.5, 300.0), 16000)

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    try:
        st, obj = get("/health")
        ok("HTTP /health", st == 200 and obj.get("ok") is True and obj["stages"]["asr"] == "echo")
        st, obj = get("/components")
        ok("HTTP /components", st == 200 and "passthrough" in obj["kws"]["available"])

        bnd = "xXsmokeXx"
        mp = (("--%s\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\n"
               "Content-Type: audio/wav\r\n\r\n" % bnd).encode() + wav +
              ("--%s--\r\n" % bnd).encode())
        req = urllib.request.Request(base + "/api/v1/chain", data=mp, method="POST",
                                     headers={"Content-Type":
                                              "multipart/form-data; boundary=" + bnd})
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read().decode("utf-8"))
        ok("HTTP 全链", obj.get("code") == 0 and obj.get("audio_rate") == 48000,
           "total_ms=%s" % obj.get("total_ms"))

        req = urllib.request.Request(base + "/api/v1/asr", data=mp, method="POST",
                                     headers={"Content-Type":
                                              "multipart/form-data; boundary=" + bnd})
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read().decode("utf-8"))
        ok("HTTP funasr 兼容", obj.get("code") == 0 and "自检" in obj.get("text", ""),
           str(obj)[:80])

        req = urllib.request.Request(base + "/v1/audio/transcriptions", data=mp, method="POST",
                                     headers={"Content-Type":
                                              "multipart/form-data; boundary=" + bnd})
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read().decode("utf-8"))
        ok("HTTP openai 兼容", "自检" in obj.get("text", ""))

        req = urllib.request.Request(
            base + "/llm", data=json.dumps({"text": "你好"}).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            obj = json.loads(r.read().decode("utf-8"))
        ok("HTTP /llm", obj.get("code") == 0 and "自检回复" in obj.get("text", ""))

        req = urllib.request.Request(
            base + "/tts", data=json.dumps({"text": "一句话。再一句话！"}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read()
        ok("HTTP /tts 出 wav", ctype.startswith("audio/wav") and raw[:4] == b"RIFF",
           "%d bytes" % len(raw))

        req = urllib.request.Request(base + "/reset", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            obj = json.loads(r.read().decode("utf-8"))
        ok("HTTP /reset", obj.get("code") == 0)

        try:
            get("/nope")
            ok("HTTP 404", False)
        except urllib.error.HTTPError as e:
            ok("HTTP 404", e.code == 404)
    finally:
        srv.shutdown()


def t_runtime():
    """单运行时：sherpa-onnx 是唯一 onnx 运行时；pip onnxruntime 默认被禁。"""
    import sys as _sys
    from core import SharedEnv
    c = Config()
    env = SharedEnv(c)
    ok("运行时: sherpa 可拉起", bool(env.sherpa()))
    ok("运行时: 未 import pip onnxruntime", "onnxruntime" not in _sys.modules)
    blocked = False
    try:
        env.ort()
    except RuntimeError:
        blocked = True
    ok("运行时: pip ORT 被禁(single_runtime)", blocked)
    ch = Chain(c)
    w = ch.warm()
    ok("运行时: warm 拉起 sherpa", w.get("sherpa") is True, str(w.get("elapsed_ms")) + "ms")


def t_router():
    """router 级：ASR 后交给 klar NLP 定去向；echo 档三条路都能测。"""
    from backends.router import EchoRouter
    c = Config()
    r = EchoRouter(c, {}, None)
    ok("router: 默认 llm", r.run(("你好", None))["action"] == "llm")
    dec = r.run(("固定:现在几点", None))
    ok("router: fixed 带回应", dec["action"] == "fixed" and dec.get("reply") == "现在几点", str(dec))
    ok("router: verify", r.run(("verify:开门", None))["action"] == "verify")
    from backends import REGISTRY
    ok("router 两档已登记", {"klar", "echo"} <= set(REGISTRY["router"]),
       str(sorted(REGISTRY["router"])))


def main():
    print("=" * 68)
    print("lang/server smoke —— python %s" % sys.version.split()[0])
    print("=" * 68)
    pin_selftest_config()      # 先把 chain 钉回 echo/sine，再读配置（顺序不能反）
    t_config()
    t_runtime()
    t_router()
    t_audioio()
    t_tokenizer()
    t_chain()
    t_http()
    fails = [p for p in PASS if not p[1]]
    print("-" * 68)
    print("通过 %d / %d" % (len(PASS) - len(fails), len(PASS)))
    if fails:
        for name, _, detail in fails:
            print("  FAIL: %s %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
