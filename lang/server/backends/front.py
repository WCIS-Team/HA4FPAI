#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""front.py —— 链路前段的三档：sar（前处理/重采样）、vad（切段）、kws（唤醒门控）。

sar（她定＝音频前处理/重采样段）：
  auto       48000→16000 走 decimate3-mavg3（HA4FPAI 实测正解），其余方向线性（默认）
  linear     一律线性插值
  decimate3  强制 decimate3（率不对 3 整除会报错，别硬来）

vad：
  webrtc   webrtcvad 纯 C 档（板上两条链的现役分段器；没装该包则该档不可用）
  silero   silero v4/v5 onnx 直跑（经 shared 共享 onnxruntime——「共用 env」的正主之一）。
           输入名按 v4（input/state/sr）与 v5（input/h/c）自适应，模型路径在档里给。
           未上板验证：接模型时以 /health 的 vad 档自检为准。

kws：
  passthrough 留空占位：恒放行（她定 2026-11-04 的默认档）
  sherpa      sherpa-onnx KWS 档（zipformer transducer，2026-11-04 实现）。
              参数全录见 ../../kws/para.md；模型在 ../../kws/models/。
              要 sherpa-onnx 包在位，且 kws_profiles.sherpa.model_dir 指到模型目录。
"""

import array
import os
import sys

from backends import audioio
from core import Stage, WORK_RATE


def _f32(x):
    return [max(-1.0, min(1.0, float(v))) for v in x]


def _lang_dir():
    """lang/ 根（server/ 的**上级**）。现成资产（vad/、kws/）都在那儿，复用不重写。

    本文件在 server/backends/ 下 ⇒ 上溯两级才是 lang/ 根：
    backends/ → server/ → lang/
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _lang_sub(*parts):
    return os.path.join(_lang_dir(), *parts)


# ---------------------------------------------------------------- sar
class SarAuto(Stage):
    name = "auto"

    def run(self, samples, rate):
        return audioio.to_work_rate(samples, rate, WORK_RATE)


class SarLinear(Stage):
    name = "linear"

    def run(self, samples, rate):
        if rate != WORK_RATE:
            return audioio.resample_linear(samples, rate, WORK_RATE), WORK_RATE
        return samples, rate


class SarDecimate3(Stage):
    name = "decimate3"

    def run(self, samples, rate):
        if rate != 48000:
            raise ValueError("decimate3 只认 48000 进（来了 %d），改用 auto/linear" % rate)
        return audioio.decimate3_mavg3(samples), 16000


