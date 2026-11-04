#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板端下载 ModelScope 仓库文件（标准库，断点续传＋体积校验）。

用法：
  python3 ms_get.py <仓库> <目标目录> <文件名> [<文件名> ...]
例子：
  python3 /root/funasr/asr/ms_get.py iic/SenseVoiceSmall /root/funasr/models/SenseVoiceSmall chn_jpn_yue_eng_ko_spectok.bpe.model
  python3 /root/funasr/asr/ms_get.py iic/SenseVoiceSmall-onnx /root/funasr/models/SenseVoiceSmall model_quant.onnx config.yaml am.mvn tokens.json

地址形态 `https://www.modelscope.cn/models/<repo>/resolve/master/<path>`（板端实测该域名 443 可达，HF 不可达）。
目标文件体积已与仓库登记一致就跳过，所以重跑很便宜；体积不对则删掉重下。
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://www.modelscope.cn/models/%s/resolve/master/%s"
API = "https://www.modelscope.cn/api/v1/models/%s/repo/files?Revision=master&Recursive=true"


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%d B" % n


def repo_sizes(repo):
    raw = urllib.request.urlopen(API % repo, timeout=25).read().decode("utf-8")
    return {f["Path"]: f["Size"] for f in json.loads(raw)["Data"]["Files"] if f["Type"] == "blob"}


def get_one(repo, dstdir, path, want):
    dst = os.path.join(dstdir, os.path.basename(path))
    have = os.path.getsize(dst) if os.path.exists(dst) else 0
    if have == want:
        print("HAVE %-46s %s  体积一致，跳过" % (os.path.basename(path), human(have)))
        return 0
    if have:
        print("RESET %s 现有 %s != 应有 %s，删掉重下" % (path, human(have), human(want)))
        os.remove(dst)
        have = 0
    url = BASE % (repo, urllib.parse.quote(path))
    req = urllib.request.Request(url, headers={"Range": "bytes=%d-" % have} if have else {})
    resp = urllib.request.urlopen(req, timeout=90)
    tmp = dst + ".part"
    t0 = time.time()
    done = have
    nxt = 20 << 20
    with open(tmp, "ab" if have else "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if done >= nxt:
                rate = done / max(1e-9, time.time() - t0) / 1048576.0
                print("     %s / %s  %.1f%%  %.1f MB/s" % (human(done), human(want),
                                                            100.0 * done / want, rate))
                sys.stdout.flush()
                nxt += 20 << 20
    if done != want:
        os.replace(tmp, tmp)
        print("FAIL %s 只拿到 %s / %s，.part 留着下次续传" % (path, human(done), human(want)))
        return 1
    os.replace(tmp, dst)
    print("OK   %-46s %s  用时 %.0f s" % (os.path.basename(path), human(done), time.time() - t0))
    return 0


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    repo, dstdir = sys.argv[1], sys.argv[2]
    paths = sys.argv[3:]
    os.makedirs(dstdir, exist_ok=True)
    sizes = repo_sizes(repo)
    bad = 0
    for p in paths:
        if p not in sizes:
            print("SKIP-NOFILE %s 仓库里没有这个路径，可选：%s" % (p, sorted(sizes)[:12]))
            bad += 1
            continue
        bad += get_one(repo, dstdir, p, sizes[p])
    print("GET-DONE %s" % ("FAIL" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
