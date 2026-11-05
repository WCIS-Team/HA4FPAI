#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py —— lang/server 的组件契约、共享运行时与链编排。

定位（2026-11-04 她定）：本服务是**编排层**，不重写 HA4FPAI 已有的组件——
voice_gateway（Wyoming 语音链）、npuserv（NPU 槽位）、piper/ekho 服务、云 API
全部以后端身份接进来，谁在用由 lang/config/server.yaml 的 `chain` 节决定。

各级契约（stage 名与 config 的 chain 节一致；采样一律 float 列表/数组，单声道）：
  sar       (samples, rate) -> (samples, rate)             音频前处理/重采样到工作采样率
  vad       (samples @工作率) -> [seg, ...]                说话段切分；档 none＝整段直通
  kws       (samples) -> (hit, tag)                        唤醒门控；passthrough＝恒放行（留空占位）
  kws_route (samples, (hit, tag)) -> {action,route,reply}   KWS 之后的路由（规则在 lang/kws/router.py）
  asr       [seg] -> (text, [seg_meta])                    语音转文本（多段逐识别再拼接）
  router    (text, session) -> {action,reply}              ASR 后交 klar NLP 定去向：llm/fixed/verify
  spk       (segs) -> {match,score,scores}                 声纹（CAM++）：**默认 none**，被 verify 点名才装载
  llm       (text, session) -> (reply, meta)               对话；失败降级回 asr 文本
  tokenizer (text) -> [chunk, ...]                         长句切短，压 TTS 峰值 RSS（她定）
  tts       (chunk) -> (samples, rate)                     合成；失败只丢音频不丢文本

错误语义：asr/llm/tts **失败不断链**——记进 stages_error 继续跑（对齐 remote_asr 的
「单段失败只记 error」与 mimo_bridge 的「LLM 挂了降级回识别文本」）。

共用 env（她定）：ORT 系组件经 SharedEnv 共享同一份 onnxruntime（import 一次、
SessionOptions 配一次、各组件独立 session）——voice_kit 单例模式的推广。注意
sherpa-onnx 轮子里捆的 ORT 与 pip 的 onnxruntime 是两份库，各归各的组件家族；
NPU 槽位（npuserv）受 CMA 约束**必须**独立 worker 进程，只能 HTTP 编排、不能进本进程。

实现口径：core 与 audioio 是**纯标准库**（板上 py3.8、本机 py3.13 都能跑，mimo_bridge 同款）；
numpy/onnxruntime 只允许在具体的 ORT 系后端内部惰性 import。
"""

import contextlib
import threading
import time

# 工作采样率：链内统一成这个数（48k 麦克风经 sar 降下来；远端 wav 多数原生 16k）
WORK_RATE = 16000


class SharedEnv(object):
    """进程内**唯一**的 onnx 运行时：sherpa-onnx（它自己捆了 onnxruntime）。

    她的口径（2026-11-04）：server 启动就把 sherpa-onnx runtime 拉起来，VAD/KWS/ASR/TTS
    所有 onnx 模型都在**同一个 env**（同一份 sherpa-onnx + 它捆的 ORT）里跑，省内存。

    注意 `pip install onnxruntime` 是**另一份运行时**：实测 `import sherpa_onnx` 不会把
    `onnxruntime` 带进 `sys.modules`，两者是独立的两份库。所以默认 `env.single_runtime: true`
    下，`ort()` 直接拒绝加载 pip ORT（要 raw-ORT 的 `vad: silero` 档必须显式关掉）。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._sherpa = None
        self._ort = None
        self._opts = None
        self._lock = threading.Lock()

    def _b(self, key, default=False):
        v = self.cfg.get("env", key, "")
        if v in (None, ""):
            return default
        return str(v).strip().lower() not in ("0", "false", "no", "off", "none")

    def sherpa(self):
        """唯一的那份 sherpa-onnx（含它捆的 ORT）。全进程只 import 一次。"""
        with self._lock:
            if self._sherpa is None:
                import sherpa_onnx
                self._sherpa = sherpa_onnx
            return self._sherpa

    def ort(self):
        """pip 的 onnxruntime —— **第二份运行时**，默认禁止。"""
        with self._lock:
            if self._ort is None:
                if self._b("single_runtime", True) and not self._b("allow_pip_onnxruntime", False):
                    raise RuntimeError(
                        "单运行时模式：已在用 sherpa-onnx（自带 ORT），不再加载 pip onnxruntime。"
                        "要 raw-ORT 档（如 vad: silero）请把 env.single_runtime 设 false，"
                        "或用 vad: sherpa / rms / none")
                import onnxruntime
                intra = int(self.cfg.get("env", "intra_op_threads", "2") or 2)
                o = onnxruntime.SessionOptions()
                o.intra_op_num_threads = intra
                o.inter_op_num_threads = 1
                self._ort = onnxruntime
                self._opts = o
            return self._ort

    def ort_opts(self):
        self.ort()
        return self._opts


