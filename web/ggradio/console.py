#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""console.py —— lang 语音链的"试验台"：参数可调 + 逐段可跑可下载。

两个能力（2026-11-04 她定）：

  ① 参数面板：把 `lang/config/*.yaml` 里**每一个**参数列出来（说明取自 YAML 注释），
     在网页上直接改、直接写回原文件（保留注释与顺序，不做 YAML 重排）。
  ② 逐段试验台：`lang/server` 的七级（sar/vad/kws/asr/llm/tokenizer/tts）**任选子集**，
     上传音频（或文本）→ 跑你勾的那几级 → 每一级的产物都能下载：
        上传音频 → 只过 KWS → 下载结果 JSON
        上传音频 → KWS → ASR → 下载文本
        文本 → LLM → 分词 → TTS → 下载 wav
        （全链：勾满七级）

为什么不用 Gradio：import gradio 会 eager 拉 pandas/matplotlib/fastapi，
RSS 250–400 MB；本文件纯标准库，RSS ≈ 18–25 MB，py3.8/3.13 通跑。

链路是**进程内**跑的（直接 import lang/server 的 core.Chain），不经过 HTTP，
所以逐段粒度是真的逐段；默认档（echo/sine/none/passthrough/punct）不需要任何模型。

起法：
  python console.py                          # 自动找 ../../lang（或 --lang-dir）
  python console.py --lang-dir S:/work/WCIS/repos/HA4FPAI/lang --port 7861
  python console.py --host 0.0.0.0           # 给别的机器看（麦克风仍受 HTTPS 限制）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "page.html"

# ---------------------------------------------------------------- lang 定位
def find_lang_dir(explicit=None):
    """--lang-dir > LANG_DIR > 从本文件向上找 repos/HA4FPAI/lang。"""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("LANG_DIR"):
        cands.append(Path(os.environ["LANG_DIR"]))
    p = HERE
    for _ in range(6):
        cands.append(p / "lang")
        cands.append(p.parent / "lang")
        p = p.parent
    for c in cands:
        try:
            if (c / "config").is_dir() and (c / "server").is_dir():
                return c.resolve()
        except OSError:
            continue
    return None


LANG_DIR = None
SERVER_DIR = None
CHAIN = None
CHAIN_LOCK = threading.Lock()
# 产物落临时目录（**不进库**）：这里出的是试验台的中间件，随时可删。
ARTIFACTS = Path(os.environ.get("CONSOLE_ARTIFACTS")
                 or (Path(__import__("tempfile").gettempdir()) / "lang-console-artifacts"))


def bootstrap(lang_dir):
    """把 lang/server 塞进 sys.path 并装配 Chain（惰性：档位组件用到才建）。"""
    global LANG_DIR, SERVER_DIR, CHAIN
    LANG_DIR = Path(lang_dir).resolve()
    SERVER_DIR = LANG_DIR / "server"
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    import cfg as langcfg            # noqa: E402  lang/server/cfg.py
    import core                      # noqa: E402  lang/server/core.py
    cfgdir = LANG_DIR / "config"
    CHAIN = core.Chain(langcfg.Config(str(cfgdir)))
    try:
        snapshot_startup()
    except Exception:
        pass
    return CHAIN


def reload_chain():
    with CHAIN_LOCK:
        return bootstrap(LANG_DIR)


# ---- 恢复默认：把九个 YAML 回到控制台**启动时**读到的样子 ----------------
# 语义＝"这个会话改坏了，回到我开 console 那一刻的配置"（没有更权威的"出厂值"）。
_STARTUP = {}


def snapshot_startup():
    _STARTUP.clear()
    for fp in config_files():
        _STARTUP[fp.name] = {p["path"]: p["value"] for p in parse_file(fp)}


def reset_defaults():
    """把每个文件里与启动快照不一致的参数写回去。返回写回的条目数。"""
    n = 0
    for fname, orig in _STARTUP.items():
        try:
            now = {p["path"]: p["value"] for p in parse_file(LANG_DIR / "config" / fname)}
        except Exception:
            continue
        for path, val in orig.items():
            if now.get(path) != val and val != "***":
                try:
                    write_param(fname, path, val)
                    n += 1
                except Exception:
                    pass
    reload_chain()
    return n


# ---------------------------------------------------------------- YAML 参数解析
# 只用行解析：这些 YAML 全是"嵌套 map + 标量 + 注释"，没有列表/锚点。
# 好处＝能把**注释当说明**取出来，且写回时保留注释与顺序（PyYAML 做不到）。

_LINE = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+)\s*:\s*(.*)$")
_SECRET_KEYS = ("api_key",)


def _split_inline_comment(rest):
    """'0.5  # 门限' → ('0.5', '门限')；引号内的 # 不算注释。"""
    quote = None
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "#" and (i == 0 or rest[i - 1] in " \t"):
            return rest[:i].rstrip(), rest[i + 1:].strip()
    return rest.rstrip(), ""


def _unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _type_of(v, raw):
    q = raw.strip().startswith(("'", '"'))
    if q:
        return "str"
    low = v.strip().lower()
    if low in ("true", "false"):
        return "bool"
    if re.match(r"^-?\d+$", v.strip()):
        return "int"
    if re.match(r"^-?\d*\.\d+$", v.strip()):
        return "float"
    return "str"


def _mask(key, value):
    if any(key.endswith(s) for s in _SECRET_KEYS) and value:
        return "***"
    return value


