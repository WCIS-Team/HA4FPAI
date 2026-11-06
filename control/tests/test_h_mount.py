# -*- coding: utf-8 -*-
"""H02 验收：掉盘/挂载身份 guard 与同设备校验。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_h_mount.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control import mountguard   # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


MOUNTINFO = """\
36 35 98:0 / /mnt/data rw,relatime shared:1 - ext4 /dev/sda1 rw
22 1 8:0 / / rw,relatime shared:2 - ext4 /dev/mmcblk0p2 rw
"""


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-mount-')
    try:
        ok = mountguard.check_mount(MOUNTINFO, '/mnt/data', expected_uuid='sda1', fstype='ext4')
        check(ok['ok'] is True, 'H02：正确挂载/UUID/fstype 通过')
        check(mountguard.check_mount(MOUNTINFO, '/mnt/data', expected_uuid='xxx')['reason']
              == 'uuid_mismatch', 'H02：UUID 不符拒绝')
        check(mountguard.check_mount(MOUNTINFO, '/mnt/data', fstype='xfs')['reason']
              == 'fstype_mismatch', 'H02：fstype 不符拒绝')
        check(mountguard.check_mount(MOUNTINFO, '/mnt/other')['reason'] == 'not_mounted',
              'H02：掉盘/未挂载 → not_mounted')
        try:
            mountguard.require_mount(MOUNTINFO, '/mnt/other')
            check(False, 'H02：require_mount 未挂载应抛错')
        except RuntimeError:
            check(True, 'H02：require_mount 未挂载抛错（拒绝写，不另建空库）')

        f1 = os.path.join(tmp, 'business.sqlite3')
        f2 = os.path.join(tmp, 'business.sqlite3-wal')
        for p in (f1, f2):
            with open(p, 'wb') as f:
                f.write(b'x')
        sd = mountguard.same_device([f1, f2])
        check(sd['ok'] is True, 'H02：DB/WAL 同设备通过')
        missing = mountguard.same_device([f1, os.path.join(tmp, 'nope')])
        check(missing['ok'] is False and missing['reason'] == 'stat_failed',
              'H02：缺失文件 → stat_failed 拒绝')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for okk, _ in RESULTS if okk)
    print('test_h_mount: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
