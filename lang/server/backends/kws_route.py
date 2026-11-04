#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kws_route.py —— KWS 之后那次路由的**薄调用层**（实现不在这里）。

路由规则写在 `lang/kws/router.py`（她 2026-11-04 定："具体写在 kws 文件夹里，server 去调用"）。
本文件只做三件事：
  1. 把 `lang/kws/` 塞进 sys.path，import 它的 `router`；
  2. 把 (samples, (hit, tag)) 转给 `router.route()`；
  3. 把它的 dict 原样交回 `core.Chain`。

改路由请去 `lang/kws/router.py` 与 `lang/config/kws.yaml` 的 `kws_route_profiles`；别在这里加代码。
"""

import os
import sys

_KWS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "kws"))
if _KWS_DIR not in sys.path:
    sys.path.insert(0, _KWS_DIR)

import router as kws_router                              # noqa: E402  lang/kws/router.py

from core import Stage

__all__ = ["KwsRoute"]


class KwsRoute(Stage):
    """档名 `default`：直接转发到 lang/kws/router.py 的 route()。"""

    name = "default"

    def run(self, args):
        samples, kws_result = (args + (None,))[:2] if isinstance(args, (tuple, list)) \
            else (args, None)
        ctx = {"cfg": self.cfg, "rate": 16000}
        return kws_router.route(samples, kws_result, self.profile, ctx)