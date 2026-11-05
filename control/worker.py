# -*- coding: utf-8 -*-
"""持久任务 worker（WP10 部署层）：周期触发 run_due，带 restore_epoch 栅栏。

规划 §命令、任务与重试：worker 与直接动作共用执行合同；旧世代不得领新任务
——worker 启动时记录 restore_epoch，每轮复核，发现世代变化即退出
（systemd Restart=always 会以新世代重启），绝不跨世代执行。
"""
import argparse
import os
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control import commands, ids, tasks  # noqa: E402
from control.events import EventLog  # noqa: E402
from control.service import App  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='HA4FPAI 控制层任务 worker')
    parser.add_argument('--db', default=os.path.join('data', 'business.sqlite3'))
    parser.add_argument('--exports-dir', default=os.path.join('data', 'exports'))
    parser.add_argument('--interval', type=int, default=60, help='轮询秒数（最小 30）')
    parser.add_argument('--once', action='store_true', help='跑一轮就退出（调试/手工）')
    args = parser.parse_args(argv)

    interval = max(30, int(args.interval))
    app = App(args.db, args.exports_dir, token=None)
    epoch = app.store.restore_epoch()
    print('task-worker started: store=%s epoch=%d interval=%ds'
          % (app.store.meta('store_id'), epoch, interval))
    while True:
        try:
            current = app.store.restore_epoch()
            if current != epoch:
                # 世代变化：本 worker 已是旧世代，立即退出交 systemd 重启
                print('restore_epoch changed %d -> %d; worker exits (fencing)'
                      % (epoch, current))
                return 3
            results = tasks.run_due(app.store, app.registry, app.events, commands)
            if results:
                print('run_due: %d due -> %s'
                      % (len(results), [(r['task_id'][:8], r['state']) for r in results]))
        except KeyboardInterrupt:
            return 0
        except Exception as exc:                      # noqa: BLE001
            # 单轮失败不致命；记下轮再试。持续失败由 systemd 日志暴露。
            print('run_due error: %s: %s' % (type(exc).__name__, exc))
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    sys.exit(main())
