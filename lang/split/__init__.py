#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lang/split —— TTS 前的切分器：三档可切换，纯标准库、零第三方依赖。

    from split import split_for_tts          # 新档：标点优先 + 严格字数上限（默认 20 字）
    from split import Punct, Window          # 原来的两档（行为未改）

三档在 `lang/server` 里靠 `chain.tokenizer` 切换（`tts_split`｜`punct`｜`window`），
参数在 `lang/config/split.yaml` 的 `tokenizer_profiles`。直接用脚本的用法见 `cli.py`
与 `README.md`；自检 `selftest.py`。
"""

from .legacy_tiers import Punct, Window
from .tts_split import ServerStage, split_for_tts

__all__ = ["split_for_tts", "ServerStage", "Punct", "Window"]