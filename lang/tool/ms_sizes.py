#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板端跑：盘点 FunASR 官方（含 -onnx 兄弟仓）与 sherpa-onnx 镜像仓里到底有哪些权重文件、多大。

只依赖标准库，走 www.modelscope.cn 的仓库文件接口（板端实测该域名 443 可达，HF 不可达）。

用法（板端）：
  python3 /root/funasr/asr/ms_sizes.py                # 跑内置候选清单
  python3 /root/funasr/asr/ms_sizes.py --min 1        # 只列 >=1 MB 的文件
  python3 /root/funasr/asr/ms_sizes.py iic/SenseVoiceSmall-onnx ...   # 只查指定仓库

命中（HTTP 200）就把文件逐个列出来，没命中只记 HTTP 码——404 也是结论。
"""

import json
import sys
import urllib.error
import urllib.request

REPOS = [
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch-onnx",
    "damo/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1",
    "damo/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1-onnx",
    "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1-onnx",
    "iic/speech_paraformer-large_asr_nat-en-16k-common-vocab10865-onnx",
    "iic/speech_paraformer_asr-en-16k-vocab4199-large-pytorch-onnx",
    "iic/speech_paraformer-large_asr_nat-en-16k-common-vocab96370-onnx",
    "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    "iic/SenseVoiceSmall",
    "iic/SenseVoiceSmall-onnx",
    "iic/sensevoice_small-onnx",
    "damo/sensevoice",
    "iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
    "damo/punc_ct-transformer_cn-en-common-vocab471067-large-onnx",
    "csukuangfj/sherpa-onnx-paraformer-zh-2023-03-28",
    "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
    "csukuangfj/sherpa-onnx-paraformer-zh-int8-2024-03-09",
    "csukuangfj/sherpa-onnx-streaming-paraformer-zh-cn-16k-common-2022-12-02",
    "csukuangfj/sherpa-onnx-whisper-tiny",
    "csukuangfj/sherpa-onnx-whisper-base",
    "csukuangfj/sherpa-onnx-whisper-small",
]

API = "https://www.modelscope.cn/api/v1/models/%s/repo/files?Revision=master&Recursive=true"


def mb(n):
    return "%.1f MB" % (n / 1048576.0)


def one(repo, min_mb):
    try:
        raw = urllib.request.urlopen(API % repo, timeout=25).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return "%-76s HTTP %s" % (repo, e.code)
    except Exception as e:
        return "%-76s ERR %s" % (repo, repr(e)[:40])
    try:
        files = json.loads(raw)["Data"]["Files"]
    except Exception:
        return "%-76s NOJSON %s" % (repo, raw[:50])
    big = sorted([f for f in files if f["Type"] == "blob" and f["Size"] >= min_mb * 1048576],
                 key=lambda f: -f["Size"])
    onnx_total = sum(f["Size"] for f in files if f["Path"].lower().endswith(".onnx"))
    out = ["%-76s 文件>=%gMB: %d  onnx 合计 %s" % (repo, min_mb, len(big), mb(onnx_total))]
    aux = sorted(f["Path"] for f in files if f["Type"] == "blob" and f["Size"] < min_mb * 1048576)
    for f in big[:8]:
        out.append("      %-62s %s" % (f["Path"], mb(f["Size"])))
    need = [p for p in aux if p.split("/")[-1] in
            ("config.yaml", "am.mvn", "tokens.json", "configuration.json", "bpe.model",
             "dict.txt", "deduction.txt", "multilingual.tiktoken", "README.md")]
    out.append("      辅助小文件: %s" % (", ".join(need) if need else "(无)"))
    return "\n".join(out)


def main():
    argv = [a for a in sys.argv[1:]]
    min_mb = 5.0
    repos = REPOS
    if "--min" in argv:
        i = argv.index("--min")
        min_mb = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if argv:
        repos = argv
    print("候选 %d 个仓库，阈值 %g MB（ModelScope 文件接口，板端直连）" % (len(repos), min_mb))
    for r in repos:
        print(one(r, min_mb))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
