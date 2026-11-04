"""Run one command on the FMSH board with a long, explicit timeout and show the exit code.

board-ssh.py caps every exec at 60 s, which is too short for a Qwen ignition step and makes a
hang look like a client bug. This keeps the same read-only-by-default posture: you pass the
command, it prints stdout/stderr/rc.

Usage:
    python qwen_board.py "cd /root/qwen2 && ..." [timeout_s]
"""
import socket
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import paramiko

HOST = "192.168.1.113"
CMD = sys.argv[1]
TMO = int(sys.argv[2]) if len(sys.argv) > 2 else 600

sock = socket.create_connection((HOST, 22), timeout=8)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password="fmsh", sock=sock,
          look_for_keys=False, allow_agent=False, timeout=10)

t0 = time.time()
_, out, err = c.exec_command(CMD, timeout=TMO)
rc = out.channel.recv_exit_status()
so = out.read().decode("utf-8", "replace")
se = err.read().decode("utf-8", "replace")
sys.stdout.write(so)
if se.strip():
    sys.stdout.write("[stderr] " + se)
print("[rc=%s] %.1fs" % (rc, time.time() - t0))
c.close()
