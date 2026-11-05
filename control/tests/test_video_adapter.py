# -*- coding: utf-8 -*-
"""视频域适配器自检：本机起契约模拟服务器，不连真实 8091/8092/8093，不连板端。

跑法（仓库根）：
    python -B -X utf8 control/tests/test_video_adapter.py
输出 PASS/FAIL 计数；全绿退出码 0。

契约依据：docs/VIDEO-DOMAIN-PLACEHOLDER.md V1–V5 行 + 视频仓 docs/api-index.md
（2026-11-05 核对）。模拟服务器只实现本次接回用到的端点与错误分支；
错误场景（503 / 回读不一致 / 坏 JSON / 未就绪）经 STATE 开关切换。
「连接拒绝」用占住后关闭的端口构造，不碰任何真实服务端口。
"""
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from control.adapters import video  # noqa: E402
from control.errors import DomainError  # noqa: E402
from control.store import Store  # noqa: E402

RESULTS = []


def check(cond, name):
    RESULTS.append((bool(cond), name))
    print(('PASS ' if cond else 'FAIL ') + name)


def expect_domain(fn, code, name):
    """断言 fn 抛 DomainError 且机器码一致；返回异常对象供后续取 message/data。"""
    try:
        fn()
    except DomainError as exc:
        check(exc.code == code, name + ('' if exc.code == code else '（实际 %s）' % exc.code))
        return exc
    except Exception as exc:  # noqa: BLE001
        check(False, name + '（抛了别的: %r）' % exc)
        return None
    check(False, name + '（未抛出）')
    return None


# ---- 契约固定样例（模拟服务器侧）----

RECOGNIZE_OK = {
    'entry': {
        'frame_id': 'frame-0001',
        'ages': {'capture_ms': 12, 'delivery_ms': 34},
        'result': {
            'frame_token': 'tok-0001',
            'identities': [{'name': '张三', 'score': 0.93}],
            'face_count': 1,
            'presentation_attack_detection': {
                'status': 'model_live',
                'security_calibrated': True,
                'reason': 'ok',
                'model_result': {'live_score': 0.97},
            },
        },
    }
}
PREVIEW_JPEG = b'\xff\xd8\xff\xe0fake-jpeg-payload\x00\x01\x02\xff\xd9'
RECORDINGS_OK = {'page': 1, 'limit': 50, 'total': 2, 'items': [
    {'id': 'rec-1', 'trash_state': 'none', 'duration_s': 12.5},
    {'id': 'rec-2', 'trash_state': 'trash'},
]}


class MockState(object):
    """模拟服务器的可切换行为；reset() 恢复契约默认分支。"""

    def __init__(self):
        self.requests = []
        self.reset()

    def reset(self):
        self.recognize_status = 200
        self.recognize_body = json.dumps(RECOGNIZE_OK).encode('utf-8')
        self.recognize_raw = None       # 非 JSON 场景：直接回该字节串
        self.preview_status = 200
        self.preview_body = PREVIEW_JPEG
        self.status_ready = True
        self.mode_status = 200
        self.mode_override = None       # 回读不一致场景：强制回别的值
        self.recordings_status = 200
        self.recordings_body = RECORDINGS_OK
        self.recordings_raw = None
        self.purge_status = 200
        self.purge_body = {'purged': True}


STATE = MockState()


class MockVideoHandler(BaseHTTPRequestHandler):
    """契约模拟：固定 JSON/字节 + 可切换错误分支；记录每个请求供断言。"""

    def log_message(self, fmt, *args):  # 静默，测试输出只留 PASS/FAIL
        pass

    def _read_body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b''

    def _record(self, body):
        STATE.requests.append({'method': self.command, 'path': self.path,
                               'body': body,
                               'origin': self.headers.get('Origin'),
                               'content_type': self.headers.get('Content-Type')})

    def _reply(self, status, body, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_bytes(self, obj):
        return json.dumps(obj).encode('utf-8')

    def do_GET(self):
        self._record(self._read_body())
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/camera/preview.jpg':
            if STATE.preview_status == 200:
                self._reply(200, STATE.preview_body, 'image/jpeg')
            else:
                self._reply(STATE.preview_status,
                            self._json_bytes({'error': 'no fresh frame'}))
        elif path == '/api/status':
            self._reply(200, self._json_bytes({'ready': STATE.status_ready,
                                               'sessions': 0}))
        elif path == '/api/recordings':
            body = STATE.recordings_raw
            if body is None:
                body = self._json_bytes(STATE.recordings_body)
            self._reply(STATE.recordings_status, body)
        else:
            self._reply(404, self._json_bytes({'error': 'not found'}))

    def do_POST(self):
        body = self._read_body()
        self._record(body)
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/recognize-camera':
            payload = STATE.recognize_raw
            if payload is None:
                payload = STATE.recognize_body
            self._reply(STATE.recognize_status, payload)
        elif path == '/api/mode':
            try:
                req = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                req = {}
            mode = STATE.mode_override if STATE.mode_override is not None else req.get('mode')
            self._reply(STATE.mode_status, self._json_bytes({'mode': mode}))
        elif path == '/api/purge':
            try:
                req = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                req = {}
            if req.get('confirm') == req.get('id'):  # 服务端也校验（契约如此）
                self._reply(STATE.purge_status, self._json_bytes(STATE.purge_body))
            else:
                self._reply(403, self._json_bytes({'error': 'confirm mismatch'}))
        else:
            self._reply(404, self._json_bytes({'error': 'not found'}))


def make_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), MockVideoHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, 'http://127.0.0.1:%d' % server.server_address[1]


