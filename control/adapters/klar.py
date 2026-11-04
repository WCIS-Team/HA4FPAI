# -*- coding: utf-8 -*-
"""klar V2 统一适配器与受控直接短语匹配（执行器与语言路由共用）。

现状（2026-11-05 规划核对）：独立执行器走 /api/v2/parse 的嵌套
decision/candidates/plan/steps；lang 的 KlarRouter 默认 /parse 读平铺
action/intent/reply —— 两套协议未统一（见 lang/config/klar.yaml 注释）。
本模块提供统一解析：调用方只消费归一化后的提案，不再各写一份解析。

安全口径：这里只产生"提案"，执行一律走统一命令管线；
klar 的 safety_decision 不充当系统鉴权（规划 §安全与事件反馈）。
"""
import json
import urllib.request

from .. import ids

DEFAULT_URL = 'http://127.0.0.1:10520/api/v2/parse'
_PUNCT = '，。！？、；：,.!?;: \t\r\n　'


def normalize_text(text):
    return ''.join(ch for ch in str(text or '') if ch not in _PUNCT)


def default_post(url, payload, timeout):
    """默认 HTTP POST（JSON）；失败抛异常，由 parse_v2 归一为 unavailable。"""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8', 'replace') or '{}')


def parse_v2(http_post, text, language='zh-CN', timeout=5, url=None, session=None):
    """调 klar /api/v2/parse 并归一化；任何失败返回 decision=unavailable。"""
    payload = {'text': text, 'language': language}
    if session:
        payload['session'] = session
    try:
        status, data = http_post(url or DEFAULT_URL, payload, timeout)
    except Exception as exc:
        return {'decision': 'unavailable', 'error': '%s: %s' % (type(exc).__name__, exc)}
    if status != 200 or not isinstance(data, dict):
        return {'decision': 'unavailable', 'status': status}
    decision = (data.get('decision') or {}).get('type') or 'unknown'
    out = {'decision': decision, 'speech': data.get('speech') or ''}
    steps = (((data.get('candidates') or [{}])[0].get('plan') or {}).get('steps')) or []
    intents = []
    for step in steps:
        intent = step.get('intent') or {}
        slots = {}
        for slot in (intent.get('slots') or []):
            slots[slot.get('name')] = slot.get('value')
        intents.append({'intent': intent.get('name'), 'slots': slots})
    out['intents'] = intents
    return out


def match_direct(text, direct_table):
    """受控直接短语：仅整句归一化精确匹配。

    不做子串匹配——"不要开始录制"、"怎么开始录制"、"你说开始录制了吗"
    都不会命中 "开始录制"（规划 §命令、任务与重试）。
    """
    key = normalize_text(text)
    if not key:
        return None
    for phrase in sorted(direct_table, key=lambda p: -len(p)):
        if key == normalize_text(phrase):
            proposal = direct_table[phrase]
            return dict(proposal) if isinstance(proposal, dict) else {'reply': str(proposal)}
    return None


def digest_proposal(proposal):
    return ids.digest_of(proposal)
