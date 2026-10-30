#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voiceprint.py — 声纹录入 / 校验（sherpa-onnx campplus-zh）。

模型：/mnt/data/models/speaker/campplus-zh-common.onnx（28.3 MB，192 维）
实测（板上）：RTF ≈ 0.24（10s 音频 2.46s），峰值 RSS 134 MB（含 sherpa-onnx 本体）
区分度实测：同一文件 cos=1.0000，不同人 cos=-0.074 / -0.088

用法
    python3 voiceprint.py enroll --wav owner.wav --name owner
    python3 voiceprint.py verify --wav test.wav  --threshold 0.5
    python3 voiceprint.py list

声纹库落盘：/mnt/data/klar/voiceprints.json（name -> 192 维向量）
"""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

MODEL = "/mnt/data/models/speaker/campplus-zh-common.onnx"
STORE = os.environ.get("KLAR_VOICEPRINT_STORE", "/mnt/data/klar/voiceprints.json")
DEFAULT_THRESHOLD = 0.5


def _import():
    import sherpa_onnx
    return sherpa_onnx


def read_16k(path):
    with wave.open(path) as f:
        sr = f.getframerate()
        x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000:
        if sr == 48000:
            x = ((x[:-2] + x[1:-1] + x[2:]) / 3.0)[::3]
        else:
            n = int(len(x) * 16000 / sr)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        sr = 16000
    return x.astype(np.float32), sr


class Voiceprint:
    """进程内单例：一个 extractor 共用（shared env，和语音链同一套 sherpa-onnx）。"""

    _inst = None

    def __init__(self, num_threads=2):
        if not os.path.exists(MODEL):
            raise RuntimeError("声纹模型不存在: %s" % MODEL)
        so = _import()
        self._ex = so.SpeakerEmbeddingExtractor(
            so.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=num_threads))
        self.dim = self._ex.dim

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def embed_samples(self, x, sr=16000):
        ex = self._ex
        st = ex.create_stream()
        ch = int(sr * 0.1)
        for i in range(0, len(x), ch):
            st.accept_waveform(sr, x[i:i + ch])
        st.input_finished()
        while not ex.is_ready(st):
            time.sleep(0.001)
        return np.asarray(ex.compute(st), dtype=np.float32)

    def embed_wav(self, path):
        x, sr = read_16k(path)
        return self.embed_samples(x, sr)


def load_store():
    if os.path.exists(STORE):
        with open(STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "dim": 192, "speakers": {}}


def save_store(store):
    os.makedirs(os.path.dirname(STORE) or ".", exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)


def cos(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def verify(emb, store=None, threshold=DEFAULT_THRESHOLD):
    """返回 (matched_name or None, best_score, scores_dict)."""
    store = store or load_store()
    scores = {}
    for name, vec in (store.get("speakers") or {}).items():
        scores[name] = cos(emb, vec)
    if not scores:
        return None, 0.0, {}
    best = max(scores, key=scores.get)
    if scores[best] >= threshold:
        return best, scores[best], scores
    return None, scores[best], scores


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll"); e.add_argument("--wav", required=True); e.add_argument("--name", required=True)
    v = sub.add_parser("verify"); v.add_argument("--wav", required=True)
    v.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    v.add_argument("--json", action="store_true")
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "list":
        st = load_store()
        spk = st.get("speakers") or {}
        print("声纹库 %s：%d 人（dim=%d）" % (STORE, len(spk), st.get("dim", 0)))
        for n in spk:
            print("  -", n)
        return 0

    vp = Voiceprint.get()
    t0 = time.time()
    emb = vp.embed_wav(args.wav)
    el = time.time() - t0

    if args.cmd == "enroll":
        st = load_store()
        st.setdefault("speakers", {})[args.name] = [float(x) for x in emb]
        st["dim"] = len(emb)
        save_store(st)
        print("已录入 %r（%d 维，用时 %.2fs），库中共 %d 人"
              % (args.name, len(emb), el, len(st["speakers"])))
        return 0

    name, score, scores = verify(emb, threshold=args.threshold)
    if args.json:
        print(json.dumps({"matched": name, "score": score, "threshold": args.threshold,
                          "scores": scores, "elapsed_s": el}, ensure_ascii=False))
    else:
        print("声纹校验：%s（最佳 %.3f，阈值 %.2f，用时 %.2fs）"
              % (("匹配 " + name) if name else "未匹配", score, args.threshold, el))
        for n, s in sorted(scores.items(), key=lambda x: -x[1]):
            print("   %-12s %.4f" % (n, s))
    return 0 if name else 1


if __name__ == "__main__":
    sys.exit(main())
