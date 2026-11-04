#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音链路：采集/现成 wav → 分段 → ASR 后端（工作线程）→ 段音频 + JSONL + 心跳。

用法（板端）：
  python3 /root/funasr/asr_chain.py --source mic
  python3 /root/funasr/asr_chain.py --source wav \
      --input /root/funasr/testdata/c247_60.wav --backend remote-api \
      --asr-url http://127.0.0.1:18090/api/v1/asr --pace realtime
  python3 /root/funasr/asr_chain.py --source wav --input /root/tone1k10s.wav

**远程档（10-27 加）**：--backend remote-api 时板上不留声学模型，段音频按 HTTP 送出去，
文本由远端回；配合 --segmenter webrtc（纯 C 判决，无 ORT），整条链路里就没有任何
ONNX 推理（--punc 标点档已随 funasr 删除，2026-11-04），系统 python3 直接能跑：
  python3 /root/funasr/asr_chain.py --source wav --input x.wav \
      --segmenter webrtc --backend remote-api --asr-url http://127.0.0.1:18090/api/v1/asr

输入：--source mic 起 arecord 子进程读 plughw 的原始 PCM；--source wav 读现成文件。
分段：--segmenter rms＝按帧 RMS（dBFS）双门限，段内低于 close 累计到 sil-stop 即断，超过
      max-seg 强制断；webrtc＝WebRTC VAD 子帧占比双门限（纯 C，无权重、无 ORT）。
      原第三档 fvad（FSMN-VAD）已随 funasr 删除（2026-11-04）。两档断句口径一致：
      sil-stop／min-seg／max-seg 同一套。
后端：--backend slot 不推理（只落 wav＋jsonl，asr_status=no_model/slot_pending）；
      --backend remote-api 走远程 HTTP（见 asr/remote_asr.py，板上不留权重）；
      其余档名走 backends.py 的 build()（现役 sensevoice-sherpa），装载在起链时一次，
      推理在工作线程里做，所以主循环的帧节奏不受推理耗时影响——这正是仿实时要量的东西。
标点：--punc 本地 CT_Transformer 档已随 funasr 删除（2026-11-04；历史读数：装载
      35~43 s、VmHWM 530+ MiB，见 para.md §八）。
节奏：--pace free 有多少读多少（吞吐量口径）；--pace realtime 按帧时刻表读，
      模拟麦克风的 100 ms 一帧（真麦上读满就会溢出，这里用 qdepth/lag 表达同样的事）。
出口：--state 每秒原子重写 JSON 心跳（判活＝它的 mtime 在走）；--jsonl 每段一行；
      --segs-dir 存段音频 wav。
