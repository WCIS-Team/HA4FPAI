#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""router.py —— **KWS 之后的那次路由**（实现在 lang/kws/，由 lang/server 调用）。

链路位置（她 2026-11-04 定）：

    sar → rms → kws ──[ 本文件 route() ]──> vad → asr → router(klar) → llm → …

KWS 只回答"有没有命中唤醒词、命中的是哪个 tag"；**接下来往哪走**由本文件决定。
server 侧只做薄调用（`lang/server/backends/kws_route.py`），路由规则、tag→去向的映射、
miss 策略全部写在这里和 `lang/config/kws.yaml` 的 `kws_route_profiles.default` 里。

## 返回形状

    {"action": "continue"|"stop"|"direct"|"verify", "route": "<名字>", "reply": "<固定回应>", "reason": "…"}

  continue  继续 vad → asr（常规唤醒后说话）
  stop      就此打住（只唤醒不说话／噪声误触）
  direct    跳过 vad/asr，直接用 `reply` 走分词＋TTS（例如"我在"这类固定应答）
  verify    跳过 vad/asr，把这段音频交给**声纹**（`chain.spk`；默认 none ＝ 只记 skipped）

## 配置（`kws.yaml` → `kws_route_profiles.default`）

    on_miss: stop            # 没命中唤醒词：stop | continue
    default_route: general   # 命中但没登记 tag 时给的路由名
    default_action: continue
    routes:                  # tag（KWS 报的关键词）→ 路由名（继续 vad/asr）
      开门: device
    direct:                  # tag → 固定回应（action=direct，跳过 vad/asr）
      小克小克: "我在。"
      再见: "拜拜。"         # 播完结束当前请求；不走静默 stop
    verify:                  # tag → 走声纹（action=verify，跳过 vad/asr）
      验证声纹: 1

> 为什么是三张扁平表而不是 `routes.<tag>.{route,action,reply}`：本仓的 YAML 解析器只做**两层**
> 嵌套映射，三层会被拍平（2026-11-04 实测）。扁平表同样表达得清楚，且解析结果可预测。

匹配顺序：`handlers` → `direct` → `stop` → `verify` → `routes` → 默认。
handlers 是 tag→内置动态动作名的表；支持 time/date/weekday（读取运行机器本地时钟，直达 TTS）。
未知动作停止，不执行任意代码。每张表内 tag 匹配为精确优先、再双向子串。
完整配置例子见 ../config/kws.yaml 的就地注释。
"""

import time

__all__ = ["route", "ROUTE_ACTIONS"]


ROUTE_ACTIONS = ("continue", "stop", "direct", "verify")


def _norm(v):
    return str(v or "").strip()


def _match(tag, routes):
    """精确 tag 优先，再子串（双向），都没有返回 None。"""
    tag = _norm(tag)
    if not tag:
        return None
    if tag in routes:
        return tag
    for key in routes:
        k = _norm(key)
        if not k:
            continue
        if k in tag or tag in k:
            return key
    return None


def route(samples, kws_result, profile=None, ctx=None):
    """KWS 结果 → 去向决定。`samples` 留给以后按能量/时长做细分，现在只透传（保证签名稳定）。"""
    prof = profile or {}
    hit, tag = (kws_result + (None,))[:2] if isinstance(kws_result, (tuple, list)) \
        else (bool(kws_result), None)

    if not hit:
        action = _norm(prof.get("on_miss") or "stop").lower()
        if action not in ROUTE_ACTIONS:
            action = "stop"
        return {"action": action, "route": None, "reason": "kws miss"}

    handlers = prof.get("handlers") or {}
    routes = prof.get("routes") or {}
    direct = prof.get("direct") or {}
    stop = prof.get("stop") or {}
    verify = prof.get("verify") or {}
    default_action = _norm(prof.get("default_action") or "continue").lower()
    if default_action not in ROUTE_ACTIONS:
        default_action = "continue"
    default_route = _norm(prof.get("default_route") or "general")

    # 动态动作只允许内置白名单；配置不会执行 Python 表达式或 shell。
    key = _match(tag, handlers)
    if key is not None:
        handler = _norm(handlers[key])
        if handler in ("time", "date", "weekday"):
            now = time.localtime()
            if handler == "time":
                reply = time.strftime("现在是%H点%M分。", now)
            elif handler == "date":
                reply = time.strftime("今天是%Y年%m月%d日。", now)
            else:
                reply = "今天是星期%s。" % "一二三四五六日"[now.tm_wday]
            return {"action": "direct", "route": handler, "reply": reply,
                    "reason": "tag=%r -> handler(%s)" % (tag, handler)}
        return {"action": "stop", "route": None,
                "reason": "未知 KWS handler: %r" % (handler,)}

    key = _match(tag, direct)
    if key is not None:
        return {"action": "direct", "route": "ack", "reply": _norm(direct[key]),
                "reason": "tag=%r -> direct(%r)" % (tag, key)}

    key = _match(tag, stop)
    if key is not None:
        return {"action": "stop", "route": None,
                "reason": "tag=%r -> stop(%r)" % (tag, key)}

    key = _match(tag, verify)                     # 直接走声纹（跳过 vad/asr）
    if key is not None:
        return {"action": "verify", "route": "spk",
                "reason": "tag=%r -> verify(%r)" % (tag, key)}

    key = _match(tag, routes)
    if key is not None:
        return {"action": "continue", "route": _norm(routes[key]) or default_route,
                "reason": "tag=%r -> %r" % (tag, key)}

    return {"action": default_action, "route": default_route,
            "reason": "kws hit, tag=%r 未登记" % (tag,)}