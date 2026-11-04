#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— lang/split 的无依赖自检（纯标准库，不装载模型、不外发）。

python -X utf8 lang/split/selftest.py        # 任意 cwd 都能跑

分三层：
  ① 行为用例：严格口径（默认，每块 ≤ max_chars 无例外）与宽松口径（strict=False 的三处外溢）
     各钉一遍，含引号/省略号/ASCII 记号/末块合并；
  ② 不变量：对所有语料都成立——内容守恒（去空白后逐字等于原文）、无空块、块不长于上限、
     块首不以右引号起、块尾不留开引号；
  ③ 三档可切换：`tts_split`（新）、`punct`、`window`（原来的两档）都满足 server 的档契约。
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tts_split as T                                     # noqa: E402
from legacy_tiers import Punct, Window                    # noqa: E402
from tts_split import ServerStage, split_for_tts           # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    if cond:
        print("[ok] " + name)
    else:
        print("[FAIL] " + name + (("  -> " + str(detail)) if detail else ""))


def ws_free(s):
    """去掉所有空白，用于「内容守恒」比对（切分只允许动空白，不许丢字）。"""
    return "".join(ch for ch in s if not ch.isspace())


def boundaries(chunks):
    """块边界在拼接串里的偏移集合。"""
    out, acc = set(), 0
    for c in chunks[:-1]:
        acc += len(c)
        out.add(acc)
    return out


def spoken_len(chunk):
    """「念出来的长度」：空白不发音。"""
    return len("".join(chunk.split()))


def len_ok_loose(chunk, hi):
    """宽松口径：块长在「已声明例外」允许的范围内。"""
    if spoken_len(chunk) <= hi + T.MIN_TAIL:
        return True
    if len(chunk.rstrip(T.TAIL_MARKS + T.WEAK + T.BLANKS)) <= hi:
        return True
    return any(len(tok) > hi for tok in chunk.split() if tok)


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- ① 行为用例

def case_basics():
    check("空输入 -> []", split_for_tts("") == [])
    check("纯空白 -> []", split_for_tts("  \n\t\u3000 ") == [])
    check("短文本单块", split_for_tts("你好呀。") == ["你好呀。"])
    check("默认 max_chars=20", split_for_tts("a" * 25) == ["a" * 20, "a" * 5],
          split_for_tts("a" * 25))
    check("max_chars=0 抛 ValueError", _raises(ValueError, lambda: split_for_tts("你好", 0)))
    check("prefer 非法值抛 ValueError",
          _raises(ValueError, lambda: split_for_tts("你好", 4, prefer="xx")))


def case_max_chars_tripwire():
    """max_chars 的防呆：超安全线只告警不拦，且告警里要带实测依据。"""
    check("20 字不告警", T.check_max_chars(20) is None)
    check("1000 字（安全线内）不告警", T.check_max_chars(1000) is None)
    msg = T.check_max_chars(3000)
    check("3000 字告警且带实测上限", bool(msg) and "2827" in msg, msg)
    check("安全线常量与实测一致", T.MAX_CHARS_TRIPWIRE < T.EDGE_SEGMENT_LIMIT_CHARS,
          (T.MAX_CHARS_TRIPWIRE, T.EDGE_SEGMENT_LIMIT_CHARS))
    check("每字音频秒数在实测范围内", 0.15 < T.EDGE_SEC_PER_CHAR < 0.30, T.EDGE_SEC_PER_CHAR)

    import contextlib
    import io as _io
    err = _io.StringIO()
    with contextlib.redirect_stderr(err):
        st = ServerStage(None, {"max_chars": "5000"}, None)
    check("ServerStage 超线往 stderr 告警", "安全线" in err.getvalue(), err.getvalue()[:80])
    check("ServerStage 超线不硬失败（配置照读）", st.max_chars == 5000)


