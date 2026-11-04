# lang/vad/webrtc —— WebRTC VAD 全套（分段器 ＋ 本体 ＋ 包装 ＋ 接线）

2026-11-04 她从「四件散在三处」收拢到这里：

## 里面有什么

| 文件 | 是什么 | 来源（移动前） |
|---|---|---|
| `webrtcseg.py` | **分段器**：`WebRtcSeg` 类，把 100 ms 主帧切 5×20 ms 子帧，返回**语音子帧占比** | `vad/webrtcseg.py` |
| `pylite/_webrtcvad.cpython-38-aarch64-linux-gnu.so` | **本体**：218,432 B，ELF 64-bit **aarch64**（`e_machine=183`）C 扩展 | `asr/pylite/` |
| `pylite/webrtcvad.py` | **包装**：`Vad(mode)` / `is_speech(buf, rate)` / `valid_rate_and_frame_length()` | `asr/pylite/` |
| `pylite/webrtcvad-2.0.10.dist-info/` | 包元数据（`pkg_resources.get_distribution('webrtcvad')` 靠它拿版本号） | `asr/pylite/` |
| **接线** | `webrtcseg.py` 里的 `_ensure_pylite()` ＋ 两个起链脚本的 PYLITE 三级查找 | 见下 |

## 为什么这么放：自包含

`webrtcseg.py` 顶部有 `_ensure_pylite()`——构造 `WebRtcSeg` 前把**同目录下的 `pylite/`**
塞进 `sys.path`（幂等）：

```python
def _ensure_pylite():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "pylite"), here):
        if os.path.isdir(cand) and cand not in sys.path:
            sys.path.insert(0, cand)
```

⇒ **不再依赖外部 `PYTHONPATH`／`PYLITE` 环境变量**。只要这四件在同目录，`import webrtcseg`
之后就能用。起链脚本里的 PYLITE 仍保留（兼容板上老布局），但**不再是硬前提**。

## 接线：三级查找

两个起链脚本（`asr/run_remote_chain.sh`、`asr/run_onnx_chain.sh`）现在是：

```bash
if [ -z "${PYLITE:-}" ]; then
  if [ -d /root/funasr/pylite ]; then          # ② 板上老位置
    PYLITE=/root/funasr/pylite
  else
    PYLITE="$(cd "$(dirname "$0")/../../vad/webrtc/pylite" 2>/dev/null && pwd)"   # ③ 仓库相对
  fi
fi
[ -n "${PYLITE:-}" ] && export PYTHONPATH="${PYLITE}${PYTHONPATH:+:$PYTHONPATH}"
```

① 显式 `PYLITE=` 最优先；② 板上老位置；③ 本脚本相对路径（`asr/asr/` → `../../vad/webrtc/pylite`）。

**顺带补了一个既有缺口**：`run_onnx_chain.sh`（链路 B）**原来根本没有 PYLITE 接线**，
而它 2026-11-04 起默认 `SEG=webrtc` ⇒ 板上会 import 不到 `webrtcvad`。这次一并补上。

## 判决口径（为什么包这一层）

WebRTC 的 `is_speech()` 只吃 **10/20/30 ms** 的单声道 16 bit 帧，而链路主循环是 **100 ms 一帧**
（帧节奏要和麦克风对齐）。所以分段器把一帧切 **5×20 ms** 子帧逐块判，返回**连续量「占比」**而不是
布尔，再交给主循环的双门限迟滞：

```
占比 ≥ --webrtc-open-ratio  (0.4)  → 判语音，起段
占比 ≤ --webrtc-close-ratio (0.2)  → 判静音，累计 --sil-stop-ms (600) 就断句
```

**占比是连续量而不是布尔量**，断句边界比「整帧 0/1」稳，和 rms 档的双门限形状也对得上。

## 平台限制（重要）

| 平台 | 能跑吗 | 原因 |
|---|---|---|
| **板上 Ubuntu 20.04 / py3.8 / aarch64** | ✅ 能 | `.so` 就是给它的；`_webrtcvad.cpython-38-aarch64-linux-gnu.so` 文件名里的 `cpython-38` 与板上 py3.8 对齐 |
| **PC（Windows x86_64 / py3.11·3.13）** | ❌ 不能 | ① `.so` 是 **aarch64 ELF**，x86 加载不了；② 包装要 `pkg_resources`（setuptools），本机当前 python 没有 |

**PC 上的报错顺序**（2026-11-04 实测，用来确认路径引导是对的）：
`import webrtcseg` ✅ → 构造 `WebRtcSeg` → `_ensure_pylite()` 把
`lang\vad\webrtc\pylite` 塞进 `sys.path` ✅ → `import webrtcvad` →
**卡在 `ModuleNotFoundError: No module named 'pkg_resources'`**（越过路径问题，卡在依赖/架构）。

⇒ **PC 上要真跑 webrtc 档**，两条路：① `pip install webrtcvad`（x86 版，装到当前 python）；
② 等板上跑。**这不影响板上可用性**——板端当年就是这么跑通的（venv/py3.8 + 这颗 `.so`）。

## 与 `vad/rms.py`、`vad/models/` 的分工

| 路径 | 是什么 | 额外依赖 |
|---|---|---|
| `vad/rms.py` | rms 档的信号原语（`dbfs` / `decimate` / `seg_peak_dbfs`） | **零**（标准库） |
| `vad/webrtc/`（本目录） | WebRTC 档全套 | 这颗 aarch64 `.so` ＋ `pkg_resources` |
| `vad/models/` | silero / ten 的 `.onnx`（**还没接进链路**） | sherpa-onnx 库 |

## 板端部署对应关系

| 仓库 | 板上 |
|---|---|
| `lang/vad/webrtc/pylite/` | `/root/funasr/vad/webrtc/pylite/`（推荐）或老位置 `/root/funasr/pylite/` |
| `lang/vad/webrtc/webrtcseg.py` | 同目录 |
| `lang/vad/rms.py` | `/root/funasr/vad/rms.py` |

两种布局都能跑：新版靠 `_ensure_pylite()` 自包含，老版靠起链脚本的 PYLITE ② 兜底。
