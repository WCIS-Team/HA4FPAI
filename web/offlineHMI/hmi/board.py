# -*- coding: utf-8 -*-
"""板上 REST 客户端：8091/8092/8093。

接口契约对齐源码（A 级）：
  serve_photos.py        /api/camera/preview.jpg  /api/camera/status
                         /api/recognize  /api/recognize-camera(空体)  /api/enroll(X-Person-Name)
                         /api/gallery  /gallery-media/<id>  /api/health
  recording_service.py   /api/mode{mode}  /api/recordings  /api/recordings/<id>/frame?at=
                         /api/recordings/<id>.<ext>  /api/trash|restore|purge{id}  /api/storage
标准库实现（urllib），与板端无第三方依赖对齐；校机也可用。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class Board:
    def __init__(self, base: str, timeout: float = 6.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    # -- 基础 ----------------------------------------------------------------
    def _req(self, method: str, path: str, data: bytes | None = None,
             headers: dict | None = None, raw: bool = False):
        url = self.base + path
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                if raw:
                    return r.status, body, dict(r.headers)
                ctype = r.headers.get("Content-Type", "")
                return r.status, (json.loads(body) if "json" in ctype else body), None
        except urllib.error.HTTPError as e:
            body = e.read()
            if raw:
                return e.code, body, dict(e.headers)
            try:
                return e.code, json.loads(body), None
            except Exception:
                return e.code, {"error": body[:200].decode("utf-8", "replace")}, None

    def _post_json(self, path: str, obj: dict):
        return self._req("POST", path, json.dumps(obj).encode(),
                         {"Content-Type": "application/json"})

    # -- 8091 相机 -----------------------------------------------------------
    def camera_preview_jpeg(self) -> bytes:
        st, body, _ = self._req("GET", "/api/camera/preview.jpg", raw=True)
        if st != 200:
            raise RuntimeError("preview: %s %s" % (st, body[:120]))
        return body

    def camera_status(self) -> dict:
        st, body, _ = self._req("GET", "/api/camera/status")
        if st != 200:
            raise RuntimeError("camera status %s" % st)
        return body

    def recognize_camera(self) -> dict:
        """空体 POST = 从相机取当前帧识别（serve_photos.py:228）。"""
        st, body, _ = self._req("POST", "/api/recognize-camera", b"",
                                {"Content-Length": "0"})
        if st != 200:
            raise RuntimeError(body.get("error", st) if isinstance(body, dict) else st)
        return body

    def recognize_bytes(self, jpeg: bytes, name: str = "upload.jpg") -> dict:
        return self._req("POST", "/api/recognize", jpeg,
                         {"Content-Type": "application/octet-stream",
                          "X-Photo-Name": name})[1]

    def enroll_bytes(self, jpeg: bytes, person: str, name: str = "enroll.jpg") -> dict:
        return self._req("POST", "/api/enroll", jpeg,
                         {"Content-Type": "application/octet-stream",
                          "X-Person-Name": person,
                          "X-Photo-Name": name})[1]

    def gallery(self) -> dict:
        return self._req("GET", "/api/gallery")[1]

    def gallery_media(self, photo_id: str) -> bytes:
        st, body, _ = self._req("GET", "/gallery-media/" + photo_id, raw=True)
        if st != 200:
            raise RuntimeError("gallery-media %s" % st)
        return body

    # -- 8093 录像 -----------------------------------------------------------
    def recorder_status(self) -> dict:
        return self._req("GET", "/api/status")[1]

    def recorder_mode(self, mode: str) -> dict:
        return self._post_json("/api/mode", {"mode": mode})[1]

    def recordings(self, page: int = 1, limit: int = 20, trash: bool = False) -> dict:
        q = "?page=%d&limit=%d%s" % (page, limit, "&trash=1" if trash else "")
        return self._req("GET", "/api/recordings" + q)[1]

    def recording_frame_jpeg(self, rec_id: str, at: float) -> bytes:
        st, body, _ = self._req(
            "GET", "/api/recordings/%s/frame?at=%.3f" % (rec_id, at), raw=True)
        if st != 200:
            raise RuntimeError("frame %s" % st)
        return body

    def recording_download(self, rec_id: str, ext: str) -> bytes:
        st, body, hdr = self._req(
            "GET", "/api/recordings/%s.%s" % (rec_id, ext), raw=True)
        if st != 200:
            raise RuntimeError("download %s" % st)
        return body

    def trash(self, rec_id: str) -> dict:
        return self._post_json("/api/trash", {"id": rec_id})[1]

    def restore(self, rec_id: str) -> dict:
        return self._post_json("/api/restore", {"id": rec_id})[1]

    def purge(self, rec_id: str) -> dict:
        return self._post_json("/api/purge", {"id": rec_id, "confirm": rec_id})[1]

    def storage(self) -> dict:
        return self._req("GET", "/api/storage")[1]


class MockServer:
    """无板离线开发：与 Board 同接口的假实现，固定几条录像/画廊数据。"""

    RECS = [
        {"id": "rec-20261104T030000Z-0aa11bb2", "duration": 61.2, "frames": 1836},
        {"id": "rec-20261104T021500Z-0cc33dd4", "duration": 30.0, "frames": 900},
        {"id": "rec-20261103T221000Z-0ee55ff6", "duration": 120.5, "frames": 3615},
    ]

    def _png_frame(self, t: float) -> bytes:
        import io
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (960, 540), (16, 24, 32))
        d = ImageDraw.Draw(im)
        d.text((24, 24), "MOCK frame t=%.1fs" % t, fill=(240, 240, 240))
        d.ellipse((400 + int(t * 40) % 200, 220, 520 + int(t * 40) % 200, 340),
                  outline=(80, 220, 120), width=3)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def camera_preview_jpeg(self):
        return self._png_frame(time.time() % 60)

    def camera_status(self):
        return {"ready": True, "width": 960, "height": 540, "fps": 30, "codec": "jpeg"}

    def recognize_camera(self):
        return {"status": "ok", "faces": [], "elapsed_ms": 123}

    def recognize_bytes(self, jpeg, name=""):
        return {"status": "ok", "faces": [], "elapsed_ms": 145}

    def enroll_bytes(self, jpeg, person, name=""):
        return {"status": "ok", "name": person}

    def gallery(self):
        return {"photos": []}

    def gallery_media(self, photo_id):
        return self._png_frame(0)

    def recorder_status(self):
        return {"mode": "off", "current": None}

    def recorder_mode(self, mode):
        return {"mode": mode}

    def recordings(self, page=1, limit=20, trash=False):
        return {"page": 1, "total": len(self.RECS),
                "recordings": self.RECS[:limit]}

    def recording_frame_jpeg(self, rec_id, at):
        return self._png_frame(at)

    def recording_download(self, rec_id, ext):
        return b"MOCK"

    def trash(self, rec_id):
        return {"id": rec_id, "trashed": True}

    def restore(self, rec_id):
        return {"id": rec_id, "restored": True}

    def purge(self, rec_id):
        return {"id": rec_id, "purged": True}

    def storage(self):
        return {"used_mib": 512, "quota_mib": 4096}
