#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_split.py —— TTS 前的切分器：把长句切成适合逐段朗读的小块（纯标准库，零依赖）。

**这里不做中文分词**。要解决的是「长文本整段塞进 TTS 会撞峰值 RSS／单段字数上限」，
不是词语边界：jieba 那类分词器要词典或模型、多占内存，且给不出「在哪断气」的答案——
断句靠标点＋字数上限就够了。口径来源：`D-1793754986`（「分词器＝LLM 长句切短压 TTS 峰值
RSS」）、`lang/tts/models/edge/README.md`（Edge 每句一次网络往返，实测约 3 s）。

**默认 `max_chars=20` 且 `strict=True`**（她 2026-11-04 定：「超过 20 字就强制截断」）：
严格模式下**每一块的原始字符数都不超过 `max_chars`**，没有例外、没有外溢。注意这里的
「截断」＝**在 20 字处强制切开**，不是把多出来的字丢掉——切分只动空白，一个字都不丢
（`selftest.py` 的「内容守恒」不变量钉住这一条）。

切点优先级（窗口＝前 `max_chars` 个字符；默认 `prefer="sentence"`）：
  ① 句末符 `。！？!?；;…` 与换行——取窗口内**最靠后且结构合法**的一个；`.` 只在其后
     不是字母数字时才算句末（`3.14`、`e.g` 不算）；
  ② 分句符 `，,、：`、空格／全角空格／连接号，以及右引号右括号——同样取最靠后的一个。
     **ASCII `:` 不算切点**（它长在 `https://`、`12:30`、`18091:端口` 里），连接号 `-` 同理不切；
  ③ 窗口里没有任何合法标点 ⇒ 在 `max_chars` 处硬断。`prefer="fill"` 时①与②合成一遍，
     纯按位置取最靠后（块数更少，代价是可能落在逗号而不是句号上）。

结构合法性（`_legal_cut`）：
  - 不从开引号／开括号**后面**断（「他说：」「 这类不拆开）；
  - 不从右引号／右括号**前面**断（`。」`／`。）` 跟着左块走）；
  - 不从分句符前面断（下一块不以 `，` 开头）；
  - 硬断点落在 ASCII 记号（单词／数字／URL／千分位）中间时，先退到该记号开头。

块长：**严格模式（默认）＝每块 ≤ `max_chars` 原始字符数，无例外**。宽松模式
（`strict=False`，给「音质优先、可略超」的场合）允许三处外溢，都写在这里免得被当 bug 修掉：
  1. 收尾标点外溢：句末符后紧跟的 `」』”）】` 与连续省略号 `……` 并入左块，可比上限多几个字符；
  2. 超长 ASCII 记号：单个英文词／URL／长数字比 `max_chars` 还长时整块保留；
  3. 过短末块合并：末块 < `min_tail` 个字符时并进前一块（最多外溢 `min_tail-1` 个字符）——
     孤零零的一两个字要多一次 TTS 往返（Edge 约 3 s），而且单独念出来是半截字。

严格模式的代价（她选的：长度优先）：**右引号 `」』”` 或分句符 `，` 正好落在第 `max_chars`
字之后时，只能让下一块以它起头**——要把它们并回左块就得超 1 个字符。要消除这个现象就把
`strict` 关掉（允许那几个字符的外溢）。

`max_chars` 默认 20 **不是拍的**：现役 `edge` 档实测单块音频上限 **600 s ≈ 2827 汉字**
（3000 字起静默丢尾、不报错），本仓实测每字 ≈ 0.21 s 音频 ⇒ 20 字只占上限的 0.7%；真正卡住
上限的是本地 matcha 的延迟拐点（20 音素 1730 ms，板上实测）。推导与全部读数在
`lang/config/split.yaml` 头注；代码里留了 `MAX_CHARS_TRIPWIRE=1000`，超线 `ServerStage` 会往
stderr 告警（不硬失败）。

用法：
    from tts_split import split_for_tts
    split_for_tts("……一段长文本……")                    # 默认 20 字严格上限 -> ["…块1…", …]
    split_for_tts(text, max_chars=40, strict=False)      # 宽松：引号/长词/短末块可略超

    # 命令行：python -X utf8 cli.py --max-chars 20 --file in.txt
    # 自检：  python -X utf8 selftest.py
