# -*- coding: utf-8 -*-
"""B01–B04 验收：结构约束迁移、坏行隔离、直 SQL 拒绝、入口范围校验。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_schema_constraints.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import persons, samples                       # noqa: E402
from control.errors import ConflictError                   # noqa: E402
from control.events import EventLog                        # noqa: E402
from control.schema import MIGRATIONS                      # noqa: E402
from control.settings import Settings                      # noqa: E402
from control.store import Store                            # noqa: E402

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
    except Exception as exc:                                # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def v1_only_sql():
    """返回 MIGRATION_1 的 SQL（模拟旧库；从正本取，避免复制漂移）。"""
    return dict(MIGRATIONS)[1]


def build_v1(path):
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in [s.strip() for s in v1_only_sql().split(';') if s.strip()]:
        conn.execute(statement)
    # 与真实 1.0 库一致：登记 MIGRATION_1 的版本与 checksum（否则 Store 视其为未迁移）
    import hashlib
    from control.schema import MIGRATION_1
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations('
                 'version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)')
    checksum = hashlib.sha256(MIGRATION_1.encode('utf-8')).hexdigest()
    conn.execute('INSERT INTO schema_migrations(version, applied_at, checksum) VALUES(1,?,?)',
                 ('2026-11-05T00:00:00+00:00', checksum))
    return conn


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-schema-')
    try:
        # ---- B01：干净 v1 库升级，数据不丢 ----
        p1 = os.path.join(tmp, 'v1-clean.sqlite3')
        conn = build_v1(p1)
        now = '2026-11-06T00:00:00+00:00'
        pid1 = 'a' * 31 + '1'
        pid2 = 'b' * 31 + '2'
        conn.execute('INSERT INTO persons VALUES(?,?,?,?,?,?,?)',
                     (pid1, '张三', 'active', 1, now, now, None))
        conn.execute('INSERT INTO persons VALUES(?,?,?,?,?,?,?)',
                     (pid2, '李四', 'inactive', 3, now, now, None))
        conn.execute("INSERT INTO models VALUES('m-voice','voice',192,'float32-le','cosine',"
                     "'%064d',NULL,NULL,NULL,NULL,NULL,NULL,?)" % (1,),
                     (now,))
        blob = b'\x00' * (192 * 4)
        conn.execute('INSERT INTO voice_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('s' * 31 + '1', pid1, 'm-voice', 192, 'float32-le', blob,
                      '0' * 64, None, None, None, 1, now, None))
        conn.execute('INSERT INTO commands(command_id, request_id, idempotency_key, action,'
                     ' params_json, params_digest, status, created_at, updated_at)'
                     ' VALUES(?,?,?,?,?,?,?,?,?)',
                     ('c' * 31 + '1', 'r' * 31 + '1', 'k1', 'system.time', '{}',
                      '0' * 64, 'succeeded', now, now))
        conn.close()

        store = Store(p1)
        persons_n = store.query_one('SELECT COUNT(*) AS n FROM persons')['n']
        models_n = store.query_one('SELECT COUNT(*) AS n FROM models')['n']
        voice_n = store.query_one('SELECT COUNT(*) AS n FROM voice_samples')['n']
        cmd_n = store.query_one('SELECT COUNT(*) AS n FROM commands')['n']
        check(persons_n == 2 and models_n == 1 and voice_n == 1 and cmd_n == 1,
              'B01：干净 v1 库升级后行数不丢（2/1/1/1）')
        check(store.meta('schema_version') == str(max(v for v, _ in MIGRATIONS)), 'B01：schema_version 到最新')
        rejects = store.query_one('SELECT COUNT(*) AS n FROM persons_rejects')['n']
        check(rejects == 0, 'B01：合法数据零隔离行')

        # ---- B01：坏行隔离 ----
        p2 = os.path.join(tmp, 'v1-dirty.sqlite3')
        conn = build_v1(p2)
        conn.execute("INSERT INTO persons VALUES('ccccccccccccccccccccccccccccccc1','王五','active',1,?,?,'')",
                     (now, now))
        conn.execute("INSERT INTO persons VALUES('ddddddddddddddddddddddddddddddd2','坏状态','zombie',1,?,?,'')",
                     (now, now))                                # 非法 status
        conn.execute("INSERT INTO models VALUES('m-bad','voice',0,'float32-le','cosine',"
                     "NULL,NULL,NULL,NULL,NULL,NULL,NULL,?)", (now,))   # 非法 dim
        conn.close()
        store2 = Store(p2)
        good_n = store2.query_one("SELECT COUNT(*) AS n FROM persons WHERE status='active'")['n']
        zombie = store2.query_one("SELECT COUNT(*) AS n FROM persons WHERE status='zombie'")['n']
        check(good_n == 1 and zombie == 0, 'B01：坏状态行未迁入（合法行保留）')
        rows = store2.query("SELECT entity, key FROM persons_rejects ORDER BY entity")
        got = sorted((r['entity'], r['key']) for r in rows)
        check(('models', 'm-bad') in got and ('persons', 'ddddddddddddddddddddddddddddddd2') in got,
              'B01：坏行进入 persons_rejects 可审计（models+persons）')
        dim0 = store2.query_one('SELECT COUNT(*) AS n FROM models')['n']
        check(dim0 == 0, 'B01：dim<=0 的模型未迁入')

        # ---- B02：直 SQL 绕过 Python 也被 CHECK 拒绝 ----
        s = Store(os.path.join(tmp, 'fresh.sqlite3'))
        ev = EventLog(s)
        person = persons.create(s, ev, '测试人')
        pid = person['person_id']
        raises(lambda: s.conn.execute(
            'INSERT INTO persons(person_id, display_name, status, revision, created_at, updated_at)'
            ' VALUES(?,?,?,?,datetime("now"),datetime("now"))',
            ('e' * 31 + '1', '直插', 'zombie', 1)).fetchall(),
            sqlite3.IntegrityError, 'B02：直 SQL 非法 status 被 CHECK 拒绝')
        raises(lambda: s.conn.execute(
            'INSERT INTO persons(person_id, display_name, status, revision, created_at, updated_at)'
            ' VALUES(?,?,?,?,datetime("now"),datetime("now"))',
            ('f' * 31 + '1', '负版本', 'active', -1)).fetchall(),
            sqlite3.IntegrityError, 'B02：直 SQL revision<0 被拒绝')
        raises(lambda: s.conn.execute(
            "INSERT INTO models VALUES('m-x','nail',10,'float32-le','cosine',NULL,"
            "NULL,NULL,NULL,NULL,NULL,NULL,datetime('now'))").fetchall(),
            sqlite3.IntegrityError, 'B02：直 SQL 非法 modality 被拒绝')

        # ---- B03：样本 BLOB 结构约束 ----
        samples.register_model(s, ev, 'campplus-test', 'voice', 192)
        vec = [0.001] * 192
        sample = samples.enroll(s, ev, 'voice', pid, 'campplus-test', vec)
        check(sample is not None, 'B03：合法向量入库（前置）')
        blob_len = s.query_one('SELECT length(blob) AS n FROM voice_samples'
                               ' WHERE sample_id=?', (sample['sample_id'],))['n']
        check(blob_len == 192 * 4, 'B03：float32-le BLOB 长度 = dim×4')
        # dim≠length(blob) 的跨列语义在写入口保证（samples.enroll dim_mismatch 覆盖）；
        # 库层单列 CHECK 管的是 dtype/typeof/active：
        raises(lambda: samples.enroll(s, ev, 'voice', pid, 'campplus-test', [0.5] * 10),
               ConflictError, 'B03：入口拒绝 dim≠模型维度 的向量（dim_mismatch）')
        raises(lambda: s.conn.execute(
            'INSERT INTO voice_samples(sample_id, person_id, model_id, dim, dtype, blob,'
            ' blob_sha256, active, created_at) VALUES(?,?,?,?,?,?,?,?,datetime("now"))',
            ('g' * 31 + '1', pid, 'campplus-test', 192, 'float32-le', b'\x00' * 40,
             '1' * 64, 1)).fetchall(),
            sqlite3.IntegrityError, 'B03：直 SQL 截断 BLOB（length≠dim×4）被库拒')
        raises(lambda: s.conn.execute(
            'INSERT INTO voice_samples(sample_id, person_id, model_id, dim, dtype, blob,'
            ' blob_sha256, active, created_at) VALUES(?,?,?,?,?,?,?,?,datetime("now"))',
            ('h' * 31 + '1', pid, 'campplus-test', 192, 'float16-le', b'\x00' * 768,
             '2' * 64, 1)).fetchall(),
            sqlite3.IntegrityError, 'B03：非法 dtype（float16-le）被库拒')
        raises(lambda: s.conn.execute(
            'INSERT INTO voice_samples(sample_id, person_id, model_id, dim, dtype, blob,'
            ' blob_sha256, active, created_at) VALUES(?,?,?,?,?,?,?,?,datetime("now"))',
            ('i' * 31 + '1', pid, 'campplus-test', 192, 'float32-le', 'not-a-blob',
             '3' * 64, 1)).fetchall(),
            sqlite3.IntegrityError, 'B03：非 BLOB 类型被 typeof CHECK 拒绝')
        # active 只能是 0/1（直 SQL 验证）
        raises(lambda: s.conn.execute(
            'UPDATE voice_samples SET active=2 WHERE sample_id=?',
            (sample['sample_id'],)).fetchall(),
            sqlite3.IntegrityError, 'B03：active 越界 UPDATE 被拒绝')

        # ---- B04：命令/设置 JSON 与数值范围 ----
        raises(lambda: s.conn.execute(
            'INSERT INTO commands(command_id, request_id, idempotency_key, action,'
            ' params_json, params_digest, status, created_at, updated_at)'
            ' VALUES(?,?,?,?,?,?,?,?,datetime("now"))',
            ('j' * 31 + '1', 'r' * 31 + '2', 'k-bad', 'system.time', 'not-json',
             '0' * 64, 'accepted', now)).fetchall(),
            sqlite3.IntegrityError, 'B04：commands.params_json 非 JSON 被库拒')
        raises(lambda: s.conn.execute(
            'INSERT INTO commands(command_id, request_id, idempotency_key, action,'
            ' params_json, params_digest, status, created_at, updated_at)'
            ' VALUES(?,?,?,?,?,?,?,?,datetime("now"))',
            ('k' * 31 + '1', 'r' * 31 + '3', 'k-bad2', 'system.time', '{}',
             '0' * 64, 'done', now)).fetchall(),
            sqlite3.IntegrityError, 'B04：commands.status 非法值被库拒')

        st = Settings(s)
        raises(lambda: st.update({'audio.output.volume': -5}), ConflictError,
              'B04：音量低于 0 被入口拒绝')
        raises(lambda: st.update({'audio.output.volume': 150}), ConflictError,
              'B04：音量高于 100 被入口拒绝')
        raises(lambda: st.update({'face.match.threshold': 1.5}), ConflictError,
              'B04：阈值超 [0,1] 被入口拒绝')
        st.update({'audio.output.volume': 80})
        row = s.query_one("SELECT value FROM settings WHERE key='audio.output.volume'")
        check(json.loads(row['value']) == 80.0
              and s.query_one('SELECT json_valid(value) AS v FROM settings'
                              " WHERE key='audio.output.volume'")['v'] == 1,
              'B04：合法设置入库且 value 恒为合法 JSON')
        raises(lambda: s.conn.execute(
            "UPDATE settings SET value='not-json' WHERE key='audio.output.volume'").fetchall(),
            sqlite3.IntegrityError, 'B04：直 SQL 把设置改成非 JSON 被库拒')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_schema_constraints: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())