def parse_file(path):
    """→ [{path,key,value,type,desc,line,pre,comment,secret}]（pre/comment 供写回用）。

    desc 的拼法：**最近的 section 行内注释（带 [档名] 前缀）** ＋ 上方整行注释 ＋ 本行行内注释。
    之所以要 section 注释：`asr_profiles.sherpa:` 那行写着「本地流式 zipformer（板端路径…）」，
    它下面的 9 个参数自己没注释——不继承的话参数面板的"说明"列全是空的。
    """
    text = path.read_text("utf-8")
    lines = text.split("\n")
    stack = []          # [(indent, key, section_desc)]
    pending = []        # 连续注释行缓冲
    out = []
    for no, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            pending = []
            continue
        if s.startswith("#"):
            pending.append(s.lstrip("#").strip())
            continue
        m = _LINE.match(raw)
        if not m:
            pending = []
            continue
        indent = len(m.group(1))
        key = m.group(2)
        rest = m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join([k for _, k, _ in stack] + [key])
        value_raw, inline = _split_inline_comment(rest)
        if value_raw == "":
            # section（`chain:` / `sherpa:`）；行内注释＝这一节的说明，给子项继承
            stack.append((indent, key, inline or (pending[0] if pending else "")))
            pending = []
            continue
        sec_desc = ""
        for _i, sk, sd in reversed(stack):
            if sd:
                sec_desc = "[%s] %s" % (sk, sd)
                break
        parts = []
        if sec_desc:
            parts.append(sec_desc)
        parts.extend(pending)
        if inline:
            parts.append(inline)
        value = _unquote(value_raw)
        out.append({
            "path": full, "key": key, "value": _mask(key, value),
            "type": _type_of(value, value_raw),
            "desc": " · ".join([x for x in parts if x]), "line": no,
            "pre": raw[:m.start(3)], "comment": rest[len(value_raw):],
            "secret": any(key.endswith(x) for x in _SECRET_KEYS),
            "quoted": value_raw.strip().startswith(("'", '"')),
        })
        pending = []
    return out


def tts_combo_items(front, ac, voc, integ):
    """TTS 组合 → 要写回的 (文件, 参数, 值) 清单；非法组合直接拒（前端已不给选，后端再兜一道）。"""
    front, ac, voc, integ = (front or "", ac or "", voc or "", integ or "")
    if front:
        if integ:
            raise ValueError("前端非空时不能选一体 TTS（mel+vits / mel+ekho 不存在）")
        if ac != "matcha":
            raise ValueError("前端 %s 只能配声学 matcha" % front)
        if voc not in ("vocos",):
            raise ValueError("声码器只能是 vocos（HiFi-GAN 权重还没配到 tts.yaml ⇒ 红：不可用）")
        return [("server.yaml", "chain.tts", "sherpa"),
                ("tts.yaml", "tts_profiles.sherpa.kind", "matcha")]
    if ac or voc:
        raise ValueError("前端为空时不能选声学/声码器；请选一体 TTS")
    if integ == "edge":
        return [("server.yaml", "chain.tts", "edge")]
    if integ not in ("edge", "ekho"):
        raise ValueError("一体 TTS 只能是 edge 或 ekho（piper 档与服务已于 2026-11-04 删除）")
    return [("server.yaml", "chain.tts", "http"),
            ("online.yaml", "online.tts.proto", integ)]


def config_files():
    d = LANG_DIR / "config"
    return sorted([p for p in d.iterdir() if p.suffix in (".yaml", ".yml")])


def chain_options():
    """每级可选档（权威来源＝lang/server/backends/__init__.py 的 REGISTRY）。"""
    try:
        from backends import REGISTRY
        return {k: sorted(v.keys()) for k, v in REGISTRY.items()}
    except Exception:
        return {}


# ---- 选项配色 + TTS 组合（2026-11-04 她定）-------------------------------
# 颜色口径：bad=红（不可用/未实测）；warn=黄（实测可用但非交付）；无=正常（现役交付，如 ekho）。
LLM_LOCAL_MODELS = ["ernie-4.5-0.3b", "qwen3-0.6b", "mock"]   # npuserv 的 3 个本地件 → 全红
TONE = {
    "llm_profiles.npuserv.model": dict((m, "bad") for m in LLM_LOCAL_MODELS),
    "online.tts.proto": {"json": "warn"},    # ekho 保持正常色
}

# TTS 组合：前端(mel) × 声学 × 声码器，或前端为空时的一体 TTS。
# **不存在的组合不出现**：mel+vits / mel+ekho 根本不在候选里（她 2026-11-04 定）。
TTS_COMBO = {
    "frontend": [{"v": "", "tone": "warn"},
                 {"v": "sherpa-mel", "tone": "warn"}],
    "acoustic": [{"v": "", "tone": "warn"},
                 {"v": "matcha", "tone": "warn"}],
    "vocoder": [{"v": "", "tone": "warn"},
                {"v": "vocos", "tone": "warn"},
                {"v": "HiFi-GAN-v2", "tone": "warn"}],
    "integrated": [{"v": "edge", "tone": None},
                   {"v": "ekho", "tone": None}],
}
# 实测唯一成功上板的神经组合（整链相对实时 0.716–0.777）；其余见 lang/tts/README.md §3。
TTS_COMBO_OK = {"frontend": "sherpa-mel", "acoustic": "matcha", "vocoder": "vocos"}


# ---- 枚举型参数：给下拉/单选，而不是让人瞎填字符串 -------------------------
# 权威来源优先：chain.* 走 REGISTRY；其余手工登记（值域在 README/para.md 里写死的那几个）。
# 没登记的再从行内注释里"猜"：注释里成串出现 `a | b | c` 且都是标识符样子的，就当选项。
ENUMS = {
    "online.asr.proto": ["funasr", "openai", "json"],
    "online.tts.proto": ["ekho", "json"],
    "online.llm.reasoning": ["none", "low", "medium", "high"],
    "audio.resample_mode": ["decimate3-mavg3", "linear"],
    "sar.resample_mode": ["decimate3-mavg3", "linear"],
    "tts_profiles.sherpa.kind": ["matcha", "vits"],
    "llm_profiles.npuserv.model": LLM_LOCAL_MODELS,   # 3 个本地件（全红）
    "asr_profiles.sherpa.decoding_method": ["greedy_search", "modified_beam_search"],
    "sherpa.provider": ["cpu", "cuda"],
    "klar.nlu.kind": ["klar-ha-nlu"],
    "online.mimo.llm_reasoning": ["none", "low", "medium", "high"],
    "online.edge.decoder": ["miniaudio", "soundfile", "mpg123", "ffmpeg", "auto"],
    "online.edge.voice": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural",
                          "zh-CN-YunjianNeural", "zh-CN-XiaoyiNeural"],
}
_NUM_TIERS = {                      # 纯数字枚举（webrtc 的 mode 0..3）
    "vad_profiles.webrtc.mode": [0, 1, 2, 3],
}
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")


