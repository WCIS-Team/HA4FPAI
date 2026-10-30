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
        "unit": svc_unit("klar-voice-gateway"),
        "rss_mb": unit_rss("klar-voice-gateway"),
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
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HA4FPAI 控制台</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e6e8ee;--dim:#8b93a7;
      --ok:#3ddc84;--warn:#ffb020;--bad:#ff5c5c;--acc:#5aa9ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr);gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h3{margin:0 0 8px;font-size:12px;color:var(--dim);font-weight:600;letter-spacing:.03em}
.big{font-size:20px;font-weight:650}
.kv{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--dim);margin-top:5px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
.c-ok{color:var(--ok)}.c-warn{color:var(--warn)}.c-bad{color:var(--bad)}.c-dim{color:var(--dim)}
section{margin-bottom:22px}
section>h2{font-size:15px;margin:0 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.cmds{display:flex;flex-wrap:wrap;gap:8px}
button{background:#222836;color:var(--fg);border:1px solid var(--line);border-radius:8px;
       padding:7px 13px;cursor:pointer;font-size:13px}
button:hover{background:#2b3344;border-color:var(--acc)}
button.primary{background:#1d3a5c;border-color:#2f5d95}
input[type=text]{background:#11141b;color:var(--fg);border:1px solid var(--line);
       border-radius:8px;padding:9px 12px;width:340px;font-size:13px}
pre{background:#0c0e13;border:1px solid var(--line);border-radius:8px;padding:11px;
    overflow:auto;max-height:340px;font-size:12px;line-height:1.45;margin:0}
.logrow{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:#a8b0c4}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600}
.chk{display:flex;align-items:center;gap:6px;color:var(--dim);font-size:12px}
</style></head><body><div class="wrap">
<h1>HA4FPAI 控制台</h1>
<div class="sub">板上语音链路 + klar NLU + 板子自有 API 的统一入口 · <span id="clock"></span></div>

<section><h2>系统体检</h2><div class="grid" id="health">加载中…</div></section>

<section><h2>命令（点一下就跑）</h2>
  <div class="cmds" id="cmds"></div>
  <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <input type="text" id="custom" placeholder="或者自己输入一句，如 我回来了，开门">
    <button class="primary" onclick="runCustom()">执行</button>
    <label class="chk"><input type="checkbox" id="speak"> 播报(TTS)</label>
    <label class="chk"><input type="checkbox" id="dry"> 只演练</label>
  </div>
  <pre id="result" style="margin-top:12px">（结果会显示在这里）</pre>
</section>

<section><h2>声纹库</h2><div id="vp" class="card">加载中…</div></section>

<section><h2>执行历史</h2><div class="card"><table id="hist"><thead>
<tr><th>时间</th><th>输入</th><th>通道</th><th>结果</th></tr></thead><tbody></tbody></table></div></section>

<section><h2>日志</h2>
  <div class="card"><h3>gateway</h3><pre id="log-gw" class="logrow"></pre></div>
  <div class="card" style="margin-top:12px"><h3>klar engine</h3><pre id="log-klar" class="logrow"></pre></div>
</section>
</div>
<script>
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
setInterval(()=>document.getElementById('clock').textContent=new Date().toLocaleTimeString(),1000);
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
