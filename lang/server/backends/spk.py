#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spk.py —— 声纹（说话人验证/识别）档：CAM++ 说话人嵌入 ＋ 登记库。

**她 2026-11-04 定的三条口径（这个文件就是照它写的）**：
  1. **默认不入链**：`chain.spk` 默认 `none`（`server.yaml`），本档只有被路由/接口点名才用；
  2. **不常驻**：进程启动不装载模型（`core.Chain.SHERPA_STAGES` 里没有 `spk`）；
     第一次真用才建 `SpeakerEmbeddingExtractor`，**闲置 `idle_unload_s` 秒自动丢弃**（会话与
     登记库都放掉），也可 `POST /api/v1/spk/unload` 手动卸；
  3. **被调用时才测**：链路里由 `router`（klar NLP）或 `kws_route`（唤醒词）给出
     `action=verify` 时，拿**当前这条请求的那几段音频**去跟库里的某个声纹比。

契约（与 core.py 头注同级）：
  run(segs) -> {"match": str, "score": float, "scores": {name: float},
                "threshold": float, "speech_s": float, "dim": int}
  —— `match` 为空串表示"库里没有一个人过门限"；`scores` 是每个人跟这段音频的余弦。

参数面与实测口径见 `lang/vertify/para.md`（4 个实例参数、门限 0.5、fbank 固定面、
int8 三档实测）。**这里只做三件事**：算嵌入、跟库比余弦、读写登记库。

代价（本机实测，x86_64）：fp32 会话装载 398–478 ms、60 s 音频 827 ms；峰值 RSS 126 MB
（独占进程）。板上未测 —— 所以**默认闲置卸载**，别让它跟 ASR/KWS 抢那 993 MB。
"""

import json
import math
import os
import threading
import time

from backends import audioio
from core import WORK_RATE, Stage


def _lang_dir():
    """lang/ 根：本文件在 server/backends/ ⇒ 上溯两级。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _resolve(p):
    """相对路径按 lang/ 根解析（`vertify/models/...` 之类）。"""
    p = str(p or "")
    if not p or os.path.isabs(p):
        return os.path.normpath(p) if p else p
    return os.path.normpath(os.path.join(_lang_dir(), p))


def _cos(a, b):
    """纯 python 余弦（dim=192，一次几微秒；不引 numpy 到判决路径）。"""
    num = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        num += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return num / math.sqrt(na * nb)


