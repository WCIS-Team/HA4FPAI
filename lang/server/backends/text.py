#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text.py —— 分词器档（三档全部实现已搬到 `lang/split/`，本文件只做转出）。

2026-11-04 她定：链路切到新档 `tts_split`（严格 20 字上限），并把原来的 `punct`／`window`
一起放进 `lang/split/`，做成可切换的三档。所以：

  lang/split/tts_split.py     新档（标点＋字数上限，严格不超上限）
  lang/split/legacy_tiers.py  原来的 punct / window（逐行照搬，行为未改）
  lang/server/backends/text.py ← 本文件：只把三档的名字转出去，注册表照常 import

改切句算法请去 `lang/split/`，别在这里加代码；本文件保持"薄壳"形态，免得又出现两份实现。
"""

import os
import sys

_SPLIT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "split"))
if _SPLIT_DIR not in sys.path:
    sys.path.insert(0, _SPLIT_DIR)

from legacy_tiers import Punct, Window                  # noqa: E402  原来的两档
from tts_split import ServerStage as TtsSplit           # noqa: E402  新档（严格字数上限）

__all__ = ["Punct", "Window", "TtsSplit"]