"""

import re
import sys

__all__ = ["split_for_tts", "ServerStage", "ENDERS", "WEAK", "CLOSERS", "OPENERS"]

ENDERS = "。！？!?；;…\n\r."
CLOSERS = "」』”’）)】》〉］]｝}"
OPENERS = "「『“‘（(【《〈［[｛{"
WEAK = "，,、：—～~·"
SPACES = " \t\u3000\u00a0"
BLANKS = SPACES + "\n\r"                      # 判「块尾/块首还剩下什么」时换行也算空白
TAIL_MARKS = "。！？!?；;….．" + CLOSERS      # 收尾时可并进左块的标点（不含换行与空白）
WORD_RE = re.compile(r"[A-Za-z0-9_]")                 # 「小数点后接字母数字」判定用
TOKEN_RE = re.compile(r"[A-Za-z0-9_.,/:%@+\-]")      # 硬断时不可劈开的 ASCII 记号（URL/邮箱/小数/千分位）
MIN_TAIL = 4                                 # 末块短于此值就并进前一块（多一次往返就是多 3 s）

# ── max_chars 的依据（实测，2026-11-04；推导与读数在 lang/config/split.yaml 头注）──────────
# 现役 edge 档单块音频上限 600 s ≈ 2827 汉字（3000 字起静默丢尾），每字约 0.21 s 音频；
# 本地 matcha 的延迟拐点是 20 音素 1730 ms（板上实测）。20 字＝上限的 0.7%、≈20 音素。
EDGE_SEC_PER_CHAR = 0.21                     # 实测（edge-tts 7.3.1）：每字约 0.21 s 音频
EDGE_SEGMENT_LIMIT_CHARS = 2827              # 实测：600 s 音频上限 → 约 2827 汉字，超出静默丢字
MAX_CHARS_TRIPWIRE = 1000                    # 超过这条线就告警（离丢字区间只剩 3 倍）

__all__ += ["check_max_chars", "MAX_CHARS_TRIPWIRE"]


# ------------------------------------------------------------------ 切点判定


def _legal_cut(s, p):
    """切点 p（左块为 s[:p]）在结构上是否可接受。

    判的是**去掉空白之后**的两侧：`还 （ 给` 在空格处断会把 `（` 留在左块尾部，
    所以必须按「左块最后一个非空白字符」「右块第一个非空白字符」来判。
    """
    if p <= 0:
        return False
    left = s[:p].rstrip(BLANKS)
    if not left:                      # 切出来是纯空白 → 不接受
        return False
    right = s[p:].lstrip(BLANKS)
    prev = left[-1]
    nxt = right[0] if right else ""
    if prev in OPENERS:               # 「他说：」|你好| → 不从开引号后断
        return False
    if nxt and nxt in CLOSERS:        # |…。」| → 右引号跟着左块
        return False
    if nxt and nxt in WEAK:           # |…。|，继续| → 下一块不以分句符开头
        return False
    if prev in ".．" and nxt and (WORD_RE.match(nxt) or nxt == "/"):   # 3.14 / e.g / a.com/ → 不断
        return False
    if prev == "," and nxt and nxt.isdigit() and len(left) >= 2 and left[-2].isdigit():  # 1,000
        return False
    return True


def _absorb_tail(s, p, ceiling):
    """把紧跟切点的收尾标点（连续省略号、成对的右引号右括号）并进左块；**不超过 ceiling**。"""
    while p < ceiling and s[p] in TAIL_MARKS:
        p += 1
    return p


def _scan(s, hi, marks, ceiling):
    """窗口前 hi 个字符里，最靠后的合法切点；没有返回 0。"""
    for k in range(min(hi, len(s)) - 1, -1, -1):
        if s[k] in marks:
            p = _absorb_tail(s, k + 1, ceiling)
            if _legal_cut(s, p):
                return p
    return 0


def _scan_forward(s, hi, marks, ceiling):
    """越过窗口往后找第一个合法切点（hard_cut=False 且非严格时用：宁可超长也不硬断）。"""
    for k in range(hi, len(s)):
        if s[k] in marks:
            p = _absorb_tail(s, k + 1, ceiling)
            if _legal_cut(s, p):
                return p
    return 0


def _hard_cut(s, hi, ceiling):
    """没有任何标点可用时的硬断点：护住 ASCII 记号（词/数字/URL）、右引号分句符与开引号。

    三步的**顺序有讲究**：先躲开 ASCII 记号（不然 `（` 后面紧跟的 `abc` 会把断点拽到 `（`
    之后），再把右块开头的分句符/右引号归给左块（`商（   “abc` 不许断成 `商（`+`“abc`），
    最后才处理左块尾部的开引号——三步**只会把断点往前挪或原地不动**，不会超过 ceiling。
    严格模式下 `ceiling == hi`，所以「超长记号整块保留」那一步不会发生（`elif` 挡住）。
    """
    p = hi
    if 0 < p < len(s) and TOKEN_RE.match(s[p - 1]) and TOKEN_RE.match(s[p]):
        k = p
        while k > 0 and TOKEN_RE.match(s[k - 1]):
            k -= 1
        if k > 0:                                   # 退到记号开头（3.14159 / https://… 整段留给下一块）
            p = k
        elif ceiling > hi:                          # 宽松模式才允许把超长记号整块留下
            while p < len(s) and TOKEN_RE.match(s[p]):
                p += 1
    while p < ceiling and p < len(s):               # 右块不许以「空白＋分句符/右引号」起头
        q = p
        while q < ceiling and q < len(s) and s[q] in BLANKS:   # 空白不发音，可跟着标点吞进左块
            q += 1
        if q < ceiling and q < len(s) and s[q] in WEAK + CLOSERS:
            p = q + 1
        else:
            break
    q = len(s[:p].rstrip(BLANKS))                   # 左块（去尾随空白后）不许以开引号收尾
    while q > 1 and s[q - 1] in OPENERS:
        q = len(s[:q - 1].rstrip(BLANKS))           # 缩掉这一个开引号，以及它前面又冒出来的空白
    return q if q > 0 else p


def _find_cut(s, hi, prefer="sentence", hard_cut=True, strict=True):
    """返回左块终点（1..hi）。s 的长度已假定大于 hi。"""
    ceiling = hi if strict else len(s)
    order = (ENDERS + WEAK + CLOSERS,) if prefer == "fill" else (ENDERS, WEAK + CLOSERS)
    for marks in order:
        p = _scan(s, hi, marks, ceiling)
        if p:
            return p
    if not strict and not hard_cut:
        p = _scan_forward(s, hi, ENDERS + WEAK + CLOSERS, ceiling)
        if p:
            return p
    return _hard_cut(s, hi, ceiling)


# ------------------------------------------------------------------ 收尾整理


def _glue(a, b):
    """合并两块：若切开处原本是英文词间的空格，补回一个空格（不然 "the lazy"+"dog" 变成 "the lazydog"）。"""
    if a and b and WORD_RE.match(a[-1]) and WORD_RE.match(b[0]):
        return a + " " + b
    return a + b


def _is_marks_only(chunk):
    return all((ch in TAIL_MARKS) or (ch in WEAK) or (ch in SPACES) or (ch in ENDERS)
               for ch in chunk)


def _rebalance_tail(a, b, max_chars, min_tail):
    """末块过短、又并不进前一块时，把两块之间的边界往左挪——**两块仍都不超上限**。

    例：21 字切成 `20 + 1`，挪成 `17 + 4`，末块不再是一个孤字（多一次 TTS 往返约 3 s，
    而且单独念出来是半截字）。挪的时候躲开 ASCII 记号与开引号；挪不动就原样返回。
    """
    merged = _glue(a, b)
    cut = len(merged) - min_tail
    if cut < 1 or cut >= len(merged):
        return a, b
    while cut < len(merged) - 1 and TOKEN_RE.match(merged[cut - 1]) and TOKEN_RE.match(merged[cut]):
        cut += 1                                   # 别切进 ASCII 记号（词/数字/URL）
    q = len(merged[:cut].rstrip(BLANKS))           # 左块（去尾随空白后）不许以开引号收尾
    while q > 1 and merged[q - 1] in OPENERS:
        q = len(merged[:q - 1].rstrip(BLANKS))
    cut = q
    left, right = merged[:cut].strip(), merged[cut:].strip()
    if not left or not right or len(left) > max_chars or len(right) > max_chars:
        return a, b
    if len(right) < min_tail:                      # 被记号/引号挡回来，末块还是太短 → 不折腾
        return a, b
    return left, right


def _tidy(chunks, max_chars, min_tail=MIN_TAIL, strict=True):
    """去空白、把纯标点块并回前一块、把过短的末块并进前一块。

    **严格模式下合并要先算账**：并进去之后若超过 `max_chars` 就不并（上限优先）。
    宽松模式不看余量：孤零零的 1–3 个字要多一次 TTS 往返（Edge 约 3 s），单独念出来还是
    半截字；宁可让前一块比 `max_chars` 多几个字符（峰值 RSS 看的是最长块）。
    """
    out = []
    for c in chunks:
        c = c.strip()
        if not c:
            continue
        if _is_marks_only(c) and out and (not strict or len(out[-1]) + len(c) <= max_chars):
            out[-1] = _glue(out[-1], c)
            continue
        out.append(c)
    if len(out) > 1 and min_tail > 0 and len(out[-1]) < min_tail:
        if not strict or len(out[-2]) + len(out[-1]) <= max_chars:
            out[-2] = _glue(out[-2], out[-1])       # 并得下（或宽松模式）就直接并
            out.pop()
        else:
            out[-2], out[-1] = _rebalance_tail(out[-2], out[-1], max_chars, min_tail)
    return out


# ------------------------------------------------------------------ 对外接口


def check_max_chars(max_chars):
    """`max_chars` 是否离谱到会丢字／撑爆峰值。返回告警文案，`None` 表示没问题。

    不做硬失败（上板换档时不许让配置把人拦住），只让 `ServerStage` 往 stderr 说一句。
    判据来自实测：edge 档单块 600 s 音频上限 ≈ 2827 汉字（3000 字起静默丢尾）。
    """
    n = int(max_chars)
    if n <= MAX_CHARS_TRIPWIRE:
        return None
    return ("max_chars=%d 超过安全线 %d：现役 edge 档单块上限约 %d 汉字（=600 s 音频，实测 3000 字"
            "起静默丢尾），按每字约 %.2f s 音频算，%d 字≈%.0f s 音频。要更长的块先确认 TTS 上限。"
            % (n, MAX_CHARS_TRIPWIRE, EDGE_SEGMENT_LIMIT_CHARS, EDGE_SEC_PER_CHAR,
               n, n * EDGE_SEC_PER_CHAR))


def split_for_tts(text, max_chars=20, prefer="sentence", hard_cut=True,
                  min_tail=MIN_TAIL, strict=True):
    """长文本 → TTS 可逐段合成的块列表。

    text       任意文本；空串／纯空白返回 []
    max_chars  每块字数上限（默认 **20**；中文按字，英文按字符——不是词数）
    prefer     "sentence"（默认：句末优先，再分句）｜"fill"（按位置取最靠后，块数更少）
    hard_cut   True＝找不到标点就在上限处硬断｜False＝越过上限找下一个标点（**仅宽松模式有效**）
    min_tail   末块短于此值就并入前一块；0＝关闭（**严格模式下放不下就不并**）
    strict     True（默认）＝每块原始字符数 ≤ max_chars，无例外；False＝允许三处外溢（见模块头注）
    """
    if not text:
        return []
    max_chars = int(max_chars)
    if max_chars < 1:
        raise ValueError("max_chars 至少为 1，当前 %r" % (max_chars,))
    if prefer not in ("sentence", "fill"):
        raise ValueError("prefer 只认 'sentence' 或 'fill'，当前 %r" % (prefer,))

    s = text.strip()
    out = []
    while len(s) > max_chars:
        p = _find_cut(s, max_chars, prefer, hard_cut, strict)
        if p <= 0:                      # 兜底：不该发生，但绝不许让循环空转
            p = max_chars
        p = min(p, len(s))
        head = s[:p].strip()
        if head:
            out.append(head)
        s = s[p:].lstrip()
    if s.strip():
        out.append(s.strip())
    return _tidy(out, max_chars, min_tail, strict)


class ServerStage(object):
    """`lang/server` 的分词器档（鸭子类型，不 import server 的任何东西）。

    服务器侧契约（见 `lang/server/core.py` 头注）：`name` ＋ `__init__(cfg, profile, shared)`
    ＋ `run(text) -> [chunk]`。已登记在 `lang/server/backends/__init__.py` 的 `"tokenizer"`，
    档名 `tts_split`；默认档由 `lang/config/server.yaml` 的 `chain.tokenizer` 指定。

    配置（`lang/config/split.yaml` 的 `tokenizer_profiles.tts_split`）：
      max_chars（默认 20）／strict（默认 1＝每块不超过上限）／prefer／hard_cut／min_tail
    """

    name = "tts_split"

    def __init__(self, cfg=None, profile=None, shared=None):
        profile = profile or {}
        self.max_chars = int(profile.get("max_chars", 20) or 20)
        _warn = check_max_chars(self.max_chars)
        if _warn:
            print("[tts_split] " + _warn, file=sys.stderr)
        self.strict = str(profile.get("strict", "1") or "1") not in ("0", "false", "no", "off")
        self.prefer = str(profile.get("prefer", "sentence") or "sentence")
        self.hard_cut = str(profile.get("hard_cut", "1") or "1") not in ("0", "false", "no", "off")
        self.min_tail = int(profile.get("min_tail", MIN_TAIL) or 0)

    def run(self, text):
        return split_for_tts(text, self.max_chars, self.prefer, self.hard_cut,
                             self.min_tail, self.strict)