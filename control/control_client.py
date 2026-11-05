# -*- coding: utf-8 -*-
"""控制服务客户端：executor / console / 语音桥共用的统一入口调用器。

统一路径（规划 §统一执行路径）：文本 → /v1/interpret 提案 → /v1/commands 执行。
调用方用 HA4FPAI_CONTROL_URL 环境变量决定是否走统一路径；未配置时走旧路径
（部署时切换，见阶段02 取舍记录）。连接失败如实上抛/报错，不伪造成功。
"""
import json
import os
import uuid
import urllib.error
import urllib.request


class ControlClient(object):
    def __init__(self, base_url, token=None, timeout=30):
        self.base = base_url.rstrip('/')
        if token is None:
            # 部署形态：executor/console 单元经环境变量携带令牌（配合 --token-file）
            token = os.environ.get('HA4FPAI_CONTROL_TOKEN') or None
        self.token = token
        self.timeout = timeout

    def _request(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        if self.token:
            req.add_header('Authorization', 'Bearer ' + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode('utf-8', 'replace') or '{}')
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode('utf-8', 'replace') or '{}')
            except ValueError:
                return exc.code, {}
        # 连接类异常不上抛转换，让调用方决定语义（回退或报错）

    def interpret(self, text, session=None):
        return self._request('POST', '/v1/interpret', {'text': text, 'session': session})

    def command(self, action, params=None, target=None, idempotency_key=None,
                confirmation_id=None, client_id=None, session_id=None):
        body = {'action': action, 'params': params or {},
                'idempotency_key': idempotency_key or uuid.uuid4().hex,
                'client_id': client_id}
        if target is not None:
            body['target'] = target
        if confirmation_id:
            body['confirmation_id'] = confirmation_id
        if session_id:
            body['session_id'] = session_id
        return self._request('POST', '/v1/commands', body)

    def handle_text(self, text, client_id='client', session=None):
        """旧 handle_text 形状的统一路径实现；一次解释＋逐提案执行。"""
        status, result = self.interpret(text, session=session)
        if status != 200 or not isinstance(result, dict):
            return {'ok': False, 'channel': 'control', 'input': text,
                    'error': 'interpret failed (%s)' % status, 'detail': result}
        source = result.get('source')
        if source == 'direct_reply':
            reply = result.get('reply') or ''
            return {'ok': True, 'channel': 'control', 'input': text,
                    'matched': text, 'message': reply,
                    'detail': {'kind': 'reply', 'end_request': result.get('end_request')}}
        proposals = result.get('proposals') or []
        executable = [p for p in proposals if p.get('action')]
        if not executable:
            note = result.get('note') or result.get('speech') or ''
            return {'ok': False, 'channel': source or 'control', 'input': text,
                    'error': 'no executable proposal',
                    'detail': {'note': note, 'proposals': proposals}}
        results, messages, all_ok = [], [], True
        for proposal in executable:
            st, cmd = self.command(proposal['action'],
                                   params=proposal.get('params') or {},
                                   target=proposal.get('target'),
                                   client_id=client_id, session_id=session)
            results.append({'status': st, 'command': cmd})
            if isinstance(cmd, dict) and cmd.get('command'):
                c = cmd['command']
                ok = c.get('status') == 'succeeded'
                all_ok = all_ok and ok
                messages.append(c.get('message') or c.get('code') or '')
            else:
                all_ok = False
                messages.append('command failed')
        return {'ok': all_ok, 'channel': 'control', 'input': text,
                'message': '；'.join(m for m in messages if m),
                'detail': {'results': results,
                           'unmapped': [p for p in proposals if not p.get('action')]}}
