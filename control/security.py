# -*- coding: utf-8 -*-
"""鉴权、凭据与网络边界（H03）：令牌文件、同源判据、日志脱敏。

规则（规划 §安全边界）：
  * 服务令牌文件原子写、权限 0600（posix），非空；轮换不回显旧值。
  * Origin 不是权限：写请求只认 Bearer；跨源一律拒（Origin 仅作额外 CSRF 约束）。
  * 日志/错误脱敏：已知密钥不得出现在文本或请求体里。
"""
import os
import secrets
import stat
import tempfile

MASK = '***'


class SecurityError(RuntimeError):
    pass


def _posix_mode(path):
    if os.name != 'posix':
        return None
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError as exc:
        raise SecurityError('无法读取令牌文件权限: %r' % exc)


def write_token_file(path, token=None, mode=0o600):
    """原子写入令牌（0600、非空）；返回写入的 token。"""
    token = token or secrets.token_urlsafe(32)
    if not token or len(token) < 16:
        raise SecurityError('令牌过短或为空')
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.token-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(token + '\n')
            f.flush()
            os.fsync(f.fileno())
        if os.name == 'posix':
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return token


def load_token_file(path, require_mode=True):
    """读取令牌并校验：非空、权限 0600（posix 且 require_mode）。"""
    if not os.path.isfile(path):
        raise SecurityError('令牌文件不存在: %s' % path)
    if require_mode:
        mode = _posix_mode(path)
        if mode is not None and mode != 0o600:
            raise SecurityError('令牌文件权限应为 0600，实际 %o' % mode)
    with open(path, 'r', encoding='utf-8') as f:
        token = f.read().strip()
    if not token:
        raise SecurityError('令牌文件为空')
    return token


def rotate_token(path):
    """轮换令牌（原子写新值）；返回新令牌。调用方负责让旧会话失效。"""
    return write_token_file(path)


def origin_allowed(origin, host, scheme='http'):
    """同源判据：无 Origin（非浏览器）允许；有则必须与 Host 同源。"""
    if not origin:
        return True
    return origin == '%s://%s' % (scheme, host) or origin == 'https://' + host


def redact(text, secrets_):
    out = text if isinstance(text, str) else str(text)
    for s in secrets_ or ():
        if s:
            out = out.replace(s, MASK)
    return out


def assert_no_secret(payload, secrets_):
    """确认请求体/日志不含已知密钥；命中即抛（不打印密钥本体）。"""
    text = payload if isinstance(payload, str) else str(payload)
    for s in secrets_ or ():
        if s and s in text:
            raise SecurityError('检测到凭据泄漏（已阻止）')
    return True
