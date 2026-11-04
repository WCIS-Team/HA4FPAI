# -*- coding: utf-8 -*-
"""SQLite 业务主库：连接配置、版本化迁移与 store 世代元数据。

单写入口 = 本对象的一把进程内写锁 + BEGIN IMMEDIATE 短事务；
这是**资料提交层**的串行，不是音视频全局锁（规划 §模块与写入所有权）。
跨进程部署时业务主库只能由控制服务一个进程打开。
事务内禁止等待 HTTP / NPU / TTS / 媒体拷贝等外部资源。
"""
import contextlib
import hashlib
import sqlite3
import threading

from . import ids
from .schema import MIGRATIONS


def _split_statements(sql):
    return [s.strip() for s in sql.split(';') if s.strip()]


class Store(object):
    def __init__(self, path):
        self.path = str(path)
        self.write_lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()
        self._ensure_meta()

    def _configure(self):
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=FULL')

    def _migrate(self):
        with self.tx() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations('
                         'version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)')
            done = dict((r['version'], r['checksum']) for r in
                        conn.execute('SELECT version, checksum FROM schema_migrations').fetchall())
            for version, sql in MIGRATIONS:
                checksum = hashlib.sha256(sql.encode('utf-8')).hexdigest()
                if version in done:
                    if done[version] != checksum:
                        raise RuntimeError('schema drift at migration %d' % version)
                    continue
                for statement in _split_statements(sql):
                    conn.execute(statement)
                conn.execute('INSERT INTO schema_migrations(version, applied_at, checksum) VALUES(?,?,?)',
                             (version, ids.now_iso(), checksum))

    def _ensure_meta(self):
        with self.tx() as conn:
            row = conn.execute("SELECT value FROM store_meta WHERE key='store_id'").fetchone()
            if row is None:
                for key, value in [
                    ('store_id', ids.new_id()),
                    ('schema_version', str(max(v for v, _ in MIGRATIONS))),
                    ('generation', '1'),
                    ('restore_epoch', '1'),
                    ('domain_revision', '0'),
                    ('write_protocol', '1'),
                ]:
                    conn.execute('INSERT INTO store_meta(key, value) VALUES(?,?)', (key, value))

    @contextlib.contextmanager
    def tx(self):
        """短写事务；出现异常回滚，正常提交。"""
        with self.write_lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
            else:
                self.conn.execute('COMMIT')

    def query(self, sql, args=()):
        with self.write_lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def query_one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    # ---- store_meta 助手（避免依赖 SQLite upsert）----
    def meta(self, key, default=None):
        row = self.query_one('SELECT value FROM store_meta WHERE key=?', (key,))
        return row['value'] if row else default

    def _meta_set(self, conn, key, value):
        cur = conn.execute('UPDATE store_meta SET value=? WHERE key=?', (str(value), key))
        if cur.rowcount == 0:
            conn.execute('INSERT INTO store_meta(key, value) VALUES(?,?)', (key, str(value)))

    def bump_domain_revision(self, conn):
        """资料域版本号：persons/entities/settings 等权威变更时同事务递增。"""
        row = conn.execute("SELECT value FROM store_meta WHERE key='domain_revision'").fetchone()
        value = int(row['value']) + 1 if row else 1
        self._meta_set(conn, 'domain_revision', value)
        return value

    def restore_epoch(self):
        return int(self.meta('restore_epoch', '1'))

    def bump_restore_epoch(self):
        """恢复旧备份时递增；旧世代 worker / 迟到回执不得推进新世代状态。"""
        with self.tx() as conn:
            row = conn.execute("SELECT value FROM store_meta WHERE key='restore_epoch'").fetchone()
            value = (int(row['value']) + 1) if row else 1
            self._meta_set(conn, 'restore_epoch', value)
        return value

    def identity(self):
        """只读身份信息：状态页必须能区分数据世代。"""
        return {k: self.meta(k) for k in
                ('store_id', 'generation', 'restore_epoch', 'domain_revision', 'schema_version')}

    def integrity(self):
        row = self.query_one('PRAGMA integrity_check')
        return row['integrity_check'] if row else 'unknown'

    def foreign_key_check(self):
        return self.query('PRAGMA foreign_key_check')
