# -*- coding: utf-8 -*-
"""H03 验收：令牌文件、同源判据、日志脱敏。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_h_security.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import security   # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def raises(fn, exc_type, name):
    try:
        fn()
        check(False, name + '（未抛出）')
    except exc_type:
        check(True, name)
    except Exception as exc:                 # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-sec-')
    try:
        tokpath = os.path.join(tmp, 'token')
        tok = security.write_token_file(tokpath)
        check(len(tok) >= 16 and os.path.isfile(tokpath), 'H03：原子写令牌文件')
        check(security.load_token_file(tokpath) == tok, 'H03：读回令牌一致')
        if os.name == 'posix':
            import stat as _st
            mode = _st.S_IMODE(os.stat(tokpath).st_mode)
            check(mode == 0o600, 'H03：令牌文件权限 0600')
            os.chmod(tokpath, 0o644)
            raises(lambda: security.load_token_file(tokpath), security.SecurityError,
                   'H03：权限放宽到 0644 → 拒绝加载')
        # 空令牌拒绝
        empty = os.path.join(tmp, 'empty')
        with open(empty, 'w') as f:
            f.write('\n')
        raises(lambda: security.load_token_file(empty, require_mode=False),
               security.SecurityError, 'H03：空令牌拒绝')

        # 轮换
        new = security.rotate_token(tokpath)
        check(new != tok and security.load_token_file(tokpath) == new, 'H03：轮换后读回新值')

        # 同源判据（Origin 不是权限，只做同源约束）
        check(security.origin_allowed(None, 'h') is True, 'H03：无 Origin（非浏览器）允许')
        check(security.origin_allowed('http://h', 'h') is True, 'H03：同源允许')
        check(security.origin_allowed('http://evil', 'h') is False, 'H03：跨源拒绝')

        # 脱敏
        secret = 'sk-ABC-123'
        text = 'token=%s end' % secret
        red = security.redact(text, [secret])
        check(secret not in red and security.MASK in red, 'H03：日志脱敏不含密钥')
        raises(lambda: security.assert_no_secret({'k': secret}, [secret]),
               security.SecurityError, 'H03：请求体含密钥被拦')
        check(security.assert_no_secret({'k': 'ok'}, [secret]) is True,
              'H03：无密钥通过')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_h_security: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
