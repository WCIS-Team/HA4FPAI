#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAM 门禁探针（板端跑）：一个后端装载＋一段真音频推理，报告峰值内存。

用法（板端）：
  /root/funasr/venv/bin/python /root/funasr/asr/load_probe.py \
      --backend funasr-paraformer --model-dir /root/funasr/models/Paraformer-large \
      --wav /root/funasr/testdata/a067.wav --seconds 20

判据：她定的是"ram 占用超过 0.5g 就不试了" ⇒ 取本进程峰值 VmHWM（≈ru_maxrss）与 512 MiB 比。
VmHWM 是"进程自己曾占用的物理内存峰值"，比瞬时 VmRSS 严格（不会因为采样时机好而漏判）。
附带报系统 MemAvailable 前后差＝这块 993 MB 板子上装载后还剩多少余量。

不出声：只读 wav，不打开 playback。
"""

import argparse
import json
import re
import sys
import time
import wave

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GATE_MIB = 512.0


def status_kb(field):
    val = 0
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                val = int(re.search(r"(\d+)", line).group(1))
                break
    return val


def mem_available_kb():
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    return -1


def read_wav(path, seconds):
    wf = wave.open(path, "rb")
    rate = wf.getframerate()
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
        raise SystemExit("要 16 bit 单声道 wav：%s" % path)
    n = int(seconds * rate)
    payload = wf.readframes(n)
    wf.close()
    return rate, payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--jsonl", default="")
    args = ap.parse_args()

    avail0 = mem_available_kb()
    hwm0 = status_kb("VmHWM")

    _rate, payload = read_wav(args.wav, args.seconds)
    audio_s = len(payload) / 2.0 / 16000.0

    sys.path.insert(0, "/root/funasr/asr")
    import backends

    t0 = time.time()
    be = backends.build(args.backend, args.model_dir, args.threads)
    load_s = time.time() - t0
    load_hwm = status_kb("VmHWM")
    load_rss = status_kb("VmRSS")

    t1 = time.time()
    text, asr_ms = be(payload)
    first_ms = (time.time() - t1) * 1000.0
    infer_hwm = status_kb("VmHWM")

    t2 = time.time()
    text2, asr_ms2 = be(payload)
    second_ms = (time.time() - t2) * 1000.0

    avail1 = mem_available_kb()
    peak_mib = max(load_hwm, infer_hwm) / 1024.0
    rec = {
        "backend": args.backend,
        "model_dir": args.model_dir,
        "threads": args.threads,
        "audio_s": round(audio_s, 3),
        "load_s": round(load_s, 2),
        "load_hwm_mib": round(load_hwm / 1024.0, 1),
        "load_rss_mib": round(load_rss / 1024.0, 1),
        "infer_hwm_mib": round(infer_hwm / 1024.0, 1),
        "peak_hwm_mib": round(peak_mib, 1),
        "gate_mib": GATE_MIB,
        "pass_gate": peak_mib <= GATE_MIB,
        "sys_avail_before_mib": round(avail0 / 1024.0, 1),
        "sys_avail_after_mib": round(avail1 / 1024.0, 1),
        "first_infer_ms": round(first_ms, 1),
        "second_infer_ms": round(second_ms, 1),
        "asr_ms_reported": [asr_ms, asr_ms2],
        "rtf_second": round(second_ms / 1000.0 / audio_s, 4) if audio_s else None,
        "text_head": text2[:60],
        "chars": len(text2),
    }
    print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    print("PROBE %s peak=%.1f MiB gate=%.0f MiB %s load=%.1fs rtf=%.3f chars=%d"
          % (args.backend, peak_mib, GATE_MIB,
             "PASS" if rec["pass_gate"] else "OVER", load_s,
             rec["rtf_second"] or -1, rec["chars"]), flush=True)
    if args.jsonl:
        with open(args.jsonl, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0 if rec["pass_gate"] else 2


if __name__ == "__main__":
    sys.exit(main())
