#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_probe.py -- 开机时判断"这块 HDMI 屏有没有人在用"，决定要不要起 kiosk。

## 为什么要"人确认"兜底（2026-11-04 板上实测，A 级）
这块板的 HDMI 是 **PL 单向 TMDS 输出**，没有任何回读通路：
  * 引脚约束里只有 4 个 TMDS 输出（AC17/AA15/Y16/W16），**无 HPD、无 DDC**；
  * `/sys/class/drm` 没有 connector 状态；`/dev/i2c-*` 不存在 ⇒ EDID 读不到；
  * `/sys/kernel/debug/gpio` 里 4 个 PS GPIO 芯片只有 2 条线被占（PHY reset / SPI），无 HPD 线；
  * 设备树与 dmesg 里 `hdmi|hpd|edid` 零命中。
⇒ 真·自动检测做不到。本脚本按"能自动就自动、不能就一分钟人确认"的阶梯判断：

  L1  DRM connector 状态（`/sys/class/drm/card*-*/status` == connected）—— 现在没有，将来换带 DRM 的位流即可用
  L2  PS GPIO 里有名字带 hpd 的线（`/sys/kernel/debug/gpio`）—— 现在没有，将来把 HPD 接进 PS 即可用
  L3  **一分钟人确认**：在 HDMI 上画倒计时，期间收到鼠标点击 = 有人在看这块屏 → 视为"有显示器"
  L4  `--assume yes|no` 强制结论（无屏调试 / 临时绕过）

## 退出码（给 systemd 用）
  0 = 已判定，无论"有"还是"没有"（**so Restart 不会因为"没有"而重试**）
  3 = 判定过程中出错（--then-exec 启动失败等）
是否启动 kiosk 由 `--then-exec "<命令>"` 决定：只有"有显示器"时才 exec 过去。

用法：
  sudo python3 hdmi_probe.py --backend udmabuf --mouse evdev --timeout 60 \
       --then-exec "/usr/bin/python3 /root/offlineHMI/kiosk.py --backend udmabuf --mouse evdev --host 127.0.0.1"
  python3 hdmi_probe.py --assume no            # 无屏机器上验退出码
"""
from __future__ import annotations

import argparse
import glob
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmi.display import SCREEN_W, SCREEN_H, make_backend
from hmi.mouse import make_mouse
from hmi.ui import Canvas, Theme, MUTED, TEXT


# ---------------------------------------------------------------- L1 DRM
def drm_connected() -> "tuple[bool, str]":
    pats = ["/sys/class/drm/card*-*/status", "/sys/class/drm/*/status"]
    for pat in pats:
        for path in glob.glob(pat):
            try:
                with open(path) as f:
                    st = f.read().strip()
            except OSError:
                continue
            if st == "connected":
                return True, "drm:" + path
    return False, ""


# ---------------------------------------------------------------- L2 HPD GPIO
def gpio_hpd() -> "tuple[bool, str]":
    path = "/sys/kernel/debug/gpio"
    try:
        with open(path) as f:
            txt = f.read()
    except OSError:
        return False, ""
    for line in txt.splitlines():
        low = line.lower()
        if ("hpd" in low or "hotplug" in low) and "in  hi" in low:
            return True, "gpio:" + line.strip()
    return False, ""


# ---------------------------------------------------------------- L3 人确认
def wait_click(backend, mouse, timeout_s: float, theme: Theme) -> bool:
    """画倒计时，期间有任何点击 → True；超时 → False。"""
    deadline = time.time() + timeout_s
    clicked = False
    while time.time() < deadline and not clicked:
        left = max(0, int(round(deadline - time.time())))
        c = Canvas(theme)
        c.rect((0, 0, SCREEN_W, SCREEN_H), fill=(12, 16, 22))
        c.text((SCREEN_W // 2, 300), "正在检测 HDMI 显示器", fill=TEXT,
               anchor="mm", size=56)
        c.text((SCREEN_W // 2, 400), "如果你能看到这块屏幕，请点击任意位置",
               fill=MUTED, anchor="mm", size=32)
        c.text((SCREEN_W // 2, 520), "剩余 %d 秒" % left, fill=(240, 200, 120),
               anchor="mm", size=44)
        c.rect((SCREEN_W // 2 - 160, 620, 320, 96), fill=(64, 180, 120))
        c.text((SCREEN_W // 2, 668), "我看到了", fill=(20, 26, 32),
               anchor="mm", size=36)
        backend.present(c.to_rgb565())
        t_end = time.time() + 0.1
        while time.time() < t_end:
            ev = mouse.poll()
            if ev.click:
                clicked = True
                break
            time.sleep(0.02)
    return clicked


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="HDMI 存在性判定（开机门卫）")
    ap.add_argument("--backend", default="udmabuf", help="udmabuf[:off] | png:<dir>")
    ap.add_argument("--mouse", default="evdev", help="evdev[:dev] | win32 | mock:<pts>")
    ap.add_argument("--timeout", type=float, default=60.0, help="人确认窗口（秒）")
    ap.add_argument("--assume", choices=("yes", "no"), help="强制结论，跳过检测")
    ap.add_argument("--no-ui", action="store_true", help="不画倒计时（纯检测，无屏环境用）")
    ap.add_argument("--font", default=None)
    ap.add_argument("--then-exec", default=None, help="判定为'有'时要 exec 的命令")
    args = ap.parse_args()

    if args.assume:
        present, why = (args.assume == "yes"), "--assume " + args.assume
    else:
        present, why = drm_connected()
        if not present:
            present, why = gpio_hpd()
        if not present and not args.no_ui:
            try:
                backend = make_backend(args.backend)
                mouse = make_mouse(args.mouse)
            except Exception as e:                 # 没有显示/输入设备：直接判无
                print("PROBE: 无法打开显示或输入设备（%s）→ 判为无显示器" % e)
                return 0
            present = wait_click(backend, mouse, args.timeout, Theme(args.font))
            why = "click-within-%gs" % args.timeout if present else "no-click-in-%gs" % args.timeout
            close = getattr(backend, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    print("PROBE: present=%s reason=%s" % (present, why))
    if present and args.then_exec:
        cmd = shlex.split(args.then_exec)
        print("PROBE: exec -> %s" % " ".join(cmd))
        sys.stdout.flush()
        try:
            os.execvp(cmd[0], cmd)                 # execvp：带 PATH 查找（Linux/Win 都行）
        except OSError as e:
            print("PROBE: exec 失败: %s" % e, file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())