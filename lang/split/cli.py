#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli.py —— `tts_split` 的命令行：拿真实文本试切，给 TTS 定 max_chars 用。

    python -X utf8 cli.py "第一句话。第二句很长很长……"       # 直接给文本（默认 20 字严格上限）
    python -X utf8 cli.py --max-chars 40 --loose --file in.txt # 宽松：允许引号/长词/短末块外溢
    echo 文字 | python -X utf8 cli.py --max-chars 20           # 从 stdin 读
    python -X utf8 cli.py --file in.txt --raw                  # 一块一行（直接喂 TTS）
    python -X utf8 cli.py --file in.txt --json                 # 机器可读（脚本用）
    python -X utf8 cli.py --file in.txt --stats                # 只看块数/最短/最长/均值

默认输出是「序号  块长  块文本」；判据与例外见 tts_split.py 头注。
"""

import argparse
import json
import sys
from pathlib import Path

try:                                     # 作为包用（python -m split.cli）
    from . import tts_split
except ImportError:                      # 直接跑（python -X utf8 cli.py）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tts_split


def read_input(args):
    if args.file:
        return Path(args.file).read_text(encoding="utf-8-sig")
    if args.text is not None:
        return args.text
    if sys.stdin.isatty():
        raise SystemExit("没给文本：把文本作为参数、用 --file，或者从 stdin 灌进来")
    return sys.stdin.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="TTS 前的切分器（按标点＋字数上限切，不做分词）")
    ap.add_argument("text", nargs="?", help="要切的文本；省略则读 --file 或 stdin")
    ap.add_argument("-n", "--max-chars", type=int, default=20,
                    help="每块字数上限（默认 20，与链路默认档一致）")
    ap.add_argument("-f", "--file", help="从文本文件读（UTF-8）")
    ap.add_argument("--prefer", choices=["sentence", "fill"], default="sentence",
                    help="sentence＝句末优先（默认）；fill＝按位置取最靠后，块数更少")
    ap.add_argument("--loose", action="store_true",
                    help="宽松模式：允许收尾标点/超长英文记号/过短末块外溢几个字符")
    ap.add_argument("--no-hard-cut", action="store_true",
                    help="不硬断：越过上限找下一个标点（只在 --loose 下有效）")
    ap.add_argument("--min-tail", type=int, default=tts_split.MIN_TAIL,
                    help="末块短于此值就并/重平衡；0＝不改（默认 %d）" % tts_split.MIN_TAIL)
    ap.add_argument("--raw", action="store_true", help="一块一行，不带序号与块长")
    ap.add_argument("--json", action="store_true", help="输出 JSON（块列表 + 参数 + 统计）")
    ap.add_argument("--stats", action="store_true", help="只输出统计")
    args = ap.parse_args(argv)

    text = read_input(args)
    chunks = tts_split.split_for_tts(
        text, max_chars=args.max_chars, prefer=args.prefer,
        hard_cut=not args.no_hard_cut, min_tail=args.min_tail, strict=not args.loose,
    )
    lens = [len("".join(c.split())) for c in chunks]          # 去掉空白后的字数
    stats = {
        "chunks": len(chunks),
        "chars": sum(lens),
        "min": min(lens) if lens else 0,
        "max": max(lens) if lens else 0,
        "avg": round(sum(lens) / len(lens), 1) if lens else 0.0,
        "max_chars": args.max_chars,
        "prefer": args.prefer,
        "strict": not args.loose,
        "hard_cut": not args.no_hard_cut,
    }

    if args.json:
        print(json.dumps({"chunks": chunks, "stats": stats}, ensure_ascii=False, indent=2))
        return 0
    if args.stats:
        print("块数 %(chunks)d｜字数 %(chars)d｜最短 %(min)d｜最长 %(max)d｜均值 %(avg)s"
              "｜上限 %(max_chars)d｜严格 %(strict)s" % stats)
        return 0
    if args.raw:
        for c in chunks:
            print(c)
        return 0
    for i, c in enumerate(chunks, 1):
        print("%3d  [%2d]  %s" % (i, len("".join(c.split())), c))
    print("—— 块数 %(chunks)d｜最长 %(max)d（上限 %(max_chars)d）｜均值 %(avg)s" % stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())