不出声：全程只读，不打开任何 playback 设备，不写 ALSA 控件，不调 aplay。
"""

import argparse
import collections
import json
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import wave

try:
    import resource
except ImportError:
    resource = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# rms 信号原语（dbfs/decimate/seg_peak_dbfs）住在 ../vad（lang 层），
# 2026-11-04 从本文件抽出。模块级 import：WavSource.read 的 decimate 与
# 主循环的 dbfs 都走它，方法体里查的是模块全局名 _rms_mod。
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vad"))
import rms as _rms_mod  # noqa: E402
rms = _rms_mod  # 主循环裸名 rms.dbfs / rms.seg_peak_dbfs 都指这里
BACKEND_CHOICES = ["slot", "remote-api", "sensevoice-sherpa"]
BACKEND_DIRS = {
    # 远程档没有板上权重：目录位填 endpoint（起链时由 --asr-url 覆盖），不做 model_ready 检查
    "remote-api": "",
    "sensevoice-sherpa": "/root/funasr/models/SenseVoiceSmall",
}


class PcmRing(object):
    """保留最近 keep_s 秒的 PCM，VAD 段闭合时按全流毫秒切片。

    VAD 给的 start/end 是**全流绝对毫秒**（detect_mode=1 时库内部的 frm_cnt 只增不清，
    切完一句走的 ResetDetection() 不动它），所以这里只要记住 buf[0] 对应的样本号就能换算。
    裁剪掉的部分切不回来：返回 lost_ms 让 jsonl 里能看到"这段开头实际丢了多久"，
    并累计到 self.lost_ms_total，别把它当成干净的段。
    """

    def __init__(self, rate, keep_s=30.0):
        self.rate = rate
        self.buf = bytearray()
        self.first_sample = 0
        self.lost_ms_total = 0.0
        self.keep_bytes = int(rate * 2 * keep_s)

    def push(self, data):
        self.buf += data
        if len(self.buf) > self.keep_bytes:
            drop = (len(self.buf) - self.keep_bytes) // 2 * 2
            del self.buf[:drop]
            self.first_sample += drop // 2

    def cut(self, start_ms, end_ms):
        a = int(start_ms * self.rate / 1000.0)
        b = int(end_ms * self.rate / 1000.0)
        lost_ms = 0.0
        if a < self.first_sample:
            lost_ms = (self.first_sample - a) * 1000.0 / self.rate
            self.lost_ms_total += lost_ms
            a = self.first_sample
        if b <= a:
            return b"", round(lost_ms, 1)
        lo = (a - self.first_sample) * 2
        hi = (b - self.first_sample) * 2
        return bytes(self.buf[lo:hi]), round(lost_ms, 1)


def hwm_mib():
    """本进程峰值物理内存（MiB）。Linux 先读 VmHWM，拿不到再退回 ru_maxrss；Windows 返回 None。"""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return round(int(re.search(r"(\d+)", line).group(1)) / 1024.0, 1)
    except Exception:
        pass
    if resource is not None:
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    return None



def proc_mem():
    """本进程 (VmHWM MiB, VmSwap MiB, majflt)。

    板上 2026-10-11 起了 1 G swap（eMMC 直写实测 17.6 MB/s）之后，majflt 是必须盯的一列：
    一次从 swap 读回页就是毫秒级 stall，会直接进 asr_ms 的尾巴，VmSwap 给出换了出去多少。
    """
    hwm = swap = majflt = None
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    hwm = int(re.search(r"(\d+)", line).group(1))
                elif line.startswith("VmSwap:"):
                    swap = int(re.search(r"(\d+)", line).group(1))
        with open("/proc/self/stat") as fh:
            majflt = int(fh.read().rsplit(") ", 1)[1].split()[9])
    except Exception:
        pass
    return (round(hwm / 1024.0, 1) if hwm else None,
            round(swap / 1024.0, 1) if swap is not None else None, majflt)


def sysmem():
    """系统侧 (CmaFree kB, SwapUsed MiB, MemAvailable MiB)，取不到的项为 None。

    CmaFree 是 10-11 那次硬挂死最像的机制（装 232 MB 权重能把 96 MB 压到 0），所以要在链路里
    自己记最低点，不能只靠外部心跳。
    """
    got = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k = line.split(":")[0]
                if k in ("CmaFree", "SwapTotal", "SwapFree", "MemAvailable"):
                    got[k] = int(line.split()[1])
    except Exception:
        pass
    swap_used = None
    if "SwapTotal" in got and "SwapFree" in got:
        swap_used = round((got["SwapTotal"] - got["SwapFree"]) / 1024.0, 1)
    return (got.get("CmaFree"), swap_used,
            round(got["MemAvailable"] / 1024.0, 1) if "MemAvailable" in got else None)


def pct(sorted_vals, q):
    """排序列表的第 q 分位（最近邻），空列表返回 None。"""
    if not sorted_vals:
        return None
    i = int(round((len(sorted_vals) - 1) * q))
    return sorted_vals[i]


class Source(object):
    def __init__(self, rate):
        self.rate = rate

    def read(self, count):
        raise NotImplementedError

    def close(self):
        pass


class WavSource(Source):
    def __init__(self, path, rate):
        self.wf = wave.open(path, "rb")
        self.nch = self.wf.getnchannels()
        self.in_rate = self.wf.getframerate()
        if self.wf.getsampwidth() != 2:
            raise SystemExit("只支持 16 bit PCM wav：%s 是 %d bit" % (path, self.wf.getsampwidth() * 8))
        if self.in_rate < rate or self.in_rate % rate != 0:
            raise SystemExit("采样率 %d 不能整比降采样到 %d" % (self.in_rate, rate))
        self.factor = self.in_rate // rate
        self.rate = rate

    def read(self, count):
        src_samples = (count // 2) * self.factor
        raw = self.wf.readframes(src_samples)
        if not raw:
            return None
        if self.nch == 2:
            mono = bytearray()
            for i in range(0, len(raw) - 3, 4):
                mono += raw[i:i + 2]
            raw = bytes(mono)
        return _rms_mod.decimate(raw, self.factor) or None

    def close(self):
        self.wf.close()


class MicSource(Source):
    def __init__(self, device, rate):
        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1",
               "-r", str(rate), "-D", device]
        # arecord 的 stderr 自己收着，别丢 DEVNULL：设备名填错时它当场退出，链路只看到"零帧"，
        # 而零帧和"房间本来就没声音"在 jsonl/state 里长得一模一样。
        err_r, err_w = os.pipe()
        os.set_blocking(err_r, False)
        self.err_fd = err_r
        self.err_tail = b""
        self.rc = None
        self.device = device
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_w)
        os.close(err_w)              # 父进程这份要关掉，否则子进程死了也读不到 EOF
        self.rate = rate

    def drain_err(self):
        while True:
            try:
                chunk = os.read(self.err_fd, 4096)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not chunk:            # EOF＝arecord 已经走了
                self.rc = self.proc.poll()
                return
            self.err_tail += chunk
            if len(self.err_tail) > 4000:
                self.err_tail = self.err_tail[-4000:]

    def read(self, count):
        self.drain_err()
        data = b""
        while len(data) < count:
            chunk = self.proc.stdout.read(count - len(data))
            if not chunk:
                self.drain_err()     # 报错那一句通常就在 EOF 前一刻写出来
                return data if data else None
            data += chunk
        return data

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.drain_err()
        self.rc = self.proc.poll()
        try:
            os.close(self.err_fd)
        except OSError:
            pass


def write_wav(path, rate, payload):
    tmp = path + ".part"
    wf = wave.open(tmp, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(rate)
    wf.writeframes(payload)
    wf.close()
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    tmp = path + ".part"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def model_ready(model_dir):
    if not model_dir or not os.path.isdir(model_dir):
        return False
    for root, _dirs, files in os.walk(model_dir):
        for name in files:
            if name.endswith((".onnx", ".pt", ".pb", ".bin", ".model", ".imodel")):
                return True
    return False


class AsrWorker(threading.Thread):
    """推理线程：主循环只管读帧和分段，段丢进队列，这里取出来转写并写 jsonl。

    分成两个线程是因为仿实时要量的就是"推理追不追得上"——放在主循环里，
    推理时间会变成读帧的间隙，帧节奏被阻塞，lag 就测不出真实形状了。
    """

    def __init__(self, be, out, state, state_lock, slot_status="", queue_alarm=0,
                 punc=None, tts=None):
        super(AsrWorker, self).__init__()
        self.daemon = True
        self.be = be
        self.punc = punc
        self.tts = tts
        self.q = queue.Queue()
        self.out = out
        self.state = state
        self.lock = state_lock
        self.slot_status = slot_status
        self.queue_alarm = queue_alarm
        self.alarm_hits = 0
        self.stop_now = False
        self.ms = []

    def submit(self, item):
        self.q.put(item)
        if self.queue_alarm and self.q.qsize() > self.queue_alarm:
            self.alarm_hits += 1
            with self.lock:
                self.state["queue_alarm_hits"] = self.alarm_hits

    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                self.q.task_done()
                return
            stamp, path, payload, dur_s, peak, seg_end, tail, seq, extra = item
            if self.stop_now:
                self.q.task_done()
                continue
            t0 = time.time()
            _, _, mf0 = proc_mem()
            text_raw = None
            punc_ms = None
            if self.be is None:
                text, asr_ms, status, err = None, 0, self.slot_status or "no_model", ""
            else:
                try:
                    text_raw, asr_ms = self.be(payload)
                    status, err = "ok", ""
                except Exception as exc:
                    text_raw, status = None, "error"
                    asr_ms, err = int((time.time() - t0) * 1000), repr(exc)[:200]
            # 标点挂在 ASR 之后、写盘之前：text 是最终文本，text_raw 留 ASR 原样，
            # 这样"标点档有没有真的串进来"在 jsonl 里一眼能看出来。
            text = text_raw
            if self.punc is not None and text_raw:
                try:
                    text, punc_ms = self.punc(text_raw)
                except Exception as exc:
                    punc_ms = None
                    err = (err + " | " if err else "") + "punc:" + repr(exc)[:120]
            # TTS 挂在最后一步：文本 → wav 落盘。它和 ASR 一样在工作线程里，
            # 所以 tts_ms 会进 lag_s——这一档就是"板上自己说话要多久"的直接读数。
            tts_path = None
            tts_ms = None
            tts_status = None
            if self.tts is not None and text:
                try:
                    tts_path, tts_ms = self.tts.say(text, seq, stamp)
                    tts_status = "ok"
                except Exception as exc:
                    tts_status = "error"
                    err = (err + " | " if err else "") + "tts:" + repr(exc)[:120]
            wall = time.time()
            _, vmswap, mf1 = proc_mem()
            rec = {
                "seq": seq,
                "utc": stamp,
                "wav": path,
                "dur_s": dur_s,
                "peak_dbfs": peak,
                "samples": len(payload) // 2,
                "text": text,
                "asr_status": status,
                "asr_ms": asr_ms,
                "rtf": round(asr_ms / 1000.0 / dur_s, 4) if dur_s else None,
                "queue_ms": int((t0 - seg_end) * 1000),
                "lag_s": round(wall - seg_end, 3),
                "backend": self.state.get("backend"),
                "majflt": (mf1 - mf0) if (mf0 is not None and mf1 is not None) else None,
                "vm_swap_mib": vmswap,
            }
            if self.punc is not None:
                rec["text_raw"] = text_raw
                rec["punc_ms"] = punc_ms
            if self.tts is not None:
                rec["tts_status"] = tts_status
                rec["tts_ms"] = tts_ms
                rec["tts_wav"] = tts_path
            if err:
                rec["error"] = err
            if extra:
                rec.update(extra)
            if tail:
                rec["tail"] = True
            self.out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.out.flush()
            self.ms.append(asr_ms)
            with self.lock:
                s = self.state
                s["segments_asred"] = s.get("segments_asred", 0) + 1
                s["asr_ms_last"] = asr_ms
                s["vm_swap_mib"] = vmswap
                if mf1 is not None:
                    s["majflt"] = mf1
                s["asr_ms_max"] = max(s.get("asr_ms_max", 0), asr_ms)
                s["asr_ms_total"] = s.get("asr_ms_total", 0) + asr_ms
                s["asred_s_total"] = s.get("asred_s_total", 0.0) + dur_s
                s["rtf_last"] = rec["rtf"]
                s["lag_last"] = rec["lag_s"]
                s["lag_max"] = max(s.get("lag_max", 0.0), rec["lag_s"])
                s["qdepth"] = self.q.qsize()
                s["last"] = rec
                if tts_ms is not None:
                    s["tts_ms_last"] = tts_ms
                    s["tts_ms_total"] = s.get("tts_ms_total", 0) + tts_ms
                    s["tts_ms_max"] = max(s.get("tts_ms_max") or 0, tts_ms)
                if tts_status == "error":
                    s["tts_errors"] = s.get("tts_errors", 0) + 1
                if punc_ms is not None:
                    s["punc_ms_last"] = punc_ms
                    s["punc_ms_total"] = s.get("punc_ms_total", 0) + punc_ms
                    s["punc_ms_max"] = max(s.get("punc_ms_max") or 0, punc_ms)
                if status == "error":
                    s["asr_errors"] = s.get("asr_errors", 0) + 1
            self.q.task_done()


def main():
    ap = argparse.ArgumentParser(description="语音链路（分段＋后端工作线程＋仿实时节奏）")
    ap.add_argument("--source", choices=["mic", "wav"], default="mic")
    ap.add_argument("--device", default="plughw:MCP01,0",
                    help="ALSA 采集设备；默认点名 MCP01（板子上那台 USB 麦的卡短名，来自 USB 产品"
                         "字符串描述符，与插在哪个口、probe 顺序都无关）。"
                         "plughw 的 plug 层在板端把原生 48k 重采样到 --rate")
    ap.add_argument("--input", default="")
    ap.add_argument("--rate", type=int, default=16000)
    ap.add_argument("--frame-ms", type=int, default=100)
    ap.add_argument("--open-dbfs", type=float, default=-45.0)
    ap.add_argument("--close-dbfs", type=float, default=-55.0)
    ap.add_argument("--sil-stop-ms", type=int, default=600)
    ap.add_argument("--min-seg-ms", type=int, default=300)
    ap.add_argument("--max-seg-s", type=float, default=20.0)
    ap.add_argument("--segmenter", choices=["rms", "webrtc"], default="rms",
                    help="rms＝双门限能量分段；webrtc＝WebRTC VAD 子帧占比双门限"
                         "（纯 C，无权重、无 onnxruntime）。fvad 档已随 FSMN/funasr 删除"
                         "（2026-11-04）")
    ap.add_argument("--vad-lead-ms", type=int, default=0,
                    help="切音频时在 VAD 起点前再多带这么多毫秒；默认 0＝库内已含 "
                         "window(200)+lookback(200) 的前向扩展，再补会和上一句尾部重叠")
    ap.add_argument("--vad-min-ms", type=int, default=250,
                    help="VAD 闭合段短于这个长度就丢掉")
    ap.add_argument("--vad-keep-s", type=float, default=40.0,
                    help="PCM 环形缓冲保留多少秒，决定能往回切多远")
    # —— WebRTC VAD 分段（--segmenter webrtc）：纯 C 判决，不吃权重、不要 onnxruntime ——
    ap.add_argument("--webrtc-mode", type=int, default=3,
                    help="WebRTC aggressiveness 0..3：0 最宽松、3 最严。默认 3＝10-27 在 "
                         "c247_60 上扫档的结论：0/1/2 会把整段都判成语音（60 s 只切 4 段、"
                         "两段撞 20 s 上限），3 才切出 8 段、零撞限")
    ap.add_argument("--webrtc-sub-ms", type=int, default=20,
                    help="子帧长度，只认 10/20/30 ms；100 ms 主帧会被切成 5 个 20 ms 子帧逐块判")
    ap.add_argument("--webrtc-open-ratio", type=float, default=0.4,
                    help="一帧里语音子帧占比 ≥ 它才算语音帧；默认 0.4（原来那档）。"
                         "想更严格可给 0.6，但 10-27 实测在 c247_60/a067 上分段结果没变")
    ap.add_argument("--webrtc-open-frames", type=int, default=1,
                    help="要连续几帧达标才起段；默认 1＝单帧就起（原来那档），2＝更严格")
    ap.add_argument("--webrtc-close-ratio", type=float, default=0.2,
                    help="占比 ≤ 它就算静音帧（累计 sil-stop 断句）；默认 0.2＝5 个子帧里"
                         "最多 1 个判语音。取 0 会把换气/齿音全算成语音，断不开句")
    # —— 远程 ASR（--backend remote-api）：板上不留声学模型，段音频走 HTTP ——
    ap.add_argument("--asr-url", default=os.environ.get("ASR_REMOTE_URL", ""),
                    help="远程 ASR endpoint，例如 http://127.0.0.1:18090/api/v1/asr")
    ap.add_argument("--asr-proto", choices=["multipart", "openai", "json"],
                    default="multipart",
                    help="multipart＝HTTP multipart/form-data 字段 audio，回 {code,text}"
                         "（旧名 funasr 已废；注意 FunASR 官方 runtime 是 WebSocket，不是这个）；"
                         "openai＝/v1/audio/transcriptions 字段 file+model（事实标准）；"
                         "json＝base64 JSON")
    ap.add_argument("--asr-field", default="", help="上传字段名，默认 audio(multipart)/file(openai)")
    ap.add_argument("--asr-model", default="", help="openai 协议里的 model 名；默认不传，"
                                                   "需要就显式给（如 whisper-1）")
    ap.add_argument("--asr-token", default=os.environ.get("ASR_REMOTE_TOKEN", ""),
                    help="非空则带 Authorization: Bearer <token>")
    ap.add_argument("--asr-timeout", type=float, default=float(os.environ.get("ASR_REMOTE_TIMEOUT", 30)))
    ap.add_argument("--asr-retry", type=int, default=int(os.environ.get("ASR_REMOTE_RETRY", 1)),
                    help="失败重试次数（不含首次）；asr_ms 记的是含重试的真实往返")
    ap.add_argument("--asr-extra", default="", help="额外字段，形如 language=zh,itn=1")
    # —— 本地标点（默认关：CT_Transformer 装载 35~43 s、VmHWM 530+ MiB）——
    # —— TTS（Piper，常驻服务）：ASR 文本 → wav；服务见 asr/piper_tts_server.py ——
    ap.add_argument("--tts", choices=["none", "piper", "ekho"], default="none",
                    help="piper＝板上 ONNX 档（服务见 asr/piper_tts_server.py，默认 5002）；"
                         "ekho＝板上 ekho-server（纯 C++、不吃 ONNX，默认 2046）")
    ap.add_argument("--tts-url", default="",
                    help="TTS 服务地址；不填用引擎默认（piper 5002 / ekho 2046）")
    ap.add_argument("--tts-dir", default="/root/funasr/tts")
    ap.add_argument("--tts-timeout", type=float,
                    default=float(os.environ.get("TTS_PIPER_TIMEOUT", 120)))
    ap.add_argument("--tts-length-scale", type=float,
                    default=float(os.environ.get("TTS_LENGTH_SCALE", 1.0)),
                    help="<1 说得更快；板上 piper RTF≈1.1~1.8，想追实时可以试 0.8")
    ap.add_argument("--sv-language", default="zh", help="SenseVoice 语言标签，auto＝自动")
    ap.add_argument("--sv-textnorm", default="withitn",
                    help="SenseVoice 逆文本规整：withitn＝数字转阿拉伯字符")
    ap.add_argument("--hotwords", default="", help="funasr-contextual 的热词串，空格分隔")
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--model-dir", default="/root/funasr/models")
    ap.add_argument("--backend", choices=BACKEND_CHOICES, default="slot")
    ap.add_argument("--backend-dir", default="", help="不填就按后端名查板端默认目录")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pace", choices=["free", "realtime"], default="free")
    ap.add_argument("--queue-alarm", type=int, default=0,
                    help=">0 时待转写段数超过它就记一次（真麦上这等价于缓冲区溢出）")
    ap.add_argument("--drain-timeout", type=float, default=900.0,
                    help="读完音频后再给这么多秒把积压转写完，超时就把没做完的记进 backlog")
    ap.add_argument("--segs-dir", default="/root/funasr/segments")
    ap.add_argument("--jsonl", default="/root/funasr/out/asr.jsonl")
    ap.add_argument("--state", default="/dev/shm/asr/state")
    args = ap.parse_args()

    os.makedirs(args.segs_dir, exist_ok=True)
    for path in (args.jsonl, args.state):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    frame_bytes = int(args.rate * args.frame_ms / 1000) * 2
    sil_stop_frames = max(1, args.sil_stop_ms // args.frame_ms)
    min_seg_frames = max(1, args.min_seg_ms // args.frame_ms)
    max_seg_frames = int(args.max_seg_s * 1000 // args.frame_ms)

    if args.source == "wav":
        if not args.input:
            raise SystemExit("--source wav 要给 --input")
        src = WavSource(args.input, args.rate)
    else:
        src = MicSource(args.device, args.rate)

    if args.segmenter == "fvad" and src.rate != 16000:
        raise SystemExit("--segmenter fvad 只支持 16000 Hz：FSMN-VAD 档的 config.yaml 写死 "
                         "fs=16000、frame_in_ms=10。当前 --rate/source 给出的是 %d" % src.rate)

    be_opts = {"language": args.sv_language, "textnorm": args.sv_textnorm,
               "hotwords": args.hotwords,
               # 远程档读这几个键（见 asr/remote_asr.py）
               "url": args.asr_url, "proto": args.asr_proto, "field": args.asr_field,
               "model": args.asr_model, "token": args.asr_token,
               "timeout": args.asr_timeout, "retry": args.asr_retry,
               "extra": args.asr_extra, "rate": args.rate}
    load_s = None
    remote_info = None
    remote_health = None
    if args.backend == "slot":
        be_dir = args.model_dir
        be = None
        ready = model_ready(be_dir)
        backend_label = "slot-filled" if ready else "slot-empty"
        slot_status = "slot_pending" if ready else "no_model"
    else:
        if args.backend == "remote-api":
            # 远程档没有板上权重：目录位放 endpoint，不做 model_ready 检查
            be_dir = args.asr_url or "(未设置 ASR_REMOTE_URL)"
            ready = bool(args.asr_url)
        else:
            be_dir = args.backend_dir or BACKEND_DIRS[args.backend]
            ready = True
        backend_label = args.backend
        slot_status = ""
        t_load = time.time()
        sys.path.insert(0, os.path.join(HERE, "asr"))
        import backends
        be = backends.build(args.backend, be_dir, args.threads, be_opts)
        load_s = round(time.time() - t_load, 2)
        if args.backend == "remote-api":
            remote_info = be.describe() if hasattr(be, "describe") else None
            remote_health = be.health() if hasattr(be, "health") else None
            print("remote-api endpoint=%s proto=%s health=%s"
                  % (be_dir, args.asr_proto, remote_health), flush=True)
    load_hwm = hwm_mib()

    vad = None
    vad_load_s = None
    # rms 信号原语（dbfs/decimate/seg_peak_dbfs）与分段器住在 ../vad（lang 层）；
    # import 提在 main() 顶部（WavSource 构造之前），此处不再重复 import。

    # 2026-11-04：fvad（FSMN-VAD）档已随 funasr_onnx 一起删除，这里不再装载任何东西。
    vad_hwm = None
    ring = None

    # WebRTC VAD 分段：不装载任何模型，import 一个小扩展就完事（板上实测 <1 ms）
    wseg = None
    webrtc_load_s = None
    webrtc_hwm = None
    if args.segmenter == "webrtc":
        t_load = time.time()
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vad", "webrtc"))   # 2026-11-04：webrtc 分段器+本体+包装都在 ../vad/webrtc/
        import webrtcseg
        wseg = webrtcseg.WebRtcSeg(args.webrtc_mode, args.webrtc_sub_ms, src.rate)
        webrtc_load_s = round(time.time() - t_load, 3)
        webrtc_hwm = hwm_mib()

    # TTS 档：链路的最后一环（文本 → wav）。两个引擎都是"另起常驻服务、这里只发 HTTP"，
    # 所以链路进程本身不会因为 TTS 多 import 任何东西。
    tts = None
    tts_health = None
    tts_info = None
    if args.tts != "none":
        sys.path.insert(0, os.path.join(HERE, "asr"))
        t_load = time.time()
        if args.tts == "piper":
            import tts_piper
            tts = tts_piper.PiperTts({"url": args.tts_url, "timeout": args.tts_timeout,
                                      "length_scale": args.tts_length_scale,
                                      "out_dir": args.tts_dir})
        else:
            import tts_ekho
            tts = tts_ekho.EkhoTts({"url": args.tts_url, "timeout": args.tts_timeout,
                                    "out_dir": args.tts_dir})
        tts_info = tts.describe() if hasattr(tts, "describe") else None
        tts_health = tts.health() if hasattr(tts, "health") else None
        print("tts=%s endpoint=%s load_s=%.2f health=%s"
              % (args.tts, tts_info.get("url") if tts_info else "?",
                 time.time() - t_load, tts_health), flush=True)

    # 标点档：默认关。开着就是 CT_Transformer（35~43 s 装载、530+ MiB 峰值）
    # 2026-11-04：CT_Transformer 标点档已随 funasr_onnx 一起删除。
    punc = None
    punc_load_s = None
    punc_hwm = None

    _, _, majflt_start = proc_mem()

    started = time.time()
    state = {
        "unix": started,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "pid": os.getpid(),
        "source": args.source,
        "input": args.input or args.device,
        "rate": src.rate,
        "backend": backend_label,
        "backend_name": args.backend,
        "threads": args.threads,
        "pace": args.pace,
        "frame_ms": args.frame_ms,
        "model_ready": ready,
        "model_dir": be_dir,
        "load_s": load_s,
        "load_hwm_mib": load_hwm,
        "segmenter": args.segmenter,
        "webrtc_mode": args.webrtc_mode if wseg is not None else None,
        "webrtc_sub_ms": args.webrtc_sub_ms if wseg is not None else None,
        "webrtc_open_ratio": args.webrtc_open_ratio if wseg is not None else None,
        "webrtc_close_ratio": args.webrtc_close_ratio if wseg is not None else None,
        "webrtc_load_s": webrtc_load_s,
        "webrtc_hwm_mib": webrtc_hwm,
        "asr_url": args.asr_url if args.backend == "remote-api" else "",
        "asr_proto": args.asr_proto if args.backend == "remote-api" else "",
        "remote_info": remote_info,
        "remote_health": remote_health,
        "tts": args.tts,
        "tts_url": (tts_info or {}).get("url", ""),
        "tts_info": tts_info,
        "tts_health": tts_health,
        "tts_ms_last": None,
        "tts_ms_total": 0,
        "tts_ms_max": None,
        "tts_errors": 0,
        "punc": bool(punc is not None),
        "punc_load_s": punc_load_s,
        "punc_hwm_mib": punc_hwm,
        "punc_ms_last": None,
        "punc_ms_total": 0,
        "punc_ms_max": None,
        "vad_load_s": vad_load_s,
        "vad_hwm_mib": vad_hwm,
        "webrtc_open_frames": args.webrtc_open_frames if wseg is not None else None,
        "vad_lead_ms": args.vad_lead_ms,
        "queue_alarm": args.queue_alarm,
        "frames": 0,
        "consumed_s": 0.0,
        "segments": 0,
        "segments_asred": 0,
        "lag_max": 0.0,
        "asr_ms_max": 0,
        "asr_ms_min": None,
        "asr_ms_p50": None,
        "asr_ms_p95": None,
        "majflt": None,
        "majflt_run": None,
        "vm_hwm_mib": None,
        "vm_swap_mib": None,
        "swap_used_mib": None,
        "swap_used_max_mib": None,
        "cma_free_kb": None,
        "cma_free_min_kb": None,
        "mem_avail_mib": None,
        "mem_avail_min_mib": None,
        "qdepth_max": 0,
        "dbfs_last": -120.0,
        "in_seg": False,
        "last": None,
        "bytes_total": 0,
        "started_unix": started,
        "stop": None,
    }
    state_lock = threading.Lock()
    atomic_write_json(args.state, state)
    print("asr_chain start source=%s rate=%d frame=%dB backend=%s pace=%s load_s=%s "
          "load_hwm=%s state=%s"
          % (state["source"], src.rate, frame_bytes, backend_label, args.pace,
             state["load_s"], state["load_hwm_mib"], args.state), flush=True)

    out = open(args.jsonl, "a", buffering=1)
    worker = AsrWorker(be, out, state, state_lock, slot_status, args.queue_alarm, punc, tts)
    worker.start()

    pre = collections.deque(maxlen=max(1, args.webrtc_open_frames if wseg is not None else 1))
    open_frames = max(1, args.webrtc_open_frames) if wseg is not None else 1
    buf = []
    seg_frames = 0
    sil_frames = 0
    peak = -120.0
    last_state = 0.0
    ended = False
    seg_end_wall = 0.0
    behind_total = 0.0
    late_frames = 0

    try:
        while True:
            data = src.read(frame_bytes)
            if data is None:
                ended = True
                break
            now = time.time()
            value = rms.dbfs(data)
            state["frames"] += 1
            state["consumed_s"] = round(state["frames"] * args.frame_ms / 1000.0, 3)
            state["bytes_total"] += len(data)
            state["dbfs_last"] = round(value, 1)

            # 三档分段统一到两个布尔量：is_speech（起段）／is_sil（累计静音）。
            # rms 看 dBFS 双门限，webrtc 看语音子帧占比，迟滞形状一致，后面的断句逻辑不用改。
            if wseg is not None:
                score = wseg.ratio(data)
                state["webrtc_ratio_last"] = round(score, 3)
                is_speech = score >= args.webrtc_open_ratio
                is_sil = score <= args.webrtc_close_ratio
            else:
                is_speech = value >= args.open_dbfs
                is_sil = value < args.close_dbfs

            if args.segmenter == "fvad":
                ring.push(data)
                for s_ms, e_ms in vad.feed(data):
                    payload, lost = ring.cut(s_ms - args.vad_lead_ms, e_ms)
                    if len(payload) * 500.0 / src.rate < args.vad_min_ms:
                        continue
                    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
                    name = "seg_%s_%04d.wav" % (stamp, state["segments"] + 1)
                    path = os.path.join(args.segs_dir, name)
                    write_wav(path, src.rate, payload)
                    worker.submit((stamp, path, payload,
                                   round(len(payload) / 2.0 / src.rate, 3),
                                   rms.seg_peak_dbfs(payload, frame_bytes), now, False,
                                   state["segments"] + 1,
                                   {"segmenter": "fvad", "vad_seg_ms": [s_ms, e_ms],
                                    "lost_lead_ms": lost}))
                    state["segments"] += 1
            elif not buf:
                if is_speech:
                    pre.append(data)
                    if len(pre) >= open_frames:
                        # 攒够 open_frames 帧才真起段；这几帧一起算段头，别把字头切掉
                        buf = list(pre)
                        pre.clear()
                        seg_frames = len(buf)
                        sil_frames = 0
                        peak = max(rms.seg_peak_dbfs(f, frame_bytes) for f in buf)
                        seg_end_wall = now
                else:
                    pre.clear()
            else:
                buf.append(data)
                seg_frames += 1
                seg_end_wall = now
                peak = max(peak, value)
                if is_sil:
                    sil_frames += 1
                else:
                    sil_frames = 0
                if sil_frames >= sil_stop_frames or seg_frames >= max_seg_frames:
                    if seg_frames - sil_frames >= min_seg_frames:
                        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
                        name = "seg_%s_%04d.wav" % (stamp, state["segments"] + 1)
                        path = os.path.join(args.segs_dir, name)
                        payload = b"".join(buf)
                        write_wav(path, src.rate, payload)
                        worker.submit((stamp, path, payload,
                                       round(len(payload) / 2.0 / src.rate, 3),
                                       round(peak, 1), seg_end_wall, False,
                                       state["segments"] + 1, None))
                        state["segments"] += 1
                    buf = []
                    pre.clear()
                    seg_frames = 0
                    sil_frames = 0
                    peak = -120.0

            if now - last_state >= 1.0:
                with state_lock:
                    state["in_seg"] = (vad.open_ms is not None) if vad is not None else bool(buf)
                    if vad is not None:
                        state.update(vad.stats())
                        state["ring_kept_s"] = round(len(ring.buf) / (2.0 * src.rate), 2)
                        state["ring_lost_ms"] = round(ring.lost_ms_total, 1)
                    if wseg is not None:
                        state.update(wseg.stats())
                    state["unix"] = now
                    state["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
                    state["qdepth"] = worker.q.qsize()
                    state["qdepth_max"] = max(state.get("qdepth_max", 0), worker.q.qsize())
                    state["behind_total_s"] = round(behind_total, 2)
                    state["late_frames"] = late_frames
                    pm = proc_mem()
                    sm = sysmem()
                    state["vm_hwm_mib"] = pm[0]
                    state["vm_swap_mib"] = pm[1]
                    state["majflt"] = pm[2]
                    state["cma_free_kb"] = sm[0]
                    state["mem_avail_mib"] = sm[2]
                    if sm[2] is not None:
                        prev = state.get("mem_avail_min_mib")
                        state["mem_avail_min_mib"] = sm[2] if prev is None else min(prev, sm[2])
                    if sm[0] is not None:
                        prev = state.get("cma_free_min_kb")
                        state["cma_free_min_kb"] = sm[0] if prev is None else min(prev, sm[0])
                    state["swap_used_mib"] = sm[1]
                    if sm[1] is not None:
                        prev = state.get("swap_used_max_mib")
                        state["swap_used_max_mib"] = sm[1] if prev is None else max(prev, sm[1])
                    atomic_write_json(args.state, state)
                last_state = now

            if args.max_seconds and now - started >= args.max_seconds:
                state["stop"] = "max_seconds"
                break

            if args.pace == "realtime":
                due = started + state["frames"] * args.frame_ms / 1000.0
                delay = due - time.time()
                if delay > 0:
                    time.sleep(delay)
                else:
                    behind_total += -delay
                    late_frames += 1
    finally:
        if vad is not None:
            for s_ms, e_ms in vad.flush():
                payload, lost = ring.cut(s_ms - args.vad_lead_ms, e_ms)
                if len(payload) * 500.0 / src.rate < args.vad_min_ms:
                    continue
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time()))
                path = os.path.join(args.segs_dir, "seg_%s_tail_%04d.wav" % (stamp, state["segments"] + 1))
                write_wav(path, src.rate, payload)
                worker.submit((stamp, path, payload,
                               round(len(payload) / 2.0 / src.rate, 3),
                               rms.seg_peak_dbfs(payload, frame_bytes), time.time(), True,
                               state["segments"] + 1,
                               {"segmenter": "fvad", "vad_seg_ms": [s_ms, e_ms],
                                "lost_lead_ms": lost}))
                state["segments"] += 1
        elif buf and seg_frames - sil_frames >= min_seg_frames:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time()))
            payload = b"".join(buf)
            path = os.path.join(args.segs_dir, "seg_%s_tail_%04d.wav" % (stamp, state["segments"] + 1))
            write_wav(path, src.rate, payload)
            worker.submit((stamp, path, payload,
                           round(len(payload) / 2.0 / src.rate, 3),
                           round(peak, 1), seg_end_wall or time.time(), True,
                           state["segments"] + 1, None))
            state["segments"] += 1

        consume_end = time.time()
        qdepth_at_drain = worker.q.qsize()
        deadline = consume_end + args.drain_timeout
        while worker.q.unfinished_tasks > 0 and time.time() < deadline:
            time.sleep(0.05)
        drained = worker.q.unfinished_tasks == 0
        worker.stop_now = True
        worker.q.put(None)
        worker.join(timeout=30)

        with state_lock:
            state["in_seg"] = False
            state["stop"] = state.get("stop") or ("eof" if ended else "interrupted")
            state["unix"] = time.time()
            state["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(state["unix"]))
            state["run_s"] = round(state["unix"] - started, 1)
            state["qdepth_at_drain"] = qdepth_at_drain
            state["drained"] = drained
            # 音频跑完之后还要多久才把积压转写空：仿实时里这就是"追不追得上"的直接读数
            state["backlog_s"] = round(state["unix"] - consume_end, 1)
            state["asr_ms_total"] = state.get("asr_ms_total", 0)
            state["asred_s_total"] = round(state.get("asred_s_total", 0.0), 3)
            state["rtf_mean"] = round(state.get("asr_ms_total", 0) / 1000.0
                                      / state["asred_s_total"], 4) if state["asred_s_total"] else None
            state["max_hwm_mib"] = hwm_mib()
            pm = proc_mem()
            sm = sysmem()
            state["vm_swap_mib"] = pm[1]
            state["swap_used_mib"] = sm[1]
            if sm[0] is not None:
                prev = state.get("cma_free_min_kb")
                state["cma_free_min_kb"] = sm[0] if prev is None else min(prev, sm[0])
            state["majflt"] = pm[2]
            state["majflt_run"] = (pm[2] - majflt_start
                                   if pm[2] is not None and majflt_start is not None else None)
            ms_sorted = sorted(worker.ms)
            state["asr_ms_min"] = ms_sorted[0] if ms_sorted else None
            state["asr_ms_p50"] = pct(ms_sorted, 0.50)
            state["asr_ms_p95"] = pct(ms_sorted, 0.95)
            if vad is not None:
                state.update(vad.stats())
            if wseg is not None:
                state.update(wseg.stats())
            if tts is not None and hasattr(tts, "stats"):
                state.update(tts.stats())
            state["queue_alarm_hits"] = worker.alarm_hits
            atomic_write_json(args.state, state)
        src.close()
        for obj in (be, punc, tts):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        out.close()
        print("asr_chain stop %s frames=%d segments=%d asred=%d run_s=%s rtf_mean=%s "
              "lag_max=%s backlog_s=%s hwm=%s segmenter=%s vad_pairs=%s ring_lost_ms=%s "
              "asr_ms=%s/%s/%s majflt=%s vmswap=%s swap_used=%s cma_min=%s avail=%s"
              % (state["stop"], state["frames"], state["segments"], state["segments_asred"],
                 state.get("run_s"), state.get("rtf_mean"), state.get("lag_max"),
                 state.get("backlog_s"), state.get("max_hwm_mib"), args.segmenter,
                 state.get("vad_pairs", "-"),
                 round(ring.lost_ms_total, 1) if ring is not None else "-",
                 state.get("asr_ms_min"), state.get("asr_ms_p50"), state.get("asr_ms_p95"),
                 state.get("majflt_run"), state.get("vm_swap_mib"),
                 state.get("swap_used_max_mib"), state.get("cma_free_min_kb"),
                 state.get("mem_avail_mib")), flush=True)

    # mic 跑到"读不动了"却一帧没有＝设备不对（卡不在位／卡名认错／口被别的进程占着）。
    # 这不能算正常收尾：以前它 rc=0，只留下一份零帧 jsonl，和"房间安静地说完了话"完全同形。
    if args.source == "mic" and ended and state["frames"] == 0:
        print("asr_chain FAIL：--source mic 采到 0 帧 device=%s arecord_rc=%s stderr=%r"
              % (args.device, src.rc,
                 src.err_tail.decode("utf-8", "replace").strip()), flush=True)
        raise SystemExit(4)


if __name__ == "__main__":
    main()
