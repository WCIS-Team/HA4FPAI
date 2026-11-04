#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_registry.py — build core.entity_registry + area_registry for klar.

WHY
---
klar engine has NO built-in knowledge of your devices. Without a registry file
it falls back to a hard-coded GERMAN apartment (light.wohnzimmer etc.), which is
why Chinese commands produce German speech. Give it this file and the German
fallback disappears.

FORMAT (reverse-engineered from the engine's own error messages — see docs)
    两层结构，缺一层就报错:
      {"version":1,"minor_version":1,"key":"core.entity_registry",
       "data":{"entities":[...],"deleted_entities":[]}}
      {"version":1,"minor_version":1,"key":"core.area_registry",
       "data":{"areas":[...],"deleted_areas":[]}}
    observed errors:
      {"entities":[...]}            -> missing field `data`
      {"data":{"entities":[...]}}   -> missing field `entities`

WHERE IT GOES
    <config-dir>/.storage/core.entity_registry
    <config-dir>/.storage/core.area_registry
    本仓库默认 config-dir = /mnt/data/klar/ha-config

实体命名规则（重要）
    entity_id 用 ASCII，name 用中文。klar 的 zh-CN 包认得「客厅/厨房/灯/风扇/
    窗帘/定时器/百分之N」这些词；房间名要靠 area registry 的 name 提供。

RUN
    python3 make_registry.py --out /mnt/data/klar/ha-config   # 写盘
    python3 make_registry.py --dry-run                        # 只打印
"""
import argparse
import json
import os

# ---------------------------------------------------------------------------
# 本板可控目标。与 klar_executor.py 的 TARGETS 一一对应，改这里两处一起改。
# (entity_id, 中文名, area_id, platform)
# ---------------------------------------------------------------------------
ENTITIES = [
    # --- 人脸 / NPU ---
    ("switch.face_recognize", "人脸识别",   "npu", "demo"),
    ("switch.face_enroll",    "人脸录入",   "npu", "demo"),
    # --- 录制 ---
    ("switch.record_auto",    "自动录制",   "record", "demo"),
    ("switch.record_on",      "持续录制",   "record", "demo"),
    ("switch.record_off",     "关闭录制",   "record", "demo"),
    ("switch.record_purge",   "清空录制回收站", "record", "demo"),
    # --- 只读传感器（让 klar 能回答状态类问题）---
    ("sensor.npu_backend",    "NPU 后端",   "npu", "demo"),
    ("sensor.record_state",   "录制状态",   "record", "demo"),
    ("sensor.stream_state",   "画面状态",   "stream", "demo"),
]

# (area_id, 中文名, 别名)  —— 别名让「客厅 / 大厅 / 起居室」都能命中
AREAS = [
    ("npu",    "智能", ["识别", "人脸", "AI"]),
    ("record", "录制", ["录像", "存储"]),
    ("stream", "画面", ["直播", "预览"]),
]


def entity_obj(eid, name, area, platform):
    return {
        "entity_id": eid,
        "name": name,
        "original_name": name,
        "area_id": area,
        "platform": platform,
        "device_id": None,
        "disabled_by": None,
        "hidden_by": None,
        "entity_category": None,
        "unique_id": eid.replace(".", "_"),
        "config_entry_id": None,
        "capabilities": None,
    }


def area_obj(aid, name, aliases):
    return {"id": aid, "name": name, "aliases": aliases,
            "floor_id": None, "icon": None, "labels": []}


def build():
    ent = {"version": 1, "minor_version": 1, "key": "core.entity_registry",
           "data": {"entities": [entity_obj(*e) for e in ENTITIES],
                    "deleted_entities": []}}
    area = {"version": 1, "minor_version": 1, "key": "core.area_registry",
            "data": {"areas": [area_obj(*a) for a in AREAS],
                     "deleted_areas": []}}
    return ent, area


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="config-dir（会写 .storage/*.json）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    ent, area = build()
    if args.dry_run or not args.out:
        print("# entity_registry")
        print(json.dumps(ent, ensure_ascii=False, indent=1))
        print("# area_registry")
        print(json.dumps(area, ensure_ascii=False, indent=1))
        return 0

    store = os.path.join(args.out, ".storage")
    os.makedirs(store, exist_ok=True)
    for name, payload in (("core.entity_registry", ent), ("core.area_registry", area)):
        p = os.path.join(store, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("写入", p)
    print("共 %d 个实体 / %d 个区域" % (len(ENTITIES), len(AREAS)))
    print("重启 klar-nlu-engine 后生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