def dead_port_url():
    """占住一个端口再关掉 → 连它必得连接拒绝，不碰任何真实服务端口。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return 'http://127.0.0.1:%d' % port


def main():
    tmp = tempfile.mkdtemp(prefix='ha4fpai-video-test-')
    server, base = make_server()
    try:
        run_all(tmp, base)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for ok, _ in RESULTS if ok)
    print('video-adapter: %d/%d ok' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


def run_all(tmp, base):
    store = Store(os.path.join(tmp, 'business.sqlite3'))
    media_root = os.path.join(tmp, 'media')

    # ---- status()：模块默认态（未 configure）----
    st = video.status()
    check(st['board_verified'] is False and st['mode'] == 'http-client',
          'status：board_verified=False、mode=http-client')
    check(st['connected'] is True and st['targets']['face'] == 'http://127.0.0.1:8091',
          'status：connected=CONFIG.enabled（默认 True）')

    # 指向模拟服务器（三个服务名共用一个口，按路径区分端点）
    video.configure(targets={'face': base, 'stream': base, 'record': base},
                    media_root=None, store=store)

    # ---- V1 recognize：成功映射 ----
    out = video.recognize()
    check(out['frame_id'] == 'frame-0001' and out['frame_token'] == 'tok-0001'
          and out['face_count'] == 1, 'recognize 成功映射 frame/face_count')
    check(out['identities'] == RECOGNIZE_OK['entry']['result']['identities'],
          'recognize identities 原样')
    check(out['pad'] == RECOGNIZE_OK['entry']['result']['presentation_attack_detection']
          and out['ages'] == {'capture_ms': 12, 'delivery_ms': 34},
          'recognize pad/ages 原样透传')
    req = STATE.requests[-1]
    check(req['method'] == 'POST' and req['path'] == '/api/recognize-camera'
          and req['body'] == b'' and req['origin'] is None,
          'recognize 空请求体且不发 Origin')

    # ---- V1 recognize：字段缺失一律 None ----
    STATE.recognize_body = json.dumps({'entry': {'result': {}}}).encode('utf-8')
    out = video.recognize()
    check(all(out[key] is None for key in
              ('frame_id', 'frame_token', 'identities', 'face_count', 'pad', 'ages')),
          'recognize 字段缺失一律 None（不编造）')

    # ---- V1 recognize：503 → video_no_frame，原始 error 透传 ----
    STATE.recognize_status = 503
    STATE.recognize_body = json.dumps({'error': 'camera busy'}).encode('utf-8')
    exc = expect_domain(video.recognize, 'video_no_frame', 'recognize 503 → video_no_frame')
    check(exc is not None and 'camera busy' in exc.message
          and exc.data.get('error') == 'camera busy', '503 原始 error 透传')

    # ---- V1 recognize：非 200 / 非 JSON ----
    STATE.recognize_status = 500
    expect_domain(video.recognize, 'video_service_error', 'recognize 500 → video_service_error')
    STATE.recognize_status = 200
    STATE.recognize_raw = b'<html>oops</html>'
    expect_domain(video.recognize, 'video_service_error', 'recognize 200 非 JSON → video_service_error')

    # ---- 连接拒绝 → video_unreachable（还原 recognize 场景后切死端口）----
    STATE.reset()
    dead = dead_port_url()
    video.configure(targets={'face': dead})
    expect_domain(video.recognize, 'video_unreachable', 'recognize 连接拒绝 → video_unreachable')
    video.configure(targets={'face': base})

    # ---- V4 set_mode：成功 + 回读校验 + 不发 Origin ----
    out = video.set_mode(mode='manual')
    check(out == {'mode': 'manual', 'armed': True, 'actively_recording': None},
          'set_mode 成功：armed=True、actively_recording 如实 None')
    req = STATE.requests[-1]
    check(req['path'] == '/api/mode' and json.loads(req['body']) == {'mode': 'manual'},
          'set_mode 请求体 {"mode": ...}')
    check(req['origin'] is None, '8093 POST 不发 Origin（同源检查天然通过，不伪造）')

    STATE.mode_override = 'off'
    expect_domain(lambda: video.set_mode(mode='manual'), 'video_service_error',
                  'set_mode 回读不一致 → video_service_error')
    STATE.mode_override = None

    count = len(STATE.requests)
    expect_domain(lambda: video.set_mode(mode='hurry'), 'invalid_params',
                  'set_mode 非法模式客户端拒绝')
    check(len(STATE.requests) == count, 'set_mode 非法模式未发请求')

    # ---- purge：客户端先校验 confirm==id 再发 ----
    count = len(STATE.requests)
    expect_domain(lambda: video.purge(recording_id='rec-1', confirm='rec-2'),
                  'invalid_params', 'purge confirm≠id 客户端拒绝')
    check(len(STATE.requests) == count, 'purge confirm≠id 未发请求')
    out = video.purge(recording_id='rec-1', confirm='rec-1')
    check(out['status'] == 200 and out['data'] == {'purged': True},
          'purge confirm==id 发出并透传回包')
    req = STATE.requests[-1]
    check(req['path'] == '/api/purge'
          and json.loads(req['body']) == {'id': 'rec-1', 'confirm': 'rec-1'},
          'purge 请求体带 id+confirm')

    # ---- V5 list_recordings：透传 JSON+状态码、查询参数拼接 ----
    out = video.list_recordings(page=1, limit=5, trash='none',
                                **{'from': '2026-11-01T00:00:00+00:00'})
    check(out['status'] == 200 and out['data'] == RECORDINGS_OK,
          'recordings 原样透传 JSON+状态码（结构不猜）')
    req = STATE.requests[-1]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(req['path']).query)
    check(req['path'].startswith('/api/recordings?')
          and query.get('page') == ['1'] and query.get('limit') == ['5']
          and query.get('trash') == ['none']
          and query.get('from') == ['2026-11-01T00:00:00+00:00'],
          'recordings 查询参数 page/limit/from/trash 拼接')

    # ---- V5 play：纯地址构造，不发请求不取体 ----
    count = len(STATE.requests)
    out = video.play(recording_id='rec-1')
    check(out == {'url': base + '/api/recordings/rec-1/video'}, 'play 返回回放地址')
    check(len(STATE.requests) == count, 'play 不发请求、不取视频体')

    # ---- V3 open_view：ready 门 + 未就绪如实报错 ----
    out = video.open_view(client_id='ui-1')
    check(out['url'] == base + '/h264/manifest.json' and out['ready'] is True
          and out['client_id'] == 'ui-1', 'open_view ready → manifest 地址')
    STATE.status_ready = False
    expect_domain(video.open_view, 'video_service_error', 'open_view 未就绪 → video_service_error')
    STATE.status_ready = True

    # ---- V2 snapshot：落盘 + media_assets + sha256 ----
    video.configure(media_root=media_root)
    out = video.snapshot()
    digest = hashlib.sha256(PREVIEW_JPEG).hexdigest()
    check(out['saved'] is True and out['media_id']
          and out['sha256'] == digest and out['size_bytes'] == len(PREVIEW_JPEG),
          'snapshot saved=True，sha256/size 正确')
    full = os.path.join(media_root, 'snapshots', out['media_id'] + '.jpg')
    with open(full, 'rb') as handle:
        written = handle.read()
    check(written == PREVIEW_JPEG, 'snapshot 原子落盘字节一致')
    row = store.query_one('SELECT * FROM media_assets WHERE media_id=?',
                          (out['media_id'],))
    check(row is not None and row['storage_root'] == media_root
          and row['relative_path'] == 'snapshots/%s.jpg' % out['media_id']
          and row['kind'] == 'image/jpeg' and row['size_bytes'] == len(PREVIEW_JPEG)
          and row['sha256'] == digest and row['status'] == 'registered',
          'snapshot 写 media_assets 行（storage_root/relative_path/kind/sha256）')

    # ---- V2 snapshot：未配 media_root 不落盘 ----
    video.configure(media_root=None)
    out = video.snapshot()
    check(out['saved'] is False and out['media_id'] is None
          and out['sha256'] == digest and out['size_bytes'] == len(PREVIEW_JPEG),
          '未配 media_root：saved=False、不落盘')
    check(store.query_one('SELECT COUNT(*) AS c FROM media_assets')['c'] == 1,
          '未配 media_root 不写新 media_assets 行')

    # ---- V2 snapshot：503 → video_no_frame ----
    STATE.preview_status = 503
    expect_domain(video.snapshot, 'video_no_frame', 'snapshot 503 → video_no_frame')
    STATE.reset()

    # ---- enabled=False：总开关关掉按「未接」报告（501 语义保留）----
    video.configure(enabled=False, media_root=None, store=None)
    check(video.status()['connected'] is False, 'enabled=False → connected=False')
    expect_domain(video.recognize, 'video_domain_not_connected',
                  'enabled=False → video_domain_not_connected（复用既有错误）')

    # ---- 恢复默认配置，收尾核对 status() ----
    video.configure(enabled=True,
                    targets={'face': 'http://127.0.0.1:8091',
                             'stream': 'http://127.0.0.1:8092',
                             'record': 'http://127.0.0.1:8093'},
                    media_root=None, store=None)
    st = video.status()
    check(st['board_verified'] is False and st['connected'] is True
          and st['targets'] == {'face': 'http://127.0.0.1:8091',
                                'stream': 'http://127.0.0.1:8092',
                                'record': 'http://127.0.0.1:8093'},
          '收尾：CONFIG 恢复默认，board_verified 仍 False')


if __name__ == '__main__':
    sys.exit(main())
