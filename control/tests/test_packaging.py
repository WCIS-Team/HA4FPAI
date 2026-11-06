# -*- coding: utf-8 -*-
"""control deb 打包自检：结构、成员、control 字段、拒覆盖、md5sums 完整。"""
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 仓库根
PACK = os.path.join(ROOT, 'control', 'packaging')
RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def read_ar_members(deb_bytes):
    """最小 ar 解析：返回 [(name, data_bytes), ...]。"""
    assert deb_bytes.startswith(b'!<arch>\n'), '不是 ar 归档'
    body = deb_bytes[len(b'!<arch>\n'):]
    members = []
    pos = 0
    while pos < len(body):
        header = body[pos:pos + 60]
        # 名字字段＝名字＋'/'＋空格填充：必须先 strip 空格再去尾斜杠
        name = header[:16].decode('ascii').strip().rstrip('/')
        size = int(header[48:58].decode('ascii').strip())
        data = body[pos + 60:pos + 60 + size]
        members.append((name, data))
        pos += 60 + size + (size % 2)
    return members


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-control-pkg-')
    try:
        out = os.path.join(tmp, 'dist')
        proc = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8',
             os.path.join(PACK, 'build_control_deb.py'),
             '--version', '0.1.0+test1', '--output', out],
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        check(proc.returncode == 0, '打包脚本退出码 0')
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith('{')]
        check(len(lines) == 2, '输出两件 artifact（deb+tar.gz）')
        info = json.loads(lines[0]) if lines else {}
        deb_path = info.get('artifact', '')
        check(os.path.isfile(deb_path) and info.get('bytes', 0) > 1000, 'deb 产出且非空')
        with open(deb_path, 'rb') as f:
            deb = f.read()
        members = read_ar_members(deb)
        names = [n for n, _ in members]
        check(names == ['debian-binary', 'control.tar.gz', 'data.tar.gz'],
              'ar 成员顺序与名称正确')
        data_tar = dict(members)['data.tar.gz']
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data_tar)), mode='r:') as tar:
            infos = tar.getmembers()
            staged = [m.name for m in infos]
            # 目录项名字不带尾斜杠，必须用 isfile() 判定，不能用结尾字符猜
            staged_count = sum(1 for m in infos if m.isfile())
        check(any('usr/lib/ha4fpai-control/control/service.py' in n for n in staged),
              'data 内含 control 包源码')
        check(any('usr/lib/ha4fpai-control/control/adapters/video.py' in n for n in staged),
              'data 内含视频适配器')
        check(not any('/tests/' in n for n in staged), 'data 不含 tests（排除生效）')
        check(any(n.endswith('lib/systemd/system/ha4fpai-control.service') for n in staged),
              'data 内含 systemd 单元')
        control_tar = dict(members)['control.tar.gz']
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(control_tar)), mode='r:') as tar:
            # tree_tar 写入的成员名带 './' 前缀
            ctl = tar.extractfile('./control').read().decode('utf-8')
            md5 = tar.extractfile('./md5sums').read().decode('utf-8')
            ctl_members = {m.name: m for m in tar.getmembers()}
            script_bodies = {}
            for name in ('postinst', 'prerm', 'postrm'):
                key = './' + name
                check(key in ctl_members and (ctl_members[key].mode & 0o111) != 0,
                      'control 内含可执行 %s' % name)
                if key in ctl_members:
                    script_bodies[name] = tar.extractfile(key).read().decode('utf-8')
        check('Package: ha4fpai-control' in ctl and 'Version: 0.1.0+test1' in ctl,
              'control 文件字段正确')
        check('Installed-Size:' in ctl, 'Installed-Size 已写入')
        check(len(md5.strip().splitlines()) == staged_count,
              'md5sums 行数与 data 文件数一致')
        postrm = script_bodies.get('postrm', '')
        prerm = script_bodies.get('prerm', '')
        check('systemctl stop ha4fpai-control' in prerm, 'prerm 停服务但不动数据')
        check('purge)' in postrm and '/var/lib/ha4fpai-control' in postrm,
              'postrm purge 才删除数据')
        check('remove|upgrade' in postrm and '保留' in postrm,
              'postrm remove/upgrade 保留数据与凭据')
        # 拒绝覆盖
        proc2 = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8',
             os.path.join(PACK, 'build_control_deb.py'),
             '--version', '0.1.0+test1', '--output', out],
            capture_output=True, text=True, encoding='utf-8', timeout=300)
        check(proc2.returncode != 0 and 'Refusing to overwrite' in (proc2.stderr + proc2.stdout),
              '同版本重跑拒绝覆盖')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('test_packaging: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
