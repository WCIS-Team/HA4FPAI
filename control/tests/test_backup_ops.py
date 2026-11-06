# -*- coding: utf-8 -*-
"""B05/B06 验收：在线备份/校验/恢复世代 + WAL/磁盘运维。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_backup_ops.py
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

from control import backup, ops, persons               # noqa: E402
from control.events import EventLog                    # noqa: E402
from control.store import Store                        # noqa: E402

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
    except Exception as exc:                            # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-backup-')
    try:
        db = os.path.join(tmp, 'business.sqlite3')
        media = os.path.join(tmp, 'media')
        os.makedirs(media)
        with open(os.path.join(media, 'clip.bin'), 'wb') as f:
            f.write(b'\x00\x01\x02' * 100)
        store = Store(db)
        ev = EventLog(store)
        p1 = persons.create(store, ev, '甲')
        epoch0 = store.restore_epoch()

        # ---- B05 备份（在线）+ manifest + verify ----
        bdir = os.path.join(tmp, 'backup1')
        man = backup.backup(store, bdir, media_root=media)
        check(os.path.exists(os.path.join(bdir, 'manifest.json')),
              'B05：写 manifest.json')
        check(man['db_sha256'] and man['schema_version'] == store.meta('schema_version')
              and man['restore_epoch'] == epoch0,
              'B05：manifest 记 sha/schema_version/restore_epoch')
        check(any(m['path'] == 'clip.bin' and m['sha256'] for m in man['media']),
              'B05：媒体清单登记路径与 sha256')
        v = backup.verify(bdir)
        check(v['_verified_sha256'] == man['db_sha256'], 'B05：verify 通过且 sha 一致')

        # 备份可独立打开且 FK 完整
        ro = sqlite3.connect(os.path.join(bdir, man['db_file']))
        integ = ro.execute('PRAGMA integrity_check').fetchone()[0]
        fk = ro.execute('PRAGMA foreign_key_check').fetchall()
        n_bk = ro.execute('SELECT COUNT(*) FROM persons').fetchone()[0]
        ro.close()
        check(integ == 'ok' and fk == [] and n_bk == 1,
              'B05：备份库可开、integrity ok、fk 空、人数=1')

        # ---- B05 恢复：回滚数据并提升世代 ----
        persons.create(store, ev, '乙')       # 备份后又加一人
        n_now = store.query_one('SELECT COUNT(*) AS n FROM persons')['n']
        check(n_now == 2, 'B05：恢复前 live 有 2 人（前置）')
        store.conn.close()
        res = backup.restore(db, bdir)
        check(res['epoch_after'] == epoch0 + 1,
              'B05：恢复后 restore_epoch +1（%s→%s）' % (epoch0, res['epoch_after']))
        store2 = Store(db)
        n_restored = store2.query_one('SELECT COUNT(*) AS n FROM persons')['n']
        check(n_restored == 1, 'B05：恢复后回滚到 1 人（旧新数据不残留）')
        check(store2.restore_epoch() == epoch0 + 1, 'B05：世代已写入新库')
        store2.conn.close()

        # ---- B05 篡改/损坏拒绝恢复 ----
        tamper = os.path.join(tmp, 'backup_tamper')
        shutil.copytree(bdir, tamper)
        mpath = os.path.join(tamper, 'manifest.json')
        with open(mpath, 'r', encoding='utf-8') as f:
            mm = json.load(f)
        with open(os.path.join(tamper, mm['db_file']), 'ab') as f:
            f.write(b'X')                       # 追加一字节 → sha 变
        raises(lambda: backup.verify(tamper), backup.BackupInvalid,
               'B05：sha 被篡改 → 拒绝')
        corrupt = os.path.join(tmp, 'backup_corrupt')
        shutil.copytree(bdir, corrupt)
        with open(os.path.join(corrupt, mm['db_file']), 'wb') as f:
            f.write(b'not a database')          # 彻底坏
        # 同步 manifest 的 sha 使其绕过 sha 检查，逼出 integrity 分支
        import hashlib
        h = hashlib.sha256(open(os.path.join(corrupt, mm['db_file']), 'rb').read()).hexdigest()
        mm['db_sha256'] = h
        with open(os.path.join(corrupt, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(mm, f)
        raises(lambda: backup.verify(corrupt), backup.BackupInvalid,
               'B05：坏库（integrity）→ 拒绝')

        # ---- B06 checkpoint / disk / 错误映射 / healthy ----
        s = Store(os.path.join(tmp, 'ops.sqlite3'))
        ev2 = EventLog(s)
        for i in range(20):
            persons.create(s, ev2, '人%d' % i)
        ck = ops.checkpoint(s, 'PASSIVE')
        check(ck['mode'] == 'PASSIVE' and ck['busy'] in (0, 1)
              and 'wal_bytes' in ck, 'B06：PASSIVE checkpoint 返回 (busy,log,done)+wal')
        ck2 = ops.checkpoint(s, 'TRUNCATE')
        check(ck2['mode'] == 'TRUNCATE', 'B06：TRUNCATE checkpoint 可执行')
        raises(lambda: ops.checkpoint(s, 'BOGUS'), ValueError, 'B06：非法 mode 拒绝')
        d = ops.disk_stat(s.path)
        check(d['total_bytes'] > 0 and d['free_bytes'] >= 0, 'B06：disk_stat 读数')
        check(ops.map_sqlite_error(sqlite3.OperationalError('database is locked')) == 'db_locked',
              'B06：locked→db_locked')
        check(ops.map_sqlite_error(sqlite3.OperationalError('database or disk is full')) == 'db_full',
              'B06：full→db_full')
        check(ops.map_sqlite_error(
            sqlite3.OperationalError('attempt to write a readonly database')) == 'db_readonly',
            'B06：readonly→db_readonly')
        check(ops.map_sqlite_error(sqlite3.DatabaseError('disk I/O error')) == 'db_io',
              'B06：ioerr→db_io')
        # 长读者：PASSIVE 不阻塞（busy 仍 0），但无法全量 checkpoint —— checkpointed<log_pages
        reader = sqlite3.connect(s.path, isolation_level=None)
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM persons').fetchone()   # 读者持有旧快照
        persons.create(s, ev2, '触发帧')                             # 之后追加 WAL 帧
        ck_busy = ops.checkpoint(s, 'PASSIVE')
        check(ck_busy['checkpointed'] < ck_busy['log_pages'],
              'B06：长读者在 → PASSIVE 不能全量 checkpoint（%d<%d 可观测）'
              % (ck_busy['checkpointed'], ck_busy['log_pages']))
        reader.execute('COMMIT')
        reader.close()
        ck_after = ops.checkpoint(s, 'PASSIVE')
        check(ck_after['checkpointed'] == ck_after['log_pages'],
              'B06：读者释放后可全量 checkpoint')
        # 内存库不允许在线备份
        mem = Store(':memory:')
        raises(lambda: backup.backup(mem, os.path.join(tmp, 'membk')), backup.BackupError,
               'B05：内存库拒绝备份')
        mem.conn.close()
        h = ops.healthy(s, thresholds={'min_free_bytes': 10 ** 18, 'wal_warn_bytes': 0})
        check(h['ok'] is False
              and any(w.startswith('disk_free_below_min') for w in h['warnings']),
              'B06：阈值越界 → warnings 暴露')
        h2 = ops.healthy(s, thresholds={'min_free_bytes': 1, 'wal_warn_bytes': 10 ** 12})
        check(h2['ok'] is True and h2['warnings'] == [], 'B06：正常阈值 → ok')
        s.conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_backup_ops: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