def _options_from_comment(desc):
    """从 'echo | sherpa（本地…）| remote-api（外部…）' 里抠出选项词。"""
    if not desc or "|" not in desc:
        return None
    head = desc.split("·")[0]
    out = []
    for chunk in head.split("|"):
        tok = re.split(r"[（(：:]", chunk.strip())[0].strip().strip("\"'")
        if tok and _TOKEN.match(tok):
            out.append(tok)
    return out if len(out) >= 2 else None


def options_for(path, desc):
    """→ (options, source)：source ∈ registry / curate / comment / None。"""
    parts = path.split(".")
    if len(parts) == 2 and parts[0] == "chain":
        opts = chain_options().get(parts[1])
        if opts:
            return ["none"] + [o for o in opts if o != "none"], "registry"
    if path in ENUMS:
        return list(ENUMS[path]), "curate"
    if path in _NUM_TIERS:
        return list(_NUM_TIERS[path]), "curate"
    guessed = _options_from_comment(desc)
    if guessed:
        return guessed, "comment"
    return None, None


# ---- 数值型参数：横向滑条（2026-11-04 她定：数值不要上下，要左右滑条）----
# 手工登记的量程（min, max, step）；没登记的用 _range_guess 按数量级推。
RANGES = {
    # VAD
    "vad_profiles.silero.threshold": (0.0, 1.0, 0.01),
    "vad_profiles.silero.min_speech_ms": (50, 2000, 50),
    "vad_profiles.sherpa.threshold": (0.0, 1.0, 0.01),
    "vad_profiles.sherpa.min_silence_duration": (0.0, 2.0, 0.05),
    "vad_profiles.sherpa.min_speech_duration": (0.0, 2.0, 0.05),
    "vad_profiles.sherpa.window_samples": (128, 1024, 64),
    "vad_profiles.webrtc.min_speech_ms": (50, 2000, 50),
    "vad_profiles.webrtc.min_gap_ms": (50, 2000, 50),
    "vad_profiles.rms.open_dbfs": (-70.0, -20.0, 1.0),
    "vad_profiles.rms.close_dbfs": (-80.0, -30.0, 1.0),
    "vad_profiles.rms.min_speech_ms": (50, 2000, 50),
    "vad_profiles.rms.min_gap_ms": (50, 2000, 50),
    # KWS
    "kws_profiles.sherpa.keywords_score": (0.0, 5.0, 0.1),
    "kws_profiles.sherpa.keywords_threshold": (0.0, 1.0, 0.01),
    "kws_profiles.sherpa.num_trailing_blanks": (0, 4, 1),
    "kws_profiles.sherpa.num_threads": (1, 4, 1),
    "kws_profiles.sherpa.tail_paddings_s": (0.0, 1.5, 0.01),
    # ASR
    "asr_profiles.sherpa.num_threads": (1, 8, 1),
    "asr_profiles.sherpa.sample_rate": (8000, 48000, 8000),
    "asr_profiles.sherpa.feature_dim": (40, 128, 8),
    # server / env / audio
    "server.out_rate": (0, 48000, 8000),
    "server.chunk_gap_ms": (0, 500, 10),
    "audio.in_sample_rate": (8000, 48000, 8000),
    "audio.work_sample_rate": (8000, 48000, 8000),
    "audio.chunk_ms": (5, 200, 5),
    "env.intra_op_threads": (1, 8, 1),
    # tokenizer
    "tokenizer_profiles.punct.max_chars": (10, 200, 5),
    # TTS
    "tts_profiles.sherpa.num_threads": (1, 8, 1),
    "tts_profiles.sherpa.speed": (0.5, 2.0, 0.05),
    "tts_profiles.sherpa.sid": (0, 10, 1),
    # 在线 API
    "online.asr.timeout": (5, 300, 5),
    "online.asr.retry": (0, 5, 1),
    "online.llm.max_tokens": (16, 2048, 16),
    "online.llm.history_turns": (0, 10, 1),
    "online.tts.length_scale": (0.5, 2.0, 0.05),
    "online.tts.timeout": (5, 300, 5),
    # sherpa 家族共享
    "sherpa.num_threads_default": (1, 8, 1),
}