class SpkSherpa(Stage):
    """CAM++（3d-speaker）说话人嵌入档。模型/参数见 lang/vertify/。"""

    name = "sherpa"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = self.profile or {}
        # 模型：给 model（文件）优先；否则在 model_dir 里挑（prefer_int8=1 才拿 int8 那件）
        self.model = _resolve(p.get("model") or "")
        self.model_dir = _resolve(p.get("model_dir")
                                  or os.path.join("vertify", "models",
                                                  "3dspeaker_speech_campplus_sv_zh-cn_16k-common"))
        self.prefer_int8 = str(p.get("prefer_int8", "0")).strip().lower() not in ("", "0", "false", "no")
        self.library_path = _resolve(p.get("library") or os.path.join("vertify", "library.json"))
        self.threshold = float(p.get("threshold", 0.5) or 0.5)
        self.num_threads = int(p.get("num_threads", 1) or 1)
        self.idle_unload_s = float(p.get("idle_unload_s", 300) or 300)
        self.min_speech_ms = int(p.get("min_speech_ms", 600) or 600)
        self.reply_ok = str(p.get("reply_ok") or "声纹通过，是{name}。")
        self.reply_unknown = str(p.get("reply_unknown") or "这个声音没在库里。")
        self.reply_empty = str(p.get("reply_empty") or "没听到声音，再试一次。")

        self._ext = None            # sherpa_onnx.SpeakerEmbeddingExtractor（重，按需建）
        self._dim = 0
        self._lib = None            # {"speakers": {name: {"vectors": [[...]], "updated": ".."}}}
        self._loaded_at = 0.0
        self._last_used = 0.0
        self._timer = None
        self._inflight = 0
        self.lock = threading.RLock()      # 覆盖 Stage 的 Lock：_ensure() 会被 embed() 在持锁时再调

    # ---- 模型生命周期（不常驻的核心） --------------------------------
    def _pick_model(self):
        if self.model:
            if not os.path.isfile(self.model):
                raise ValueError("声纹模型不在：%s（spk_profiles.sherpa.model）" % self.model)
            return self.model
        if not os.path.isdir(self.model_dir):
            raise ValueError("声纹模型目录不在：%s（spk_profiles.sherpa.model_dir）" % self.model_dir)
        names = sorted(f for f in os.listdir(self.model_dir)
                       if f.endswith(".onnx") and os.path.isfile(os.path.join(self.model_dir, f)))
        if not names:
            raise ValueError("%s 里没有 .onnx" % self.model_dir)
        want_int8 = [f for f in names if ".int8." in f]
        plain = [f for f in names if ".int8." not in f and ".int8q." not in f]
        pick = (want_int8 or plain) if self.prefer_int8 else (plain or want_int8)
        return os.path.join(self.model_dir, pick[0])

    def _ensure(self):
        """第一次真用才装载；之后 touch() 维持，闲置到点自动 drop()。"""
        with self.lock:
            if self._ext is None:
                import sherpa_onnx                     # 与 VAD/KWS/ASR/TTS 共用同一份 runtime
                model = self._pick_model()
                t0 = time.time()
                ext = sherpa_onnx.SpeakerEmbeddingExtractor(
                    sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                        model=model, num_threads=self.num_threads,
                        provider="cpu", debug=False))
                self._ext = ext
                self._dim = int(ext.dim)
                self._model_path = model
                self._loaded_at = t0
                self._lib = self._load_library()
            self._last_used = time.time()
            self._arm_timer()
            return self._ext

    def _arm_timer(self):
        """每次用过就重置闲置计时；到点自己把会话丢掉（不靠任何人来调）。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self.idle_unload_s <= 0:
            return
        self._timer = threading.Timer(self.idle_unload_s, self._idle_drop)
        self._timer.daemon = True
        self._timer.start()

    def _idle_drop(self):
        if self._inflight > 0:                      # 正在跑就别动它，下一次 touch 再排
            return
        self.drop(quiet=True)

    def drop(self, quiet=False):
        """丢掉会话与库（内存还回去）；档对象本身留着（很轻），下次按需再装。"""
        with self.lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            had = self._ext is not None
            self._ext = None
            self._lib = None
            self._dim = 0
            self._loaded_at = 0.0
        if quiet:
            return had
        return had

    # ---- 登记库 -------------------------------------------------------
    def _load_library(self):
        path = self.library_path
        dim = self._dim or 192
        if not path or not os.path.isfile(path):
            return {"version": 1, "dim": dim, "speakers": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                lib = json.load(f) or {}
        except Exception:                            # 坏文件当空库，不炸链
            lib = {"version": 1, "dim": dim, "speakers": {}}
        if not isinstance(lib.get("speakers"), dict):
            lib["speakers"] = {}
        lib.setdefault("version", 1)
        lib.setdefault("dim", dim)
        return lib

    def _save_library(self):
        path = self.library_path
        d = os.path.dirname(path) or "."
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        self._lib.setdefault("speakers", {})
        self._lib["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._lib["dim"] = self._dim or self._lib.get("dim") or 192
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._lib, f, ensure_ascii=False)
        os.replace(tmp, path)

    def speakers(self):
        """[{name, count, updated}]（按名字排序）——给 /status 与页面用。

        **不装载模型**：只读登记库（几百字节 JSON），所以"没在跑"的时候查库是零代价的。
        """
        lib = self._lib if self._lib is not None else self._load_library()
        out = []
        for name in sorted((lib.get("speakers") or {})):
            v = lib["speakers"][name] or {}
            out.append({"name": name,
                        "count": len(v.get("vectors") or []),
                        "updated": v.get("updated") or ""})
        return out

    # ---- 嵌入 ---------------------------------------------------------
    def embed(self, samples, rate):
        """一段音频 → 192 维嵌入列表。库内会重采样（48k 也认），见 para.md §五。"""
        self._ensure()
        import numpy as np
        x, r = audioio.to_work_rate(samples, int(rate or WORK_RATE), WORK_RATE)
        if not x:
            return None
        arr = np.asarray(x, dtype=np.float32)
        with self.lock:                             # sherpa 会话不保证线程安全
            stream = self._ext.create_stream()
            stream.accept_waveform(sample_rate=WORK_RATE, waveform=arr)
            stream.input_finished()
            if not self._ext.is_ready(stream):
                return None
            return np.asarray(self._ext.compute(stream), dtype=np.float32).tolist()

    def _embed_segs(self, segs, rate=WORK_RATE):
        """多段 → 一条嵌入（逐段嵌入再平均；空段跳过）。"""
        vecs = []
        total_s = 0.0
        for seg in (segs or []):
            if not seg:
                continue
            v = self.embed(seg, rate)
            if v:
                vecs.append(v)
                total_s += len(seg) / float(rate or WORK_RATE)
        if not vecs:
            return None, 0.0
        if len(vecs) == 1:
            return vecs[0], total_s
        dim = len(vecs[0])
        avg = [sum(v[i] for v in vecs) / float(len(vecs)) for i in range(dim)]
        return avg, total_s

    # ---- 登记 / 验证 / 删除 -------------------------------------------
    def enroll(self, name, samples, rate, replace=False):
        name = (name or "").strip()
        if not name:
            raise ValueError("缺 name（给这个声纹一个名字）")
        ms = len(samples or []) / float(rate or WORK_RATE) * 1000.0
        if ms < self.min_speech_ms:
            raise ValueError("这段太短（%.0f ms < %d ms），至少说一句话" % (ms, self.min_speech_ms))
        vec = self.embed(samples, rate)
        if not vec:
            raise ValueError("这段算不出嵌入（空音频或太短）")
        lib = self._lib if self._lib is not None else self._load_library()
        sp = lib["speakers"].setdefault(name, {"vectors": [], "updated": ""})
        if replace:
            sp["vectors"] = []
        sp["vectors"].append(vec)
        sp["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._lib = lib
        self._save_library()
        self._arm_timer()
        return {"name": name, "dim": self._dim, "added": 1,
                "total": len(sp["vectors"]), "speech_s": round(ms / 1000.0, 3),
                "speakers": self.speakers()}

    def verify(self, samples, rate, name=None, segs=None):
        """一段（或多段）音频跟库比：给 name 是 1:1，不给是 1:N。"""
        t0 = time.time()
        if segs is None:
            vec, speech_s = self._embed_segs([samples], rate)
        else:
            vec, speech_s = self._embed_segs(segs, WORK_RATE)
        if not vec:
            return {"match": "", "score": 0.0, "scores": {}, "threshold": self.threshold,
                    "speech_s": round(speech_s, 3), "dim": self._dim, "ms": 0,
                    "error": "empty"}
        scores = {}
        speakers = (self._lib or {}).get("speakers", {})
        want = (name or "").strip()
        for sp_name, sp in speakers.items():
            if want and sp_name != want:
                continue
            vs = sp.get("vectors") or []
            if vs:
                scores[sp_name] = round(max(_cos(vec, v) for v in vs), 4)
        best, best_s = "", None                        # 取真实最高分（可以是负的）
        for sp_name, s in scores.items():
            if best_s is None or s > best_s:
                best, best_s = sp_name, s
        top = float(best_s) if best_s is not None else 0.0
        match = best if (best and top >= self.threshold) else ""
        if want:
            match = want if top >= self.threshold else ""
        return {"match": match, "score": round(top, 4), "scores": scores,
                "threshold": self.threshold, "speech_s": round(speech_s, 3),
                "dim": self._dim, "ms": int((time.time() - t0) * 1000)}

    def forget(self, name):
        if self._lib is None:                        # 删名字不需要装模型
            self._lib = self._load_library()
        sp = (self._lib or {}).get("speakers", {})
        if name not in sp:
            return {"removed": False, "speakers": self.speakers()}
        del sp[name]
        self._save_library()
        return {"removed": True, "speakers": self.speakers()}

    # ---- 链路里的入口（core 调它） -------------------------------------
    def run(self, segs):
        """`(segs)` → 验证结果。链里 `router`/`kws_route` 给 verify 时走这里。"""
        self._inflight += 1
        try:
            return self.verify(None, WORK_RATE, segs=segs)
        finally:
            self._inflight -= 1
            self._arm_timer()

    def status(self):
        loaded = self._ext is not None
        return {"active": "sherpa", "loaded": loaded,
                "dim": self._dim or ((self._lib or {}).get("dim") or 192),
                "model": getattr(self, "_model_path", "") or (self.model or self.model_dir),
                "threshold": self.threshold,
                "library": self.library_path,
                "idle_unload_s": self.idle_unload_s,
                "loaded_s_ago": round(time.time() - self._loaded_at, 1) if loaded else None,
                "last_used_s_ago": round(time.time() - self._last_used, 1) if self._last_used else None,
                "speakers": self.speakers() if loaded else
                            [{"name": n, "count": len((v or {}).get("vectors") or []),
                              "updated": (v or {}).get("updated", "")}
                             for n, v in sorted(
                                 (self._load_library().get("speakers") or {}).items())]}