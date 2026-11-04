# -*- coding: utf-8 -*-
"""视频域适配器 —— 本分支**有意留空**（她 2026-11-05 决定）。

规划正本：wiki/mainchain/联合控制接口与视频接入.md §视频域适配与媒体。
留空范围与逐项接法：docs/VIDEO-DOMAIN-PLACEHOLDER.md（必读）。

硬边界（接回时也必须遵守）：
  * 摄像头 / iCraft SDK / DMA / VPU 编码器继续由视频侧单 owner 进程独占；
    本服务只经 HTTP 域接口调用，不得新开 SDK/采集实例、不得重复编码。
  * 8093 控制接口有同源（Origin/Host）校验；跨端口调用须经服务端同源转发
    或权限等同的代理，不得用全局 CORS 放水。
  * 识别结果必须携带本次 frame_id/frame_token 与采集/交付年龄；过期结果
    不得用于授权（规划 §安全与事件反馈）。

目标端点备忘（以现场服务核对为准，当前不作可用性承诺）：
  8091  POST /api/recognize-camera   单帧识别（frame_id/token、PAD、identities）
        GET  /api/camera/preview.jpg 按需快照（编码为 H.264 时可能不可用）
        POST /api/enroll             登记照片（X-Person-Name）
  8092  GET  /h264/manifest.json | init.mp4 | seg-*.m4s   只读预览分片
  8093  POST /api/mode                录像模式 off/auto/manual（回读校验）
        GET  /api/recordings          分页列表
        POST /api/trash | /api/restore | /api/purge（purge 需 confirm==id）
"""
from ..errors import VideoDomainNotConnected

TARGETS = {'face': 'http://127.0.0.1:8091',
           'stream': 'http://127.0.0.1:8092',
           'record': 'http://127.0.0.1:8093'}
CONNECT_DOC = 'docs/VIDEO-DOMAIN-PLACEHOLDER.md'


def _stub(capability):
    raise VideoDomainNotConnected(capability)


def recognize(target=None, **kw):
    """接回：POST 8091 /api/recognize-camera → 映射 frame_token/identities/PAD。"""
    _stub('camera.recognize')


def snapshot(target=None, **kw):
    """接回：GET 8091 /api/camera/preview.jpg → 落持久文件 + media_assets 行。"""
    _stub('camera.snapshot')


def open_view(client_id=None, **kw):
    """接回：返回 8092 /h264/ 播放地址；播放确认由客户端回执，不发假成功。"""
    _stub('camera.open_view')


def set_mode(mode=None, device_id=None, **kw):
    """接回：POST 8093 /api/mode → 回读模式区分 armed / actively_recording。"""
    _stub('recording.set_mode')


def list_recordings(target=None, **kw):
    """接回：GET 8093 /api/recordings → 分页映射进业务目录（保留 trash 语义）。"""
    _stub('recording.list')


def play(recording_id=None, **kw):
    """接回：按 recording_id 取回放地址（MP4 Range），不经过 LLM。"""
    _stub('recording.play')


def status():
    """能力状态：恒为未接；接回后由真实健康检查替换。"""
    return {'connected': False, 'doc': CONNECT_DOC, 'targets': TARGETS}