def case_strict_cap():
    """她 2026-11-04 的硬要求：超过 20 字就强制切，且绝不超。"""
    text = "这是一个没有标点的超长句子一直写下去看它会不会超过二十个字符的硬上限"
    chunks = split_for_tts(text)                                   # 默认 20 字严格
    check("严格：每块 <= 20", all(len(c) <= 20 for c in chunks), chunks)
    check("严格：一块不丢字", "".join(chunks) == text, chunks)
    check("严格：20 字处强制切", chunks[0] == text[:20], chunks[:2])

    # 带标点：句末优先，但同样不许超
    text2 = ("第一句话到这里。第二句也完了！最后来一句特别长的，中间有逗号，还有内容，"
             "一直长到超过二十个字符上限看看会怎样处理掉")
    chunks = split_for_tts(text2)
    check("严格：带标点也不超 20", all(len(c) <= 20 for c in chunks), chunks)
    check("严格：句末优先仍生效（20 字内能装两句就装）",
          chunks[0].startswith("第一句话到这里。") and len(chunks[0]) <= 20, chunks[:2])
    check("严格：内容守恒（带标点）", ws_free("".join(chunks)) == ws_free(text2))

    # hard_cut=False 在严格模式下不生效（上限优先）
    chunks = split_for_tts("啊" * 30 + "。哦", hard_cut=False)
    check("严格：hard_cut=False 仍不超上限", all(len(c) <= 20 for c in chunks), chunks)


def case_strict_quotes_and_tokens():
    text = "他说：「这里有一句话。」然后他就走了，没有再说别的。"
    chunks = split_for_tts(text, 12)
    check("严格：右引号不落块首", not any(c[0] in T.CLOSERS for c in chunks[1:]), chunks)
    check("严格：开引号不落块尾", not any(c[-1] in T.OPENERS for c in chunks[:-1]), chunks)
    check("严格：每块 <= 12", all(len(c) <= 12 for c in chunks), chunks)

    # 长 URL 在严格模式下会被切（上限优先），但一块不丢字
    url = "https://example.com/very/long/path/that/never/ends"
    chunks = split_for_tts("链接是" + url + "收下", 20)
    check("严格：长 URL 也切到 <= 20", all(len(c) <= 20 for c in chunks), chunks)
    check("严格：URL 拼接回原文", "".join(chunks) == "链接是" + url + "收下", chunks)


def case_loose_overdraft():
    """宽松口径的三处外溢（strict=False），钉住「别被当 bug 修掉」。"""
    check("宽松：连续省略号不拆散", split_for_tts("等等……", 3, strict=False) == ["等等……"],
          split_for_tts("等等……", 3, strict=False))
    url = "https://example.com/very/long/path/that/never/ends"
    chunks = split_for_tts("链接是" + url + "收下", 20, strict=False)
    check("宽松：超长 URL 整段保留", any(url in c for c in chunks), chunks)
    text = "价格是1,000,000元整不便宜"
    inside = {text.index("1,000,000") + i for i in range(1, len("1,000,000"))}
    check("宽松：1,000,000 不被断开",
          not (boundaries(split_for_tts(text, 8, strict=False)) & inside),
          split_for_tts(text, 8, strict=False))
    check("宽松：过短末块并回前一块",
          split_for_tts("好的。嗯嗯", 5, strict=False) == ["好的。嗯嗯"],
          split_for_tts("好的。嗯嗯", 5, strict=False))
    chunks = split_for_tts("会议定在12:30开始", 6, strict=False)
    check("宽松：末块过短时宁可外溢也不留 1 字块",
          len(chunks) == 2 and all(len(c) > 1 for c in chunks), chunks)
    chunks = split_for_tts("啊" * 30 + "。哦", 10, hard_cut=False, min_tail=0, strict=False)
    check("宽松：hard_cut=False 越过上限找标点", chunks[0] == "啊" * 30 + "。", chunks)


