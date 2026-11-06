# -*- coding: utf-8 -*-
"""语音失败与危险确认的多轮处理（G02）。
规则（规划 §语音）：回复只从**结构化结果**生成，LLM 话术不作设备证据；
固定回复/否定/疑问/澄清/确认/超时 unknown 分别处理；重复确认不得双执行。
本模块是纯状态机，不依赖真实 ASR/TTS（执行端接线属 G01）。
"""
from . import ids

CONFIRM = 'confirm'
NEGATION = 'negation'
QUESTION = 'question'
CLARIFY = 'clarify'
FIXED = 'fixed'
TIMEOUT = 'timeout'
KINDS = (CONFIRM, NEGATION, QUESTION, CLARIFY, FIXED, TIMEOUT)


def new_session():
    return {'session_id': ids.new_id(), 'state': 'idle', 'pending': None,
            'confirm_digest': None, 'executed': False, 'unknown': False, 'history': []}


def render_result(structured):
    """把结构化执行结果渲染成回复文本（不掺任何 LLM 自由文本）。"""
    if not structured:
        return '没有可报告的结果。'
    status = structured.get('status')
    if status == 'succeeded':
        msg = structured.get('message') or '已执行'
        return '执行成功：%s' % msg
    if status == 'failed':
        return '执行失败：%s' % (structured.get('message') or structured.get('code') or '未知原因')
    if status == 'unknown':
        return '无法确认是否已执行，请核对后再操作。'
    return '结果状态：%s' % status


def ask_confirmation(session, action, params_digest):
    session['state'] = 'awaiting_confirm'
    session['pending'] = action
    session['confirm_digest'] = params_digest
    session['executed'] = False
    session['unknown'] = False
    return {'reply': '该操作需要确认：%s。请回复“确认”或“取消”。' % action,
            'action': 'ask', 'state': session['state']}


def handle(session, kind, *, structured=None, action=None, params_digest=None,
           clarify_question=None, info_text=None):
    """处理一轮语音输入。返回 {'reply','action','state'}。

    action ∈ {None,'ask','execute','cancel','unknown','noop','clarify'}
    """
    if kind not in KINDS:
        raise ValueError('未知的语音输入类型: %r' % (kind,))
    session['history'].append(kind)
    if kind == FIXED:
        # 咨询类：只报结构化信息，不触发执行
        return {'reply': info_text or render_result(structured), 'action': None,
                'state': session['state']}
    if kind == QUESTION:
        return {'reply': info_text or '这是查询，已按结构化信息回答。', 'action': None,
                'state': session['state']}
    if kind == CLARIFY:
        session['state'] = 'awaiting_clarify'
        return {'reply': clarify_question or '请再说明一下你的需求。', 'action': 'clarify',
                'state': session['state']}
    if kind == NEGATION:
        session['pending'] = None
        session['confirm_digest'] = None
        session['state'] = 'idle'
        return {'reply': '已取消。', 'action': 'cancel', 'state': session['state']}
    if kind == CONFIRM:
        if session['executed']:
            return {'reply': '该操作已经执行过，未重复执行。', 'action': 'noop',
                    'state': session['state']}
        if session['state'] != 'awaiting_confirm' or not session['pending']:
            return {'reply': '当前没有待确认的操作。', 'action': 'noop',
                    'state': session['state']}
        if params_digest is not None and session['confirm_digest'] is not None \
                and params_digest != session['confirm_digest']:
            return {'reply': '确认与待执行内容不一致，已拒绝。', 'action': 'noop',
                    'state': session['state']}
        session['executed'] = True
        session['state'] = 'done'
        return {'reply': '已确认，正在执行：%s' % session['pending'], 'action': 'execute',
                'state': session['state'], 'action_name': session['pending']}
    if kind == TIMEOUT:
        if session['state'] == 'awaiting_confirm' and session['pending']:
            session['unknown'] = True
            session['state'] = 'unknown'
            return {'reply': '确认超时，无法确认是否执行；已标记为未知，请核对现场。',
                    'action': 'unknown', 'state': session['state']}
        session['state'] = 'idle'
        return {'reply': '等待超时。', 'action': None, 'state': session['state']}
    return {'reply': '', 'action': None, 'state': session['state']}
