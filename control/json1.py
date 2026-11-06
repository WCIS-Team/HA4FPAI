# -*- coding: utf-8 -*-
"""JSON1 能力与版本合同（C01）。

规则（规划 §JSON1迁移桥）：运行时探测 json_valid/json_type/json_extract/json_each，
声明最低已测 SQLite 3.31.1；缺 JSON1 时**清晰拒绝**，绝不偷偷换成 Python 语义。
不依赖 JSONB/STRICT 等 3.31 之外的语法。
"""
import sqlite3

MIN_TESTED = (3, 31, 1)
REQUIRED = ('json_valid', 'json_type', 'json_extract', 'json_each')


class Json1Unavailable(RuntimeError):
    """当前连接不满足 JSON1 合同（缺函数或版本过低）。"""


def _ver_tuple(text):
    out = []
    for part in str(text).split('.')[:3]:
        num = ''
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num or 0))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _run(conn, sql, want):
    """执行一次探测 SQL 并比对期望值；异常归一成 (False, reason)。"""
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        return False, 'error:%r' % exc
    if row is None:
        return False, 'no-row'
    got = row[0]
    return (got == want), 'got:%r' % (got,)


def probe(conn):
    """返回能力探测结果 dict（不改库）。"""
    version = sqlite3.sqlite_version
    vt = _ver_tuple(version)
    checks = {}
    missing = []
    probes = {
        'json_valid_ok': ("SELECT json_valid('{}')", 1),
        'json_valid_bad_zero': ("SELECT json_valid('{bad')", 0),
        'json_type': ("SELECT json_type('{\"a\":1}', '$.a')", 'integer'),
        'json_extract': ("SELECT json_extract('{\"a\":2}', '$.a')", 2),
        'json_each': ("SELECT count(*) FROM json_each('[1,2,3]')", 3),
    }
    for name, (sql, want) in probes.items():
        ok, reason = _run(conn, sql, want)
        checks[name] = ok
        if not ok and 'no such function' in reason:
            for fn in REQUIRED:
                if fn in reason and fn not in missing:
                    missing.append(fn)
    # jsonb 只是信息项（3.45+），不作要求
    try:
        conn.execute("SELECT jsonb('{}')").fetchone()
        has_jsonb = True
    except sqlite3.Error:
        has_jsonb = False
    ok = all(checks.values())
    return {
        'sqlite_version': version,
        'version_tuple': vt,
        'min_tested': MIN_TESTED,
        'meets_min': vt >= MIN_TESTED,
        'ok': ok and vt >= MIN_TESTED,
        'checks': checks,
        'missing': missing,
        'has_jsonb': has_jsonb,
    }


def require(conn):
    """合同门禁：不满足则抛 Json1Unavailable（消息含版本与缺失项）。"""
    info = probe(conn)
    if not info['meets_min']:
        raise Json1Unavailable(
            'SQLite %s 低于已测最低版本 %s；JSON1 迁移桥拒绝运行（不降级）'
            % (info['sqlite_version'], '.'.join(str(x) for x in MIN_TESTED)))
    if not info['ok']:
        bad = [k for k, v in info['checks'].items() if not v]
        raise Json1Unavailable(
            'JSON1 能力不足（SQLite %s）：缺函数 %s / 未通过项 %s'
            % (info['sqlite_version'], info['missing'] or REQUIRED, bad))
    return info