def _range_guess(ptype, value):
    """没有登记量程时按数量级推一个可用的 (min, max, step)。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if ptype == "float":
        if -1.0 <= v <= 1.0:
            return (-1.0, 1.0, 0.01)
        if 0.0 <= v <= 10.0:
            return (0.0, 10.0, 0.1)
        if 0.0 <= v <= 100.0:
            return (0.0, 100.0, 1.0)
        m = max(abs(v) * 2, 10.0)
        return (-m, m, m / 50.0)
    m = max(int(abs(v)) * 2, 10)
    return (0, m, 1)


def range_for(path, ptype, value):
    """→ (min, max, step) | None（非数值或推不出就不给滑条）。"""
    if path in RANGES:
        return list(RANGES[path])
    if ptype not in ("int", "float"):
        return None
    return _range_guess(ptype, value)


def _fmt_value(ptype, value, quoted):
    if ptype == "bool":
        return "true" if str(value).strip().lower() in ("1", "true", "yes", "on") else "false"
    if ptype in ("int",):
        return str(int(float(value)))
    if ptype == "float":
        return repr(float(value)) if "." in str(value) or "e" in str(value).lower() else str(float(value))
    v = str(value)
    if quoted:
        q = '"' if '"' not in v else "'"
        return q + v + q
    if v == "" or re.search(r"[:#\s\"']", v):
        return '"' + v.replace('"', '\\"') + '"'
    return v


def write_param(fname, path, value):
    """按行改写一个参数，保留缩进/注释/其余行原样。"""
    fp = LANG_DIR / "config" / fname
    lines = fp.read_text("utf-8").split("\n")
    params = {p["path"]: p for p in parse_file(fp)}
    p = params.get(path)
    if not p:
        raise KeyError("参数不存在: %s" % path)
    idx = p["line"] - 1
    new = p["pre"] + _fmt_value(p["type"], value, p["quoted"]) + p["comment"]
    lines[idx] = new
    fp.write_text("\n".join(lines), "utf-8")
    return new


# ---------------------------------------------------------------- 逐段运行
STAGE_ORDER = ("sar", "rms", "kws", "kws_route", "vad", "asr", "router", "spk",
               "llm", "tokenizer", "tts")
STAGE_IO = {
    "sar": ("audio", "audio", "音频前处理/重采样（48k→16k 走 decimate3）"),
    "rms": ("audio", "json", "能量门/电平（KWS 之前；整段静音直接短路）"),
    "kws": ("audio", "json", "唤醒门控（passthrough＝恒放行）"),
    "kws_route": ("audio", "json", "KWS 之后的路由（规则在 lang/kws/router.py）"),
    "vad": ("audio", "segs", "说话段切分；没切出＝整段直通"),
    "asr": ("audio", "json", "语音转文本（多段逐识别再拼）"),
    "router": ("text", "json", "klar NLP 定去向（llm/固定回应/verify；verify 暂留空跳过）"),
    "spk": ("audio", "json", "声纹验证（CAM++；默认不入链，按需装载）"),
    "llm": ("text", "json", "对话；失败降级回输入文本"),
    "tokenizer": ("text", "json", "长句切短（压 TTS 峰值 RSS）"),
    "tts": ("text", "audio", "逐块合成（本试验台把每个 chunk 都合成并拼接）"),
}


def _wav_bytes(samples, rate):
    from backends import audioio
    return audioio.encode_wav(samples, int(rate or 16000))


def _save(name, data: bytes):
    ARTIFACTS.mkdir(exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    path = ARTIFACTS / ("%s-%s" % (uuid.uuid4().hex[:8], safe))
    path.write_bytes(data)
    return path.name


def run_stages(stages, raw_audio=None, rate_hint=None, text=None,
               session="default", out_rate=0, tts_tier=None, audio_output="server"):
    """按 STAGE_ORDER 跑选中的段；每段的产物落 artifacts 并回链接。"""
    from backends import audioio
    import core as langcore

    if audio_output not in ("browser", "server"):
        raise ValueError("audio_output 只能是 browser 或 server")
    want = [s for s in STAGE_ORDER if s in stages]
    if not want:
        raise ValueError("没选任何段")
    out = {"ok": True, "selected": want, "stages": [], "artifacts": [], "result": {}}
    samples = rate = None
    segs = None
    cur_text = (text or "").strip()

    if any(STAGE_IO[s][0] == "audio" for s in want):
        if not raw_audio:
            raise ValueError("含音频段时必须上传音频")
        samples, rate = audioio.decode_payload(raw_audio, rate_hint=rate_hint,
                                               work_rate=langcore.WORK_RATE)
        if not samples:
            raise ValueError("空音频")
        out["result"]["in_rate"] = rate
        out["result"]["in_seconds"] = round(len(samples) / float(rate), 3)

    for name in want:
        t0 = time.time()
        row = {"name": name, "io": "%s→%s" % STAGE_IO[name][:2], "ok": True}
        try:
            if name == "spk":
                # chain.spk 默认 none（她 2026-11-04 定：不入链、按需装载）。
                # 试验台里**勾了 spk 段就是"要用"** ⇒ none 时退一步强制 sherpa 档，
                # 与 /api/spk/status 同口径；档位没实现/模型不在会抛出来落到本行 error。
                inst = CHAIN.stage("spk")
                if inst is None:
                    inst = CHAIN.stage("spk", "sherpa")
            else:
                inst = CHAIN.stage(name, tts_tier if name == "tts" else None)
            if inst is None:
                row.update(ok=False, note="该级档位为 none/空，已跳过", ms=0)
                out["stages"].append(row)
                continue

            if name == "sar":
                if rate != langcore.WORK_RATE or True:
                    samples, rate = inst.run(samples, rate)
                    data = _wav_bytes(samples, rate)
                    row["artifacts"] = [_save("sar-%d.wav" % rate, data)]
                    out["result"]["out_rate"] = rate
                    out["result"]["out_seconds"] = round(len(samples) / float(rate), 3)

            elif name == "rms":
                meta = inst.run(samples) or {}
                row["result"] = meta
                row["note"] = ("静音（短路）" if meta.get("silent")
                               else "peak %.1f dBFS" % meta.get("peak_dbfs", -120))
                out["result"]["rms"] = meta

            elif name == "vad":
                segs = inst.run(samples) or [samples]
                row["note"] = "%d 段" % len(segs)
                meta = []
                import zipfile
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for i, seg in enumerate(segs):
                        w = _wav_bytes(seg, rate)
                        z.writestr("seg%02d.wav" % i, w)
                        meta.append({"i": i, "seconds": round(len(seg) / float(rate), 3)})
                row["artifacts"] = [_save("vad-segs.zip", buf.getvalue())]
                row["meta"] = meta

            elif name == "kws":
                if segs is None:
                    segs = [samples]
                hit, tag = inst.run(segs)
                row["result"] = {"hit": bool(hit), "tag": tag}
                out["result"]["wake"] = bool(hit)
                out["result"]["kws_tag"] = tag
                row["artifacts"] = [_save("kws.json", json.dumps(
                    {"hit": bool(hit), "tag": tag}, ensure_ascii=False, indent=2).encode())]

            elif name == "kws_route":
                hit = bool(out["result"].get("wake"))
                tag = out["result"].get("kws_tag")
                dec = inst.run((samples, (hit, tag))) or {}
                row["result"] = dec
                out["result"]["kws_route"] = dec.get("route")
                out["result"]["kws_route_action"] = dec.get("action")
                row["artifacts"] = [_save("kws_route.json", json.dumps(
                    dec, ensure_ascii=False, indent=2).encode())]

            elif name == "asr":
                if segs is None:
                    segs = [samples]
                txt, meta = inst.run(segs)
                cur_text = (txt or "").strip()
                row["result"] = {"text": cur_text, "segments": meta}
                out["result"]["asr_text"] = cur_text
                row["artifacts"] = [_save("asr.txt", cur_text.encode("utf-8"))]

            elif name == "router":
                if not cur_text:
                    raise ValueError("router 需要文本（先跑 ASR 或直接填文本）")
                dec = inst.run((cur_text, session)) or {}
                row["result"] = dec
                out["result"]["route"] = dec.get("action", "llm")
                if dec.get("action") == "fixed":
                    cur_text = (dec.get("reply") or "").strip()
                    out["result"]["text"] = cur_text
                elif dec.get("action") == "verify":
                    row["note"] = "verify 还没实装：留空并跳过"
                row["artifacts"] = [_save("router.json", json.dumps(
                    dec, ensure_ascii=False, indent=2).encode())]

            elif name == "spk":
                # 逐 VAD 段各算一个嵌入（vertify/para.md §七：别拿整条录音算一个）；
                # 没勾 vad／没切出段就整段算一条。返回 dict 原样进 row["result"]。
                dec = inst.run(segs or [samples])
                if isinstance(dec, (tuple, list)):      # 兼容 (name, score) 口径
                    dec = {"match": dec[0] if len(dec) > 0 else None,
                           "score": dec[1] if len(dec) > 1 else None}
                dec = dec or {}
                row["result"] = dec
                out["result"]["spk"] = dec
                if dec.get("match") is not None or dec.get("score") is not None:
                    row["note"] = "match=%s score=%s" % (dec.get("match"), dec.get("score"))
                row["artifacts"] = [_save("spk.json", json.dumps(
                    dec, ensure_ascii=False, indent=2, default=str).encode())]

            elif name == "llm":
                if not cur_text:
                    raise ValueError("LLM 需要文本（先跑 ASR 或直接填文本）")
                reply, meta = inst.run(cur_text, session=session)
                row["result"] = {"reply": reply, "meta": meta}
                cur_text = (reply or "").strip()
                out["result"]["text"] = cur_text
                row["artifacts"] = [_save("llm.txt", cur_text.encode("utf-8"))]

            elif name == "tokenizer":
                if not cur_text:
                    raise ValueError("分词器需要文本")
                chunks = [c for c in inst.run(cur_text) if c.strip()]
                row["result"] = {"chunks": chunks}
                out["result"]["chunks"] = chunks
                row["artifacts"] = [_save("chunks.json", json.dumps(
                    chunks, ensure_ascii=False, indent=2).encode())]

            elif name == "tts":
                if not cur_text:
                    raise ValueError("TTS 需要文本")
                chunks = out["result"].get("chunks") or [cur_text]
                pieces = []
                for ch in chunks:
                    with inst.lock:
                        smp, rt = inst.run(ch)
                    if smp:
                        pieces.append((smp, rt))
                if not pieces:
                    raise ValueError("TTS 没合成出音频")
                gap_s = float(CHAIN.cfg.get("server", "chunk_gap_ms", 80) or 80) / 1000.0
                if audio_output == "browser":
                    # 浏览器接收 TTS 原生率；此路径禁止任何服务端输出重采样。
                    mrate = int(pieces[0][1])
                    if mrate <= 0 or any(int(rt) != mrate for _, rt in pieces):
                        raise ValueError("TTS 各块采样率不一致，原生播放路径拒绝隐式重采样")
                    merged = []
                    gap = [0.0] * max(0, int(mrate * gap_s))
                    for index, (smp, _) in enumerate(pieces):
                        if index:
                            merged.extend(gap)
                        merged.extend(smp)
                else:
                    merged, mrate = audioio.concat(
                        pieces, gap_s=gap_s,
                        out_rate=int(out_rate or CHAIN.cfg.get("server", "out_rate", 0) or 0))
                out["result"]["audio_output"] = audio_output
                out["result"]["output_sar"] = audio_output != "browser"
                data = _wav_bytes(merged, mrate)
                row["result"] = {"rate": mrate, "seconds": round(len(merged) / float(mrate), 3),
                                 "chunks": len(pieces)}
                out["result"]["audio_rate"] = mrate
                row["artifacts"] = [_save("tts-%d.wav" % mrate, data)]

        except Exception as exc:
            row.update(ok=False, error=("%s: %s" % (type(exc).__name__, exc))[:300])
        row["ms"] = int((time.time() - t0) * 1000)
        out["stages"].append(row)

    for r in out["stages"]:
        for a in r.get("artifacts") or []:
            out["artifacts"].append({"name": a, "stage": r["name"],
                                     "url": "/api/artifact/" + a})
    return out


# ---------------------------------------------------------------- 声纹（spk）
# 口径（她 2026-11-04 定）：`chain.spk` 默认 none＝**不入链**，按需装载。
# 所以控制台这几条路由**强制取 sherpa 档**（`CHAIN.stage("spk", "sherpa")`）：
# 打一次「记录声纹」才第一次装模型；点「卸载模型」把它放掉。
# 档位没实现/模型不在时**不抛 500**，回 {"ok":false,"msg":...} 让页面显示原因。
_SPK_TIER = "sherpa"


def spk_inst():
    """→ spk 的 sherpa 档实例（按需装载）。不可用就抛，由调用方转成人话。"""
    if CHAIN is None:
        raise RuntimeError("链路还没装配")
    inst = CHAIN.stage("spk", _SPK_TIER)
    if inst is None:
        raise RuntimeError("spk 档位 %r 不可用（chain.spk=%s）"
                           % (_SPK_TIER, CHAIN.active("spk") or "none"))
    return inst


def _spk_attr(inst, name, default=None):
    """取实例上的标量/无参方法（实现写成属性或方法都认）。"""
    v = getattr(inst, name, None)
    if callable(v):
        try:
            v = v()
        except Exception:
            return default
    return default if v is None else v


def spk_speakers(inst):
    """→ [{"name","count","updated"}]（形状固定，页面与 README 都按这个用）。"""
    raw = None
    for nm in ("speakers", "list_speakers", "all_speakers"):
        raw = _spk_attr(inst, nm, None)
        if raw:
            break
    out = []
    for s in (raw or []):
        if isinstance(s, dict):
            out.append({"name": str(s.get("name") or s.get("speaker") or ""),
                        "count": int(s.get("count") or s.get("segments") or 0),
                        "updated": str(s.get("updated") or s.get("mtime") or "")})
        else:
            out.append({"name": str(s), "count": 0, "updated": ""})
    return out


def spk_status():
    """GET /api/spk/status 的形状；档位不可用 → {"ok":false,"msg":...}（HTTP 仍是 200）。

    档位自己报了 `status()` 就以它为准（`loaded`/`dim`/`library` 这些它最清楚）；
    没有就按属性猜——两种实现都不用改控制台。
    """
    try:
        inst = spk_inst()
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]}
    st = _spk_attr(inst, "status", None)
    st = st if isinstance(st, dict) else {}

    th = st.get("threshold", _spk_attr(inst, "threshold", None))
    if th in (None, ""):                              # 兜底：档位没暴露就读 YAML 的 profile
        th = CHAIN.cfg.profile("spk", _SPK_TIER).get("threshold")
    try:
        th = float(th)
    except (TypeError, ValueError):
        th = 0.0
    try:
        dim = int(st.get("dim") or _spk_attr(inst, "dim", 0) or 0)
    except (TypeError, ValueError):
        dim = 0
    try:
        idle = int(st.get("idle_unload_s") or _spk_attr(inst, "idle_unload_s", 0) or 0)
    except (TypeError, ValueError):
        idle = 0
    lib = st.get("library") or _spk_attr(inst, "library", "") or ""
    if isinstance(lib, dict):
        lib = lib.get("path") or lib.get("dir") or json.dumps(lib, ensure_ascii=False)
    speakers = st.get("speakers")
    if not isinstance(speakers, list):
        speakers = spk_speakers(inst)
    return {"ok": True,
            # active ＝ **配置里的档**（chain.spk，默认 none）——与 /api/stages 的口径一致；
            # 这五条路由本身是强制取 sherpa 的，这点看 `loaded` 与 msg 就够。
            "active": CHAIN.active("spk") or "none",
            "loaded": bool(st.get("loaded", _spk_attr(inst, "loaded", False))),
            "dim": dim, "threshold": th,
            "speakers": speakers,
            "library": str(lib), "idle_unload_s": idle}


def read_audio_body(body, ctype):
    """请求体 → 音频字节。multipart 认 `audio`/`file` 字段；JSON 认 `audio_b64`。

    服务端只解 16-bit PCM WAV（或裸 PCM16）——页面那边是 WebAudio 抓 PCM 自己编 WAV 传上来的，
    不是 MediaRecorder 的 webm/opus（那个 decode_payload 解不了）。
    """
    if "multipart/form-data" in (ctype or "").lower():
        m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
        if not m:
            raise ValueError("multipart 缺 boundary")
        parts = parse_multipart(body, m.group(1))
        for key in ("audio", "file"):
            if key in parts:
                return parts[key][2]
        raise ValueError("multipart 里没有 audio/file 字段")
    try:
        obj = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        raise ValueError("body 既不是 multipart 也不是 JSON")
    b64 = (obj or {}).get("audio_b64") or ""
    if not b64:
        raise ValueError("JSON 里没有 audio_b64")
    if b64.startswith("data:"):                       # 容忍 data URI 前缀
        b64 = b64.split(",", 1)[-1]
    try:
        return base64.b64decode(b64)
    except Exception as exc:
        raise ValueError("audio_b64 解不动: %s" % exc)


def spk_decode(raw):
    """→ (samples, rate)；解不出就报错（别把空音频喂进模型）。"""
    from backends import audioio
    samples, rate = audioio.decode_payload(raw or b"", work_rate=16000)
    if not samples:
        raise ValueError("空音频（只认 16-bit PCM WAV 或裸 PCM16）")
    return samples, int(rate or 16000)


def spk_enroll(name, raw, replace=False):
    """登记一个人的一段音频 → {"code":0,...}；失败抛，路由转成 400。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("缺 name（?name=NAME）")
    samples, rate = spk_decode(raw)
    inst = spk_inst()
    res = inst.enroll(name, samples, rate, replace=bool(replace))
    res = res if isinstance(res, dict) else {}
    spk = spk_speakers(inst)
    total = res.get("total") or res.get("count")
    if not total:                                     # 档位没回总数就从说话人表里读
        for s in spk:
            if s["name"] == name:
                total = s["count"]
                break
    return {"code": 0, "name": name,
            "dim": int(res.get("dim") or _spk_attr(inst, "dim", 0) or 0),
            "total": int(total or 0), "speakers": spk}


