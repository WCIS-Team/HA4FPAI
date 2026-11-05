# -*- coding: utf-8 -*-
"""语音桥：ASR 文本 → 统一命令 → 可念的回复（规划 §界面与语音闭环）。

lang/server 不直接写库、不直接调设备；部署时把本桥作为 ASR 后的执行面
（WP9 六个首批场景）。返回的 reply 一定来自结构化结果或固定应答，
LLM 生成的文字不冒充执行回执（source=llm_handoff 时如实返回空 reply）。
"""
from .errors import DomainError


def run_text(app, text, session=None, client_id='voice'):
    """返回 {'ok','reply','source','results'}；失败也带可念的中文说明。"""
    from . import actions as actions_mod
    from . import commands
    result = actions_mod.interpret(app.store, app.registry, text, session=session)
    source = result.get('source')
    if source == 'direct_reply':
        return {'ok': True, 'reply': result.get('reply') or '', 'source': source,
                'results': [], 'end_request': bool(result.get('end_request'))}
    outcomes, reply_parts, all_ok = [], [], True
    for proposal in (result.get('proposals') or []):
        action = proposal.get('action')
        if not action:
            outcomes.append({'kind': 'unmapped', 'proposal': proposal})
            all_ok = False
            continue
        try:
            out = commands.create(app.store, app.registry, app.events,
                                  action=action, params=proposal.get('params') or {},
                                  target=proposal.get('target'),
                                  client_id=client_id, session_id=session,
                                  principal_id='voice-bridge')
            cmd = out['command']
            outcomes.append(cmd)
            if cmd['status'] == 'succeeded':
                reply_parts.append(cmd.get('message') or '')
            else:
                all_ok = False
                reply_parts.append('没做成：%s' % (cmd.get('message') or cmd.get('code') or '失败'))
        except DomainError as exc:
            all_ok = False
            outcomes.append(exc.to_json())
            reply_parts.append('没做成：%s' % exc.message)
    if not outcomes:
        return {'ok': False, 'reply': '', 'source': source, 'results': [],
                'note': result.get('note') or '没有可执行的设备动作'}
    return {'ok': all_ok, 'reply': '；'.join(p for p in reply_parts if p),
            'source': source, 'results': outcomes}
