#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板端 ASR 后端封装：链路只认 build(name, model_dir, threads) 和 be(text, ms)。

现役两档（2026-11-04 她裁「把 fsmn 和 funasr 删了」，funasr-onnx 四档已删）：
  remote-api               = **不在板上推理**：段音频按 HTTP 送到远端，文本由远端回（10-27 加）
  sensevoice-sherpa        = sherpa-onnx 跑 SenseVoiceSmall（保留，只作交叉核对）

删掉的四档（历史见 git：asr_chain/funasrarm/asr/backends.py @ 11248e88 之前）：
  funasr-paraformer / funasr-paraformer-online / funasr-sensevoice / funasr-contextual
  —— 它们都 `from funasr_onnx.*` 懒加载，随 funasr 依赖一起删除。

**公开更正（10-11）**：本页旧版写着"funasr-onnx 那条走不通：sentencepiece 在 PyPI 上只有
cp39+ 的 aarch64 轮子，板上是 cp38，源码编译要 cmake"。这句是错的——PyPI/aliyun 上有
`sentencepiece-0.2.0-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl`（1.3 MB），
已直接装进 /root/funasr/venv，`import sentencepiece` 版本自报 0.2.0，SenseVoiceSmall 装载实测
35.5 s／VmHWM 397.0 MiB 通过 0.5 G 门禁。⇒ **SenseVoice 不需要任何交叉编译**，
本机 podman 那台（localhost/fpai-ub2004:tts-saved）本轮没有出动。

librosa 与 sentencepiece 由 /root/funasr/asr/shims 里的垫片顶掉（见那两个文件头部说明）：
链路一律把 16 kHz 单声道 float32 ndarray 直接喂模型，正常不会走到 librosa.load；
垫片一旦被调用就抛，免得把测试数据做脏。**sentencepiece 那层现在是真包，垫片已改名
sentencepiece.py.disabled 存档**（sys.path[0] 会让垫片盖掉真包，所以必须挪开）。

不出声：这里只读音频数组，不开任何 playback 设备。
"""

import os
import time

def _to_f32(pcm16_bytes):
    import array
    a = array.array("h")
    a.frombytes(pcm16_bytes[: len(pcm16_bytes) // 2 * 2])
    return [v / 32768.0 for v in a]


def _text_from(res):
    """funasr-onnx 各家返回形状不一：str / {'preds':..} / {'text':..} / 列表，统一成文本。"""
    if res is None:
        return ""
    if isinstance(res, dict):
        for k in ("preds", "text", "sentence"):
            if k in res:
                return _text_from(res[k])
        return ""
    if isinstance(res, (list, tuple)):
        # 离线 Paraformer 的 preds＝(整句, 逐字列表)，只取整句；整元组一起拼会把每段说两遍
        # （10-12 板上核文本时才看见，此前所有 funasr-paraformer 场的 text 都带这个重复）。
        if (len(res) == 2 and isinstance(res[0], str)
                and isinstance(res[1], (list, tuple))):
            return res[0].strip()
        return " ".join(_text_from(x) for x in res).strip()
    return str(res).strip()


class RemoteApi(object):
    """远程 ASR 档：板上不留声学模型，段音频按 HTTP 送出去（协议与配置见 remote_asr.py）。

    这个类唯一的职责是把 remote_asr.RemoteAsr 塞进 BUILDERS 那张表，让链路对
    "本地 ONNX 档"和"远端服务"用同一句 be(payload) -> (text, ms)。
    所以它**不装载任何东西**、也不做 model_ready 检查：model_dir 这个位置放的是 endpoint。
    远端连不上不会把链带崩——单段失败由 AsrWorker 记 asr_status=error 后继续。
    """

    name = "remote-api"

    def __init__(self, model_dir, threads=4, opts=None):
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from remote_asr import RemoteAsr
        o = dict(opts or {})
        if model_dir and str(model_dir).startswith("http"):
            o.setdefault("url", model_dir)
        self.c = RemoteAsr(o)

    def __call__(self, pcm16_bytes):
        return self.c(pcm16_bytes)

    def describe(self):
        return self.c.describe()

    def health(self, timeout=3.0):
        return self.c.health(timeout)

    def stats(self):
        return self.c.stats()

    def close(self):
        self.c.close()


class SenseVoiceSherpa(object):
    """SenseVoiceSmall 走 sherpa-onnx：同一份权重、另一套运行时，留着只作交叉核对。

    两条已核实的形状差异（板上实测，不是文档）：
      · from_sense_voice 要求 model.onnx 的 custom metadata 里有 vocab_size，官方 int8 档
        只有 {'onnx.infer': 'onnxruntime.quant'} ⇒ **model_quant.onnx 这条路是死的**，
        这一档只能用非量化 model.onnx（板上没有；10-10 三档 recognizer 全 rc=255 就是这条）。
      · funasr-onnx 侧 `self.blank_id = 0`，而 sherpa-onnx 要求 tokens.txt 里必须有个叫
        <blk>/<eps>/<blank> 的符号并按名字查 id（板上 libsherpa_onnx*.so 的报错字符串）。
        所以把 0 号位从 `<unk>` 改名成 `<blk>` 写进 tokens.txt——id 不变，只换名字。
    """

    name = "sensevoice-sherpa"

    def __init__(self, model_dir, threads=4, opts=None):
        import json
        import sherpa_onnx
        model = os.path.join(model_dir, "model_quant.onnx")
        if not os.path.exists(model):
            model = os.path.join(model_dir, "model.onnx")
        # ModelScope 官方仓只发 tokens.json，实测是 25055 项的 list，下标＝token id；
        # sherpa-onnx 要的是每行 "符号 id" 的 tokens.txt。
        tokens = os.path.join(model_dir, "tokens.txt")
        if not os.path.exists(tokens):
            with open(os.path.join(model_dir, "tokens.json")) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                pairs = sorted((v, k) for k, v in data.items())
            else:
                pairs = list(enumerate(data))
            with open(tokens, "w") as fh:
                for i, tok in pairs:
                    if i == 0 and tok == "<unk>":
                        tok = "<blk>"
                    fh.write("%s %d\n" % (tok, i))
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, num_threads=threads, sample_rate=16000,
            language="zh", use_itn=True)

    def __call__(self, pcm16_bytes):
        import numpy as np
        wave = np.array(_to_f32(pcm16_bytes), dtype=np.float32)
        t0 = time.time()
        stream = self.rec.create_stream()
        stream.accept_waveform(16000, wave)
        self.rec.decode_stream(stream)
        return stream.result.text.strip(), int((time.time() - t0) * 1000)

    def close(self):
        pass


# 后端名→板上默认权重目录的表在 asr_chain.py 里（slot 档不带 venv 也要能跑，
# 所以那份表不能依赖本文件的 import）。

# build() 把 opts 当第三个位置参数喂给每一档，所以所有 __init__ 必须是 (model_dir, threads, opts)；
# 不读 opts 的档也要收下并忽略，否则整批在装载那一步 TypeError。

BUILDERS = {
    RemoteApi.name: RemoteApi,
    SenseVoiceSherpa.name: SenseVoiceSherpa,
}


def build(name, model_dir, threads=4, opts=None):
    if name not in BUILDERS:
        raise SystemExit("未知后端 %s，可选：%s" % (name, ", ".join(sorted(BUILDERS))))
    return BUILDERS[name](model_dir, threads, opts)