def spk_verify(raw, name=None):
    """比对一段音频 → 档位返回原样（match/score/scores/threshold/speech_s/ms）。"""
    samples, rate = spk_decode(raw)
    inst = spk_inst()
    return inst.verify(samples, rate, name=(name or None))


def spk_unload():
    inst = spk_inst()
    inst.drop()
    return {"code": 0, "unloaded": True}


def spk_forget(name):
    """删一个声纹；name 为空就只回列表。"""
    inst = spk_inst()
    name = (name or "").strip()
    if name:
        inst.forget(name)
    return {"code": 0, "speakers": spk_speakers(inst)}


# ---------------------------------------------------------------- multipart
def parse_multipart(body, boundary):
    """标准库 MIME 解析，保留音频末尾的 CR/LF 字节，禁止 strip 二进制内容。"""
    from email import policy
    from email.parser import BytesParser
    header = ('Content-Type: multipart/form-data; boundary="%s"\r\n'
              'MIME-Version: 1.0\r\n\r\n' % boundary).encode("latin-1")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    out = {}
    if not message.is_multipart():
        raise ValueError("无效的 multipart 音频上传")
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[name] = (part.get_filename(), part.get_content_type(),
                         part.get_payload(decode=True) or b"")
    return out


# ---------------------------------------------------------------- 服务托管
# 关键事实：`lang/server/app.py` 在**启动时**读一次 lang/config/ 建 Chain，
# 之后改 YAML 不会生效——**必须重启进程**。所以这里的"重启"是：
#   terminate 子进程 → 等它退出 → 用同一套参数再 spawn 一次 → 探 /health。
# 如果端口上已经有别人起的服务（不是本控制台拉起的），只显示状态、不接管重启。
import subprocess

