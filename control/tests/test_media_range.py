# -*- coding: utf-8 -*-
"""F05/F06 验收：媒体真实性校验、Range/鉴权、对账。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_media_range.py
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import media                    # noqa: E402
from control.errors import ConflictError     # noqa: E402
from control.store import Store              # noqa: E402

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


def png(width=4, height=4, total=40):
    head = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + \
        struct.pack('>II', width, height) + b'\x08\x02\x00\x00\x00'
    return (head + b'\x00' * max(0, total - len(head)))[:max(total, len(head))]


def wav(payload_len, byte_rate):
    head = b'RIFF' + b'\x00' * 4 + b'WAVE' + b'fmt ' + b'\x00' * 4
    head = head.ljust(32, b'\x00')
    head = head[:28] + struct.pack('<I', byte_rate) + head[32:]
    return head + b'\x00' * payload_len


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-media-')
    try:
        # ---- F05 真实编码 ----
        check(media.sniff(png()) == 'png', 'F05：PNG 魔数识别')
        check(media.validate_payload(png(), 'image/png')['width'] == 4,
              'F05：合法 PNG 通过并读尺寸')
        raises(lambda: media.validate_payload(b'\x00' * 40, 'image/png'),
               ConflictError, 'F05：伪 PNG（无魔数）拒绝')
        raises(lambda: media.validate_payload(png(), 'audio/wav'),
               ConflictError, 'F05：声明与实际编码不符拒绝')
        raises(lambda: media.validate_payload(png(10000, 10000, 40), 'image/png'),
               ConflictError, 'F05：像素超解码预算拒绝')
        raises(lambda: media.validate_payload(wav(2000, 1), 'audio/wav'),
               ConflictError, 'F05：WAV 时长超预算拒绝')
        raises(lambda: media.validate_payload(b'x' * 100, 'image/png', max_bytes=50),
               ConflictError, 'F05：超大小拒绝')

        store = Store(os.path.join(tmp, 'b.sqlite3'))
        mroot = os.path.join(tmp, 'media')
        raises(lambda: media.save_upload(store, mroot, b'\x00' * 40, 'image/png'),
               ConflictError, 'F05：save_upload 对伪 PNG 拒绝落盘')
        row = media.save_upload(store, mroot, png(), 'image/png')
        check(row['kind'] == 'image/png' and row['size_bytes'] == len(png()),
              'F05：合法上传入库')

        # ---- F06 Range ----
        check(media.parse_range(None, 100) is None, 'F06：无 Range 头')
        r = media.parse_range('bytes=0-9', 100)
        check(r['start'] == 0 and r['end'] == 9, 'F06：start-end')
        r = media.parse_range('bytes=90-', 100)
        check(r['start'] == 90 and r['end'] == 99, 'F06：open-ended')
        r = media.parse_range('bytes=-10', 100)
        check(r['start'] == 90 and r['suffix'] is True, 'F06：后缀范围')
        check(media.parse_range('bytes=200-300', 100).get('unsatisfiable') is True,
              'F06：越界 → 416')
        check(media.parse_range('bytes=abc', 100).get('unsatisfiable') is True,
              'F06：非法范围 → 416')
        fpath = os.path.join(tmp, 'blob.bin')
        with open(fpath, 'wb') as f:
            f.write(bytes(range(100)))
        check(media.read_range(fpath, 10, 19) == bytes(range(10, 20)),
              'F06：read_range 精确切片（不全量读）')

        # 鉴权
        with store.tx() as conn:
            conn.execute("INSERT INTO principals(principal_id, kind, display_name, created_at)"
                         " VALUES('p1','operator','管理员',datetime('now'))")
            conn.execute('INSERT INTO sessions(token_hash, principal_id, created_at, expires_at,'
                         " revoked) VALUES('tok-ok','p1',datetime('now'),NULL,0)")
            conn.execute('INSERT INTO sessions(token_hash, principal_id, created_at, expires_at,'
                         " revoked) VALUES('tok-rev','p1',datetime('now'),NULL,1)")
            conn.execute('INSERT INTO sessions(token_hash, principal_id, created_at, expires_at,'
                         " revoked) VALUES('tok-old','p1',datetime('now'),"
                         "'2000-01-01T00:00:00.000+00:00',0)")
        check(media.check_read_access(store, 'tok-ok', row['media_id']) == 'p1',
              'F06：有效会话可读')
        raises(lambda: media.check_read_access(store, None, row['media_id']),
               ConflictError, 'F06：无令牌拒绝')
        raises(lambda: media.check_read_access(store, 'tok-rev', row['media_id']),
               ConflictError, 'F06：撤销会话拒绝')
        raises(lambda: media.check_read_access(store, 'tok-old', row['media_id']),
               ConflictError, 'F06：过期会话拒绝')

        # ---- F05 对账 ----
        full = os.path.join(mroot, row['relative_path'])
        with open(os.path.join(mroot, 'uploads', 'orphan.bin'), 'wb') as f:
            f.write(b'zzz')
        rec = media.reconcile(store, mroot)
        check('uploads/orphan.bin' in rec['orphans'], 'F05：孤儿文件被列出（不删）')
        os.remove(full)
        rec = media.reconcile(store, mroot)
        check(row['media_id'] in rec['missing_files'], 'F05：缺失文件被报告')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_media_range: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
