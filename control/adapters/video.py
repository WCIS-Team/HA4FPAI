# -*- coding: utf-8 -*-
"""视频域适配器 —— HTTP 客户端（V1–V5 已实现，板端未验收）。

登记正本：docs/VIDEO-DOMAIN-PLACEHOLDER.md（V1–V5 行标「已实现（板端未验）」，
V6–V10 仍留空）。端点契约来自视频仓 docs/api-index.md 与源码核对（2026-11-05）；
板端验收前不作现场可用性承诺，本机一律用契约模拟服务器测试（禁连真实端口）。

硬边界（实现后仍必须遵守）：
  * 摄像头 / iCraft SDK / DMA / VPU 编码器继续由视频侧单 owner 进程独占；
    本服务只经 HTTP 域接口调用，不得新开 SDK/采集实例、不得重复编码。
  * 8093 控制接口有同源（Origin/Host）校验：Origin 不在 (None, http://Host)
    即 403。urllib 是非浏览器客户端，默认**不发送 Origin 头**，正好落在
    服务端允许集（None）里，同源检查天然通过——这是合法通道而非绕过；
    本模块绝不伪造 Origin/Referer 去伪装浏览器同源请求。
  * 识别结果必须携带本次 frame_id/frame_token 与采集/交付年龄（ages）；
    字段缺失一律 None，不编造；过期（stale）结果不得用于任何授权。
  * board_verified 恒为 False，直到板端验收通过并回写 docs 登记页。

目标端点备忘（以现场服务核对为准）：
  8091  POST /api/recognize-camera   单帧识别（空请求体；frame_id/token、PAD、identities）
        GET  /api/camera/preview.jpg 按需快照（无帧 → 503 JSON）
  8092  GET  /api/status             就绪探测（open_view 用）
        GET  /h264/manifest.json     只读预览清单（本模块只构造地址，不代理分片）
  8093  POST /api/mode               录像模式 off/auto/manual（回读校验）
        GET  /api/recordings         分页列表（结构未知处原样透传不猜）
        POST /api/purge              清空录像（confirm 必须==id，客户端先校验、服务端再校验）

py3.8 兼容：纯标准库（urllib / json / hashlib / os / tempfile / http.client），
无 3.9+ 语法；错误一律复用 control/errors.py 的 DomainError 体系，不新造异常类。
"""
import hashlib
import http.client
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .. import ids
from ..errors import DomainError, VideoDomainNotConnected

CONFIG = {
    'enabled': True,
    'targets': {'face': 'http://127.0.0.1:8091',
                'stream': 'http://127.0.0.1:8092',
                'record': 'http://127.0.0.1:8093'},
    'media_root': None,
}
CONNECT_DOC = 'docs/VIDEO-DOMAIN-PLACEHOLDER.md'
_TIMEOUT_S = 10.0
_RECORD_QUERY_KEYS = ('page', 'limit', 'from', 'to', 'trash')
_MODES = ('off', 'auto', 'manual')

_STORE = None
_UNSET = object()


def configure(enabled=_UNSET, targets=None, media_root=_UNSET, store=_UNSET):
    """注入部署/测试配置（服务层启动或测试装配时调用）；不传的键保持原值。

    enabled：适配器总开关；False 时所有动作按 video_domain_not_connected 报告。
    targets：按服务名（face/stream/record）合并 base url，不整表替换。
    media_root / store：快照受控媒体目录与业务库句柄；显式传 None 表示清除——
    media_root=None 时 snapshot 不落盘、不写库，如实返回 saved=False。
    """
    global _STORE
    if enabled is not _UNSET:
        CONFIG['enabled'] = bool(enabled)
    if targets:
        merged = dict(CONFIG['targets'])
        merged.update(targets)
        CONFIG['targets'] = merged
    if media_root is not _UNSET:
        CONFIG['media_root'] = media_root
    if store is not _UNSET:
        _STORE = store


def _base(service):
    """取服务 base url；未启用/未配置按「未接」语义报告（501 保留）。"""
    if not CONFIG['enabled']:
        raise VideoDomainNotConnected(service, '视频域适配器已禁用（enabled=False）')
    base = CONFIG['targets'].get(service)
    if not base:
        raise VideoDomainNotConnected(service, '视频服务 %s 未配置目标地址' % service)
    return base


def _http(base, method, path, payload=None):
    """发一次 HTTP 请求，返回 (status, body_bytes)。

    传输层失败（连接拒绝/超时/HTTP 协议坏包）→ DomainError('video_unreachable')；
    HTTP 状态层错误原样交回调用方映射。urllib 默认不发 Origin 头（见模块
    docstring），本函数与所有调用方都不得添加 Origin/Referer 伪装同源。
    """
    url = base.rstrip('/') + path
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b''
        return exc.code, body
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise DomainError('video_unreachable',
                          '视频服务不可达: %s %s（%s）' % (method, url, exc),
                          {'service_base': base})


def _json_of(body):
    """解析 JSON 响应体；坏 JSON 返回 None，由调用方决定语义。"""
    try:
        return json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None


