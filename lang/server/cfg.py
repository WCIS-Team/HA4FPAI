#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cfg.py —— lang/server 的配置加载。

**优先读目录 `lang/config/*.yaml`（一组件一文件，按文件名序深合并）**；没有目录时回退
单文件 `lang/config.yaml`（旧布局，兼容保留）。

只认「key: value」「key:（开子节）」与 # 注释——与 archive/mimo_bridge.py 同一套迷你口径，
不引第三方 yaml（板上 py3.8 与本机 py3.13 都能跑）。值一律按**字符串**保存，类型转换交给
as_bool / as_int / as_float。**支持任意层级嵌套**（缩进栈：`asr_profiles.sherpa.encoder` 是两层）。

查找目录顺序：`LANG_CONFIG_DIR` → `<server>/config` → `<lang>/config`（显式参数最优先）。
约定：配置文件**不写密钥**（D-27），密钥只从环境变量或密钥文件来。
"""

import io
import os
import re

_KEY_RE_TOP = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*):(.*)$")
_KEY_RE_NEST = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_\-]*):(.*)$")


def _clean(v):
    """剥行内注释、去引号。**整值是注释 ⇒ 返回空串**（节头 `key:   # 说明` 才会被当成开子节）。

    **引号感知**：值被 "..." 或 '...' 包住时，取引号内全部（里面即便有 ` #` 也不算注释），
    引号外的东西忽略；未加引号时才按 ` #` 判行内注释。URL/密钥里含 `#` 也不会被截断。
    """
    v = v.strip()
    if not v:
        return ""
    if v[0] in ("'", '"'):
        q = v[0]
        end = v.find(q, 1)
        return v[1:end] if end != -1 else v[1:]
    if v.startswith("#"):
        return ""
    for sep in (" #", "\t#"):
        if sep in v:
            v = v.split(sep)[0].strip()
            break
    return v


def parse(text):
    """yaml 子集 → 嵌套 dict。**支持任意层级**（靠缩进栈）。

    只认 `key: value`、`key:`（开子节）与 `# 注释`；值一律字符串。
    之所以要嵌套：`asr_profiles.sherpa.encoder` 是两层，旧的一层解析器会把它压平。
    """
    cfg = {}
    stack = [(-1, cfg)]                       # (缩进, 该层 dict)
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*):(.*)$", stripped)
        if not m:
            continue
        key, val = m.group(1), _clean(m.group(2))
        while stack and stack[-1][0] >= indent:   # 回到同级或更浅的父层
            stack.pop()
        parent = stack[-1][1]
        if val:
            parent[key] = val
        else:
            child = {}
            parent[key] = child
            stack.append((indent, child))
    return cfg


def find_config_dir(explicit=None):
    """定位配置**目录**（`lang/config/`），返回绝对路径；找不到返回 None。

    查找顺序：显式参数 → `LANG_CONFIG_DIR` → `<server>/config` → `<lang>/config`。
    """
    cand = []
    if explicit:
        cand.append(explicit)
    if os.environ.get("LANG_CONFIG_DIR"):
        cand.append(os.environ["LANG_CONFIG_DIR"])
    here = os.path.dirname(os.path.abspath(__file__))
    cand.append(os.path.join(here, "config"))                 # server/config
    cand.append(os.path.join(os.path.dirname(here), "config"))  # lang/config
    for p in cand:
        if p and os.path.isdir(p):
            return os.path.abspath(p)
    return None


def find_config_file(explicit=None):
    """单文件回退：`lang/config.yaml`（旧布局，兼容保留）。"""
    cand = []
    if explicit and os.path.isfile(explicit):
        cand.append(explicit)
    if os.environ.get("LANG_CONFIG"):
        cand.append(os.environ["LANG_CONFIG"])
    here = os.path.dirname(os.path.abspath(__file__))
    cand.append(os.path.join(here, "config.yaml"))
    cand.append(os.path.join(os.path.dirname(here), "config.yaml"))
    for p in cand:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _deep_merge(dst, src):
    """把 src 深合并进 dst（dict 递归，其余覆盖）。"""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


class Config(object):
    """配置薄封装。**优先读目录 `lang/config/*.yaml`（一组件一文件，深合并）**；
    没有目录时回退单文件 `lang/config.yaml`。缺文件不报错——各档都有内置默认。"""

    def __init__(self, path=None):
        self.path = None
        self.sources = []
        self.data = {}
        d = find_config_dir(path)
        if d:
            self.path = d
            for fn in sorted(os.listdir(d)):
                if not fn.endswith((".yaml", ".yml")):
                    continue
                fp = os.path.join(d, fn)
                with io.open(fp, encoding="utf-8") as f:
                    _deep_merge(self.data, parse(f.read()))
                self.sources.append(fp)
            return
        f = find_config_file(path)
        if f:
            self.path = f
            with io.open(f, encoding="utf-8") as fh:
                self.data = parse(fh.read())
            self.sources = [f]

    def section(self, name):
        return self.data.get(name) or {}

    def get(self, section, key, default=None):
        sec = self.section(section)
        v = sec.get(key)
        return default if v is None or v == "" else v

    def profile(self, stage, name):
        """取 <stage>_profiles.<name> 节；没有就给空 dict（档内置默认兜底）。"""
        return self.section("%s_profiles" % stage).get(name) or {}

    def online(self, kind):
        """在线（外部）API 配置：`lang/config/online.yaml` 的 `online.<kind>` 节（asr/llm/tts/mimo）。"""
        sec = self.section("online")
        v = sec.get(kind) if isinstance(sec, dict) else None
        return v if isinstance(v, dict) else {}


def as_bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off", "none")


def as_int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def as_float(v, default=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default
