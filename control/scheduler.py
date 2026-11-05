# -*- coding: utf-8 -*-
"""同进程持久任务调度（A02）：到期内建 run_due，不再依赖独立 worker 进程。

目标架构（docs/REFACTOR-ARCHITECTURE.md §2）：调度循环住在控制服务进程里，
共享同一个 App（同一条写路径）；独立 worker 直写业务库与单写者冲突，已按
清单 A02 废弃为默认部署方式（`control/worker.py` 保留仅作 --once 手工
对账工具，不再部署为常驻服务单元）。

栅栏语义保留：每轮先读 restore_epoch；与启动时不同 → 本循环停止并以
退出码 3 通知外层（systemd Restart 会以新世代重启整个控制服务）。
"""
import threading
import time


class Scheduler(object):
    def __init__(self, app, interval=60):
        self.app = app
        self.interval = max(30, int(interval))
        self.stop_event = threading.Event()
        self.thread = None
        self.epoch = app.store.restore_epoch()
        self.last_results = []
        self.last_error = None
        self.last_run_at = 0.0
        self.exit_requested = False

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='task-scheduler',
                                       daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout=10):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def status(self):
        return {'interval_s': self.interval, 'epoch': self.epoch,
                'last_run_at': self.last_run_at,
                'last_results': self.last_results,
                'last_error': self.last_error,
                'exit_requested': self.exit_requested,
                'running': self.thread is not None and self.thread.is_alive()}

    def _loop(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.run_once()
            except Exception as exc:                       # noqa: BLE001
                self.last_error = '%s: %s' % (type(exc).__name__, exc)

    def run_once(self):
        current = self.app.store.restore_epoch()
        if current != self.epoch:
            # 世代已变：停止本调度线程并请求服务整体退出，
            # 由 systemd Restart=always 以新世代重启（跨世代栅栏）。
            self.exit_requested = True
            self.stop_event.set()
            return []
        from control import commands, tasks
        results = tasks.run_due(self.app.store, self.app.registry,
                                self.app.events, commands)
        self.last_results = results
        self.last_error = None
        self.last_run_at = time.time()
        return results
