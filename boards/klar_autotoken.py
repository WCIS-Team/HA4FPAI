#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_autotoken.py — 让 klar UI 永远不再弹 401。

## 为什么会弹 401

klar 的安全策略是：**没有 token 时，只允许 localhost 写入**。
浏览器从别的机器访问 `http://板:10520` 时，所有写操作被拒，弹"设 token"页。

源码级证据（前端 `index-Bg17n22J.js`）：
```js
let e = localStorage.getItem(`klar_token`) || ``;
return { "content-type": `application/json`, ...e ? {"x-klar-token": e} : {} }
```
```js
function et(e){ e.trim() && localStorage.setItem(`klar_token`, e.trim()) }
```

也就是说：**UI 从浏览器 localStorage 的 `klar_token` 读值，塞进 `x-klar-token` 头。**
只要那个 key 有值，401 就不再出现。

实测（从外部网络打板子 10520）：
```
无 header              -> 401
x-klar-token: <token>  -> 200  ✅
Authorization: Bearer  -> 200  ✅
```

## 本脚本做什么

提供一个**极薄的同源代理**：浏览器访问本服务，它把请求转发到 klar，
并**自动补上 `x-klar-token`**。这样：

  * 用户完全不用手填 token
  * 也不用改 klar 二进制（做不到）
  * 也不用把 token 硬编码进别人的前端

同时对 HTML 响应注入一小段 JS，把 token 预置进 `localStorage`——
这样即使前端自己发请求，也自带 token。

## 用法

    python3 klar_autotoken.py --port 10521 --upstream http://127.0.0.1:10520

然后浏览器开 **http://板:10521**（不是 10520）。
控制台的「Klar NLU 控制台」标签也已指向它。

## 安全说明（必须知道）

这个代理等于**把 klar 的写权限开放给任何能访问 10521 的人**。
只适合家庭内网。若要对公网暴露，必须自己在前面加鉴权。
"""
import argparse
import http.client
import json
import logging
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("autotoken")
TOKEN_FILE = os.environ.get("KLAR_TOKEN_FILE", "/mnt/data/klar/.klar-token")

# 注入到 HTML 的脚本：把 token 预置进 localStorage（UI 自己会读它）
INJECT = """
<script>
(function(){
  try { localStorage.setItem('klar_token', %s); } catch(e) {}
})();
</script>
"""


def read_token():
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:                       # noqa: BLE001
        LOG.error("读不到 token 文件 %s: %s", TOKEN_FILE, e)
        return ""


class Proxy(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:10520"
    token = ""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ---- 内部：转发 ----
    def _proxy(self, method):
        u = urllib.parse.urlsplit(self.upstream)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=30)

        # 原样带上 path+query
        path = self.path
        body = None
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            body = self.rfile.read(n)

        headers = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "connection", "content-length", "accept-encoding"):
                continue
            headers[k] = v
        # 关键：无条件补 token（客户端自己带也行，我们覆盖成正确值）
        if self.token:
            headers["x-klar-token"] = self.token
        if body is not None:
            headers["Content-Length"] = str(len(body))
        headers["Accept-Encoding"] = "identity"

        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except Exception as e:                   # noqa: BLE001
            LOG.warning("upstream failed: %s", e)
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("上游不可达: %s" % e).encode())
            return
        finally:
            try:
                conn.close()
            except Exception:                    # noqa: BLE001
                pass

        ctype = resp.getheader("Content-Type", "") or ""

        # 对 HTML 注入 token（让 UI 自己的 fetch 也带 token）
        if "text/html" in ctype and self.token:
            text = data.decode("utf-8", "ignore")
            script = INJECT % json.dumps(self.token)
            if "</head>" in text:
                text = text.replace("</head>", script + "</head>", 1)
            else:
                text = script + text
            data = text.encode("utf-8")

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("content-length", "connection", "transfer-encoding",
                             "content-encoding"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def do_PUT(self):
        self._proxy("PUT")

    def do_DELETE(self):
        self._proxy("DELETE")

    def do_PATCH(self):
        self._proxy("PATCH")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "content-type,x-klar-token,authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,PATCH,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10521)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--upstream", default="http://127.0.0.1:10520")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    Proxy.upstream = args.upstream
    Proxy.token = read_token()
    if not Proxy.token:
        LOG.warning("token 为空 —— 代理不会补鉴权头；请检查 %s", TOKEN_FILE)
    else:
        LOG.info("token 已加载（%d 字符）", len(Proxy.token))

    srv = ThreadingHTTPServer((args.host, args.port), Proxy)
    LOG.info("klar 免 401 代理: http://%s:%d/  -> %s", args.host, args.port, args.upstream)
    LOG.info("请用这个地址访问 Klar UI（不是 10520），就不会再弹 401")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
