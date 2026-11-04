# -*- coding: utf-8 -*-
"""Line-framed JSON between the supervisor and a model worker.

One object per line, UTF-8, "\n" terminated -- the same framing e41_chatd.py already
speaks (board/e41_chatd.py:215-216), so a model that already has a working socket
daemon can be wrapped instead of rewritten.

Kept in one place because both sides need it: broken framing looks exactly like a model
bug from the outside.  The handshake travels in a file, not over a pipe, per 台账 §十五
(SSH 一断，管道里的遗言全丢).
"""
import json
import socket


def send_obj(sock, obj):
    sock.sendall((json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
                 .encode("utf-8"))


class LineReader(object):
    """Newline-delimited objects over a socket, with per-read timeouts."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read(self, timeout=None):
        """Next object, or None on clean EOF.  timeout=None blocks forever."""
        self.sock.settimeout(timeout)
        while True:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line, self.buf = self.buf[:nl], self.buf[nl + 1:]
                if line.strip():
                    return json.loads(line.decode("utf-8"))
                continue
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk


def connect(port, timeout=10.0):
    """Control channel is loopback-only by design; the worker binds 127.0.0.1:0."""
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s
