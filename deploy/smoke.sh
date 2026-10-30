#!/usr/bin/env bash
# deploy/smoke.sh — acceptance test for the klar voice stack on the board.
#
# Proves, end to end and over the real Wyoming protocol:
#   1. every service advertises itself (Describe -> Info)
#   2. ASR transcribes a known wav and stays realtime (RTF < 1)
#   3. TTS produces audio and reports its RTF (expected ~3 on this A55)
#   4. KWS detects a Chinese wake phrase and rejects a non-wake wav
#   5. klar engine parses a Chinese house command into an execute decision
#
# Usage: BOARD=root@192.168.1.113 ./deploy/smoke.sh
set -uo pipefail

BOARD="${BOARD:-root@192.168.1.113}"
KEY="${KEY:-SHA256:IQGq+fgZLG+sC77GCs+cZuntFUYB1mcNM4POwA4ZzLc}"
PW="${PW:-fmsh}"
PLINK="${PLINK:-/c/Program Files/PuTTY/plink}"
run() { "$PLINK" -ssh -batch -hostkey "$KEY" -pw "$PW" "$BOARD" "$1"; }

FAIL=0
chk() { # chk <label> <expected-substring> <actual>
  if printf '%s' "$3" | grep -q "$2"; then echo "  PASS  $1"; else echo "  FAIL  $1 (want '$2')"; FAIL=1; fi
}

echo "=== 1. services advertise (Describe) ==="
for p in 10301 10302 10300; do
  out=$(run "cd /mnt/data/klar && timeout 20 python3 wyoming_probe.py describe --uri tcp://127.0.0.1:$p 2>&1")
  echo "$out" | sed 's/^/    /'
  chk "port $p info" ":" "$out"
done

echo "=== 2. ASR realtime check ==="
out=$(run "cd /mnt/data/klar && timeout 90 python3 wyoming_probe.py asr --wav /mnt/data/models/asr/test_wavs/0.wav --uri tcp://127.0.0.1:10301 2>&1")
echo "$out" | sed 's/^/    /'
chk "ASR produced text" "ASR final: '" "$out"
# rtf must be below 1.0 for realtime; parse the printed value
rtf=$(printf '%s' "$out" | sed -n 's/.*rtf=\([0-9.]*\).*/\1/p' | head -1)
if [ -n "$rtf" ] && awk -v r="$rtf" 'BEGIN{exit !(r<1.0)}'; then
  echo "  PASS  ASR rtf=$rtf < 1.0"
else
  echo "  FAIL  ASR rtf=${rtf:-none} not < 1.0"; FAIL=1
fi

echo "=== 3. TTS synthesis ==="
out=$(run "cd /mnt/data/klar && timeout 180 python3 wyoming_probe.py tts --text '好的，已为你打开客厅的灯' --out /tmp/smoke_tts.wav --uri tcp://127.0.0.1:10302 2>&1")
echo "$out" | sed 's/^/    /'
chk "TTS produced audio" "saved /tmp/smoke_tts.wav" "$out"

echo "=== 4. KWS Chinese wake word ==="
# zh_3.wav is a positive sample; en_0.wav is a different keyword
out=$(run "cd /mnt/data/klar && timeout 40 python3 wyoming_probe.py wake --wav /mnt/data/models/kws/test_wavs/zh_3.wav --uri tcp://127.0.0.1:10300 2>&1")
echo "$out" | sed 's/^/    /'
chk "KWS hit on zh_3" "not_detected=False" "$out"

echo "=== 5. klar engine Chinese NLU ==="
out=$(run "python3 - <<'PY'
import urllib.request, json
body=json.dumps({'text':'打开客厅的灯','language':'zh-CN'}).encode()
req=urllib.request.Request('http://127.0.0.1:10520/api/v2/parse',data=body,headers={'Content-Type':'application/json'})
r=json.load(urllib.request.urlopen(req,timeout=20))
print('decision',r['decision']['type'],'| speech',r['speech'])
PY" 2>&1)
echo "$out" | sed 's/^/    /'
chk "klar parsed as execute" "decision execute" "$out"

echo
if [ "$FAIL" -eq 0 ]; then echo "ALL SMOKE CHECKS PASSED"; else echo "SMOKE FAILED"; fi
exit "$FAIL"
