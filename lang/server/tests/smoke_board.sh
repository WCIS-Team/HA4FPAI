#!/usr/bin/env bash
# tests/smoke_board.sh —— 板端语音验收（语音归 lang/server 之后；她 2026-11-04 定）
#
# 原在 HA4FPAI/deploy/smoke.sh，随语音链路一并移交到本服务。它验的是**Wyoming 三口**，
# 与上层 klar/executor 无关；第 5 项是顺手对 executor 的交叉检查（上层仍在 HA4FPAI）。
#
# 前提：板上已把本服务部署到 /mnt/data/langserver（见 deploy/README.md），
#       服务由 lang-server-voice.service 起（10300/10301/10302）。
#
# Proves, end to end and over the real Wyoming protocol:
#   1. every voice service advertises itself (Describe -> Info)
#   2. ASR transcribes a known wav and stays realtime (RTF < 1)
#   3. TTS produces audio and reports its RTF (expected ~3 on this A55)
#   4. KWS detects a Chinese wake phrase
#   5. (cross-check) klar executor direct channel still works on the upper layer
#
# Usage: BOARD=root@192.168.1.113 ./tests/smoke_board.sh
set -uo pipefail

BOARD="${BOARD:-root@192.168.1.113}"
KEY="${KEY:-SHA256:IQGq+fgZLG+sC77GCs+cZuntFUYB1mcNM4POwA4ZzLc}"
PW="${PW:-fmsh}"
PLINK="${PLINK:-/c/Program Files/PuTTY/plink}"
LANG_SERVER_DIR="${LANG_SERVER_DIR:-/mnt/data/langserver}"
KLAR_DIR="${KLAR_DIR:-/mnt/data/klar}"
PROBE="tools/wyoming_probe.py"
run() { "$PLINK" -ssh -batch -hostkey "$KEY" -pw "$PW" "$BOARD" "$1"; }

FAIL=0
chk() { # chk <label> <expected-substring> <actual>
  if printf '%s' "$3" | grep -q "$2"; then echo "  PASS  $1"; else echo "  FAIL  $1 (want '$2')"; FAIL=1; fi
}

echo "=== 0. service unit (lang-server-voice) ==="
out=$(run "systemctl is-active lang-server-voice 2>&1")
echo "$out" | sed 's/^/    /'
chk "lang-server-voice active" "active" "$out"

echo "=== 1. services advertise (Describe) ==="
for p in 10301 10302 10300; do
  out=$(run "cd $LANG_SERVER_DIR && timeout 20 python3 $PROBE describe --uri tcp://127.0.0.1:$p 2>&1")
  echo "$out" | sed 's/^/    /'
  chk "port $p info" ":" "$out"
done

echo "=== 2. ASR realtime check ==="
out=$(run "cd $LANG_SERVER_DIR && timeout 90 python3 $PROBE asr --wav /mnt/data/models/asr/test_wavs/0.wav --uri tcp://127.0.0.1:10301 2>&1")
echo "$out" | sed 's/^/    /'
chk "ASR produced text" "ASR final: '" "$out"
rtf=$(printf '%s' "$out" | sed -n 's/.*rtf=\([0-9.]*\).*/\1/p' | head -1)
if [ -n "$rtf" ] && awk -v r="$rtf" 'BEGIN{exit !(r<1.0)}'; then
  echo "  PASS  ASR rtf=$rtf < 1.0"
else
  echo "  FAIL  ASR rtf=${rtf:-none} not < 1.0"; FAIL=1
fi

echo "=== 3. TTS synthesis ==="
out=$(run "cd $LANG_SERVER_DIR && timeout 180 python3 $PROBE tts --text '好的，已为你打开客厅的灯' --out /tmp/smoke_tts.wav --uri tcp://127.0.0.1:10302 2>&1")
echo "$out" | sed 's/^/    /'
chk "TTS produced audio" "saved /tmp/smoke_tts.wav" "$out"

echo "=== 4. KWS Chinese wake word ==="
out=$(run "cd $LANG_SERVER_DIR && timeout 40 python3 $PROBE wake --wav /mnt/data/models/kws/test_wavs/zh_3.wav --uri tcp://127.0.0.1:10300 2>&1")
echo "$out" | sed 's/^/    /'
chk "KWS hit on zh_3" "not_detected=False" "$out"

echo "=== 5. (上层交叉检查) executor 双通道 ==="
out=$(run "cd $KLAR_DIR && python3 klar_executor.py --do '现在什么状态' --json 2>&1")
echo "$out" | sed 's/^/    /'
chk "executor direct channel" '"channel": "direct"' "$out"
chk "executor ran action" '"ok": true' "$out"

echo
if [ "$FAIL" -eq 0 ]; then echo "ALL SMOKE CHECKS PASSED"; else echo "SMOKE FAILED"; fi
exit "$FAIL"