#!/bin/bash
# hb.sh — 心跳日志器：给"硬挂死"留现场
#
# 为什么需要它（2026-10-11 那次实测逼出来的）：
#   板子整机挂死时，journald 还压在内存缓冲里的日志一行都没落盘，
#   事后 /var/log/journal 里找不到那次启动的任何记录，无法定位卡在哪。
#   心跳改成直接写 SD 卡、每行都自带可判读的现场量，
#   挂死后重新上电读"最后一行"就能知道当时是什么状态。
#
# 每行字段（key=value，便于 grep/awk）：
#   ts=<墙钟> mono=<开机秒> load=<load1> avail=<MemAvailable kB> cma=<CmaFree kB>
#   dstate=<D 状态进程数> top=<rss kB>/<comm> [tgt=<状态> rss=<kB> vsz=<kB> wchan=<..>]
#   dstate 突增＝卡在内核不可中断等待（多半是 I/O）；top/tgt 是谁吃了内存。
#
# 用法：
#   hb.sh start <tag> [interval_s] [pid]   后台起，日志 /root/hb/<tag>.log
#   hb.sh stop  <tag>
#   hb.sh status
#   hb.sh tail  <tag> [n]
#
# 落盘说明：每行是独立的小 write（>> 追加），内核 dirty_expire 默认 30 s 就刷到卡，
# 硬挂死时未刷的那几行可能丢，但只要挂死超过 30 s 之前的行都在盘上。
set -u

LOGDIR=${HB_LOGDIR:-/root/hb}
PROG=hb.sh

case "${1:-}" in
  start)
    TAG=${2:-}
    INT=${3:-${HB_INTERVAL:-5}}
    PIDW=${4:-}
    [ -n "$TAG" ] || { echo "用法: $PROG start <tag> [interval_s] [pid]" >&2; exit 2; }
    case "$INT" in ''|*[!0-9]*) echo "interval 必须是整数秒: $INT" >&2; exit 2 ;; esac
    mkdir -p "$LOGDIR"
    LOG="$LOGDIR/$TAG.log"; PIDF="$LOGDIR/$TAG.pid"
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "hb $TAG 已在跑 (pid $(cat "$PIDF"))"; exit 0
    fi
    setsid bash -c '
      set -u
      LOG=$1; INT=$2; PIDW=$3
      PG=$(getconf PAGESIZE)
      {
        echo "# hb start $(printf "%(%F %T)T\n" -1) host=$(cat /proc/sys/kernel/hostname) kern=$(uname -r) interval=${INT}s watch_pid=${PIDW:-none} page=${PG}B"
        echo "# fields: ts mono load avail cma dstate top [tgt rss vsz wchan]"
      } >> "$LOG"
      while :; do
        ts=$(printf "%(%F %T)T\n" -1)
        read -r l1 _ < /proc/loadavg
        avail=0; cma=0
        while read -r k v _; do
          case $k in
            MemAvailable:) avail=$v ;;
            CmaFree:)      cma=$v ;;
          esac
        done < /proc/meminfo
        # 一次 /proc 扫描：统计 D 状态数 + 找 RSS 最大的进程（纯 builtin，不 fork）
        dstate=0; top_rss=0; top_pid=0; top_comm="-"
        for f in /proc/[0-9]*/stat; do
          read -r line < "$f" 2>/dev/null || continue
          case $line in *") "*) ;; *) continue ;; esac
          comm=${line#*(}; comm=${comm%%)*}
          rest=${line##*) }
          set -- $rest
          st=$1
          [ -n "$st" ] || continue
          [ "$st" = "D" ] && dstate=$((dstate + 1))
          rss=$(( ${22:-0} * PG / 1024 ))
          if [ "$rss" -gt "$top_rss" ]; then
            top_rss=$rss; top_comm=$comm
            top_pid=${f#/proc/}; top_pid=${top_pid%/stat}
          fi
        done
        line="ts=$ts mono=$(cut -d" " -f1 /proc/uptime) load=$l1 avail=$avail cma=$cma dstate=$dstate top=$top_rss/$top_comm"
        if [ -n "$PIDW" ] && [ -r "/proc/$PIDW/status" ]; then
          rs=0; vs=0; stt="?"
          while read -r k v _; do
            case $k in
              State:)  stt=${v%% *} ;;
              VmRSS:)  rs=${v%kB} ;;
              VmSize:) vs=${v%kB} ;;
            esac
          done < "/proc/$PIDW/status"
          wc="-"
          [ -r "/proc/$PIDW/wchan" ] && wc=$(cat "/proc/$PIDW/wchan" 2>/dev/null)
          [ -n "$wc" ] || wc="-"
          line="$line tgt=$stt rss=$rs vsz=$vs wchan=$wc"
        else
          line="$line tgt=gone"
        fi
        echo "$line" >> "$LOG"
        sleep "$INT"
      done
    ' _ "$LOG" "$INT" "$PIDW" </dev/null >/dev/null 2>&1 &
    echo $! > "$PIDF"
    sleep 1.2
    if kill -0 "$(cat "$PIDF")" 2>/dev/null && [ -f "$LOG" ]; then
      echo "hb $TAG 已启动 pid=$(cat "$PIDF") interval=${INT}s watch=${PIDW:-none} log=$LOG"
    else
      echo "hb $TAG 启动失败" >&2; rm -f "$PIDF"; exit 1
    fi
    ;;
  stop)
    TAG=${2:-}
    [ -n "$TAG" ] || { echo "用法: $PROG stop <tag>" >&2; exit 2; }
    PIDF="$LOGDIR/$TAG.pid"
    if [ -f "$PIDF" ]; then
      p=$(cat "$PIDF"); kill "$p" 2>/dev/null
      sleep 0.3; kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null
      rm -f "$PIDF"; echo "hb $TAG 已停 (pid $p)"
    else
      echo "hb $TAG 没在跑"
    fi
    ;;
  status)
    n=0
    for f in "$LOGDIR"/*.pid; do
      [ -f "$f" ] || continue
      t=$(basename "$f" .pid); p=$(cat "$f")
      if kill -0 "$p" 2>/dev/null; then s="在跑"; n=$((n + 1)); else s="僵尸pid"; fi
      lines=0; last="-"
      if [ -f "$LOGDIR/$t.log" ]; then
        lines=$(wc -l < "$LOGDIR/$t.log")
        last=$(tail -n 1 "$LOGDIR/$t.log")
      fi
      printf "%-16s %-7s pid=%-7s 行数=%-8s %s\n" "$t" "$s" "$p" "$lines" "$last"
    done
    [ "$n" = "0" ] && echo "(没有在跑的心跳)"
    exit 0
    ;;
  tail)
    TAG=${2:-}
    [ -n "$TAG" ] || { echo "用法: $PROG tail <tag> [n]" >&2; exit 2; }
    tail -n "${3:-20}" "$LOGDIR/$TAG.log"
    ;;
  *)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac