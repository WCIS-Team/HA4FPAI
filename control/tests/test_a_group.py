# -*- coding: utf-8 -*-
"""A 组阻断项验收：A02 单写者内置调度、A03 包/单元布局、A05 PRAGMA 门禁。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_a_group.py

不执行 lang/npuserv；不连板端真实服务。
"""
import io
import gzip
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control.service import App, Handler  # noqa: E402
from control.store import Store  # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def request(base, method, path, body=None, token=None):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode('utf-8', 'replace') or '{}')


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-a-group-')
    server = None
    try:
        # ---- A05：文件库门禁读回 ----
        store = Store(os.path.join(tmp, 'a.sqlite3'))
        actual = store.verify_pragmas(file_db=True)
        check(actual['journal_mode'] == 'wal' and actual['synchronous'] == 2
              and actual['busy_timeout'] == 5000 and actual['foreign_keys'] == 1,
              'A05：文件库 PRAGMA 读回 wal/2/5000/1')
        # 篡改场景：新建库关掉 FULL 后必须被门禁拒绝
        bad = Store(os.path.join(tmp, 'bad.sqlite3'))
        bad.conn.execute('PRAGMA synchronous=NORMAL')
        rejected = False
        try:
            bad.verify_pragmas(file_db=True)
        except RuntimeError:
            rejected = True
        check(rejected, 'A05：synchronous 降级被启动门禁拒绝')

        # ---- A02：内置调度共享同一 App/写路径，任务到期被执行 ----
        app = App(os.path.join(tmp, 'b.sqlite3'), os.path.join(tmp, 'exports'),
                  token='a-token', media_root=os.path.join(tmp, 'media'),
                  scheduler_interval=0)          # App 不自动建；显式构造以便控时
        from control import tasks
        from control.scheduler import Scheduler
        app.scheduler = Scheduler(app, interval=0)
        row = tasks.create(app.store, app.registry, 'system.time', {},
                           run_at='2026-01-01T00:00:00')
        results = app.scheduler.run_once()
        check(len(results) == 1 and results[0]['task_id'] == row['task_id']
              and results[0]['state'] == 'succeeded',
              'A02：内置调度 run_once 执行到期任务')
        cmd_row = app.store.query_one(
            'SELECT principal_id FROM commands ORDER BY created_at DESC LIMIT 1')
        check(cmd_row is not None and cmd_row['principal_id'] == 'task-worker',
              'A02：任务命令经同一 App/写路径落库')
        sched = app.scheduler
        sched.run_once()
        again = [r for r in sched.last_results if r['task_id'] == row['task_id']]
        check(again == [], 'A02：同一 occurrence 不重复执行（幂等）')
        check(sched.status()['running'] is False and sched.exit_requested is False,
              'A02：未启动线程时 status 如实（不含幽灵线程）')

        # 世代栅栏：bump epoch 后 run_once 请求退出
        app.store.bump_restore_epoch()
        app.scheduler.run_once()
        check(app.scheduler.exit_requested is True,
              'A02：restore_epoch 变化 → 调度请求整体退出（栅栏保留）')

        # ---- A02/A03：服务真实启动即带调度；/healthz /v1/status 暴露调度状态 ----
        app2 = App(os.path.join(tmp, 'c.sqlite3'), os.path.join(tmp, 'exports2'),
                   token='a-token', media_root=os.path.join(tmp, 'media2'),
                   scheduler_interval=30)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = app2
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % server.server_address[1]
        time.sleep(0.3)
        status, obj = request(base, 'GET', '/healthz')
        check(status == 200 and obj.get('ok') is True
              and obj.get('scheduler', {}).get('running') is True,
              'A02：/healthz 如实报告内置调度线程运行中')
        status, obj = request(base, 'GET', '/v1/status')
        sched_status = obj.get('scheduler') or {}
        check(status == 200 and sched_status.get('interval_s') == 30
              and sched_status.get('epoch') == app2.store.restore_epoch(),
              'A02：/v1/status 暴露调度间隔与世代')

        # ---- A03：包/单元布局一致性 ----
        unit = open(os.path.join(ROOT, 'control', 'deploy',
                                 'ha4fpai-control.service'), encoding='utf-8').read()
        check('WorkingDirectory=/usr/lib/ha4fpai-control' in unit
              and '--scheduler-interval' in unit
              and '--token-file /etc/ha4fpai-control/token' in unit,
              'A03：control 单元指向 /usr/lib 布局＋内置调度＋/etc 令牌')
        worker = open(os.path.join(ROOT, 'control', 'deploy',
                                   'ha4fpai-task-worker.service'), encoding='utf-8').read()
        check('Type=oneshot' in worker and 'WantedBy=' in worker
              and 'DEPRECATED' in worker,
              'A03：旧 worker 单元降级为 onshot 手工工具（不再常驻）')
        postinst = open(os.path.join(ROOT, 'control', 'packaging', 'debian', 'postinst'),
                        encoding='utf-8').read()
        check('/etc/ha4fpai-control' in postinst
              and 'ha4fpai-task-worker' in postinst,
              'A03：postinst 建 /etc 令牌目录并禁用误启的旧 worker 单元')
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_a_group: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())