# ---------------------------------------------------------------- vad
class VadWebrtc(Stage):
    """复用 `lang/vad/webrtc/`（自包含：带板上 aarch64 的 `.so` 与包装）。

    那边的 `WebRtcSeg.ratio(frame)` 是给「100 ms 一帧」设计的（返回语音子帧占比 0..1，
    20 ms 子帧 ×5）。这里整段进来 ⇒ 也按 **100 ms** 喂，再用双门限迟滞判段
    （open_ratio 起段 / close_ratio 断句 / min_gap 合并 / min_speech 滤短）。

    ⚠️ **两条踩过的坑（2026-11-04 实测）**：
      1. `pylite/webrtcvad.py` 顶部 `import pkg_resources`，而 setuptools≥81 已把它移除
         （本机 setuptools 82 ⇒ `ModuleNotFoundError: pkg_resources`）。它只用它取**版本号**，
         所以本档**绕过那个包装**，直接吃同目录的 C 扩展 `_webrtcvad`；
      2. `.so` 只有 **aarch64** 版（板上用）。本机 Windows 没有可用 `.so` ⇒ 这一档在本机
         必然 FAIL，**板上跑才是判据**——失败时会明说缺什么，不会假装成功。
    """
    name = "webrtc"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = self.profile
        self.mode = int(p.get("mode", 2) or 2)
        self.open_ratio = float(p.get("open_ratio", 0.6) or 0.6)
        self.close_ratio = float(p.get("close_ratio", 0.3) or 0.3)
        self.frame = int(WORK_RATE * 0.1)              # 100 ms 一帧（与链路主循环同节奏）
        self.min_speech = int(WORK_RATE * float(p.get("min_speech_ms", 200) or 200) / 1000)
        self.min_gap = int(WORK_RATE * float(p.get("min_gap_ms", 300) or 300) / 1000)
        wd = _lang_sub("vad", "webrtc")
        if wd not in sys.path:
            sys.path.insert(0, wd)
        pl = os.path.join(wd, "pylite")
        if os.path.isdir(pl) and pl not in sys.path:
            sys.path.insert(0, pl)
        try:
            from webrtcseg import WebRtcSeg
            self.seg = WebRtcSeg(mode=self.mode, sub_ms=20, rate=WORK_RATE)
        except ModuleNotFoundError as exc:
            if "pkg_resources" not in str(exc) and "_webrtcvad" not in str(exc):
                raise
            try:
                import _webrtcvad as _c               # 绕过旧包装，直接用 C 扩展
            except ModuleNotFoundError:
                raise ValueError(
                    "webrtc 档要 _webrtcvad 的 C 扩展：%s/pylite/ 里只有 **aarch64** 版 .so，"
                    "本机（%s）跑不了——这一档只能在板上验证；本机想测 VAD 用 silero/rms 档"
                    % (wd, sys.platform))
            _vad, _process = _c.create, _c.process
            try:
                _c.init(_vad)
            except Exception:
                pass

            class _MinimalVad(object):                 # 只实现 is_speech 的最小包装
                def __init__(self, mode):
                    self.h = _c.create()
                    _c.init(self.h)
                    _c.set_mode(self.h, int(mode))

                def is_speech(self, buf, rate):
                    return bool(_process(self.h, rate, buf, len(buf) // 2))

            class _Ratio(object):                       # 与 WebRtcSeg.ratio 同口径
                def __init__(self, mode, rate):
                    self.v = _MinimalVad(mode)
                    self.rate = rate

                def ratio(self, data):
                    n = len(data) // (self.rate * 20 // 1000 * 2)
                    if n <= 0:
                        return 0.0
                    step = self.rate * 20 // 1000 * 2
                    hit = sum(1 for i in range(n)
                              if self.v.is_speech(data[i * step:(i + 1) * step], self.rate))
                    return hit / float(n)

            self.seg = _Ratio(self.mode, WORK_RATE)

    def run(self, samples):
        a = array.array("h", (max(-32768, min(32767, int(v * 32768.0))) for v in samples))
        flags = []
        for i in range(0, len(a) - self.frame + 1, self.frame):
            flags.append((i, self.seg.ratio(a[i:i + self.frame].tobytes())))
        runs, start, last = [], None, None              # 双门限迟滞
        speaking = False
        for pos, ratio in flags:
            if speaking:
                if ratio <= self.close_ratio:
                    speaking = False
                    if start is not None and pos - last >= self.min_gap:
                        runs.append((start, last))
                        start = None
                else:
                    last = pos + self.frame
            elif ratio >= self.open_ratio:
                speaking = True
                start = pos
                last = pos + self.frame
        if start is not None:
            runs.append((start, last))
        return [_f32(samples[s:e]) for s, e in runs if e - s >= self.min_speech]


class VadRms(Stage):
    """能量双门限档：复用 `lang/vad/rms.py` 的 `dbfs()`（纯标准库、零依赖）。"""
    name = "rms"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = self.profile
        self.open_dbfs = float(p.get("open_dbfs", -35) or -35)
        self.close_dbfs = float(p.get("close_dbfs", -40) or -40)
        self.frame = int(WORK_RATE * 0.1)
        self.min_speech = int(WORK_RATE * float(p.get("min_speech_ms", 200) or 200) / 1000)
        self.min_gap = int(WORK_RATE * float(p.get("min_gap_ms", 300) or 300) / 1000)
        vd = _lang_sub("vad")
        if vd not in sys.path:
            sys.path.insert(0, vd)
        import rms
        self.rms = rms

    def run(self, samples):
        a = array.array("h", (max(-32768, min(32767, int(v * 32768.0))) for v in samples))
        flags = []
        for i in range(0, len(a) - self.frame + 1, self.frame):
            flags.append((i, self.rms.dbfs(a[i:i + self.frame].tobytes())))
        runs, start, last = [], None, None
        speaking = False
        for pos, db in flags:
            if speaking:
                if db < self.close_dbfs:
                    speaking = False
                    if start is not None and pos - last >= self.min_gap:
                        runs.append((start, last))
                        start = None
                else:
                    last = pos + self.frame
            elif db >= self.open_dbfs:
                speaking = True
                start = pos
                last = pos + self.frame
        if start is not None:
            runs.append((start, last))
        return [_f32(samples[s:e]) for s, e in runs if e - s >= self.min_speech]


class VadSherpa(Stage):
    """Silero VAD using the official VoiceActivityDetector API.

    Older vendor wheels exposing Vad remain supported. Each run is a complete
    independent stream; reset and flush prevent state leakage and lost tails.
    """
    name = "sherpa"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        sherpa_onnx = self.shared.sherpa()       # 唯一运行时（与 VAD/KWS/ASR/TTS 共用）
        factory = getattr(sherpa_onnx, "VoiceActivityDetector", None)
        if factory is None:
            factory = getattr(sherpa_onnx, "Vad", None)
        if factory is None:
            raise ValueError("sherpa-onnx wheel exposes neither VoiceActivityDetector nor Vad")
        c = self.profile
        mcfg = sherpa_onnx.VadModelConfig()
        model = c.get("model", "")
        if not model or not os.path.isfile(model):
            raise ValueError("sherpa-vad 模型不在：%r（vad_profiles.sherpa.model）" % model)
        mcfg.silero_vad.model = model
        mcfg.silero_vad.threshold = float(c.get("threshold", 0.5) or 0.5)
        mcfg.silero_vad.min_silence_duration = float(c.get("min_silence_duration", 0.5) or 0.5)
        mcfg.silero_vad.min_speech_duration = float(c.get("min_speech_duration", 0.25) or 0.25)
        mcfg.sample_rate = int(c.get("sample_rate", WORK_RATE) or WORK_RATE)
        mcfg.num_threads = int(c.get("num_threads", 1) or 1)
        self.win = int(c.get("window_samples", 512) or 512)
        mcfg.silero_vad.window_size = self.win
        self.vad = factory(mcfg)
        import threading
        self._lock = threading.Lock()

    def run(self, samples):
        import numpy as np
        x = np.asarray(samples, dtype=np.float32)
        segs = []
        with self._lock:
            self.vad.reset()
            # Some wheels retain queued segments across reset.
            while not self.vad.empty():
                self.vad.pop()
            try:
                for i in range(0, x.size, self.win):
                    frame = x[i:i + self.win]
                    if frame.size < self.win:
                        frame = np.pad(frame, (0, self.win - frame.size))
                    self.vad.accept_waveform(frame)
                    while not self.vad.empty():
                        segs.append(np.asarray(self.vad.front.samples, dtype=np.float32).tolist())
                        self.vad.pop()
                self.vad.flush()
                while not self.vad.empty():
                    segs.append(np.asarray(self.vad.front.samples, dtype=np.float32).tolist())
                    self.vad.pop()
            finally:
                self.vad.reset()
        return [s for s in segs if s]


class VadSilero(Stage):
    """silero onnx 直跑（v4/v5 自适应）。**共用 onnxruntime** 的示例档。

    模型默认取 `lang/vad/models/silero_vad_v5.onnx`（2.3 MB 那份＝HA4FPAI 板上现役同一份）。
    未上板验证：接模型时以 run 出的段与 stages_error 为准。
    """
    name = "silero"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        self.model_path = (self.profile.get("model")
                           or _lang_sub("vad", "models", "silero_vad_v5.onnx"))
        if not os.path.isfile(self.model_path):
            raise ValueError("silero 模型不在：%s（档里可给 vad_profiles.silero.model）"
                             % self.model_path)
        self.threshold = float(self.profile.get("threshold", 0.5) or 0.5)
        self.win = int(self.profile.get("window_samples", 512) or 512)
        self.min_speech = float(self.profile.get("min_speech_ms", 200) or 200) / 1000.0
        self.sess = self.shared.ort().InferenceSession(self.model_path, self.shared.ort_opts())
        in_names = set(i.name for i in self.sess.get_inputs())
        self.v5 = "h" in in_names and "c" in in_names   # v5: input/h/c；v4: input/state/sr
        if not (self.v5 or "state" in in_names):
            raise RuntimeError("silero 模型输入不认识：%s" % sorted(in_names))
        self._reset()

    def _reset(self):
        import numpy as np
        z = np.zeros((2, 1, 64 if self.v5 else 128), dtype=np.float32)
        self._hc = (z, z.copy()) if self.v5 else z

    def run(self, samples):
        import numpy as np
        out_names = [o.name for o in self.sess.get_outputs()]
        segs, cur = [], []
        x = np.asarray(samples, dtype=np.float32)
        i = 0
        while i + self.win <= x.size:
            win = x[i:i + self.win].reshape(1, self.win)
            if self.v5:
                feed = {"input": win, "h": self._hc[0], "c": self._hc[1]}
            else:
                feed = {"input": win, "state": self._hc,
                        "sr": np.array(WORK_RATE, dtype=np.int64)}
            res = self.sess.run(None, feed)
            by = dict(zip(out_names, res))
            if "output" in by:                           # 输出按名字认，不按顺序猜
                prob = by["output"]
                states = [by[k] for k in out_names if k != "output"]
            else:
                prob = res[-1]
                states = [r for k, r in zip(out_names, res) if k != out_names[-1]]
            if self.v5:
                self._hc = (states[0], states[1])
            else:
                self._hc = states[0]
            p = float(np.asarray(prob).reshape(-1)[0])
            if p >= self.threshold:
                cur.extend(x[i:i + self.win].tolist())
            elif cur:
                segs.append(cur)
                cur = []
            i += self.win
        if cur:
            segs.append(cur)
        return [s for s in segs if len(s) >= int(self.min_speech * WORK_RATE)]


# ---------------------------------------------------------------- kws（留空）
class KwsPassthrough(Stage):
    """留空占位：恒放行。等真 KWS 进来时替换这个档，接口（hit, tag）不变。"""
    name = "passthrough"

    def run(self, segs):
        return True, ""


class KwsSherpa(Stage):
    """sherpa-onnx KWS 档（zipformer transducer）。**2026-11-04 实现**。

    契约：run(segs) -> (hit, tag)。hit=True 表示这段里听到了唤醒词；
    tag 是命中的显示名（keywords.txt 里 @ 后面的字），没命中为空串。

    参数口径见 ../../kws/para.md（sherpa-onnx v2.0.1 源码级全录）。
    本档只暴露那 13 个实例参数里**能改**的：

      model_dir        模型目录（下面 tokens/encoder/decoder/joiner/keywords_file）
      keywords_file    关键词表（相对 model_dir，或给绝对路径）
      keywords_score   boost 加权（默认 1.0）——**加权提升，不是硬约束**
      keywords_threshold 判决门限（默认 0.25）
      num_trailing_blanks 关键词后要几个 blank 才算命中（默认 1）
      num_threads      onnxruntime 线程（板上给 1）
      tail_paddings_s  末尾补静音秒数（默认 0.66，官方 Python 示例值）
      tail_paddings_s=0 就不补——但**可能漏掉最后一个字**

    运行时纪律（para.md §五）：
      * 喂进来的音频必须已经是 **16 kHz**（WORK_RATE）——库不替你重采样；
      * 命中后必须 reset_stream，否则连报；
      * 段末尾补 tail_paddings_s 秒静音，把 streaming transducer 的尾部 blank 冲出来。
    """
    name = "sherpa"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        import os
        sherpa_onnx = self.shared.sherpa()       # 唯一运行时（共用 env）
        self.model_dir = str(self.profile.get("model_dir", "") or "")
        if not self.model_dir:
            self.model_dir = _lang_sub("kws", "models", self.profile.get(
                "model", "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"))
        if not os.path.isdir(self.model_dir):
            raise ValueError("sherpa 档的 model_dir 不在：%s" % self.model_dir)
        self.kws_file = str(self.profile.get("keywords_file", "") or "")
        if self.kws_file and not os.path.isabs(self.kws_file):
            self.kws_file = os.path.join(self.model_dir, self.kws_file)
        if not self.kws_file or not os.path.isfile(self.kws_file):
            # 词表位置回退：模型目录下的 keywords.txt 优先；zh-en 那份**只有**
            # test_wavs/keywords.txt（目录里那个 keywords_raw.txt 是空的原料，不是词表）
            self.kws_file = ""
            for cand in ("keywords.txt", os.path.join("test_wavs", "keywords.txt")):
                p = os.path.join(self.model_dir, cand)
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    self.kws_file = p
                    break
            if not self.kws_file:
                raise ValueError("KWS 词表不在 %s（找过 keywords.txt 与 test_wavs/keywords.txt）；"
                                 "用 kws_profiles.sherpa.keywords_file 指到词表"
                                 % self.model_dir)
        self.score = float(self.profile.get("keywords_score", 1.0))
        self.threshold = float(self.profile.get("keywords_threshold", 0.25))
        self.trailing = int(self.profile.get("num_trailing_blanks", 1))
        self.tail_s = float(self.profile.get("tail_paddings_s", 0.66))

        # ⚠️ **构造前逐个预检**：sherpa-onnx 在 keywords 文件打不开时是
        # `InitKeywords:310 Open keywords file failed` 后 **abort 整个进程**（不是抛异常）
        # ⇒ 缺文件绝不能让它走到构造那一步，否则服务直接死、连报错都来不及。
        self.tokens = os.path.join(self.model_dir, "tokens.txt")
        self.encoder = self.profile.get("encoder") or self._pick("encoder")
        self.decoder = self.profile.get("decoder") or self._pick("decoder")
        self.joiner = self.profile.get("joiner") or self._pick("joiner")
        for path, what in ((self.tokens, "tokens.txt"), (self.encoder, "encoder"),
                           (self.decoder, "decoder"), (self.joiner, "joiner"),
                           (self.kws_file, "keywords_file")):
            if not path or not os.path.isfile(path):
                raise ValueError("KWS %s 不在：%s" % (what, path))
        self.kws = sherpa_onnx.KeywordSpotter(
            tokens=self.tokens,
            encoder=self.encoder,
            decoder=self.decoder,
            joiner=self.joiner,
            keywords_file=self.kws_file,
            num_threads=int(self.profile.get("num_threads", 1)),
            sample_rate=int(self.profile.get("sample_rate", WORK_RATE)),
            feature_dim=int(self.profile.get("feature_dim", 80)),
            keywords_score=self.score,
            keywords_threshold=self.threshold,
            num_trailing_blanks=self.trailing,
        )

    def _pick(self, prefix):
        """模型目录里按前缀挑文件：**int8 优先**（板上省内存），其次任意同名。

        实测文件名带 epoch/chunk 后缀（`encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx`），
        所以不能写死 `encoder.onnx`——那是从没存在过的名字。
        """
        import os
        names = sorted(os.listdir(self.model_dir))
        for f in names:                                  # 第一轮：int8
            if f.startswith(prefix) and f.endswith(".onnx") and ".int8." in f:
                return os.path.join(self.model_dir, f)
        for f in names:                                  # 第二轮：任意精度
            if f.startswith(prefix) and f.endswith(".onnx"):
                return os.path.join(self.model_dir, f)
        raise ValueError("%s 里没有 %s*.onnx" % (self.model_dir, prefix))

    def run(self, segs):
        import numpy as np
        hit, tag = False, ""
        for seg in segs:
            s = self.kws.create_stream()
            x = np.asarray(seg, dtype=np.float32)
            s.accept_waveform(WORK_RATE, x)
            if self.tail_s > 0:                     # 尾部补白，冲出最后的 blank
                s.accept_waveform(WORK_RATE, np.zeros(int(self.tail_s * WORK_RATE), dtype=np.float32))
            s.input_finished()
            while self.kws.is_ready(s):
                self.kws.decode_stream(s)
                r = self.kws.get_result(s)         # v2.0.1 返 str（不是 KeywordResult）
                if r:
                    hit = True
                    tag = r or tag
                    self.kws.reset_stream(s)        # 必须 reset，否则连报
        return hit, tag
