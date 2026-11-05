#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""console.py — 统一控制台（HA4FPAI 的主入口页面）。

一个页面把整条语音链路看清楚、能操作：

  /                 系统体检 + 语音链路状态 + 命令面板
  /api/health       三服务 + 语音 gateway 的实测状态
  /api/commands     可用命令（来自 klar_executor.DIRECT_COMMANDS）
  /api/run?text=..  跑一条命令，返回执行结果（可带 speak=1 播报）
  /api/speech/last  最近一次播报的 wav 路径
  /api/logs         各服务最近日志
  /api/voiceprint   声纹库状态
  /api/history      最近的执行/识别历史

为什么不用框架：板上 993 MB、py3.8，http.server + 标准库就够了。
aiohttp 虽然在（sherpa 依赖链），但这里串行请求量极小，标准库更稳。

启动：
    python3 console.py --port 10900
访问：
    http://192.168.1.113:10900/
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# klar_executor.py 已迁至仓库 klar/；板上仍是平铺同一目录，两种布局都能 import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "klar"))

LOG = logging.getLogger("console")

BOARD = {
    "face":   "http://127.0.0.1:8091",
    "stream": "http://127.0.0.1:8092",
    "record": "http://127.0.0.1:8093",
    "klar":   "http://127.0.0.1:10520",
    "asr":    "tcp://127.0.0.1:10301",
    "tts":    "tcp://127.0.0.1:10302",
    "wake":   "tcp://127.0.0.1:10300",
}
KLAR_DIR = "/mnt/data/klar"
HISTORY_FILE = os.path.join(KLAR_DIR, "console-history.jsonl")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def http(url, method="GET", body=None, timeout=8):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf8", "ignore"))
            except ValueError:
                return r.status, raw[:400].decode("utf8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(400).decode("utf8", "ignore")
        except Exception:                        # noqa: BLE001
            detail = ""
        try:
            return e.code, json.loads(detail)
        except ValueError:
            return e.code, detail
    except Exception as e:                       # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


def svc_unit(name):
    try:
        out = subprocess.run(["systemctl", "is-active", name],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:                            # noqa: BLE001
        return "unknown"


def unit_rss(name):
    try:
        pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", name],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if not pid or pid == "0":
            return None
        with open("/proc/%s/status" % pid) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:                            # noqa: BLE001
        pass
    return None


def wyoming_describe(uri):
    """不依赖 wyoming 库的轻量试探：连上去发 describe，读回 info。"""
    import socket
    try:
        host, port = uri.replace("tcp://", "").split(":")
        s = socket.create_connection((host, int(port)), timeout=4)
        s.sendall(b'{"type":"describe"}\n')
        s.settimeout(4)
        buf = s.recv(4096).decode("utf8", "ignore")
        s.close()
        ev = json.loads(buf.split("\n")[0])
        d = ev.get("data") or {}
        for kind in ("asr", "tts", "wake"):
            progs = d.get(kind) or []
            if progs:
                return {"ok": True, "kind": kind, "name": progs[0].get("name"),
                        "detail": progs[0].get("description")}
        return {"ok": True, "kind": "?", "name": "?", "detail": ""}
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def tail(path, n=40):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [ln.rstrip() for ln in lines[-n:]]
    except Exception as e:                       # noqa: BLE001
        return ["(读不到 %s: %s)" % (path, e)]


# ---------------------------------------------------------------------------
# state collectors
# ---------------------------------------------------------------------------
def health():
    h = {}

    h["gateway"] = {
        # 语音已移交 lang/server（她 2026-11-04 定）：单元名是 lang-server-voice，
        # 端口仍是 10300/10301/10302（对客户端透明）
        "unit": svc_unit("lang-server-voice"),
        "rss_mb": unit_rss("lang-server-voice"),
    }
    h["engine"] = {
        "unit": svc_unit("klar-nlu-engine"),
        "rss_mb": unit_rss("klar-nlu-engine"),
    }

    # 语音三端口
    for key, uri in (("wake", BOARD["wake"]), ("asr", BOARD["asr"]), ("tts", BOARD["tts"])):
        h[key] = wyoming_describe(uri)

    # 板子三个 API
    for key in ("face", "stream", "record"):
        st, d = http(BOARD[key] + "/api/status" if key != "face" else BOARD[key] + "/api/health")
        info = {"http": st}
        if isinstance(d, dict):
            info["state"] = d.get("status") or d.get("state") or d.get("mode")
            if key == "stream":
                info["ready"] = d.get("ready")
            if key == "record":
                info["mode"] = d.get("mode")
        else:
            info["error"] = str(d)[:100]
        h[key] = info

    # klar
    st, d = http(BOARD["klar"] + "/api/v2/last-turn")
    h["klar"] = {"http": st, "last_turn": d if st == 200 else None}

    # 内存
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                k, v = line.split(":", 1)
                mi[k] = int(v.split()[0])
        h["mem"] = {"total_mb": round(mi["MemTotal"] / 1024),
                    "avail_mb": round(mi["MemAvailable"] / 1024)}
    except Exception:                            # noqa: BLE001
        pass
    return h


def commands():
    try:
        import klar_executor as ex
        out = []
        for phrases, fn, defaults in ex.DIRECT_COMMANDS:
            out.append({"phrases": phrases,
                        "action": getattr(fn, "__name__", str(fn)),
                        "defaults": defaults})
        return out
    except Exception as e:                       # noqa: BLE001
        return [{"error": str(e)}]


def run_command(text, speak=False, dry_run=False, wav=None):
    try:
        import klar_executor as ex
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": "loading executor: %s" % e}
    out = ex.handle_text(text, dry_run=dry_run, speak_reply=speak, wav=wav)
    rec = {"ts": time.time(), "text": text, "channel": out.get("channel"),
           "ok": out.get("ok"), "message": out.get("message") or out.get("error"),
           "speech": out.get("speech_result")}
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:                            # noqa: BLE001
        pass
    return out


def voiceprint_info():
    try:
        import voiceprint as vp
        st = vp.load_store()
        spk = list((st.get("speakers") or {}).keys())
        return {"store": vp.STORE, "dim": st.get("dim"), "count": len(spk), "speakers": spk}
    except Exception as e:                       # noqa: BLE001
        return {"error": str(e)}


def history(n=30):
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [json.loads(l) for l in lines[-n:] if l.strip()]
    except Exception:                            # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HA4FPAI 控制台</title>
<script>
try{document.documentElement.dataset.theme=localStorage.getItem('ha4fpai-console-theme')==='light'?'light':'dark';}catch(e){}
</script>
<style>
:root{color-scheme:dark;--bg:#1c1c1c;--card:#242424;--surface-2:#2a2a2a;--line:#3a3a3a;--fg:#f4efe6;--dim:#aaa398;--acc:#c45c26;--cyan:#3ec1c8;--ok:#8eac7a;--warn:#d4a04a;--bad:#d38370;--primary:#ad4b1d;--sans:"IBM Plex Sans","Noto Sans SC","Microsoft YaHei",sans-serif;--mono:"IBM Plex Mono",ui-monospace,Consolas,monospace}
:root[data-theme=light]{color-scheme:light;--bg:#f4efe6;--card:#fffaf2;--surface-2:#ebe4d6;--line:#cfc6b6;--fg:#1c1c1c;--dim:#6b655c;--cyan:#1f7a80;--ok:#4e6b3d;--warn:#9a6e1c;--bad:#a44a38}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-padding-top:88px}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
button,input,a{-webkit-tap-highlight-color:transparent}
a{color:inherit}
button,input{font:inherit}
:focus-visible{outline:2px solid var(--acc);outline-offset:4px}
.topbar{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--line)}
.topbar-inner{max-width:1228px;margin:auto;padding:0 24px;min-height:72px;display:flex;align-items:center;gap:32px}
.brand{display:flex;align-items:center;gap:14px;white-space:nowrap;text-decoration:none}
.brand strong{color:var(--acc);font-size:23px;font-weight:700;letter-spacing:-.04em}
.brand span{padding-left:14px;border-left:1px solid var(--line);font-size:13px;color:var(--dim)}
.nav{display:flex;gap:24px;align-self:stretch;align-items:center;flex:1}
.nav a{display:flex;align-items:center;min-height:44px;border-bottom:1px solid transparent;text-decoration:none;font-size:14px;color:var(--dim)}
.nav a:hover,.nav a.active{color:var(--fg);border-bottom-color:var(--acc)}
.topbar-end{display:flex;align-items:center;gap:12px;margin-left:auto}
#clock{font:12px var(--mono);font-variant-numeric:tabular-nums;color:var(--dim)}
.theme-toggle{padding:6px 12px;font-size:13px;white-space:nowrap}
.wrap{width:min(1180px,calc(100% - 48px));margin:0 auto;padding:32px 0 56px}
.page-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:32px}
h1{font-size:28px;font-weight:600;letter-spacing:-.02em;margin:0 0 8px;line-height:1.3}
.sub{color:var(--dim);font-size:14px;margin:0;max-width:64ch}
.page-badge{display:flex;align-items:center;gap:8px;color:var(--dim);font:12px var(--mono);white-space:nowrap}
.page-badge::before{content:"";display:block;width:6px;height:6px;background:var(--acc)}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px}
.card h3{margin:0 0 12px;font-size:13px;color:var(--dim);font-weight:500}
.big{font-size:22px;font-weight:500;letter-spacing:-.02em;overflow-wrap:anywhere}
.kv{display:flex;justify-content:space-between;gap:16px;font-size:12px;color:var(--dim);margin-top:8px;min-width:0}
.kv>span:last-child{text-align:right;overflow-wrap:anywhere;min-width:0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:8px;vertical-align:3px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
.c-ok{color:var(--ok)}.c-warn{color:var(--warn)}.c-bad{color:var(--bad)}.c-dim{color:var(--dim)}
section{margin-bottom:32px;scroll-margin-top:16px;min-width:0}
section>h2{font-size:16px;font-weight:500;margin:0 0 16px;display:flex;align-items:center;gap:12px}
section>h2::after{content:"";height:1px;background:var(--line);flex:1}
.cmds{display:flex;flex-wrap:wrap;gap:8px}
.command-panel{padding:20px}
.command-form{margin-top:20px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-top:20px;border-top:1px solid var(--line)}
.command-form label[for=custom]{width:100%;font-size:13px;color:var(--dim)}
.tabs{display:flex;flex-wrap:wrap;gap:4px 20px;border-bottom:1px solid var(--line);padding:0 16px}
.tab{color:var(--dim);background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;padding:10px 0;min-height:48px;cursor:pointer;font-size:13px;display:inline-flex;align-items:center;text-decoration:none}
.tab:hover{background:transparent;color:var(--fg);border-bottom-color:var(--line)}
.tab.active{background:transparent;border-bottom-color:var(--acc);color:var(--fg)}
.extlink{margin-left:auto;color:var(--cyan)}
button{background:var(--surface-2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:9px 14px;min-height:44px;cursor:pointer;font-size:13px}
button:hover{background:var(--card);border-color:var(--acc)}
button.primary{background:var(--primary);border-color:var(--primary);color:#fffaf2;font-weight:600}
button.primary:hover{background:#983e16;border-color:#983e16}
input[type=text]{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;min-width:0;flex:1 1 300px;min-height:44px;font-size:14px}
input::placeholder{color:var(--dim)}
input[type=checkbox]{accent-color:var(--acc);width:16px;height:16px}
pre{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:14px;overflow:auto;max-height:300px;font:12px/1.6 var(--mono);margin:0;tab-size:2}
#result{margin-top:16px;white-space:pre-wrap;overflow-wrap:anywhere}
.logrow{color:var(--dim)}
.logs-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.table-scroll{overflow-x:auto;padding:0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:500;background:var(--surface-2);white-space:nowrap}
td{overflow-wrap:anywhere}
td:first-child{font-family:var(--mono);font-size:12px;white-space:nowrap}
tr:last-child td{border-bottom:0}
.chk{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:13px;min-height:44px;white-space:nowrap}
.embed-card{padding:0;overflow:hidden}
.embednote{margin:0;padding:12px 16px;min-height:42px;font-size:12px;line-height:1.6;color:var(--dim);border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.embed-frame{display:block;width:100%;height:680px;border:0;background:var(--bg)}
.voiceprint-view{padding:20px;margin:0}
.page-footer{display:flex;justify-content:space-between;gap:12px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--dim)}
@media(min-width:1000px){#voiceprints .kv{max-width:760px}}
@media(max-width:1100px){.topbar-inner{gap:18px;flex-wrap:wrap;padding-top:12px;padding-bottom:8px}.nav{order:3;flex-basis:100%;gap:24px;overflow:auto;white-space:nowrap}.brand span{display:none}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.logs-grid{grid-template-columns:1fr}html{scroll-padding-top:132px}}
@media(max-width:520px){.topbar-inner{padding-left:16px;padding-right:16px}.wrap{width:calc(100% - 32px);padding-top:24px}.page-head{align-items:flex-start;margin-bottom:24px}.page-badge{display:none}h1{font-size:24px}.sub{font-size:13px}.grid{gap:8px}.card{padding:12px}.big{font-size:18px}.command-panel{padding:16px}.command-form input{flex-basis:100%}.tabs{gap:0 16px;padding:0 12px}.embed-card,.table-scroll{padding:0}.extlink{margin-left:0}.embed-frame{height:600px}.nav{gap:20px}.page-footer{flex-direction:column}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style></head><body>
<header class="topbar"><div class="topbar-inner">
  <a class="brand" href="#overview" aria-label="HA4FPAI 控制台总览"><strong>Klar!</strong><span>HA4FPAI</span></a>
  <nav class="nav" aria-label="控制台导航">
    <a href="#overview" class="active" aria-current="page">总览</a>
    <a href="#commands">命令</a>
    <a href="#history">历史</a>
    <a href="#video">视频</a>
    <a href="#audio">声音</a>
    <a href="#klar">Klar NLU 控制台</a>
    <a href="#logs">日志</a>
  </nav>
  <div class="topbar-end"><time id="clock"></time><button type="button" id="theme-toggle" class="theme-toggle" onclick="toggleTheme()">切换浅色</button></div>
</div></header>
<main class="wrap">
<div class="page-head">
  <div><h1 id="page-title">统一控制台</h1><p class="sub" id="page-description">查看设备与服务状态。</p></div>
  <div class="page-badge">HA4FPAI / 悟净</div>
</div>

<section id="overview" class="page-panel" data-title="统一控制台" data-description="查看设备与服务状态。"><h2>系统体检</h2><div class="grid" id="health">加载中…</div></section>

<section id="commands" class="page-panel" data-title="命令" data-description="选择快捷命令，或输入一句话执行。" hidden><h2>命令面板</h2>
  <div class="card command-panel">
  <div class="cmds" id="cmds"></div>
  <div class="command-form">
    <label for="custom">输入命令</label>
    <input type="text" id="custom" placeholder="例如：现在几点">
    <button class="primary" onclick="runCustom()">执行</button>
    <label class="chk"><input type="checkbox" id="speak"> 播报结果</label>
    <label class="chk"><input type="checkbox" id="dry"> 只演练</label>
  </div>
  <pre id="result" role="status" aria-live="polite">选择上方命令，或输入一句话执行。结果会显示在这里。</pre>
  </div>
</section>

<section id="history" class="page-panel" data-title="历史" data-description="查看已执行的命令与结果。" hidden><h2>执行历史</h2><div class="card table-scroll"><table id="hist"><thead>
<tr><th>时间</th><th>输入</th><th>通道</th><th>结果</th></tr></thead><tbody></tbody></table></div></section>

<section id="video" class="page-panel" data-title="视频" data-description="直播画面、摄像头录像与人脸识别。" hidden>
 <div class="card embed-card" data-embed-group>
  <div class="tabs" role="tablist" aria-label="视频页面">
    <button type="button" id="video-stream-tab" class="tab active" role="tab" aria-selected="true" aria-controls="video-view" onclick="pick('stream', this)">SDI 直播</button>
    <button type="button" id="video-record-tab" class="tab" role="tab" aria-selected="false" aria-controls="video-view" onclick="pick('record', this)">录像管理</button>
    <button type="button" id="video-face-tab" class="tab" role="tab" aria-selected="false" aria-controls="video-view" onclick="pick('face', this)">NPU 人脸</button>
    <a class="tab extlink" href="http://192.168.1.113:8092/" target="_blank" rel="noopener">↗ 新窗口打开</a>
  </div>
  <div class="embed-view" id="video-view" role="tabpanel" aria-labelledby="video-stream-tab">
    <div class="embednote"></div>
    <iframe id="embed-video" class="embed-frame" data-embed="stream" title="SDI 直播" src="about:blank" referrerpolicy="origin" loading="lazy"></iframe>
  </div>
 </div>
</section>

<section id="audio" class="page-panel" data-title="声音" data-description="语音链配置、录音试验与声纹库。" hidden>
 <div class="card embed-card" data-embed-group>
  <div class="tabs" role="tablist" aria-label="声音页面">
    <button type="button" id="audio-ggradio-tab" class="tab active" role="tab" aria-selected="true" aria-controls="audio-view" onclick="pick('ggradio', this)">ggradio 语音试验台</button>
    <button type="button" id="audio-voiceprints-tab" class="tab" role="tab" aria-selected="false" aria-controls="voiceprints" onclick="pick('voiceprints', this)">声纹库</button>
    <a class="tab extlink" href="http://127.0.0.1:7861/" target="_blank" rel="noopener">↗ 新窗口打开</a>
  </div>
  <div class="embed-view" id="audio-view" role="tabpanel" aria-labelledby="audio-ggradio-tab">
    <div class="embednote"></div>
    <iframe id="embed-audio" class="embed-frame" data-embed="ggradio" title="ggradio 语音试验台" src="about:blank" referrerpolicy="origin" loading="lazy"></iframe>
  </div>
  <section id="voiceprints" class="voiceprint-view" role="tabpanel" aria-labelledby="audio-voiceprints-tab" hidden><div id="vp">加载中…</div></section>
 </div>
</section>

<section id="klar" class="page-panel" data-title="Klar NLU 控制台" data-description="语言、短语、对话与引擎设置。" hidden>
 <div class="card embed-card" data-embed-group>
  <div class="tabs"><a class="tab extlink" href="http://127.0.0.1:10521/" target="_blank" rel="noopener">↗ 新窗口打开</a></div>
  <div class="embed-view">
    <div class="embednote"></div>
    <iframe id="embed-klar" class="embed-frame" data-embed="klar" title="Klar NLU 控制台" src="about:blank" referrerpolicy="origin" loading="lazy"></iframe>
  </div>
 </div>
</section>

<section id="logs" class="page-panel" data-title="日志" data-description="查看语音服务与 Klar 引擎的运行日志。" hidden><h2>服务日志</h2><div class="logs-grid">
  <div class="card"><h3>语音服务</h3><pre id="log-gw" class="logrow"></pre></div>
  <div class="card"><h3>Klar 引擎</h3><pre id="log-klar" class="logrow"></pre></div>
</div></section>
<footer class="page-footer"><span>HA4FPAI 统一控制台</span><a href="#overview">回到总览</a></footer>
</main>
<script>
function syncThemeButton(){
  const light=document.documentElement.dataset.theme==='light';
  document.getElementById('theme-toggle').textContent=light?'切换深色':'切换浅色';
}
function toggleTheme(){
  const theme=document.documentElement.dataset.theme==='light'?'dark':'light';
  document.documentElement.dataset.theme=theme;
  try{localStorage.setItem('ha4fpai-console-theme',theme);}catch(e){}
  syncThemeButton();
  sendEmbeddedTheme();
}
syncThemeButton();
function syncNav(){
  const requested=location.hash.slice(1)||'overview';
  const aliases={applications:'video',voiceprints:'audio','history-section':'history'};
  const key=aliases[requested]||requested;
  const panels=[...document.querySelectorAll('.page-panel')];
  const panel=panels.find(p=>p.id===key)||document.getElementById('overview');
  panels.forEach(p=>{p.hidden=p!==panel;});
  document.querySelectorAll('.nav a').forEach(a=>{
    const active=a.getAttribute('href')==='#'+panel.id;
    a.classList.toggle('active',active);
    if(active){
      a.setAttribute('aria-current','page');
      a.scrollIntoView({block:'nearest',inline:'nearest'});
    }else a.removeAttribute('aria-current');
  });
  document.getElementById('page-title').textContent=panel.dataset.title;
  document.getElementById('page-description').textContent=panel.dataset.description;
  if(requested==='voiceprints') pick('voiceprints',document.getElementById('audio-voiceprints-tab'));
  const frame=panel.querySelector('iframe[data-embed]');
  if(frame&&!frame.closest('.embed-view').hidden) mountEmbed(frame);
  window.scrollTo(0,0);
}
window.addEventListener('hashchange',syncNav);
const fmt=(o)=>typeof o==='string'?o:JSON.stringify(o,null,1);
function dot(s){return s==='active'?'ok':(s==='inactive'||s==='failed')?'bad':'warn';}
function stateDot(x){
  if(x&&typeof x==='object'){
    if(x.ok===false) return 'bad';
    if(x.ready===false) return 'warn';
    if(x.http===null||x.http===undefined) return 'bad';
    if(x.http>=500) return 'bad';
    if(x.http&&x.http<400) return 'ok';
    return 'warn';
  } return 'warn';
}
const EMBED={
 klar:{url:'http://127.0.0.1:10521/',note:'klar 引擎自带 SPA（经你电脑上的本地代理 127.0.0.1:10521）：语言包、自定义短语、播报模板、settings、对话历史、LLM 配置。token 由本机代理持有，无需手填。'},
 ggradio:{url:'http://127.0.0.1:7861/',note:'ggradio 语音链试验台：参数配置、逐段运行、产物试听与下载。连接的是打开浏览器这台电脑上的 7861 服务；请先启动 ggradio。'},
 stream:{url:'http://192.168.1.113:8092/',note:'SDI 内网直播（H.264 实时画面）。当前摄像头源不可用时会显示"暂无新鲜摄像头画面"。'},
 record:{url:'http://192.168.1.113:8093/',note:'摄像头录像管理：开关录制、存储配额、回收站、录像列表。'},
 face:{url:'http://192.168.1.113:8091/',note:'NPU 人脸识别与 gallery 管理。当前服务 503 时页面打不开。'}
};
function embeddedUrl(url){
  const target=new URL(url,location.href);
  target.searchParams.set('console_embed','1');
  target.searchParams.set('console_theme',document.documentElement.dataset.theme==='light'?'light':'dark');
  target.searchParams.set('console_parent_origin',location.origin);
  return target.href;
}
function sendEmbeddedTheme(frame){
  const frames=frame?[frame]:document.querySelectorAll('iframe[data-embed]');
  for(const target of frames){
    if(!target.contentWindow||!/^https?:/.test(target.src)) continue;
    target.contentWindow.postMessage({type:'ha4fpai:theme',
      theme:document.documentElement.dataset.theme==='light'?'light':'dark'},new URL(target.src).origin);
  }
}
function mountEmbed(frame){
  const key=frame.dataset.embed;
  const entry=EMBED[key];
  if(!entry) return;
  const group=frame.closest('[data-embed-group]');
  group.querySelector('.embednote').textContent=entry.note;
  // Standalone links keep their original URL and independent theme preference.
  group.querySelector('.extlink').href=entry.url;
  if(frame.dataset.loadedKey!==key){
    frame.src=embeddedUrl(entry.url);
    frame.dataset.loadedKey=key;
  }else sendEmbeddedTheme(frame);
}
function pick(k, button){
  const group=button.closest('[data-embed-group]');
  group.querySelectorAll('[role="tab"]').forEach(tab=>{
    const active=tab===button;
    tab.classList.toggle('active',active);
    tab.setAttribute('aria-selected',String(active));
  });
  const voiceprints=group.querySelector('.voiceprint-view');
  const showVoiceprints=k==='voiceprints';
  group.querySelector('.embed-view').hidden=showVoiceprints;
  group.querySelector('.extlink').hidden=showVoiceprints;
  if(voiceprints) voiceprints.hidden=!showVoiceprints;
  if(showVoiceprints){
    if(typeof loadVP==='function') loadVP();
    return;
  }
  group.querySelector('.embed-view').setAttribute('aria-labelledby',button.id);
  const frame=group.querySelector('iframe[data-embed]');
  frame.dataset.embed=k;
  frame.title=button.textContent.trim();
  mountEmbed(frame);
}
for(const frame of document.querySelectorAll('iframe[data-embed]')){
  frame.addEventListener('load',()=>sendEmbeddedTheme(frame));
}
for(const tablist of document.querySelectorAll('[role="tablist"]')){
  tablist.addEventListener('keydown',event=>{
    const tabs=[...tablist.querySelectorAll('[role="tab"]')];
    const index=tabs.indexOf(event.target);
    if(index<0) return;
    let next;
    if(event.key==='ArrowRight') next=(index+1)%tabs.length;
    else if(event.key==='ArrowLeft') next=(index+tabs.length-1)%tabs.length;
    else if(event.key==='Home') next=0;
    else if(event.key==='End') next=tabs.length-1;
    else return;
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  });
}
window.addEventListener('message',event=>{
  const frame=[...document.querySelectorAll('iframe[data-embed]')].find(f=>f.contentWindow===event.source);
  if(!frame||!/^https?:/.test(frame.src)||event.origin!==new URL(frame.src).origin) return;
  if(event.data&&event.data.type==='ha4fpai:theme-ready') sendEmbeddedTheme(frame);
});
syncNav();
</script>
<script>
async function loadHealth(){
  const h=await (await fetch('/api/health')).json();
  let html='';
  const card=(t,dotCls,big,rows)=>`<div class="card"><h3>${t}</h3>
     <div class="big"><span class="dot ${dotCls}"></span>${big}</div>${rows||''}</div>`;
  html+=card('语音 gateway',dot(h.gateway.unit),h.gateway.unit,
      `<div class="kv"><span>RSS</span><span>${h.gateway.rss_mb??'-'} MB</span></div>`);
  html+=card('klar engine',dot(h.engine.unit),h.engine.unit,
      `<div class="kv"><span>RSS</span><span>${h.engine.rss_mb??'-'} MB</span></div>`);
  for(const [k,label] of [['wake','唤醒 KWS 10300'],['asr','ASR 10301'],['tts','TTS 10302']]){
    const v=h[k]||{}; html+=card(label, v.ok?'ok':'bad', v.ok?'在线':'离线',
      `<div class="kv"><span>${v.name||v.kind||'-'}</span></div>`);
  }
  html+=card('NPU 人脸 8091',stateDot(h.face),h.face.http===200?'正常':('HTTP '+(h.face.http??'?')));
  html+=card('画面 8092',stateDot(h.stream),h.stream.ready?'就绪':'无有效帧',
      `<div class="kv"><span>HTTP ${h.stream.http??'?'}</span></div>`);
  html+=card('录制 8093',stateDot(h.record),h.record.mode||h.record.state||'-',
      `<div class="kv"><span>HTTP ${h.record.http??'?'}</span></div>`);
  if(h.mem) html+=card('内存',((h.mem.avail_mb/h.mem.total_mb)>0.25?'ok':(h.mem.avail_mb/h.mem.total_mb>0.1?'warn':'bad')),
      h.mem.avail_mb+' MB 可用',`<div class="kv"><span>共</span><span>${h.mem.total_mb} MB</span></div>`);
  document.getElementById('health').innerHTML=html;
}
async function loadCmds(){
  const c=await (await fetch('/api/commands')).json();
  document.getElementById('cmds').innerHTML=c.map(x=>{
     if(x.error) return '<span class="c-bad">'+x.error+'</span>';
     return `<button onclick="runCmd(${JSON.stringify(x.phrases[0]).replace(/"/g,'&quot;')})">${x.phrases[0]}</button>`;
  }).join('');
}
async function doRun(text){
  const speak=document.getElementById('speak').checked;
  const dry=document.getElementById('dry').checked;
  const url=`/api/run?text=${encodeURIComponent(text)}&speak=${speak?1:0}&dry=${dry?1:0}`;
  document.getElementById('result').textContent='执行中…';
  const r=await (await fetch(url)).json();
  document.getElementById('result').textContent=fmt(r);
  loadHealth(); loadHist();
}
function runCmd(t){doRun(t);}
function runCustom(){const v=document.getElementById('custom').value.trim(); if(v) doRun(v);}
async function loadVP(){
  const v=await (await fetch('/api/voiceprint')).json();
  document.getElementById('vp').innerHTML =
   `<h3>声纹库</h3><div class="kv"><span>路径</span><span>${v.store||'-'}</span></div>
    <div class="kv"><span>维度</span><span>${v.dim||'-'}</span></div>
    <div class="kv"><span>人数</span><span>${v.count??'-'}</span></div>
    <div class="kv"><span>成员</span><span>${(v.speakers||[]).join(', ')||'(空)'}</span></div>`
    +(v.error?`<div class="c-bad">${v.error}</div>`:'');
}
async function loadHist(){
  const h=await (await fetch('/api/history')).json();
  const tb=document.querySelector('#hist tbody');
  tb.innerHTML=(h.slice().reverse()).map(r=>{
    const t=new Date(r.ts*1000).toLocaleTimeString();
    const cls=r.ok?'c-ok':'c-bad';
    return `<tr><td class="c-dim">${t}</td><td>${r.text||''}</td><td>${r.channel||''}</td>
            <td class="${cls}">${(r.message||'').slice(0,60)}</td></tr>`;
  }).join('')||'<tr><td colspan=4 class="c-dim">（还没有记录）</td></tr>';
}
async function loadLogs(){
  const g=await (await fetch('/api/logs?name=gateway')).json();
  const k=await (await fetch('/api/logs?name=klar')).json();
  document.getElementById('log-gw').textContent=(g.lines||[]).join('\n');
  document.getElementById('log-klar').textContent=(k.lines||[]).join('\n');
}
function updateClock(){document.getElementById('clock').textContent=new Date().toLocaleTimeString();}
updateClock();setInterval(updateClock,1000);
document.getElementById('custom').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing) runCustom();});
loadHealth();loadCmds();loadVP();loadHist();loadLogs();
setInterval(loadHealth,8000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass                                     # 静音 access log

    def _send(self, code, ctype, body):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
            ctype = "application/json; charset=utf-8"
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p == "/":
            return self._send(200, "text/html; charset=utf-8", INDEX_HTML)
        if p == "/api/health":
            return self._send(200, "application/json", health())
        if p == "/api/commands":
            return self._send(200, "application/json", commands())
        if p == "/api/voiceprint":
            return self._send(200, "application/json", voiceprint_info())
        if p == "/api/history":
            return self._send(200, "application/json", history(int(q.get("n", ["30"])[0])))
        if p == "/api/logs":
            name = q.get("name", ["gateway"])[0]
            path = ("/mnt/data/klar/gateway.log" if name == "gateway"
                    else "/mnt/data/klar/klar.log")
            return self._send(200, "application/json", {"name": name, "lines": tail(path)})
        if p == "/api/run":
            text = q.get("text", [""])[0]
            if not text:
                return self._send(400, "application/json", {"error": "missing text"})
            out = run_command(text,
                              speak=q.get("speak", ["0"])[0] == "1",
                              dry_run=q.get("dry", ["0"])[0] == "1")
            return self._send(200, "application/json", out)
        return self._send(404, "text/plain; charset=utf-8", "not found")

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf8")) if raw else {}
        except ValueError:
            body = {}

        if u.path == "/api/run":
            text = body.get("text") or ""
            if not text:
                return self._send(400, "application/json", {"error": "missing text"})
            return self._send(200, "application/json",
                              run_command(text, speak=bool(body.get("speak")),
                                          dry_run=bool(body.get("dry_run")))
                              )
        return self._send(404, "application/json", {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10900)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    LOG.info("console on http://%s:%d/", args.host, args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