def _snippet(body, limit=200):
    """响应体文本摘要（错误消息用），不假装它是结构化数据。"""
    text = body.decode('utf-8', 'replace').strip()
    if len(text) > limit:
        text = text[:limit] + '…'
    return text


def _pick(key, *dicts):
    """按 specificity 顺序在多层回包里找第一个出现的键；缺失→None，不猜结构。"""
    for data in dicts:
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


def _raise_no_frame(body, what):
    """503 → video_no_frame；原始 error 透传（不吞、不改写）。"""
    parsed = _json_of(body)
    original = parsed.get('error') if isinstance(parsed, dict) else None
    detail = original if original is not None else (_snippet(body) or '503')
    raise DomainError('video_no_frame', '%s无新鲜帧/相机不可用: %s' % (what, detail),
                      {'error': original, 'body_snippet': _snippet(body)})


def recognize(target=None, **kw):
    """V1：POST 8091 /api/recognize-camera（空请求体）→ 映射识别结果。

    契约回包 {"entry":{"result":{"identities":[...],"face_count":N,
    "presentation_attack_detection":{...}}}}；frame_id/frame_token/ages 若在
    result/entry/顶层任一层携带则原样透传（按此顺序取第一个命中），
    缺失一律 None，不编造。
    503 → video_no_frame（原始 error 透传）；非 200 / 非 JSON → video_service_error；
    传输失败 → video_unreachable。target 仅作调用方记录，选路固定 targets['face']。
    """
    base = _base('face')
    status, body = _http(base, 'POST', '/api/recognize-camera')
    if status == 503:
        _raise_no_frame(body, '8091 识别')
    if status != 200:
        raise DomainError('video_service_error',
                          '8091 识别服务返回非 200: %s' % _snippet(body),
                          {'status': status})
    payload = _json_of(body)
    if not isinstance(payload, dict):
        raise DomainError('video_service_error', '8091 识别服务返回非 JSON', {'status': status})
    entry = payload.get('entry') if isinstance(payload.get('entry'), dict) else {}
    result = entry.get('result') if isinstance(entry.get('result'), dict) else {}
    return {
        'frame_id': _pick('frame_id', result, entry, payload),
        'frame_token': _pick('frame_token', result, entry, payload),
        'identities': result.get('identities'),
        'face_count': result.get('face_count'),
        'pad': result.get('presentation_attack_detection'),
        'ages': _pick('ages', result, entry, payload),
    }


def snapshot(target=None, **kw):
    """V2：GET 8091 /api/camera/preview.jpg → 受控落盘 + media_assets 行。

    配置了 media_root（经 configure 注入，须同时注入业务库 _STORE）时：
    字节写同目录临时文件（write→flush→fsync）→ os.replace 原子改名 →
    目录 fsync（Windows 无目录 fsync 时容忍失败）→ 事务写
    media_assets(storage_root, relative_path='snapshots/<id>.jpg',
    kind='image/jpeg', size_bytes, sha256)，返回 saved=True。
    未配置 media_root：不落盘、不写库，返回 saved=False 与 sha256/size。
    503（无帧）→ video_no_frame；传输失败 → video_unreachable；
    非 200 / 空 200 响应体 → video_service_error。
    """
    base = _base('face')
    status, body = _http(base, 'GET', '/api/camera/preview.jpg')
    if status == 503:
        _raise_no_frame(body, '8091 快照')
    if status != 200:
        raise DomainError('video_service_error',
                          '8091 快照返回非 200: %s' % _snippet(body), {'status': status})
    if not body:
        raise DomainError('video_service_error', '8091 快照返回空响应体', {'status': status})
    sha = hashlib.sha256(body).hexdigest()
    media_root = CONFIG['media_root']
    if not media_root:
        return {'media_id': None, 'sha256': sha, 'size_bytes': len(body), 'saved': False}
    if _STORE is None:
        raise DomainError('video_service_error',
                          '已配置 media_root 但未注入业务库 store，无法登记 media_assets')
    media_id = ids.new_id()
    relative = 'snapshots/%s.jpg' % media_id
    _atomic_write(os.path.join(media_root, 'snapshots', '%s.jpg' % media_id), body)
    with _STORE.tx() as conn:
        conn.execute(
            'INSERT INTO media_assets(media_id, storage_root, relative_path, kind,'
            ' size_bytes, sha256, status, created_at) VALUES(?,?,?,?,?,?,?,?)',
            (media_id, media_root, relative, 'image/jpeg', len(body), sha,
             'registered', ids.now_iso()))
    return {'media_id': media_id, 'sha256': sha, 'size_bytes': len(body), 'saved': True}


def _atomic_write(full_path, data):
    """tmp→fsync→os.replace→目录 fsync（平台容忍）的一致性落盘。"""
    directory = os.path.dirname(full_path)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.media-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, full_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # Windows 打不开目录句柄：容忍
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # 平台不支持目录 fsync：容忍
    finally:
        os.close(dir_fd)