SRV = {"proc": None, "started": 0.0, "host": "127.0.0.1", "port": 18091,
       "log": None, "external": False}


# 本机探测必须绕过代理：Windows 上 urllib 会读环境/注册表里的 HTTP 代理，
# 把 127.0.0.1 也送去代理 ⇒ 明明服务在跑也探不到（2026-11-04 实测踩到）。
_NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _probe(host, port, timeout=2.5):
    # Wildcard addresses are bind targets, not client destinations on Windows.
    host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    try:
        with _NOPROXY.open("http://%s:%d/health" % (host, port), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def server_status():
    p = SRV.get("proc")
    alive = bool(p and p.poll() is None)
    ext = (not alive) and (_probe(SRV["host"], SRV["port"]) is not None)
    return {"managed": alive, "external": bool(ext),
            "pid": p.pid if alive else None,
            "uptime_s": int(time.time() - SRV["started"]) if alive else 0,
            "host": SRV["host"], "port": SRV["port"],
            "log": str(SRV["log"]) if SRV.get("log") else None,
            "health": _probe(SRV["host"], SRV["port"]) if (alive or ext) else None}


def start_server(logpath=None):
    if SRV["proc"] and SRV["proc"].poll() is None:
        return "already running (pid %d)" % SRV["proc"].pid
    if _probe(SRV["host"], SRV["port"]) is not None:
        SRV["external"] = True
        return "端口 %d 上已有服务在跑（外部接管，不重启）" % SRV["port"]
    logpath = logpath or (ARTIFACTS / "lang-server.log")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    lf = open(str(logpath), "wb")
    cmd = [sys.executable, "-X", "utf8", str(SERVER_DIR / "app.py"),
           "--config", str(LANG_DIR / "config"),
           "--host", SRV["host"], "--port", str(SRV["port"]), "--no-warm"]
    SRV["proc"] = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                   cwd=str(SERVER_DIR))
    SRV["started"] = time.time()
    SRV["log"] = logpath
    SRV["external"] = False
    # 等它起来（最多 8 s）
    for _ in range(150):        # 最多等 30 s：Windows 上 import + 装配要十几秒
        time.sleep(0.2)
        if _probe(SRV["host"], SRV["port"]) is not None:
            return "started (pid %d)" % SRV["proc"].pid
        if SRV["proc"].poll() is not None:
            return "启动失败，退出码 %s（看日志）" % SRV["proc"].returncode
    return "已拉起（pid %d），还没应答 /health" % SRV["proc"].pid


