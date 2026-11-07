#!/usr/bin/env python3
"""Bridge the board's live camera into a form Home Assistant can play.

Board side (192.168.1.113), all read-only:
    GET 8092/api/status                                  -> {session, ready, ...}
    GET 8091/api/camera/h264/live.mp4?session=<session>  -> continuous video/mp4
    GET 8091/api/camera/h264/live-status?session=<s>     -> {ready, sequence, ...}

The board deliberately does not publish an m3u8 (h264_media.py whitelists only
init.mp4 and seg-*.m4s, and the playlist route answers 503). This bridge reads
the live fragmented MP4 and republishes it as an HLS feed plus a JPEG still, so
HA's generic camera can consume the board's real camera.

It never opens a capture instance and never touches the iCraft SDK, DMA or VPU:
the board stays the single owner of the camera.
"""
import argparse
import http.server
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

SESSION_URL = 'http://192.168.1.113:8092/api/status'
LIVE_URL = 'http://192.168.1.113:8091/api/camera/h264/live.mp4?session=%s'


def board_session(timeout=6.0):
    with urllib.request.urlopen(SESSION_URL, timeout=timeout) as resp:
        return json.load(resp).get('session')


class FfmpegRepublisher:
    """Read the board's live MP4 and write an HLS window plus one JPEG still."""

    def __init__(self, session, out_dir, window=6, segment_time=1.0):
        self.session = session
        self.out_dir = out_dir
        self.window = window
        self.segment_time = segment_time
        self.proc = None
        self.lock = threading.Lock()
        self.session_changed = False
        self.error = None
        self.playlist = None
        self.init = None
        self.segments = []          # names, oldest first
        self.last_still = None
        self.frames = 0

    def start(self):
        self.proc = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', LIVE_URL % self.session,
            '-c', 'copy',
            '-f', 'hls', '-hls_time', str(self.segment_time),
            '-hls_list_size', str(self.window),
            '-hls_segment_type', 'fmp4',
            '-hls_flags', 'delete_segments+independent_segments+temp_file',
            '-hls_fmp4_init_filename', 'init.mp4',
            '-hls_segment_filename', self.out_dir + '/seg-%09d.m4s',
            '-y', self.out_dir + '/index.m3u8',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._still_loop, daemon=True).start()

    def _watch(self):
        err = self.proc.stderr.read().decode('utf-8', 'replace') if self.proc.stderr else ''
        with self.lock:
            self.error = (err.strip().splitlines() or [''])[-1][:160]

    def _still_loop(self):
        """Keep one fresh JPEG for HA's still_image_url."""
        while True:
            try:
                subprocess.run([
                    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-i', LIVE_URL % self.session, '-frames:v', '1',
                    '-f', 'image2', '-y', self.out_dir + '/still.jpg',
                ], timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                path = self.out_dir + '/still.jpg'
                with self.lock:
                    if self._size(path) > 1000:
                        with open(path, 'rb') as fh:
                            self.last_still = fh.read()
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.error = 'still: %s' % str(exc)[:100]
            time.sleep(1.0)

    @staticmethod
    def _size(path):
        try:
            import os
            return os.path.getsize(path)
        except OSError:
            return 0

    def refresh(self):
        """Re-read the playlist ffmpeg writes and pick up new segments.

        ffmpeg rewrites index.m3u8 in place as the window rolls (delete_segments),
        so the playlist text is passed through verbatim: HA/ffmpeg need the live
        EXT-X-MEDIA-SEQUENCE and a moving window, otherwise the stream looks ended.
        """
        try:
            with open(self.out_dir + '/index.m3u8', encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            return
        segs = [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.startswith('#')]
        if not segs:
            return
        init = 'init.mp4'
        for line in text.splitlines():
            if line.startswith('#EXT-X-MAP:'):
                init = line.split('URI="')[1].rstrip('"')
        with self.lock:
            self.playlist = text
            self.init = init
            self.segments = segs

    def read_file(self, name):
        import os
        path = os.path.join(self.out_dir, name)
        try:
            with open(path, 'rb') as fh:
                return fh.read()
        except OSError:
            return None


class Handler(http.server.BaseHTTPRequestHandler):
    bridge = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type='text/plain'):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        bridge = self.bridge
        if path in ('/hls/playlist.m3u8', '/playlist.m3u8'):
            bridge.refresh()
            with bridge.lock:
                playlist = bridge.playlist
            if not playlist:
                return self._send(503, 'no media yet')
            return self._send(200, playlist, 'application/vnd.apple.mpegurl')
        if path in ('/hls/master_playlist.m3u8', '/master_playlist.m3u8'):
            bridge.refresh()
            with bridge.lock:
                codec = bridge.codec or 'avc1.4d401f'
            return self._send(200, '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="%s"\n'
                              'playlist.m3u8\n' % codec, 'application/vnd.apple.mpegurl')
        if path == '/snapshot.jpg':
            with bridge.lock:
                still = bridge.last_still
            if still is None:
                return self._send(503, 'no still yet')
            return self._send(200, still, 'image/jpeg')
        if path == '/status.json':
            with bridge.lock:
                payload = {'session': bridge.session, 'error': bridge.error,
                           'segments': len(bridge.segments), 'init': bridge.init,
                           'still_bytes': len(bridge.last_still or b'')}
            return self._send(200, json.dumps(payload, indent=1), 'application/json')
        if path.startswith('/hls/'):
            name = path[len('/hls/'):].rsplit('/', 1)[-1]
            blob = bridge.read_file(name)
            if blob is None:
                return self._send(404, 'media not ready')
            ctype = 'video/mp4' if name.endswith('.mp4') else 'video/iso.segment'
            return self._send(200, blob, ctype)
        return self._send(404, 'not found')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', default='192.168.1.113')
    parser.add_argument('--port', type=int, default=8099)
    parser.add_argument('--window', type=int, default=6)
    parser.add_argument('--workdir', default='/tmp/board-hls')
    args = parser.parse_args()

    import os
    os.makedirs(args.workdir, exist_ok=True)
    session = board_session()
    print('board session: %s' % session, flush=True)
    bridge = FfmpegRepublisher(session, args.workdir, window=args.window)
    bridge.start()
    Handler.bridge = bridge
    server = http.server.ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
    print('board camera bridge on 127.0.0.1:%d  (playlist /hls/playlist.m3u8, still /snapshot.jpg)'
          % args.port, flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