def open_view(client_id=None, **kw):
    """V3：GET 8092 /api/status 确认 ready → 返回 /h264/manifest.json 播放地址。

    「已看到画面」以客户端回执为准：本结果只代表地址当前可用（ready），
    不播假成功；ready 缺失/为假 → video_service_error（如实报未就绪）。
    """
    base = _base('stream')
    status, body = _http(base, 'GET', '/api/status')
    if status != 200:
        raise DomainError('video_service_error',
                          '8092 状态返回非 200: %s' % _snippet(body), {'status': status})
    payload = _json_of(body)
    if not isinstance(payload, dict):
        raise DomainError('video_service_error', '8092 状态返回非 JSON', {'status': status})
    ready = payload.get('ready')
    if ready is not True:
        raise DomainError('video_service_error',
                          '流服务未就绪（ready=%r）' % (ready,),
                          {'ready': ready, 'status': status})
    return {'url': base.rstrip('/') + '/h264/manifest.json', 'client_id': client_id,
            'ready': True, 'stream_status': payload}


def set_mode(mode=None, device_id=None, **kw):
    """V4：POST 8093 /api/mode → 回读校验 mode 一致才算 armed。

    8093 有同源（Origin/Host）校验：urllib 默认不发 Origin，落在服务端
    允许集（None）天然通过；本模块不伪造 Origin 头。
    回读 {"mode"} 与请求值不一致 → video_service_error（不假成功）；
    actively_recording 无法从本接口得知，如实返回 None（确有新鲜帧与否
    属视频侧采集 owner 的状态，本服务不猜）。
    """
    if mode not in _MODES:
        raise DomainError('invalid_params', '录像模式必须是 off/auto/manual',
                          {'mode': mode})
    base = _base('record')
    status, body = _http(base, 'POST', '/api/mode', payload={'mode': mode})
    if status != 200:
        raise DomainError('video_service_error',
                          '8093 /api/mode 返回非 200: %s' % _snippet(body),
                          {'status': status})
    payload = _json_of(body)
    if not isinstance(payload, dict):
        raise DomainError('video_service_error', '8093 /api/mode 返回非 JSON',
                          {'status': status})
    if payload.get('mode') != mode:
        raise DomainError('video_service_error',
                          '录像模式回读不一致：请求 %s，回读 %r' % (mode, payload.get('mode')),
                          {'requested': mode, 'readback': payload.get('mode')})
    return {'mode': mode, 'armed': True, 'actively_recording': None}


def list_recordings(target=None, **kw):
    """V5：GET 8093 /api/recordings → 透传 JSON 与状态码，不猜结构。

    支持查询参数 page/limit/from/to/trash（调用方经关键字传入，None 不带）。
    返回 {'status': <http 状态>, 'data': <原样 JSON 或 None（非 JSON 时）>}；
    非 200 也透传（语义由调用方按状态码决定）；传输失败 → video_unreachable。
    """
    base = _base('record')
    query = dict((key, kw[key]) for key in _RECORD_QUERY_KEYS if kw.get(key) is not None)
    path = '/api/recordings'
    if query:
        path += '?' + urllib.parse.urlencode(query)
    status, body = _http(base, 'GET', path)
    return {'status': status, 'data': _json_of(body)}


def play(recording_id=None, **kw):
    """V5：按 recording_id 给回放地址（MP4 Range），不发请求、不取视频体。

    地址可用性由播放端按 Range 请求自行核实；本函数不预取、不假成功、
    不经过 LLM。recording_id 做 URL 编码防路径注入。
    """
    if not recording_id or not isinstance(recording_id, str):
        raise DomainError('invalid_params', 'play 需要 recording_id',
                          {'recording_id': recording_id})
    base = _base('record')
    url = base.rstrip('/') + '/api/recordings/' + urllib.parse.quote(recording_id,
                                                                     safe='') + '/video'
    return {'url': url}


def purge(recording_id=None, confirm=None, **kw):
    """清空指定录像：POST 8093 /api/purge，confirm 必须 == recording_id。

    客户端先行校验 confirm==id 再发（服务端还会再校验一次）；不一致 →
    invalid_params 且**不发请求**。返回 {'status':..., 'data':<原样 JSON 或
    None>}，非 200 透传。本函数只发 HTTP；业务库 recordings 目录与
    回收站/清空状态机仍属 V6 留空，因此不写任何表。
    """
    if recording_id is None or confirm != recording_id:
        raise DomainError('invalid_params', 'purge 需要 confirm 与 recording_id 完全一致',
                          {'recording_id': recording_id,
                           'confirm_matches': confirm == recording_id})
    base = _base('record')
    status, body = _http(base, 'POST', '/api/purge',
                         payload={'id': recording_id, 'confirm': confirm})
    return {'status': status, 'data': _json_of(body)}


def status():
    """能力状态：http-client 模式；board_verified 恒 False 直到板端验收。"""
    return {'connected': bool(CONFIG['enabled']),
            'mode': 'http-client',
            'board_verified': False,
            'doc': CONNECT_DOC,
            'targets': dict(CONFIG['targets'])}
