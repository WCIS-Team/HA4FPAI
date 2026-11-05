# -*- coding: utf-8 -*-
"""/v1 控制服务：统一执行入口的 HTTP 面（规划 §拟议服务API）。

安全基线：
  * 默认只绑 127.0.0.1；--host 放开时必须配 --token，否则拒绝启动
  * GET 只读；副作用只走 POST/PATCH/DELETE（整改旧 GET 执行入口的教训）
  * Bearer 令牌（服务端会话表），不带 Cookie，CSRF 面收敛为 Origin 校验：
    带 Origin 的变更请求必须同源，否则 403
  * 请求体上限 8 MiB；JSON 解析失败 400；未知路径 404（不代理任意地址）

本文件是目标骨架的第一版：/v1/* 尚未部署到板端，属 unified-control 分支。
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control import actions as actions_mod
from control import commands, ids, outbox, persons, samples, tasks  # noqa: F401
from control import voiceprint_import
from control.adapters import video as video_mod
from control.errors import AuthError, DomainError, ForbiddenError
from control.events import EventLog
from control.settings import Settings
from control.store import Store

MAX_BODY = 8 * 1024 * 1024
PUBLIC_GETS = ('/healthz', '/v1/capabilities', '/v1/status')


class App(object):
    def __init__(self, db_path, exports_dir, token=None, media_root=None,
                 scheduler_interval=0):
        self.store = Store(db_path)
        self.events = EventLog(self.store)
        self.settings = Settings(self.store)
        self.registry = actions_mod.build_registry(self.store, self.events, self.settings)
        self.exports_dir = exports_dir
        self.media_root = media_root
        # 视频快照与上传共用同一媒体根＋业务库（V2/V9 一致性）
        video_mod.configure(store=self.store, media_root=media_root)
        self._bootstrap_principal('cli', token)
        # A02：调度内置本进程（单写者）；interval=0 时不启动（测试/一次性工具）
        self.scheduler = None
        if scheduler_interval:
            from control.scheduler import Scheduler
            self.scheduler = Scheduler(self, interval=scheduler_interval).start()

    def _bootstrap_principal(self, kind, token):
        principal_id = kind
        with self.store.tx() as conn:
            row = conn.execute('SELECT * FROM principals WHERE principal_id=?',
                               (principal_id,)).fetchone()
            if row is None:
                conn.execute('INSERT INTO principals(principal_id, kind, created_at)'
                             " VALUES(?,?,?)", (principal_id, kind, ids.now_iso()))
        if token:
            self._add_session(principal_id, token)

    def _add_session(self, principal_id, token):
        with self.store.tx() as conn:
            conn.execute('INSERT OR IGNORE INTO sessions(token_hash, principal_id, created_at)'
                         ' VALUES(?,?,?)', (ids.digest_of(token), principal_id, ids.now_iso()))

    def create_session(self, principal_name):
        principal_id = 'op-' + hashlib.sha256(principal_name.encode('utf-8')).hexdigest()[:12]
        with self.store.tx() as conn:
            row = conn.execute('SELECT * FROM principals WHERE principal_id=?',
                               (principal_id,)).fetchone()
            if row is None:
                conn.execute('INSERT INTO principals(principal_id, kind, display_name,'
                             ' created_at) VALUES(?,?,?,?)',
                             (principal_id, 'operator', principal_name, ids.now_iso()))
        token = secrets.token_urlsafe(32)
        self._add_session(principal_id, token)
        return {'principal_id': principal_id, 'token': token}

    def authenticate(self, header):
        if not header or not header.startswith('Bearer '):
            raise AuthError('unauthorized', '缺少 Bearer 令牌')
        token = header[len('Bearer '):].strip()
        row = self.store.query_one('SELECT * FROM sessions WHERE token_hash=? AND revoked=0',
                                   (ids.digest_of(token),))
        if row is None:
            raise AuthError('unauthorized', '令牌无效或已撤销')
        if row['expires_at'] and row['expires_at'] <= ids.now_iso():
            raise AuthError('unauthorized', '令牌已过期')
        return row['principal_id']


class Handler(BaseHTTPRequestHandler):
    server_version = 'ha4fpai-control'
    protocol_version = 'HTTP/1.1'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):  # 安静模式：日志走调用方终端
        pass

    # ---- 基础回包 ----
    def _send(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc):
        status = getattr(exc, 'http_status', 500)
        if status == 500:
            self._send(500, {'error': {'code': 'internal', 'message': str(exc)[:300]}})
        else:
            self._send(status, exc.to_json())

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True  # 非浏览器客户端（curl 等）无 Origin；浏览器一定带
        host = self.headers.get('Host', '')
        return origin == 'http://' + host or origin == 'https://' + host

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise ForbiddenError('body_too_large', '请求体超过上限')
        raw = self.rfile.read(length) if length else b''
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode('utf-8'))
        except ValueError:
            raise DomainError('bad_json', '请求体不是合法 JSON')
        if not isinstance(value, dict):
            raise DomainError('bad_json', '请求体顶层必须是对象')
        return value

    # ---- 鉴权 ----
    def _principal(self):
        return self.app.authenticate(self.headers.get('Authorization'))

    # ---- 路由 ----
    def do_GET(self):
        try:
            path = self.path.split('?')[0]
            query = dict(pair.split('=', 1) for pair in
                         (self.path.split('?')[1].split('&') if '?' in self.path else [])
                         if '=' in pair)
            if path in PUBLIC_GETS:
                return self._send(200, self._public_get(path))
            principal = self._principal()
            if path.startswith('/v1/commands/'):
                return self._send(200, commands.get_command(self.app.store, path.rsplit('/', 1)[1]))
            if path == '/v1/persons':
                rows = persons.list_all(self.app.store, include_deleted=True)
                return self._send(200, {'persons': rows})
            if path.startswith('/v1/persons/'):
                person_id = path.split('/')[3]
                if path.endswith('/enrollments'):
                    return self._send(200, {'face': samples.list_for_person(
                        self.app.store, 'face', person_id),
                        'voice': samples.list_for_person(self.app.store, 'voice', person_id)})
                return self._send(200, persons.get(self.app.store, person_id))
            if path == '/v1/settings':
                return self._send(200, {'settings': self.app.settings.effective()})
            if path.startswith('/v1/media/'):
                # V9：按 media_id 白名单读取（防路径逃逸在 media.open_asset）
                from control import media as media_mod
                media_id = path.split('/')[3]
                full, row = media_mod.open_asset(self.app.store, media_id)
                with open(full, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', row['kind'])
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            if path == '/v1/events':
                cursor = int(query.get('after', ['0'])[0])
                limit = int(query.get('limit', ['100'])[0])
                return self._send(200, self.app.events.list_after(cursor, limit))
            if path == '/v1/tasks':
                status = query.get('status', [None])[0]
                return self._send(200, {'tasks': tasks.list_tasks(self.app.store, status)})
            if path == '/v1/exports/current':
                return self._send(200, {'manifest': outbox.read_current(self.app.exports_dir)})
            raise DomainError('not_found', '未知路径: %s' % path)
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)

    def _public_get(self, path):
        if path == '/healthz':
            sched = self.app.scheduler
            health = {'ok': True}
            if sched is not None:
                # 调度线程退出请求（世代变更）会让整体退出；健康面如实反映
                health['scheduler'] = {'running': sched.status()['running'],
                                       'exit_requested': sched.exit_requested}
                if sched.exit_requested:
                    health['ok'] = False
            return health
        if path == '/v1/capabilities':
            return actions_mod.capabilities_payload(self.app.store, self.app.registry)
        if path == '/v1/status':
            spec = self.app.registry['system.status']
            outcome = spec['run']({}, None, None, None, None)
            data = outcome['data']
            if self.app.scheduler is not None:
                data['scheduler'] = self.app.scheduler.status()
            return data

    def do_POST(self):
        try:
            if not self._origin_ok():
                raise ForbiddenError('cross_origin', '跨源变更请求被拒绝')
            principal = self._principal()
            path = self.path.split('?')[0]
            if path == '/v1/uploads':
                # V9：原始字节上传（必须在 JSON 解析之前接管请求体）；
                # Content-Type 即媒体类型（白名单校验在 media 层）
                from control import media as media_mod
                n = int(self.headers.get('Content-Length') or 0)
                if n > media_mod.MAX_UPLOAD:
                    raise ForbiddenError('upload_too_large',
                                         '上传超过上限 %d 字节' % media_mod.MAX_UPLOAD)
                raw = self.rfile.read(n) if n else b''
                ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
                row = media_mod.save_upload(self.app.store, self.app.media_root, raw, ctype)
                return self._send(200, {'media': dict(row)})
            body = self._read_json()
            if path == '/v1/session':
                name = str(body.get('principal') or 'operator')[:40]
                return self._send(200, self.app.create_session(name))
            if path == '/v1/commands':
                result = commands.create(
                    self.app.store, self.app.registry, self.app.events,
                    action=str(body.get('action') or ''),
                    params=body.get('params') or {},
                    target=body.get('target'),
                    idempotency_key=body.get('idempotency_key'),
                    request_id=body.get('request_id'),
                    session_id=body.get('session_id'),
                    client_id=body.get('client_id'),
                    principal_id=principal,
                    expected_revision=body.get('expected_revision'),
                    confirmation_id=body.get('confirmation_id'))
                return self._send(200, result)
            if path == '/v1/interpret':
                result = actions_mod.interpret(
                    self.app.store, self.app.registry, str(body.get('text') or ''),
                    session=body.get('session'),
                    klar_url=body.get('klar_url') or None)
                return self._send(200, result)
            if path == '/bridge/text':
                # 语音链执行面（WP9）：文本 → 提案 → 执行 → 可念回复
                from . import voice_bridge
                return self._send(200, voice_bridge.run_text(
                    self.app, str(body.get('text') or ''),
                    session=body.get('session'),
                    client_id=body.get('client_id') or 'voice'))
            if path == '/v1/recordings/receipts':
                # V6（HA 侧半边）：recorder 回执接收；幂等、不复活、只镜像不发明
                from . import receipts
                return self._send(200, receipts.ingest(self.app.store, self.app.events, body))
            if path == '/v1/confirmations':
                info = commands.create_confirmation(
                    self.app.store, self.app.registry, str(body.get('action') or ''),
                    body.get('params') or {}, target=body.get('target'),
                    principal_id=principal, ttl_s=int(body.get('ttl_s') or 120))
                return self._send(200, info)
            if path == '/v1/persons':
                return self._send(200, {'person': persons.create(
                    self.app.store, self.app.events, body.get('display_name'),
                    actor=principal)})
            if path.startswith('/v1/persons/') and path.endswith('/enrollments'):
                person_id = path.split('/')[3]
                modality = body.get('modality')
                if modality not in ('face', 'voice'):
                    raise DomainError('invalid_modality', 'modality 只能是 face/voice')
                row = samples.enroll(self.app.store, self.app.events, modality, person_id,
                                     body.get('model_id'), body.get('vector'),
                                     dtype=body.get('dtype') or 'float32-le',
                                     require_unit_norm=bool(body.get('require_unit_norm')),
                                     source_media_id=body.get('source_media_id'),
                                     source_ref=body.get('source_ref'))
                return self._send(200, {'sample': {k: row[k] for k in
                                                   ('sample_id', 'person_id', 'model_id',
                                                    'dim', 'dtype', 'active')}})
            if path == '/v1/voiceprint/import-preview':
                result = voiceprint_import.preview(str(body.get('path') or ''),
                                                   source=body.get('source'))
                return self._send(200, result)
            if path == '/v1/tasks':
                return self._send(200, {'task': tasks.create(
                    self.app.store, self.app.registry, str(body.get('action') or ''),
                    body.get('params') or {}, run_at=body.get('run_at'),
                    interval_s=body.get('interval_s'),
                    missed_policy=body.get('missed_policy') or 'skip')})
            if path == '/v1/tasks/run-due':
                return self._send(200, {'results': tasks.run_due(
                    self.app.store, self.app.registry, self.app.events, commands)})
            raise DomainError('not_found', '未知路径: %s' % path)
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)

    def do_PATCH(self):
        try:
            if not self._origin_ok():
                raise ForbiddenError('cross_origin', '跨源变更请求被拒绝')
            principal = self._principal()
            body = self._read_json()
            path = self.path.split('?')[0]
            if path == '/v1/settings':
                revisions = self.app.settings.update(body.get('updates') or {},
                                                     expected_revisions=body.get('expected_revisions'),
                                                     actor=principal)
                return self._send(200, {'revisions': revisions})
            if path.startswith('/v1/persons/'):
                person_id = path.split('/')[3]
                return self._send(200, {'person': persons.update(
                    self.app.store, self.app.events, person_id,
                    display_name=body.get('display_name'), status=body.get('status'),
                    expected_revision=body.get('expected_revision'), actor=principal)})
            raise DomainError('not_found', '未知路径: %s' % path)
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)

    def do_DELETE(self):
        try:
            if not self._origin_ok():
                raise ForbiddenError('cross_origin', '跨源变更请求被拒绝')
            self._principal()
            body = self._read_json()
            path = self.path.split('?')[0]
            if path.startswith('/v1/persons/'):
                person_id = path.split('/')[3]
                # 删除是危险动作：必须走命令管线 + 确认绑定
                result = commands.create(
                    self.app.store, self.app.registry, self.app.events,
                    action='person.delete', params={'person_id': person_id},
                    principal_id=principal,
                    confirmation_id=body.get('confirmation_id'),
                    idempotency_key=body.get('idempotency_key'))
                return self._send(200, result)
            if path.startswith('/v1/tasks/'):
                return self._send(200, {'task': tasks.cancel(self.app.store, path.split('/')[3])})
            raise DomainError('not_found', '未知路径: %s' % path)
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description='HA4FPAI 统一控制服务（/v1）')
    parser.add_argument('--db', default=os.path.join('data', 'business.sqlite3'))
    parser.add_argument('--exports-dir', default=os.path.join('data', 'exports'))
    parser.add_argument('--media-root', default=os.path.join('data', 'media'),
                        help='上传/快照的受控媒体根目录（V2/V9）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18100)
    parser.add_argument('--token', help='预置 Bearer 令牌（也会写入会话表）')
    parser.add_argument('--token-file', help='从文件首行读令牌（systemd 单元用，避免令牌进 ps）')
    parser.add_argument('--scheduler-interval', type=int, default=60,
                        help='内置任务调度秒数（最小30；0=关闭调度，测试/一次性用）')
    args = parser.parse_args(argv)

    token = args.token
    if not token and args.token_file:
        try:
            with open(args.token_file, 'r', encoding='utf-8') as f:
                token = f.readline().strip()
        except OSError as exc:
            raise SystemExit('token-file 不可读: %s' % exc)
    if args.host not in ('127.0.0.1', 'localhost') and not token:
        raise SystemExit('拒绝启动：放开非回环监听必须配置 --token/--token-file（规划 §安全与事件反馈）')

    db_dir = os.path.dirname(os.path.abspath(args.db))
    if not os.path.isdir(db_dir):
        os.makedirs(db_dir)
    app = App(args.db, args.exports_dir, token=token, media_root=args.media_root,
              scheduler_interval=args.scheduler_interval)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.app = app
    print('ha4fpai-control listening on http://%s:%d' % (args.host, args.port))
    print('store_id=%s generation=%s epoch=%s scheduler=%s' % (
        app.store.meta('store_id'), app.store.meta('generation'),
        app.store.meta('restore_epoch'),
        ('every %ds' % app.scheduler.interval) if app.scheduler else 'off'))
    if not args.token:
        print('（未配置 --token：请先 POST /v1/session 建立操作员会话）')
    try:
        while server.serve_forever(poll_interval=0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if app.scheduler is not None:
            app.scheduler.stop()
        server.server_close()
    if app.scheduler is not None and app.scheduler.exit_requested:
        # restore_epoch 变化：按栅栏约定退出码 3，交 systemd Restart 以新世代重启
        raise SystemExit(3)


if __name__ == '__main__':
    main()
