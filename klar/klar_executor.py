#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_executor.py — the missing "intent -> action" layer.

WHAT KLAR ENGINE IS (and why we need this file at all)
------------------------------------------------------
klar engine is a *deterministic NLU parser*. It does exactly one job:

    Chinese sentence  ->  {intent: "HassTurnOn", slots: [{entity_id: ...}]}

It does NOT execute anything. In a real Home Assistant install, HA reads that
intent and flips the switch. We have no HA, so THIS FILE is the piece that
turns the intent into an actual API call on this board.

    mic -> KWS -> VAD -> ASR -> klar engine (10520)
                                     |
                                     v  decision + intent + slots
                              klar_executor.py  <-- this file
                                     |
                                     v
                            board APIs (8091 face/NPU, 8092 stream, 8093 recorder)

DESIGN
------
* Targets are the board's own services. They are described by a small
  `targets.yaml`-style registry that ALSO produces core.entity_registry for
  klar, so the two never drift apart.
* Heavy work (模型编译/量化/训练) does NOT belong here or on the board: 113 is
  4x A55 @2.4GHz with 993 MB RAM. Anything heavy goes to the school host.
  This executor only issues HTTP calls; it is intentionally thin.
* Every action is a pure HTTP request, so it is testable without a microphone.

RUN
    python3 klar_executor.py --dry-run          # parse -> print planned call
    python3 klar_executor.py --serve            # listen for klar intents
    python3 klar_executor.py --do "打开客厅的灯"  # one-shot end to end
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 板上 python 平铺在 /mnt/data/klar/；仓库里 door_unlock 仍在 ../boards
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "boards"))

LOG = logging.getLogger("klar_exec")