def case_prefer_modes():
    text = "短句。这里还有更多字，然后继续往下写很长很长很长"
    sent = split_for_tts(text, 12, prefer="sentence")
    fill = split_for_tts(text, 12, prefer="fill")
    check("prefer=sentence 先吃满句号", sent[0] == "短句。", sent)
    check("prefer=fill 吃到更靠后的逗号", fill[0] == "短句。这里还有更多字，", fill)
    check("两种模式都守恒", ws_free("".join(sent)) == ws_free(text)
          and ws_free("".join(fill)) == ws_free(text))


def case_three_tiers():
    """三档都在 lang/split/，都满足 server 的档契约（name + __init__(cfg,profile,shared) + run）。"""
    text = "第一句话到这里。第二句也完了！最后来一句特别长的，中间有逗号，还有内容，一直长到超过上限"

    new = ServerStage(None, {"max_chars": "20"}, None)
    old = Punct(None, {"max_chars": "20"}, None)
    win = Window(None, {"width": "20"}, None)
    check("三档档名", (new.name, old.name, win.name) == ("tts_split", "punct", "window"),
          (new.name, old.name, win.name))
    for stage in (new, old, win):
        chunks = stage.run(text)
        check("档契约：%s 出块且守恒" % stage.name,
              bool(chunks) and ws_free("".join(chunks)) == ws_free(text), chunks[:3])
    check("新档严格 20：每块 <= 20", all(len(c) <= 20 for c in new.run(text)), new.run(text))
    # punct 的毛病不是"超上限"（它末尾会二次硬断），而是**断点不看语义**：长句在第 20 字被切断
    long_sentence = "这是一句特别长的话中间没有任何逗号一直写下去直到第二十五个字才出现句号。后面还有一句。"
    old_chunks = Punct(None, {"max_chars": "20"}, None).run(long_sentence)
    check("punct：长句在字数上限处被硬切（切在半句上）",
          any(len(c) == 20 for c in old_chunks) and old_chunks[0].endswith("写"), old_chunks)
    check("window 档固定 20 字", all(len(c) <= 20 for c in win.run(text)), win.run(text))

    check("ServerStage 默认 max_chars=20", ServerStage().max_chars == 20)
    check("ServerStage 默认 strict=1", ServerStage().strict is True)
    check("profile 可关 strict", ServerStage(None, {"strict": "0"}, None).strict is False)
    check("punct 默认 max_chars=40", Punct(None, {}, None).max_chars == 40)


def case_legacy_verbatim():
    """原 punct 档的两条被 smoke.py 钉住的行为，这里再钉一次（搬动过程不许改行为）。"""
    p = Punct(None, {"max_chars": 20}, None)
    chunks = p.run("第一句话到这里。第二句也完了！最后来一句特别长的，中间有逗号，还有内容，"
                   "一直长到超过二十个字符上限看看会怎样处理掉")
    check("punct：句末符必切", chunks[0] == "第一句话到这里。", chunks[:2])
    check("punct：超长残块被二次硬断到 <= 21", all(len(c) <= 21 for c in chunks),
          "max=%d" % max(len(c) for c in chunks))
    check("window：固定宽度硬切", Window(None, {"width": 5}, None).run("十" * 12)
          == ["十" * 5, "十" * 5, "十" * 2],
          Window(None, {"width": 5}, None).run("十" * 12))


# ---------------------------------------------------------------- ② 不变量

CORPUS = [
    "第一句话到这里。第二句也完了！第三句带个问号？还有分号；最后换行\n结束。",
    "这是一个很长的句子，但它有逗号，所以会在逗号处断开，而不是被硬切，看看效果如何。",
    "他说：「这里有一句话。」然后他就走了，没有再说别的。",
    "The quick brown fox jumps over the lazy dog. Second sentence here!",
    "圆周率是3.14159，光速约299792458米每秒，都别念错。",
    "啊" * 41,
    "等等……" * 7,
    "（一）首先；（二）其次；（三）最后。",
    "  \n 开头有空白  中间有多空格  结尾也有  \n",
    "链接是https://example.com/a/b/c?x=1收下",
]


