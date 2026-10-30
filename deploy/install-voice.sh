#!/usr/bin/env bash
# deploy/install-voice.sh — install / refresh the klar voice stack on the board.
#
# Runs FROM the workstation (or a build host), not on the board itself:
#   113 is 993 MB with 4 A55 cores and must not compile anything.
#   Nothing here compiles: sherpa-onnx 2.0.1 + wyoming 1.10.2 are already
#   installed as wheels, and the klar engine is a prebuilt static aarch64 binary.
#
# Usage:
#   BOARD=root@192.168.1.113 ./deploy/install-voice.sh
#
# Requires: PuTTY pscp/plink (Windows) or ssh/scp (anything else).
set -euo pipefail

BOARD="${BOARD:-root@192.168.1.113}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${KEY:-SHA256:IQGq+fgZLG+sC77GCs+cZuntFUYB1mcNM4POwA4ZzLc}"
PW="${PW:-fmsh}"
REMOTE_LIB="/mnt/data/klar"
REMOTE_CFG="/root/klar-voice.json"

if command -v pscp >/dev/null 2>&1 || [ -x "/c/Program Files/PuTTY/pscp" ]; then
  PSCP="${PSCP:-/c/Program Files/PuTTY/pscp}"
  PLINK="${PLINK:-/c/Program Files/PuTTY/plink}"
  put()  { "$PSCP" -batch -hostkey "$KEY" -pw "$PW" "$1" "$BOARD:$2" >/dev/null; }
  run()  { "$PLINK" -ssh -batch -hostkey "$KEY" -pw "$PW" "$BOARD" "$1"; }
else
  put()  { scp "$1" "$BOARD:$2"; }
  run()  { ssh "$BOARD" "$1"; }
fi

echo "[1/6] check the board has room (rootfs must stay above ~2G free)"
run "df -h / /mnt/data | tail -n +2"

echo "[2/6] push python services + config"
run "mkdir -p $REMOTE_LIB"
for f in voice_kit.py voice_common.py klar_asr.py klar_tts.py klar_wake.py wyoming_probe.py; do
  put "$REPO_ROOT/boards/$f" "$REMOTE_LIB/$f"
done
put "$REPO_ROOT/boards/klar-voice.json" "$REMOTE_CFG"

echo "[3/6] byte-compile on the board (never build there)"
run "cd $REMOTE_LIB && for f in voice_kit.py voice_common.py klar_asr.py klar_tts.py klar_wake.py wyoming_probe.py; do python3 -m py_compile \$f || { echo \"FAIL \$f\"; exit 1; }; done; echo COMPILE_OK"

echo "[4/6] install systemd units"
for u in klar-voice-asr klar-voice-tts klar-voice-wake klar-nlu-engine; do
  put "$REPO_ROOT/deploy/$u.service" "/etc/systemd/system/$u.service"
done
run "systemctl daemon-reload && systemctl enable klar-voice-asr klar-voice-tts klar-voice-wake klar-nlu-engine"

echo "[5/6] klar engine binary (skip if present)"
run "if [ ! -x /mnt/data/klar/engine/klar ]; then
  mkdir -p /mnt/data/klar/engine /mnt/data/klar/ha-config /mnt/data/klar/ha-data
  cd /mnt/data/klar/engine
  python3 - <<'PY'
import urllib.request, json, tarfile
api='https://api.github.com/repos/FABBricate-IT-Solutions/klar-ha-nlu/releases/latest'
d=json.load(urllib.request.urlopen(api,timeout=30))
url=[a['browser_download_url'] for a in d['assets'] if 'aarch64' in a['name']][0]
fn=url.split('/')[-1]
urllib.request.urlretrieve(url,fn)
with tarfile.open(fn) as t: t.extractall('.')
print('klar engine',d['tag_name'],'installed')
PY
else echo 'klar engine already present'; fi"

echo "[6/6] restart and report"
run "systemctl restart klar-voice-wake klar-voice-asr klar-voice-tts klar-nlu-engine; sleep 8; systemctl is-active klar-voice-wake klar-voice-asr klar-voice-tts klar-nlu-engine; echo '--- listening ---'; ss -lntp | grep -E '1030[012]|1050[0]|1052[0]' || true"

echo
echo "done. acceptance: ./deploy/smoke.sh"
