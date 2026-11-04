#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""legacy_tiers.py —— 原来的两档分词器：`punct`（标点切句）与 `window`（固定字数硬切）。

**2026-11-04 从 `lang/server/backends/text.py` 原样搬到这里**（她定：「原来的也放这个文件夹
里，做成可切换的」）。代码逐行照搬、**行为一点没改**——`lang/server/tests/smoke.py` 的
`t_tokenizer()` 钉的就是这两档的原有切句结果。

三档现在都在 `lang/split/` 里，切换只改 `lang/config/server.yaml` 的 `chain.tokenizer`：

    chain:
      tokenizer: tts_split     # 新档（严格 20 字上限，见 tts_split.py）
      # tokenizer: punct      # 原来的标点档：句末必切、逗号只在攒够 max_chars 才切
      # tokenizer: window     # 固定字数硬切：完全无标点容忍度，压峰值最狠、语流最差

`server/backends/text.py` 现在只是把这里的三档转出去（薄壳），不再是实现处。

档契约（`lang/server/core.py` 头注）：`name` ＋ `__init__(cfg, profile, shared)` ＋
`run(text) -> [chunk]`；参数取自 `tokenizer_profiles.<档名>`。
"""


class Punct(object):
    """标点切句：句末符（。！？；换行…）必切；逗号只在攒到 max_chars 才切；
    攒出来的超长块最后按 max_chars 二次硬断，所以**块长不超过 max_chars**。

    它的毛病不在长度而在**断点不看语义**：切点是「攒够 max_chars 后遇到的第一个逗号」，
    常常把一句话切成半句；不认英文 `. , ! ?`、不处理引号括号、也不合并末尾碎块。
    新档 `tts_split` 就是冲着这些来的（见 tts_split.py 头注）。
    """

    name = "punct"

    ENDERS = "。！？!?…；;\n\r"
    COMMAS = "，,、：: "

    def __init__(self, cfg, profile, shared):
        self.max_chars = int((profile or {}).get("max_chars", 40) or 40)
        self.soft = ((profile or {}).get("split_comma", "1") or "1") not in ("0", "false", "no")

    def run(self, text):
        chunks, buf = [], ""
        for ch in text:
            buf += ch
            if ch in self.ENDERS:
                chunks.append(buf)
                buf = ""
            elif len(buf) >= self.max_chars and (self.soft and ch in self.COMMAS or not self.soft):
                chunks.append(buf)
                buf = ""
        if buf.strip():
            chunks.append(buf)
        out = []
        for c in chunks:                                  # 行首空格收掉；超长残块硬断
            c = c.strip()
            while len(c) > self.max_chars:
                out.append(c[:self.max_chars].strip())
                c = c[self.max_chars:].strip()
            if c:
                out.append(c)
        return out


class Window(object):
    """固定字数硬切：每 width 个字一段，不认任何标点（压 TTS 峰值最狠，语流最差）。"""

    name = "window"

    def __init__(self, cfg, profile, shared):
        self.width = int((profile or {}).get("width", 40) or 40)

    def run(self, text):
        text = (text or "").strip()
        return [text[i:i + self.width].strip()
                for i in range(0, len(text), self.width) if text[i:i + self.width].strip()]