KLAR_PARSE = "http://127.0.0.1:10520/api/v2/parse"
BOARD = {
    "face":   "http://127.0.0.1:8091",   # NPU face recognise + gallery
    "stream": "http://127.0.0.1:8092",   # VPU live H.264 preview
    "record": "http://127.0.0.1:8093",   # PS VPU H264 recorder
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http(url, method="GET", body=None, timeout=30):
    """body: None = no payload; bytes = sent as-is; dict = JSON-encoded."""
    data = None
    headers = {}
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf8", "ignore"))
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf8", "ignore")
    except Exception as e:                       # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


def klar_parse(text, language="zh-CN"):
    status, data = http(KLAR_PARSE, "POST", {"text": text, "language": language})
    if status != 200 or not isinstance(data, dict):
        return {"error": "parse failed", "status": status, "detail": data}
    return data


# ---------------------------------------------------------------------------
# The executor: which intent maps to which board action
#
# Each entry: intent name -> callable(slots) -> (描述, 实际执行的函数)
# Slots come straight from klar; unknown intents fall through to "unsupported".
# ---------------------------------------------------------------------------
def act_face_recognize(slots):
    """NPU 人脸识别：抓一帧 SDI 现场画面，跑 YuNet+MobileFace"""
    st, d = http(BOARD["face"] + "/api/recognize-camera", "POST", b"")
    if st != 200 or not isinstance(d, dict):
        return False, "识别接口返回 %s" % st, d
    res = (d.get("entry") or {}).get("result") or {}
    identities = res.get("identities") or []
    n = res.get("face_count", 0)
    if not identities:
        return True, "画面里没有人脸" if n == 0 else "检测到 %d 张脸，但都不认识" % n, \
               {"faces": n, "names": []}
    names = [i.get("name") or i.get("identity") or "?" for i in identities]
    return True, "识别到 %s" % "、".join(names), {"faces": n, "names": names}


def act_face_enroll(slots):
    """录入一张人脸到 gallery（需要传入 photo，命令层暂不开放）"""
    return False, "录入人脸需要照片，语音暂不开放", None


def act_record_on(slots):
    """开启录制。

    实测 8093 只接受 mode in {off, auto, manual}（传 "on" 会返回
    {"error":"Mode must be off, auto or manual"}）。
      auto   = 检测到人脸才录
      manual = 手动/一直录
    """
    mode = slots.get("mode", "auto")
    if mode == "on":            # 语音里说"开始录制"= 一直录
        mode = "manual"
    st, d = http(BOARD["record"] + "/api/mode", "POST", {"mode": mode})
    ok = st == 200 and isinstance(d, dict) and d.get("mode") == mode
    label = {"auto": "自动（有人脸才录）", "manual": "持续录制"}.get(mode, mode)
    return ok, "录制已开启：%s" % label, d


def act_record_off(slots):
    st, d = http(BOARD["record"] + "/api/mode", "POST", {"mode": "off"})
    ok = st == 200 and isinstance(d, dict) and d.get("mode") == "off"
    return ok, "录制已关闭", d


def act_record_purge(slots):
    """清空回收站。

    实测 8093 的 /api/purge 需要具体 id（POST {} 返回 400 {"error":"'id'"}），
    也就是说引擎不提供"整批清空"。这是安全设计：删除必须逐条指名。
    所以语音这边改成"查看回收站"，避免误删；真要删走 Web UI。
    """
    st, d = http(BOARD["record"] + "/api/storage")
    if st != 200 or not isinstance(d, dict):
        return False, "读取回收站失败（%s）" % st, d
    trash = d.get("trash_count", 0)
    if trash == 0:
        return True, "回收站是空的", d
    return True, "回收站里有 %d 条，需要逐条删除（用网页端）" % trash, d


def _svc_state(st, d):
    """把 HTTP 结果翻译成人话。区分几种失败：

      None  -> 连不上（服务没起 / 网络不通）
      503   -> 服务在、但当前不可用（例如 NPU 后端在但拿不到有效帧）
      200   -> 正常
      其他  -> 服务报错，带上错误体
    """
    if st is None:
        return "连接失败"
    if st == 200:
        return "正常"
    if st == 503:
        detail = ""
        if isinstance(d, dict):
            detail = d.get("status") or d.get("error") or ""
        return "服务不可用(503)%s" % ((":" + str(detail)) if detail else "")
    if isinstance(d, dict) and (d.get("error") or d.get("reason")):
        return "异常(%s:%s)" % (st, d.get("error") or d.get("reason"))
    return "异常(%s)" % st


# 状态查询：一次读三个服务，安全的只读操作（必须定义在 DIRECT_COMMANDS 之前）
def act_status(slots):
    """查三个服务状态。ok 只在全部正常时为 True。"""
    parts, all_ok = [], True

    st, d = http(BOARD["face"] + "/api/health")
    parts.append("NPU %s" % _svc_state(st, d))
    all_ok = all_ok and st == 200

    st, d = http(BOARD["record"] + "/api/status")
    if st == 200 and isinstance(d, dict):
        parts.append("录制 %s" % d.get("mode"))
    else:
        parts.append("录制 %s" % _svc_state(st, d))
        all_ok = False

    st, d = http(BOARD["stream"] + "/api/status")
    if st == 200 and isinstance(d, dict):
        parts.append("画面 %s" % ("就绪" if d.get("ready") else "无有效帧"))
        all_ok = all_ok and bool(d.get("ready"))
    else:
        parts.append("画面 %s" % _svc_state(st, d))
        all_ok = False

    return all_ok, "；".join(parts), None


# ---------------------------------------------------------------------------
# 新话术示例：板子自己的新情况 -> 新回应（直接命令，不走 klar 话术包）
# ---------------------------------------------------------------------------
def act_time(slots):
    """现在几点：板上本地时间 HH:MM（纯本地，无服务依赖）。"""
    now = time.strftime("%H:%M")
    return True, "现在 %s" % now, {"time": time.strftime("%H:%M:%S")}


SNAPSHOT_DIR = "/mnt/data/klar/snapshots"


def act_snapshot(slots):
    """拍照存档：抓一帧 SDI 现场 JPEG，存到板上 snapshots/ 目录。

    实测 8091（wcis-face-photo）的 GET /api/camera/preview.jpg：
      200 -> Content-Type: image/jpeg，裸 JPEG 字节
      503 -> {"error":"暂无新鲜摄像头画面"}（无新鲜帧 / 相机被占）
    照片服务没起时 http() 返回 (None, 连接错误)。
    """
    st, data = http(BOARD["face"] + "/api/camera/preview.jpg", "GET")
    if st == 200 and isinstance(data, (bytes, bytearray)):
        try:
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        except OSError as e:                     # noqa: PERF203
            return False, "存档目录不可写：%s" % e, None
        name = "snap-%s.jpg" % time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(SNAPSHOT_DIR, name)
        try:
            with open(path, "wb") as f:
                f.write(bytes(data))
        except OSError as e:                     # noqa: PERF203
            return False, "写盘失败：%s" % e, None
        return True, "已存一张：%s" % name, {"path": path, "bytes": len(data)}
    if st == 503:
        return False, "拍不了：暂无新鲜摄像头画面", data
    if st is None:
        return False, "拍照服务没在跑", data
    return False, "拍照接口返回 %s" % st, data


# ---------------------------------------------------------------------------
# 多因子开门：口令 + 人脸 + 防伪 + 声纹（实现见 door_unlock.py）
# 这里只做动作包装，真正的四道闸在 door_unlock.unlock() 里。
# ---------------------------------------------------------------------------
def act_unlock(slots):
    """语音开门。需要调用方把本次音频路径放进 slots['wav']。"""
    txt = slots.get("text") or "我回来了，开门"
    wav = slots.get("wav")
    try:
        import door_unlock
    except Exception as e:                       # noqa: BLE001
        return False, "开门模块加载失败: %s" % e, None
    out = door_unlock.unlock(txt, wav, dry_run=slots.get("dry_run", False))
    st = out.get("steps") or {}
    detail = {"phrase": (st.get("phrase") or {}).get("ok"),
              "face": (st.get("face") or {}).get("ok"),
              "face_names": (st.get("face") or {}).get("names"),
              "pad": (st.get("pad") or {}).get("status"),
              "voiceprint": (st.get("voiceprint") or {}).get("matched"),
              "voiceprint_score": (st.get("voiceprint") or {}).get("score")}
    return out.get("ok", False), out.get("speech", ""), detail


def act_door_status(slots):
    """查门禁状态（人脸服务 + PAD 标定 + 声纹库人数）。"""
    parts, ok = [], True
    st, d = http(BOARD["face"] + "/api/health")
    if st == 200 and isinstance(d, dict):
        parts.append("人脸 %s" % d.get("status", "?"))
        parts.append("防伪 %s" % ("已标定" if d.get("pad_security_calibrated") else "未标定"))
    else:
        parts.append("人脸 %s" % _svc_state(st, d))
        ok = False
    try:
        import voiceprint as vp
        n = len((vp.load_store().get("speakers") or {}))
        parts.append("声纹库 %d 人" % n)
    except Exception:                            # noqa: BLE001
        parts.append("声纹库 读不到")
    return ok, "；".join(parts), None


# ---------------------------------------------------------------------------
# 三条新命令（2026-11-04 她点名）：启动人脸识别／启动声纹识别／重启（+撤销）
# ---------------------------------------------------------------------------
def act_voiceprint_start(slots):
    """启动声纹识别：模型／运行时／声纹库三样一起查，**缺什么如实报**。

    现实背景：板子重烧后 /mnt/data/models 与 sherpa_onnx 都不在了
    （D-1793767689）——这一档现在多半报缺失，这是诚实读数，不是假装能用。
    """
    try:
        import voiceprint as vp
    except Exception as e:                       # noqa: BLE001
        return False, "声纹模块加载失败：%s" % e, None
    missing = []
    if not os.path.exists(vp.MODEL):
        missing.append("模型 %s" % vp.MODEL)
    try:
        import sherpa_onnx                       # noqa: F401
    except Exception:
        missing.append("sherpa_onnx 运行时")
    try:
        n = len((vp.load_store().get("speakers") or {}))
    except Exception:                            # noqa: BLE001
        n = -1
    detail = {"model_present": os.path.exists(vp.MODEL), "speakers": n}
    if missing:
        return False, "声纹起不来，缺：%s（库中 %s 人）" % (
            "、".join(missing), "?" if n < 0 else n), detail
    return True, "声纹识别已启动，库中 %d 人" % n, detail


def _cmd(argv):
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stderr.decode("utf-8", "ignore").strip()[:160]


def act_reboot(slots):
    """重启：`shutdown -r +1`——刻意**不** now，留一分钟撤销窗。

    播报（TTS 网络往返）赶在生效前出完；语音「取消重启」或板上
    `shutdown -c` 都能撤。短语表刻意不含裸「重启」（防止在别的句子里误触发）。
    """
    rc, err = _cmd(["shutdown", "-r", "+1"])
    if rc != 0:
        return False, "重启安排失败：%s" % (err or "rc=%d" % rc), None
    return True, "好，一分钟后重启。说取消重启可以撤销", {"scheduled": "+1"}


def act_cancel_reboot(slots):
    rc, err = _cmd(["shutdown", "-c"])
    if rc == 0:
        return True, "已取消重启", None
    return False, "没有待执行的重启（%s）" % (err or "rc=%d" % rc), None


# ---------------------------------------------------------------------------
# CHANNEL B: direct command table (bypasses klar entirely)
#
# WHY THIS EXISTS
#   klar's zh-CN lexicon only knows household words (灯/风扇/窗帘/空调/电视).
#   We tried to teach it our words via POST /api/lang/overlay custom phrases:
#   the phrases persist (/api/custom returns all 11) and DO match
#   (policy_trace id=custom_sentence score=1.0), but the engine then lands on
#   `safety_decision: reject` with reason "unsafe", because the target is not
#   in its internal exposed-entity graph (/api/entities returns [] and there is
#   no write route for it). Full evidence: klar/docs/KLAR-CUSTOM-PHRASES.md
#
#   So board-owned commands are matched here by string. Deterministic, no engine
#   dependency, and faster (~0 ms vs ~25 ms for a parse round-trip).
#
# (phrases, action, default_slots)
# ---------------------------------------------------------------------------
def act_dialogue_reply(slots):
    """单次固定回应；调用方照常播报 message，播完结束当前请求。"""
    return True, slots["reply"], {"end_request": True}


DIRECT_COMMANDS = [
    (["小克小克"], act_dialogue_reply, {"reply": "我在。"}),
    (["再见"], act_dialogue_reply, {"reply": "拜拜。"}),
    # --- 开门（多因子，见 door_unlock.py）注意：必须排在普通命令之前判断 ---
    (["我回来了开门", "我回来了，开门", "我回家了开门", "开门吧", "帮我开门",
      "把门打开", "门开一下", "开一下门", "我回来了"],  act_unlock, {}),
    (["门禁状态", "门能开吗", "门锁状态"],              act_door_status, {}),
    # --- 人脸 / NPU ---
    (["打开人脸识别", "开始人脸识别", "启动人脸识别", "识别一下", "看看是谁", "谁来了",
      "人脸识别", "识别画面", "看看有人吗"],        act_face_recognize, {}),
    # --- 录制 ---
    (["开始自动录制", "打开自动录制", "自动录制"],   act_record_on,  {"mode": "auto"}),
    (["开始录制", "开启录制"],                     act_record_on,  {"mode": "on"}),
    (["停止录制", "关闭录制", "别录了"],            act_record_off, {}),
    (["清空回收站", "清空录制回收站", "删除录像"],    act_record_purge, {}),
    # --- 状态询问（只读，安全）---
    (["现在什么状态", "状态怎么样", "板子还好吗"],   act_status, {}),
    # --- 新话术：板子自有命令（直接匹配，不依赖 klar）---
    (["现在几点", "几点了", "现在时间", "看下时间"],  act_time, {}),
    (["拍照存档", "拍一张照片", "拍张照片", "存一张照片"], act_snapshot, {}),
    # --- 2026-11-04 她点名三条（声纹＝如实报缺；重启＝+1min 可撤销，裸「重启」故意不进表）---
    (["启动声纹识别", "开启声纹识别", "打开声纹识别"], act_voiceprint_start, {}),
    (["重启系统", "重启板子", "重启悟净", "把板子重启", "重启一下板子",
      "帮我重启板子", "执行重启"],                  act_reboot, {}),
    (["取消重启", "别重启了", "不重启了"],           act_cancel_reboot, {}),
]

_CMD_INDEX = None


def _build_index():
    global _CMD_INDEX
    idx = [(p, fn, d) for phrases, fn, d in DIRECT_COMMANDS for p in phrases]
    # 长词优先，避免「录制」抢在「关闭录制」前面
    idx.sort(key=lambda x: -len(x[0]))
    _CMD_INDEX = idx
    return idx


def match_direct(text):
    """Return (fn, slots, matched_phrase) or None. Exact-ish match only."""
    idx = _CMD_INDEX or _build_index()
    t = (text or "").strip()
    for phrase, fn, defaults in idx:
        if fn is act_dialogue_reply:
            normalized = "".join(ch for ch in t if not ch.isspace()
                                 and ch not in "，。！？、；：,.!?;:")
            if normalized == phrase:
                return fn, dict(defaults), phrase
            continue
        if t == phrase or t.endswith(phrase) or phrase in t:
            return fn, dict(defaults), phrase
    return None


def run_direct(text, dry_run=False, extra=None):
    hit = match_direct(text)
    if not hit:
        return None
    fn, slots, phrase = hit
    if extra:
        for k, v in extra.items():
            slots.setdefault(k, v)
        slots["text"] = text
    if dry_run:
        return {"ok": True, "channel": "direct", "matched": phrase,
                "dry_run": True}
    ok, msg, detail = fn(slots)
    return {"ok": ok, "channel": "direct", "matched": phrase,
            "message": msg, "detail": detail}


# 状态查询已在上方 DIRECT_COMMANDS 之前定义（act_status）
ACTION_MAP = {
    "HassTurnOn":  None,      # resolved by entity below
    "HassTurnOff": None,      # resolved by entity below
}


# Entity id -> (human name, action callable)
# These ids are OUR OWN registry namespace, written by make_registry.py.
TARGETS = {
    "switch.face_recognize":  ("人脸识别", act_face_recognize),
    "switch.face_enroll":     ("人脸录入", act_face_enroll),
    "switch.record_auto":     ("自动录制", act_record_on),
    "switch.record_on":       ("持续录制", act_record_on),
    "switch.record_off":      ("关闭录制", act_record_off),
    "switch.record_purge":    ("清空录制回收站", act_record_purge),
    "sensor.board_status":    ("板子状态", act_status),
}


def execute(parse_result, dry_run=False):
    """Turn one klar parse result into one board action."""
    decision = (parse_result.get("decision") or {}).get("type")
    speech = parse_result.get("speech") or ""

    if decision == "chat":
        return {"ok": True, "kind": "chat", "speech": speech, "action": None}
    if decision in ("clarify", "confirm", "reject"):
        # no action to run; klar already produced a spoken cue
        return {"ok": True, "kind": decision, "speech": speech, "action": None}
    if decision != "execute":
        return {"ok": False, "kind": decision, "speech": speech,
                "error": "unsupported decision: %s" % decision}

    steps = (((parse_result.get("candidates") or [{}])[0].get("plan") or {})
             .get("steps") or [])
    if not steps:
        return {"ok": False, "kind": "execute", "error": "no plan steps"}

    results = []
    for step in steps:
        intent = (step.get("intent") or {}).get("name")
        slots = {}
        for sl in ((step.get("intent") or {}).get("slots") or []):
            slots[sl.get("name")] = sl.get("value")

        # turn intent + slots into our entity namespace
        entity = slots.get("entity_id")
        if intent == "HassTurnOn" and entity is None and slots.get("area"):
            target = _area_default_on(slots["area"])
        else:
            target = entity

        fn, name = None, None
        if target and target in TARGETS:
            name, fn = TARGETS[target]
        elif intent in ("HassTurnOn", "HassTurnOff"):
            r = {"intent": intent, "slots": slots, "ok": False,
                 "error": "entity %r 不在本板 targets 里" % target}
            results.append(r)
            continue
        else:
            r = {"intent": intent, "slots": slots, "ok": False,
                 "error": "intent %r 未实现" % intent}
            results.append(r)
            continue

        if dry_run:
            results.append({"intent": intent, "slot_entity": target,
                            "target_name": name, "ok": True, "dry_run": True})
            continue
        ok, msg, detail = fn(slots)
        results.append({"intent": intent, "slot_entity": target,
                        "target_name": name, "ok": ok, "message": msg,
                        "detail": detail})

    speech = speech or "；".join(r.get("message", "") for r in results)
    return {"ok": all(r.get("ok") for r in results),
            "kind": "execute", "speech": speech, "actions": results}


def handle_text(text, dry_run=False, speak_reply=False, wav=None):
    """Main entry: try CHANNEL B (direct) first, fall back to CHANNEL A (klar).

    Channel B first because it is deterministic and instant; klar is only
    consulted for sentences we don't own (household words it understands).

    speak_reply=True 时把最终结果通过 Wyoming TTS 播出去（板上无 /dev/snd，
    所以只落 wav，外部喇叭/HDMI 再播）。
    wav 传给需要音频的动作（目前是开门，做声纹校验）。
    """
    # 需要音频的动作在这里补 slots
    direct = run_direct(text, dry_run=dry_run, extra={"text": text, "wav": wav})
    if direct is not None:
        out = {"channel": "direct", "input": text, **direct}
    else:
        parsed = klar_parse(text)
        if "error" in parsed:
            out = {"ok": False, "channel": "klar", "input": text,
                   "error": parsed.get("error"), "detail": parsed.get("detail")}
        else:
            out = execute(parsed, dry_run=dry_run)
            out["channel"] = "klar"
            out["input"] = text
            out["klar_decision"] = (parsed.get("decision") or {}).get("type")
            out["klar_speech"] = parsed.get("speech")

    if speak_reply and not dry_run:
        out["speech_result"] = _speak_out(out)
    return out


def _speak_out(out):
    """把执行结果念出来。优先用动作返回的 message，其次 klar 的 speech。"""
    msg = out.get("message")
    if not msg:
        for a in (out.get("actions") or []):
            if a.get("message"):
                msg = a["message"]
                break
    if not msg:
        msg = out.get("klar_speech")
    if not msg and out.get("error"):
        msg = "出错了：%s" % out["error"]
    if not msg:
        return None
    try:
        import speak as _s
        ok, info = _s.speak(msg)
        return {"ok": ok, "info": info, "text": msg}
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": str(e), "text": msg}


def _area_default_on(area):
    """If the user said a bare area, pick a sensible on-target in it."""
    defaults = {"npu": "switch.face_recognize", "record": "switch.record_auto"}
    return defaults.get(area)


# ---------------------------------------------------------------------------
# One-way path used by klar's Wyoming intent channel
# ---------------------------------------------------------------------------
def handle_wyoming_intent(payload):
    """klar speaks Wyoming `intent` on 10500; we accept the same JSON shape."""
    text = payload.get("text") or payload.get("input", {}).get("text")
    if not text:
        return {"ok": False, "error": "no text in intent payload"}
    return handle_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--do", help="一句话端到端：parse -> execute -> 打印结果")
    ap.add_argument("--wav", help="本次说话的音频（开门做声纹校验用）")
    ap.add_argument("--speak", action="store_true", help="把结果用 TTS 播报出去")
    ap.add_argument("--dry-run", action="store_true", help="只解析不执行")
    ap.add_argument("--json", "-j", action="store_true", help="输出 JSON")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.do:
        ap.print_help()
        return 0

    out = handle_text(args.do, dry_run=args.dry_run,
                      speak_reply=args.speak, wav=args.wav)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out.get("ok") else 1

    print("输入     :", args.do)
    print("通道     :", out.get("channel"))
    if out.get("channel") == "klar":
        print("klar 决策:", out.get("klar_decision"), "| 播报:", out.get("klar_speech"))
    else:
        print("命中短语 :", out.get("matched"))
    print("执行结果 :", "OK" if out.get("ok") else "FAILED")
    if out.get("dry_run"):
        print("  [dry-run] 未实际执行")
    elif out.get("message"):
        print("  ->", out.get("message"))
    for a in out.get("actions") or []:
        print("  %s: %s -> %s" % ("✓" if a.get("ok") else "✗",
                                  a.get("target_name"), a.get("message")))
    if out.get("error"):
        print("  error:", out["error"])
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
