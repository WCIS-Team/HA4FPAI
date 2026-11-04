# -*- coding: utf-8 -*-
"""统一错误类型：HTTP 状态、机器码与人类消息分离。"""


class DomainError(Exception):
    """带机器码的业务错误；HTTP 层按 http_status 回包。"""

    http_status = 400

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}

    def to_json(self):
        return {'error': {'code': self.code, 'message': self.message, 'data': self.data}}


class ConflictError(DomainError):
    http_status = 409


class NotFoundError(DomainError):
    http_status = 404


class AuthError(DomainError):
    http_status = 401


class ForbiddenError(DomainError):
    http_status = 403


class ConfirmationRequired(DomainError):
    """危险动作没有绑定确认。确认必须绑定动作、参数摘要、主体与有效期。"""

    http_status = 403

    def __init__(self, action, message='该动作为危险操作，需要先创建确认'):
        super().__init__(
            'confirmation_required', message,
            {'action': action,
             'howto': "POST /v1/confirmations {'action':..., 'params':...} 取得 confirmation_id 后放进命令"})


class UnknownOutcome(Exception):
    """域执行后无法确认是否生效；必须先查询真实状态，不得盲目重试。"""

    def __init__(self, operation_id, detail=''):
        super().__init__(detail or ('unknown outcome for ' + operation_id))
        self.operation_id = operation_id
        self.detail = detail


class VideoDomainNotConnected(DomainError):
    """视频域适配器留空：能力存在定义，但执行端未接（本分支有意为之）。"""

    http_status = 501

    def __init__(self, capability, message=None):
        super().__init__(
            'video_domain_not_connected',
            message or ('视频域能力 %s 尚未接入（本分支有意留空）' % capability),
            {'capability': capability,
             'connect_doc': 'docs/VIDEO-DOMAIN-PLACEHOLDER.md'})