def invariants():
    for max_chars in (1, 3, 5, 10, 20, 40, 100):
        for prefer in ("sentence", "fill"):
            for strict in (True, False):
                for i, text in enumerate(CORPUS):
                    chunks = split_for_tts(text, max_chars, prefer=prefer, strict=strict)
                    tag = "max=%d %s strict=%d #%d" % (max_chars, prefer, strict, i)
                    check("内容守恒 " + tag, ws_free("".join(chunks)) == ws_free(text), chunks)
                    check("无空块 " + tag, all(c.strip() for c in chunks), chunks)
                    if strict:
                        check("块长 <= 上限 " + tag,
                              all(len(c) <= max_chars for c in chunks), chunks)
                    else:
                        check("块长在例外内 " + tag,
                              all(len_ok_loose(c, max_chars) for c in chunks), chunks)
                    if not strict:      # 严格模式上限优先，右引号可能正好落在上限之后
                        bad = [c for c in chunks[1:] if len(c) > 1 and c[0] in T.CLOSERS]
                        check("块首不以右引号起 " + tag, not bad, bad)
                    bad = [c for c in chunks[:-1] if len(c) > 1 and c[-1] in T.OPENERS]
                    check("块尾不留开引号 " + tag, not bad, bad)


def fuzz(seed=20261104, n=300):
    """确定性伪随机语料：两个口径都要不空转、不丢字、不越界、不留怪块。"""
    import random
    rnd = random.Random(seed)
    alphabet = list("的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严龙飞")

    for i in range(n):
        parts = []
        for _ in range(rnd.randint(1, 40)):
            r = rnd.random()
            if r < 0.55:
                parts.append(rnd.choice(alphabet))
            elif r < 0.75:
                parts.append(rnd.choice("，。！？；：、…—～（）「」“”" + " "))
            elif r < 0.9:
                parts.append(rnd.choice(["3.14", "1,000", "https://a.b/c", "abc", "Tag!"]))
            else:
                parts.append("\n" if rnd.random() < 0.5 else "  ")
        text = "".join(parts)
        for hi in (1, 2, 3, 5, 8, 13, 20):
            for prefer in ("sentence", "fill"):
                for strict in (True, False):
                    chunks = split_for_tts(text, hi, prefer=prefer, strict=strict)
                    tag = "fuzz #%d hi=%d %s strict=%d" % (i, hi, prefer, strict)
                    if ws_free("".join(chunks)) != ws_free(text):
                        check("内容守恒 " + tag, False, (text[:60], chunks)); continue
                    if not all(c.strip() for c in chunks):
                        check("无空块 " + tag, False, chunks); continue
                    ok_len = (all(len(c) <= hi for c in chunks) if strict
                              else all(len_ok_loose(c, hi) for c in chunks))
                    if not ok_len:
                        check("块长 " + tag, False, (text[:60], chunks)); continue
                    if not strict:      # 严格模式上限优先，右引号可能正好落在上限之后
                        bad = [c for c in chunks[1:] if len(c) > 1 and c[0] in T.CLOSERS]
                        check("块首不以右引号起 " + tag, not bad, bad)
                    bad = [c for c in chunks[:-1] if len(c) > 1 and c[-1] in T.OPENERS]
                    check("块尾不留开引号 " + tag, not bad, bad)


def main():
    case_basics()
    case_max_chars_tripwire()
    case_strict_cap()
    case_strict_quotes_and_tokens()
    case_loose_overdraft()
    case_prefer_modes()
    case_three_tiers()
    case_legacy_verbatim()
    invariants()
    fuzz()
    print("\n%d ok / %d fail" % (len(OK), len(BAD)))
    if BAD:
        print("失败：" + "；".join(BAD[:10]))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())