# -*- coding: utf-8 -*-
"""executor 统一路径端到端：真起控制服务，经 HA4FPAI_CONTROL_URL 走
/v1/interpret + /v1/commands，压的是**失败语义**而不是顺利路径。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_executor_bridge.py

覆盖：
  - 成功动作（现在几点）如实成功；
  - 否定句不执行任何设备动作；
  - 设备不可达时 executor 不假成功、不静默回退旧路径；
  - 令牌错误时如实报 401；
  - 服务不可达时 channel=control 且报 unreachable（关键：不许偷偷走旧双通道）；
  - dry_run=True 时绕过控制层（文档承诺的旁路）。
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'klar'))

from control.service import App, Handler  # noqa: E402
from control.adapters import video  # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def free_port():
    """拿一个当前没人监听的端口（用于「服务不可达」场景）。"""
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-executor-bridge-')
    server = None
    try:
        # 与 smoke 同理：断言「设备不可达」必须钉死一个必然关闭的端口，
        # 否则在板端（8091/8092/8093 真在跑）会假失败。
        _dead = 'http://127.0.0.1:%d' % free_port()
        video.configure(targets={'face': _dead, 'stream': _dead, 'record': _dead})
        app = App(os.path.join(tmp, 'db.sqlite3'), os.path.join(tmp, 'exports'),
                  token='bridge-token', media_root=os.path.join(tmp, 'media'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = app
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % server.server_address[1]
        time.sleep(0.2)

        import klar_executor as kex

        # ---- dry_run 旁路：配置了 URL 也必须绕过控制层 ----
        os.environ['HA4FPAI_CONTROL_URL'] = base
        os.environ['HA4FPAI_CONTROL_TOKEN'] = 'bridge-token'
        out = kex.handle_text('现在几点', dry_run=True)
        check(out.get('channel') != 'control', 'dry_run=True 绕过控制层（不写库不留痕）')

        # ---- 成功动作 ----
        out = kex.handle_text('现在几点')
        check(out.get('ok') is True and out.get('channel') == 'control'
              and (out.get('message') or ''), 'executor：现在几点 → 控制层成功且带回话')

        # ---- 否定句：不产生任何设备动作 ----
        out = kex.handle_text('不要开始录制')
        check(out.get('ok') is not True, 'executor：否定句不假成功')
        check('recording.start' not in str(out.get('detail') or {}),
              'executor：否定句未派发录制命令')

        # ---- 设备不可达：如实失败，不假成功 ----
        out = kex.handle_text('开始录制')
        text = str(out.get('message') or '') + str(out.get('error') or '')
        detail = str(out.get('detail') or {})
        check(out.get('ok') is False
              and ('视频服务不可达' in text or '没做成' in text
                   or 'video_unreachable' in text)
              and ('video_unreachable' in detail or 'failed' in detail),
              'executor：设备不可达 → 如实说原因（不假成功）')

        # ---- 令牌错误：如实 401 ----
        os.environ['HA4FPAI_CONTROL_TOKEN'] = 'wrong-token'
        out = kex.handle_text('现在几点')
        check(out.get('ok') is False and '401' in str(out.get('error') or ''),
              'executor：令牌错 → 如实报 401')
        os.environ['HA4FPAI_CONTROL_TOKEN'] = 'bridge-token'

        # ---- 服务不可达：不静默回退旧路径 ----
        os.environ['HA4FPAI_CONTROL_URL'] = 'http://127.0.0.1:%d' % free_port()
        out = kex.handle_text('现在几点')
        check(out.get('ok') is False and out.get('channel') == 'control'
              and 'unreachable' in str(out.get('error') or ''),
              'executor：控制服务不可达 → 报错且不偷偷走旧通道')
    finally:
        os.environ.pop('HA4FPAI_CONTROL_URL', None)
        os.environ.pop('HA4FPAI_CONTROL_TOKEN', None)
        if server is not None:
            server.shutdown()
            server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_executor_bridge: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())