def stop_server():
    p = SRV.get("proc")
    if not p or p.poll() is not None:
        SRV["proc"] = None
        return "没有本控制台托管的服务"
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    SRV["proc"] = None
    return "stopped"


def restart_server():
    stop_server()
    time.sleep(0.3)
    return start_server()


def tail_log(n=60):
    lp = SRV.get("log")
    if not lp or not Path(lp).exists():
        return ""
    try:
        lines = Path(lp).read_text("utf-8", "replace").split("\n")
        return "\n".join(lines[-n:])
    except Exception as exc:
        return "读日志失败: %s" % exc


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "lang-console/1.0"
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, obj)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        buf = bytearray()
        while len(buf) < n:
            got = self.rfile.read(min(65536, n - len(buf)))
            if not got:
                break
            buf += got
        return bytes(buf)

    def log_message(self, fmt, *args):
        sys.stderr.write("[console %s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- GET
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/":
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/console-theme.js":
            return self._send(200, (HERE.parent / "shared" / "console-theme.js").read_bytes(),
                              "text/javascript; charset=utf-8")
        if path == "/recorder.js":
            return self._send(200, (HERE / "recorder.js").read_bytes(),
                              "text/javascript; charset=utf-8")
        if path == "/api/config":
            files = []
            for fp in config_files():
                try:
                    params = parse_file(fp)
                    for p in params:
                        opts, src = options_for(p["path"], p.get("desc") or "")
                        if opts:
                            p["options"] = opts
                            p["options_src"] = src
                            if str(p["value"]) not in [str(o) for o in opts] and p["value"] != "***":
                                p["value_not_in_options"] = True
                        if p["path"] in TONE:
                            p["tone"] = TONE[p["path"]]
                        rng = range_for(p["path"], p["type"], p["value"])
                        if rng:
                            p["range"] = rng
                    files.append({"name": fp.name, "params": params})
                except Exception as exc:
                    files.append({"name": fp.name, "error": str(exc)[:200], "params": []})
            return self._json(200, {"dir": str(LANG_DIR / "config"),
                                    "options": chain_options(), "files": files,
                                    "tts_combo": TTS_COMBO, "tts_combo_ok": TTS_COMBO_OK})
        if path == "/api/stages":
            actives = {s: (CHAIN.active(s) or "none") for s in STAGE_ORDER}
            return self._json(200, {"stages": [
                {"name": s, "io": "%s→%s" % STAGE_IO[s][:2], "desc": STAGE_IO[s][2],
                 "active": actives[s]} for s in STAGE_ORDER],
                "options": chain_options()})
        if path == "/api/health":
            return self._json(200, {"ok": True, "lang": str(LANG_DIR),
                                    "config": str(CHAIN.cfg.path),
                                    "actives": {s: (CHAIN.active(s) or "none") for s in STAGE_ORDER}})
        if path == "/api/spk/status":
            # 档位不可用也是 200：页面要把原因显示出来，不能吃 500 堆栈。
            try:
                return self._json(200, spk_status())
            except Exception as exc:                  # noqa: BLE001
                return self._json(200, {"ok": False,
                                        "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]})
        if path == "/api/server":
            return self._json(200, server_status())
        if path == "/api/server/log":
            return self._send(200, tail_log(80), "text/plain; charset=utf-8")
        if path.startswith("/api/artifact/"):
            name = urllib.parse.unquote(path[len("/api/artifact/"):])
            fp = (ARTIFACTS / name)
            try:
                fp = fp.resolve()
                if ARTIFACTS.resolve() not in fp.parents or not fp.is_file():
                    raise FileNotFoundError(name)
            except Exception:
                return self._json(404, {"error": "artifact not found"})
            ctype = "audio/wav" if fp.suffix == ".wav" else (
                "application/zip" if fp.suffix == ".zip" else "text/plain; charset=utf-8")
            return self._send(200, fp.read_bytes(), ctype)
        return self._json(404, {"error": "no path " + path})

    # ---- POST
    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(url.query)
        ctype = self.headers.get("Content-Type") or ""
        body = self._body()

        try:
            if path == "/api/config":
                obj = json.loads(body.decode("utf-8") or "{}")
                fname = obj.get("file")
                items = obj.get("items") or []
                if not fname or not items:
                    return self._json(400, {"error": "需要 file + items"})
                written = []
                for it in items:
                    p, v = it.get("path"), it.get("value")
                    if p is None:
                        continue
                    if v == "***":          # 掩码原样提交＝不改
                        continue
                    written.append({"path": p, "line": write_param(fname, p, v)})
                if obj.get("reload"):
                    reload_chain()
                return self._json(200, {"ok": True, "written": written,
                                        "reloaded": bool(obj.get("reload")),
                                        "needs_restart": True,
                                        "note": "已改 YAML；运行中的 lang-server 只在启动时读配置，"
                                                "要点「重启服务」才生效"})

            if path == "/api/server/restart":
                return self._json(200, {"ok": True, "result": restart_server(),
                                        "status": server_status()})
            if path == "/api/server/start":
                return self._json(200, {"ok": True, "result": start_server(),
                                        "status": server_status()})
            if path == "/api/server/stop":
                return self._json(200, {"ok": True, "result": stop_server(),
                                        "status": server_status()})

            if path == "/api/reload":
                reload_chain()
                return self._json(200, {"ok": True,
                                        "actives": {s: (CHAIN.active(s) or "none")
                                                    for s in STAGE_ORDER}})

            if path == "/api/config/reset":
                n = reset_defaults()
                return self._json(200, {"ok": True, "reset": n,
                                        "needs_restart": True,
                                        "note": "已恢复到启动快照（%d 项），运行中的服务要点「重启服务」" % n})

            if path == "/api/tts-combo":
                obj = json.loads(body.decode("utf-8") or "{}")
                try:
                    items = tts_combo_items(obj.get("frontend"), obj.get("acoustic"),
                                            obj.get("vocoder"), obj.get("integrated"))
                except ValueError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                written = [{"file": f, "path": p, "line": write_param(f, p, v)}
                           for (f, p, v) in items]
                return self._json(200, {"ok": True, "written": written,
                                        "needs_restart": True,
                                        "note": "组合已写回 YAML；点「重启服务」生效"})

            if path == "/api/run":
                fields = {}
                raw_audio = None
                rate_hint = None
                if "multipart/form-data" in ctype.lower():
                    m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
                    if not m:
                        return self._json(400, {"error": "缺 boundary"})
                    parts = parse_multipart(body, m.group(1))
                    for k, (_fn, _ct, data) in parts.items():
                        if k == "file":
                            raw_audio = data
                        else:
                            fields[k] = data.decode("utf-8", "replace")
                else:
                    fields = json.loads(body.decode("utf-8") or "{}")
                stages = [s.strip() for s in (fields.get("stages") or "").split(",") if s.strip()]
                if fields.get("rate"):
                    rate_hint = int(fields["rate"])
                res = run_stages(stages, raw_audio=raw_audio, rate_hint=rate_hint,
                                 text=fields.get("text"),
                                 session=fields.get("session") or "default",
                                 out_rate=int(fields.get("out_rate") or 0),
                                 tts_tier=fields.get("tts_tier") or None,
                                 audio_output=fields.get("audio_output") or "server")
                return self._json(200, res)

            # ---- 声纹（spk）：全部**进程内**调 CHAIN，不绕去 18091 的 lang-server。
            # 失败一律 code:1 + msg（enroll/verify 用 400，unload/library 用 200），绝不漏 500 堆栈。
            if path == "/api/spk/enroll":
                name = (query.get("name") or [""])[0]
                replace = (query.get("replace") or ["0"])[0].lower() in ("1", "true", "yes", "on")
                try:
                    return self._json(200, spk_enroll(name, read_audio_body(body, ctype), replace))
                except Exception as exc:              # noqa: BLE001
                    return self._json(400, {"code": 1,
                                            "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]})

            if path == "/api/spk/verify":
                name = (query.get("name") or [""])[0]
                try:
                    return self._json(200, spk_verify(read_audio_body(body, ctype), name))
                except Exception as exc:              # noqa: BLE001
                    return self._json(400, {"code": 1,
                                            "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]})

            if path == "/api/spk/unload":
                try:
                    return self._json(200, spk_unload())
                except Exception as exc:              # noqa: BLE001
                    return self._json(200, {"code": 1,
                                            "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]})

            if path == "/api/spk/library":
                name = (query.get("delete") or query.get("name") or [""])[0]
                try:
                    return self._json(200, spk_forget(name))
                except Exception as exc:              # noqa: BLE001
                    return self._json(200, {"code": 1,
                                            "msg": ("%s: %s" % (type(exc).__name__, exc))[:400]})

            return self._json(404, {"error": "no path " + path})
        except Exception as exc:
            return self._json(500, {"ok": False, "error": ("%s: %s" % (type(exc).__name__, exc))[:400],
                                    "trace": traceback.format_exc()[-800:]})


def main():
    ap = argparse.ArgumentParser(description="lang 语音链试验台（参数 + 逐段）")
    ap.add_argument("--lang-dir", default=None, help="repos/HA4FPAI/lang 路径（默认自动找）")
    ap.add_argument("--host", default=os.environ.get("CONSOLE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONSOLE_PORT", 7861)))
    ap.add_argument("--artifacts", default=None,
                    help="产物目录（默认系统临时目录下的 lang-console-artifacts）")
    ap.add_argument("--serve", action="store_true",
                    help="顺带托管 lang-server（app.py）：用它才能点「重启服务」让新配置生效")
    ap.add_argument("--serve-host", default="127.0.0.1")
    ap.add_argument("--serve-port", type=int, default=18091)
    args = ap.parse_args()

    if args.artifacts:
        global ARTIFACTS
        ARTIFACTS = Path(args.artifacts).resolve()
    lang = find_lang_dir(args.lang_dir)
    if not lang:
        print("找不到 lang 目录（--lang-dir 或 LANG_DIR）", file=sys.stderr)
        return 2
    bootstrap(lang)
    SRV["host"], SRV["port"] = args.serve_host, args.serve_port
    print("[console] lang=%s config=%s" % (lang, CHAIN.cfg.path))
    print("[console] actives=%s" % {s: (CHAIN.active(s) or "none") for s in STAGE_ORDER})
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[console] http://%s:%d" % (args.host, args.port))
    if args.serve:
        # 后台起服务：**先**把控制台自己的端口占上，别让 lang-server 的 8~30 s 冷启动
        # 把页面拖到打不开（2026-11-04 实测踩到）。
        threading.Thread(target=lambda: print("[console] 托管 lang-server: %s" % start_server()),
                         daemon=True).start()
    else:
        st = server_status()
        print("[console] 未托管 lang-server（--serve 才托管）；探测 %s:%d -> %s"
              % (SRV["host"], SRV["port"], "在线" if (st["managed"] or st["external"]) else "无"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if SRV.get("proc") and SRV["proc"].poll() is None:
            print("[console] 退出前停掉托管的 lang-server")
            stop_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
