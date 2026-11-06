# -*- coding: utf-8 -*-
"""配置生成与原子 applier（D03）＋受限重启合同（D04）。

规则：
  * 只写专用覆盖文件，且只写白名单键（OVERLAY_KEYS）；业务源仍是 DB。
  * 写前渲染自检 → 临时文件 + fsync → 原子 replace；失败保留旧文件与 .bak 诊断。
  * 重启只允许白名单服务（lang-server-*），绝不含 npuserv/任意服务名/命令。
"""
import hashlib
import json
import os

try:                       # py3 环境一般自带 pyyaml；没有则退回 JSON（覆盖文件仍可解析）
    import yaml
except Exception:          # noqa: BLE001
    yaml = None

from .settings import OVERLAY_KEYS


class ApplierError(RuntimeError):
    """覆盖文件生成/写入失败。"""


def _flatten(nested):
    flat = {}
    if isinstance(nested, dict):
        for _scope, sub in nested.items():
            if isinstance(sub, dict):
                for k, v in sub.items():
                    flat[k] = v
    return flat


def render_overlay(settings):
    """返回 {key: value}，只含白名单业务键（不含凭据/硬件/UI 等非 overlay 键）。"""
    data = {}
    for entry in settings.effective():
        if entry['key'] in OVERLAY_KEYS:
            data[entry['key']] = entry['value']
    return data


def _canonical(data):
    out = {}
    for key, value in data.items():
        scope = key.split('.')[0]
        out.setdefault(scope, {})[key] = value
    return out


def _dump(nested):
    if yaml is not None:
        return yaml.safe_dump(nested, allow_unicode=True, sort_keys=True,
                              default_flow_style=False)
    return json.dumps(nested, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def _load(text):
    if yaml is not None:
        return yaml.safe_load(text)
    return json.loads(text)


def validate_text(text, expected):
    """解析回读并与期望的扁平 {key:value} 比对（覆盖文件自检）。"""
    try:
        loaded = _load(text)
    except Exception as exc:                        # noqa: BLE001
        return False, 'parse_error:%r' % exc
    flat = _flatten(loaded if isinstance(loaded, dict) else {})
    # 允许浮点近似
    if set(flat.keys()) != set(expected.keys()):
        return False, 'key_set_mismatch:%r' % (sorted(set(flat) ^ set(expected)))
    for k, v in expected.items():
        if isinstance(v, float):
            try:
                if abs(float(flat[k]) - v) > 1e-9:
                    return False, 'value_mismatch:%s' % k
            except (TypeError, ValueError):
                return False, 'value_type:%s' % k
        elif flat[k] != v:
            return False, 'value_mismatch:%s' % k
    return True, 'ok'


def apply(settings, path, dry_run=False):
    """生成白名单覆盖文件并原子落盘。返回 {path, keys, sha256, bytes, backup?, dry_run}。"""
    data = render_overlay(settings)
    text = _dump(_canonical(data))
    ok, why = validate_text(text, data)
    if not ok:
        raise ApplierError('覆盖内容自检失败: %s' % why)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    result = {'path': os.path.abspath(path), 'keys': sorted(data.keys()),
              'sha256': digest, 'bytes': len(text.encode('utf-8')), 'dry_run': bool(dry_run)}
    if dry_run:
        return result
    dirn = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(dirn, exist_ok=True)
    tmp = os.path.abspath(path) + '.tmp'
    bak = os.path.abspath(path) + '.bak'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    # 落盘前对临时文件再自检（防半写）
    with open(tmp, 'r', encoding='utf-8') as f:
        ok2, why2 = validate_text(f.read(), data)
    if not ok2:
        os.remove(tmp)
        raise ApplierError('临时文件复读自检失败: %s' % why2)
    if os.path.exists(path):
        try:
            with open(path, 'rb') as src, open(bak, 'wb') as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            result['backup'] = bak
        except OSError as exc:
            os.remove(tmp)
            raise ApplierError('备份旧覆盖失败，保持旧文件不动: %r' % exc)
    try:
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ApplierError('原子替换失败: %r' % exc)
    return result


# ---- D04：受限重启合同 ----
RESTART_WHITELIST = ('lang-server-http', 'lang-server-loop', 'lang-server-voice')
FORBIDDEN_SERVICES = ('npuserv', 'ha4fpai-task-worker', 'ha4fpai-control')


def restricted_restart(names, runner=None, health_probe=None, timeout=30):
    """只允许白名单服务；npuserv/未知/注入式名称一律拒绝。

    runner(name, timeout) 与 health_probe(name) 可注入（测试/不同部署）；不给 runner
    时返回 dry_run 计划，不真的动服务。
    """
    names = [str(n) for n in names]
    refused = [n for n in names if n in FORBIDDEN_SERVICES or n not in RESTART_WHITELIST]
    if refused:
        return {'restarted': [], 'failed': [], 'refused': refused,
                'reason': 'not_whitelisted', 'dry_run': runner is None}
    if runner is None:
        return {'restarted': [], 'failed': [], 'refused': [], 'planned': names,
                'dry_run': True}
    restarted, failed = [], []
    for name in names:
        try:
            runner(name, timeout)
            healthy = True if health_probe is None else bool(health_probe(name))
        except Exception as exc:                    # noqa: BLE001
            failed.append({'service': name, 'reason': repr(exc)})
            continue
        if healthy:
            restarted.append(name)
        else:
            failed.append({'service': name, 'reason': 'unhealthy'})
    return {'restarted': restarted, 'failed': failed, 'refused': [], 'dry_run': False}
