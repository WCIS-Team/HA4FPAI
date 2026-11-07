"""Kill the synthetic RTSP publisher (ffmpeg testsrc2) inside the container."""
import os
import signal
from pathlib import Path

killed = []
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        cmd = (entry / 'cmdline').read_bytes().decode('utf-8', 'replace')
    except OSError:
        continue
    if 'testsrc2' in cmd and 'python' not in cmd:
        try:
            os.kill(int(entry.name), signal.SIGKILL)
            killed.append(entry.name)
        except OSError as exc:
            print('skip', entry.name, exc)
print('killed', killed)