class Stage(object):
    """各级组件基类：子类实现各自的 run()；带模型/会话的用 self.lock 串行化。"""

    name = "?"

    def __init__(self, cfg, profile, shared):
        self.cfg = cfg                    # 全量 Config（要读别的节时用）
        self.profile = profile or {}      # <stage>_profiles.<name> 节
        self.shared = shared
        self.lock = threading.Lock()      # ORT session 不保证线程安全，默认串行


class GateTimeout(Exception):
    """准入闸等待超时（并发已满，且在 gate_timeout_s 内没排到）。"""


class Chain(object):
    """按 config 的 chain 节装配各级，提供 run_audio / run_text 两条入口。

    组件第一次用到才 build（对齐 server_app 的 get_engine 惰性装载）；
    换档＝改 config 重启（档位注册表在 backends.REGISTRY，天然可替换）。
    """

    STAGES = ("sar", "rms", "kws", "kws_route", "vad", "asr", "router",
          "spk", "llm", "tokenizer", "tts")

    def __init__(self, cfg):
        self.cfg = cfg
        self.shared = SharedEnv(cfg)
        self._stages = {}
        self._lock = threading.Lock()
        # 准入闸（她 2026-11-04 加）：**进程内**限并发，管不到别的进程（视频不受影响）。
        mc = int(cfg.get("server", "max_concurrent", "1") or 1)
        self.max_concurrent = max(1, mc)
        self.gate_timeout_s = float(cfg.get("server", "gate_timeout_s", "30") or 30)
        self._gate = threading.BoundedSemaphore(self.max_concurrent)
        self._gate_lock = threading.Lock()
        self.gate_stats = {"runs": 0, "timeouts": 0, "wait_ms_total": 0,
                           "wait_ms_max": 0, "running": 0}

    # ---- 装配 --------------------------------------------------------------
    def active(self, stage):
        v = self.cfg.get("chain", stage, "")
        return (v or "").strip().lower()

    def stage(self, stage, override=None):
        """惰性取/建某级的组件；档名 none/空 → None（该级跳过）。

        override 用于**按请求临时换档**（如 `?tts=edge`）；不传则用 config 的 chain 档。
        实例按 (stage, 档名) 缓存，临时档与默认档互不覆盖。
        """
        name = (override or self.active(stage) or "").strip().lower()
        if name in ("", "none", "off"):
            return None
        with self._lock:
            key = (stage, name)
            inst = self._stages.get(key)
            if inst is None:
                from backends import REGISTRY            # 惰性 import，避开环
                table = REGISTRY[stage]
                if name not in table:
                    raise KeyError("%s 档 %r 不存在，可选：%s"
                                   % (stage, name, ", ".join(sorted(table))))
                inst = table[name](self.cfg, self.cfg.profile(stage, name), self.shared)
                self._stages[key] = inst
            return inst

    def reset_session(self, session=None):
        st = self._stages.get("llm")
        if st is not None and hasattr(st, "reset"):
            st.reset(session)

    # ---- 启动即热 ----------------------------------------------------------
    # spk **故意不在**这里：声纹默认不入链，第一次被点名才装、闲置到点自己卸（见 backends/spk.py）
    SHERPA_STAGES = ("vad", "kws", "asr", "tts")

    def warm(self):
        """启动时预热：**先把 sherpa-onnx runtime 拉起来**（唯一那份），再装 active 的 sherpa 档。

        目的（她 2026-11-04）：所有 onnx 模型共用同一个 env，不在请求期反复装载。
        非 sherpa 档（echo/sine/http/remote-api/edge/none/rms/webrtc）跳过。
        单档装载失败不阻断启动（返回错误清单，服务照起）。
        """
        t0 = time.time()
        info = {"stages": {}, "sherpa": False}
        try:
            self.shared.sherpa()
            info["sherpa"] = True
        except Exception as exc:                       # noqa: BLE001
            info["sherpa_error"] = ("%s: %s" % (type(exc).__name__, exc))[:200]
        for stage in self.SHERPA_STAGES:
            if self.active(stage) != "sherpa":
                continue
            try:
                inst = self.stage(stage)
                info["stages"][stage] = "ok" if inst is not None else "skip"
            except Exception as exc:                   # noqa: BLE001
                info["stages"][stage] = ("error: %s: %s" % (type(exc).__name__, exc))[:200]
        info["elapsed_ms"] = int((time.time() - t0) * 1000)
        self.warm_result = info
        return info

    # ---- 编排 --------------------------------------------------------------
    # ---- 准入闸（进程内限并发；2026-11-04 她加）--------------------------------
    @contextlib.contextmanager
    def admit(self):
        """占用一个并发槽。满则在 gate_timeout_s 内排队；超时抛 GateTimeout。

        **只在 lang-server 进程内生效**：它串行化本服务的链请求，不会影响别的进程/GPU 上的任务。
        """
        t0 = time.time()
        timeout = None if self.gate_timeout_s <= 0 else self.gate_timeout_s
        ok = self._gate.acquire(timeout=timeout)
        wait = int((time.time() - t0) * 1000)
        with self._gate_lock:
            self.gate_stats["wait_ms_total"] += wait
            self.gate_stats["wait_ms_max"] = max(self.gate_stats["wait_ms_max"], wait)
            if ok:
                self.gate_stats["running"] += 1
            else:
                self.gate_stats["timeouts"] += 1
        if not ok:
            raise GateTimeout("准入闸超时：并发上限 %d，等了 %.0f s"
                              % (self.max_concurrent, self.gate_timeout_s))
        try:
            yield wait
        finally:
            with self._gate_lock:
                self.gate_stats["running"] = max(0, self.gate_stats["running"] - 1)
                self.gate_stats["runs"] += 1
            self._gate.release()

    def gate_snapshot(self):
        with self._gate_lock:
            snap = dict(self.gate_stats)
        snap["max_concurrent"] = self.max_concurrent
        snap["gate_timeout_s"] = self.gate_timeout_s
        return snap

    def _gate_reject(self, msg, opts=None):
        return {"code": 429, "wake": False, "asr_text": "", "text": "",
                "segments": 0, "chunks": [], "stages_ms": {}, "stages_error": {},
                "msg": msg, "gate": self.gate_snapshot()}

    @staticmethod
    def _locked(inst):
        """取组件的串行锁；没有（自检假件）就用空上下文。"""
        return getattr(inst, "lock", None) or contextlib.nullcontext()

    def run_audio(self, raw, rate_hint=None, opts=None):
        try:
            with self.admit():
                return self._run_audio(raw, rate_hint=rate_hint, opts=opts)
        except GateTimeout as exc:
            return self._gate_reject(str(exc), opts)

    def run_text(self, text, opts=None):
        try:
            with self.admit():
                return self._run_text(text, opts=opts)
        except GateTimeout as exc:
            return self._gate_reject(str(exc), opts)

    def _run_audio(self, raw, rate_hint=None, opts=None):
        """音频字节 → 全链。返回带各段耗时的 dict（形状见 README）。"""
        from backends import audioio
        o = opts or {}
        t0 = time.time()
        out = {"code": 0, "wake": True, "asr_text": "", "text": "",
               "segments": 0, "chunks": [], "stages_ms": {}, "stages_error": {}}

        samples, rate = audioio.decode_payload(raw, rate_hint=rate_hint,
                                               work_rate=WORK_RATE)
        if not samples:
            out["code"] = 1
            out["msg"] = "空音频"
            return out

        def timed(stage, fn):
            """跑 fn：成功 (结果, None)，失败 (None, 错误串)；耗时无论如何都记。"""
            ts = time.time()
            try:
                return fn(), None
            except Exception as exc:                      # 失败不断链
                return None, ("%s: %s" % (type(exc).__name__, exc))[:200]
            finally:
                out["stages_ms"][stage] = int((time.time() - ts) * 1000)

        # 1) sar：前处理/重采样到工作采样率（她定：sar＝音频前处理/重采样段）
        sar = self.stage("sar")
        if sar is not None:
            res, err = timed("sar", lambda: sar.run(samples, rate))
            if err:
                out["stages_error"]["sar"] = err
            else:
                samples, rate = res
        if rate != WORK_RATE:                              # 兜底：sar 缺档但采样率不对
            res, err = timed("sar", lambda: audioio.resample_linear(samples, rate, WORK_RATE))
            if err:
                out["stages_error"]["sar"] = err
                out["code"] = 1
                out["msg"] = "重采样失败：%s" % err
                out["total_ms"] = int((time.time() - t0) * 1000)
                return out
            samples, rate = res, WORK_RATE

        # 1.5) rms：能量门/电平（她 2026-11-04 定：sar → rms → kws）
        #      先用极便宜的帧能量把"整段静音"挡掉，免得为一段无声去跑 KWS/VAD/ASR。
        rms = self.stage("rms")
        if rms is not None:
            res, err = timed("rms", lambda: rms.run(samples))
            if err:
                out["stages_error"]["rms"] = err
            else:
                out["rms"] = res
                if (res or {}).get("silent"):
                    out["wake"] = False
                    out["msg"] = "rms 静音门短路（peak %.1f dBFS / voiced %d ms）" % (
                        res.get("peak_dbfs", -120.0), res.get("voiced_ms", 0))
                    out["total_ms"] = int((time.time() - t0) * 1000)
                    return out

        # 2) kws：唤醒门控（留空占位＝恒放行）——**在 rms 之后、VAD 之前**
        kws = self.stage("kws")
        if kws is not None:
            with self._locked(kws):                   # session 不保证线程安全
                res, err = timed("kws", lambda: kws.run([samples]))
            if err:
                out["stages_error"]["kws"] = err
            else:
                hit, tag = res
                out["wake"] = bool(hit)
                out["kws_tag"] = tag
                if not hit:
                    out["total_ms"] = int((time.time() - t0) * 1000)
                    return out                              # 没唤醒：后面全跳过

        # 2.5) kws_route：**KWS 之后的那次路由**（她 2026-11-04 定）
        #      规则与 tag→去向映射写在 `lang/kws/router.py`；server 只做薄调用。
        kroute = self.stage("kws_route")
        if kroute is not None and out.get("wake"):
            res, err = timed("kws_route",
                             lambda: kroute.run((samples, (out.get("wake"), out.get("kws_tag")))))
            if err:
                out["stages_error"]["kws_route"] = err
            else:
                res = res or {}
                out["kws_route"] = res.get("route")
                out["kws_route_reason"] = res.get("reason")
                action = (res.get("action") or "continue").lower()
                if action == "stop":
                    out["total_ms"] = int((time.time() - t0) * 1000)
                    return out                              # 路由说停：不跑 vad/asr
                if action == "direct":
                    fixed = (res.get("reply") or "").strip()
                    out["text"] = fixed
                    out["route"] = "direct"
                    return self._tail(out, fixed, dict(o, llm=False), t0)  # 固定回应直达 TTS
                if action == "verify":                     # 唤醒词直接点名声纹：不跑 vad/asr
                    return self.spk_verify(out, [samples], o, t0, why="kws_route")

        # 3) vad：切说话段（在 kws/kws_route 之后）；没切出就整段直通（宁可大勿小）
        segs = [samples]
        vad = self.stage("vad")
        if vad is not None:
            with self._locked(vad):
                res, err = timed("vad", lambda: vad.run(samples))
            if err or not res:
                out["stages_error"]["vad"] = err or "vad 没切出段"
            else:
                segs = res
        out["segments"] = len(segs)

        # 4) asr：逐段识别再拼（单段失败由后端自己记，不抛）
        asr = self.stage("asr")
        if asr is not None:
            with self._locked(asr):
                res, err = timed("asr", lambda: asr.run(segs))
            if err:
                out["stages_error"]["asr"] = err
            else:
                text, seg_meta = res
                out["asr_text"] = text
                out["asr_segments"] = seg_meta
        text = (out["asr_text"] or "").strip()
        if not text:
            out["total_ms"] = int((time.time() - t0) * 1000)
            return out                                       # 没识别出内容，到此为止

        # 4.5) router：ASR 后交给 klar 的 NLP 决定「进 LLM / 固定回应 / verify」
        #      —— verify 还没实装：**留空并跳过**（她 2026-11-04 定）
        router = self.stage("router")
        if router is not None:
            res, err = timed("router", lambda: router.run((text, o.get("session"))))
            if err:
                out["stages_error"]["router"] = err
            else:
                action = (res or {}).get("action", "llm")
                out["route"] = action
                if action == "fixed":
                    fixed = (res.get("reply") or "").strip()
                    out["text"] = fixed
                    text = fixed                           # 固定回应：后续分词/TTS 用它（别再落回 ASR 原文）
                    o = dict(o)
                    o["llm"] = False                     # 跳过 LLM
                elif action == "verify":
                    # 声纹：用**当前这条请求**的段去比库（默认 spk=none ⇒ 记 skipped 后返回）
                    return self.spk_verify(out, segs, o, t0, why="router")

        return self._tail(out, text, o, t0)

    # ---- 声纹（spk）：默认不入链，被点名才装载 --------------------------
    def spk_verify(self, out, segs, o, t0, why=""):
        """拿**当前请求的那几段音频**去跟声纹库比，然后把结论念出来（或只回 JSON）。

        她 2026-11-04 定的三条：
          * `chain.spk` 默认 **none** ⇒ 这里只记 `verify_skipped` 就返回（老行为不变）；
          * 真接上时**第一次调用才装模型**（`SpkSherpa.__init__` 不碰 onnx），跑完不缓存会话；
          * 闲置到 `spk_profiles.sherpa.idle_unload_s` 由 spk 档自己丢掉（也可 /spk/unload 手动卸）。
        """
        out["verify_why"] = why
        st = self.stage("spk")
        if st is None:
            out["verify_skipped"] = True
            out["verify_note"] = "chain.spk=none（声纹默认不入链；要开就设 spk=sherpa）"
            out["total_ms"] = int((time.time() - t0) * 1000)
            return out

        ts = time.time()
        try:
            res = st.run(segs) or {}
            out["spk"] = res
        except Exception as exc:                          # 声纹失败不断链
            res = {}
            out["spk"] = {"error": ("%s: %s" % (type(exc).__name__, exc))[:200]}
            out["stages_error"]["spk"] = out["spk"]["error"]
        finally:
            out["stages_ms"]["spk"] = int((time.time() - ts) * 1000)

        prof = self.cfg.profile("spk", self.active("spk") or "sherpa") or {}
        match = (res.get("match") or "").strip()
        score = float(res.get("score") or 0.0)
        if res.get("error") == "empty" or not (res.get("scores") or match):
            reply = str(prof.get("reply_empty") or "没听到声音，再试一次。")
        elif match:
            reply = str(prof.get("reply_ok") or "声纹通过，是{name}。")
        else:
            reply = str(prof.get("reply_unknown") or "这个声音没在库里。")
        reply = reply.replace("{name}", match).replace("{score}", "%.2f" % score)

        out["route"] = "verify"
        out["text"] = reply
        if str(prof.get("reply_tts", "1")).strip().lower() in ("0", "false", "no", "off"):
            out["total_ms"] = int((time.time() - t0) * 1000)
            return out
        return self._tail(out, reply, dict(o, llm=False), t0)

    def _run_text(self, text, opts=None):
        """文本进（LLM 起）：/llm、/tts 与全链公共尾部共用。"""
        o = opts or {}
        t0 = time.time()
        out = {"code": 0, "wake": True, "asr_text": "", "text": "",
               "segments": 0, "chunks": [], "stages_ms": {}, "stages_error": {}}
        return self._tail(out, (text or "").strip(), o, t0)

    def _tail(self, out, text, o, t0):
        """公共尾部：llm → tokenizer → tts → 合成 + 输出重采样（她定的链尾两环）。"""
        import base64
        from backends import audioio

        # 5) llm：失败降级回识别文本（mimo_bridge 语义）
        reply = text
        if o.get("llm", True):
            llm = self.stage("llm")
            if llm is not None:
                ts = time.time()
                try:
                    reply, meta = llm.run(text, session=o.get("session"))
                    out["llm_ok"] = True
                    for k, v in (meta or {}).items():
                        out.setdefault(k, v)
                except Exception as exc:
                    out["llm_ok"] = False
                    out["stages_error"]["llm"] = ("%s: %s" % (type(exc).__name__, exc))[:200]
                    reply = text                            # 降级
                finally:
                    out["stages_ms"]["llm"] = int((time.time() - ts) * 1000)
        out["text"] = (reply or "").strip()

        # 6) tokenizer：长句切短（减 TTS 峰值 RSS）→ 7) tts 逐段 → 8) 合成＋输出重采样
        if o.get("tts", True) and out["text"]:
            chunks = [out["text"]]
            tok = self.stage("tokenizer")
            if tok is not None:
                ts = time.time()
                try:
                    chunks = [c for c in tok.run(out["text"]) if c.strip()]
                except Exception as exc:
                    out["stages_error"]["tokenizer"] = str(exc)[:200]
                finally:
                    out["stages_ms"]["tokenizer"] = int((time.time() - ts) * 1000)
            out["chunks"] = chunks

            tts = self.stage("tts", o.get("tts_tier"))
            if tts is not None and chunks:
                pieces, ts = [], time.time()
                try:
                    for ch in chunks:
                        with tts.lock:                      # 逐段串行合成
                            smp, rt = tts.run(ch)
                        if smp:
                            pieces.append((smp, rt))
                except Exception as exc:
                    out["stages_error"]["tts"] = str(exc)[:200]
                finally:
                    out["stages_ms"]["tts"] = int((time.time() - ts) * 1000)
                if pieces:
                    out_rate = int(o.get("out_rate", 0) or
                                   self.cfg.get("server", "out_rate", "0") or 0)
                    ts = time.time()
                    merged, mrate = audioio.concat(pieces, gap_s=self._gap_s(),
                                                   out_rate=out_rate)
                    out["stages_ms"]["mix"] = int((time.time() - ts) * 1000)
                    wav = audioio.encode_wav(merged, mrate)
                    if o.get("format") == "wav":            # 要裸 wav 的走这条
                        out["_wav"] = wav
                    else:
                        out["audio_b64"] = base64.b64encode(wav).decode("ascii")
                    out["audio_rate"] = mrate
                    out["audio_ms"] = int(len(merged) / float(mrate) * 1000)

        out["total_ms"] = int((time.time() - t0) * 1000)
        return out

    def _gap_s(self):
        return float(self.cfg.get("server", "chunk_gap_ms", "80") or 80) / 1000.0
