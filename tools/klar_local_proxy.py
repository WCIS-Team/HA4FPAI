#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_local_proxy.py —— 在**你自己电脑**上持有 klar token，无感访问板子 UI。

## 为什么改成这样

之前的方案是在**板子上**跑一个 10521 代理，它把 token 明文注入 HTML：
```html
<script>localStorage.setItem('klar_token', "336b...")</script>
```
任何能访问板子 10521 的人 `curl` 一下就能拿到 token —— **这是漏洞**，已删除。

现在的分工：
  * **板子**：只留服务端 secret（`/mnt/data/klar/.klar-token`），用来校验，**不对外暴露**
  * **你的电脑**：本地持有同一份 token，由本代理自动加 `x-klar-token` 头
  * 代理**只监听 127.0.0.1**，外网根本连不到，token 不出本机

## 无感原理

浏览器 → `http://127.0.0.1:10521/` → 本代理补 `x-klar-token` → 板子 `192.168.1.113:10520`

前端（klar UI）从 localStorage 读 token，读不到就不发这个头；
但**本代理会无条件补上**，所以前端根本不需要知道 token 存在。
⇒ 不再需要往页面注入任何东西，明文泄露问题一并消失。

## 用法

    python klar_local_proxy.py

然后浏览器开 **http://127.0.0.1:10521/**

token 读取顺序：
  1. 环境变量 KLAR_TOKEN
  2. 同目录 .klar-token
  3. ~/.klar-token

## 首次配置

把板子上的 token 抄到本机（只需一次）：
    plink ... "cat /mnt/data/klar/.klar-token" > %USERPROFILE%\\.klar-token
"""
import argparse
import http.client
import json
import logging
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("klar-local")

BOARD = "192.168.1.113"
BOARD_PORT = 10520

# 本代理只服务 klar UI 与其 API，转发白名单前缀
ALLOW_PREFIX = ("/api/", "/assets/", "/favicon", "/index.html", "/")


def load_token():
    tok = os.environ.get("KLAR_TOKEN", "").strip()
    if tok:
        LOG.info("token 来自环境变量 KLAR_TOKEN")
        return tok
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, ".klar-token"),
              os.path.join(os.path.expanduser("~"), ".klar-token")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                LOG.info("token 来自 %s（%d 字符）", p, len(tok))
                return tok
        except OSError:
            continue
    return ""


class Proxy(BaseHTTPRequestHandler):
    token = ""
    upstream = (BOARD, BOARD_PORT)

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        LOG.debug("%s %s", self.address_string(), fmt % args)

    def _proxy(self, method):
        # 只接受本机访问（双保险：监听地址已是 127.0.0.1）
        if not self.client_address[0].startswith("127."):
            self.send_error(403, "loopback only")
            return

        path = self.path
        if not path.startswith(ALLOW_PREFIX):
            self.send_error(404, "not proxied")
            return

        body = None
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            body = self.rfile.read(n)

        headers = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "connection", "content-length",
                             "accept-encoding", "x-klar-token", "authorization"):
                continue
            headers[k] = v
        # 关键：无条件补上正确的 token（覆盖前端可能带错的值）
        if self.token:
            headers["x-klar-token"] = self.token
        if body is not None:
            headers["Content-Length"] = str(len(body))
        headers["Accept-Encoding"] = "identity"

        conn = http.client.HTTPConnection(self.upstream[0], self.upstream[1], timeout=60)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except Exception as e:                       # noqa: BLE001
            LOG.warning("上游不可达: %s", e)
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("板子不可达 %s:%d — %s" % (self.upstream[0], self.upstream[1], e)).encode("utf-8"))
            return
        finally:
            try:
                conn.close()
            except Exception:                        # noqa: BLE001
                pass

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10521)
    ap.add_argument("--board", default=BOARD)
    ap.add_argument("--board-port", type=int, default=BOARD_PORT)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    Proxy.token = load_token()
    Proxy.upstream = (args.board, args.board_port)

    if not Proxy.token:
        LOG.error("找不到 token。请先把板子上的 token 抄到 ~/.klar-token，"
                  "或设置环境变量 KLAR_TOKEN。")
        LOG.error("抄取命令示例：")
        LOG.error("  plink ... root@%s \"cat /mnt/data/klar/.klar-token\" > %%USERPROFILE%%\\\\.klar-token",
                  args.board)
        sys.exit(2)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Proxy)
    LOG.info("本地代理: http://127.0.0.1:%d/  ->  http://%s:%d/",
             args.port, args.board, args.board_port)
    LOG.info("只监听本机，token 不出这台电脑。浏览器开上面的地址即可，无需手填 token。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        LOG.info("bye")


if __name__ == "__main__":
    main()
