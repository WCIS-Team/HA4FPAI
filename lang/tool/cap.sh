#!/bin/bash
# cap.sh — 带内存上限 + 心跳的运行器
#
# 上限为什么用 RLIMIT_AS 而不是 cgroup（2026-10-12 实测）：
#   这块板子的内核**没有编译任何 cgroup 控制器**——
#     /proc/cgroups                                只有表头，零行
#     /sys/fs/cgroup/unified/cgroup.controllers     空
#     /sys/fs/cgroup/memory                        不存在（v1 也没有）
#   所以 systemd-run -p MemoryMax=100M 会被"接受"但**完全不生效**：
#   实测在 100M 限额的 scope 里成功分配了 400MB，systemd 245 不报错、静默忽略。
#   prlimit --as 在没有 cgroup 时是唯一能把"无限增长 → 整机卡死"
#   换成"分配失败 → 可捕获异常"的手段。
#
# 两个必须知道的坑（都实测踩过）：
#   1) 这台 util-linux 2.34-0.1ubuntu9.6 的 `prlimit --as=200M`（带后缀）**直接 segfault**，
#      纯字节数 `--as=209715200` 正常。所以后缀由本脚本自己换算，只给 prlimit 传字节。
#   2) RLIMIT_AS 卡的是**虚拟地址空间**，不是 RSS。ORT 这类运行时 VA 远大于 RSS
#      （实测刚 import 完 onnxruntime：VmSize=207 MB / VmRSS=38 MB，约 5 倍），
#      --as 必须按 VmSize 峰值定，不能拿 RSS 直接当 AS 上限。
#      第一次用请带 `--as 2G` 跑一遍 sizing，从心跳日志读 tgt_vsz 峰值再收紧。
#
# 用法：
#   cap.sh --as 700M --tag qwen2 -- python3 foo.py arg
#   cap.sh --tag sweep --interval 10 -- bash -c '...'
#   cap.sh --as 700M --tag run --no-hb -- ./bench.sh
# 后台跑（ssh 断开也要活着）：setsid nohup cap.sh ... &
set -u

AS=""; TAG=""; INT=5; NOHB=0; CMD=()
while [ $# -gt 0 ]; do
  case "$1" in
    --as)       AS="${2:-}"; shift 2 ;;
    --tag)      TAG="${2:-}"; shift 2 ;;
    --interval) INT="${2:-}"; shift 2 ;;
    --no-hb)    NOHB=1; shift ;;
    --)         shift; CMD=("$@"); break ;;
    *)          echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[ ${#CMD[@]} -gt 0 ] || { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ -n "$TAG" ] || TAG=$(basename "${CMD[0]}" | tr -c 'A-Za-z0-9_.-' '_')

# 后缀 → 字节（prlimit 只收整数，理由见文件头第 1 条坑）
to_bytes() {
  local v="${1:-}" n u
  case "$v" in
    *[Kk]) n=${v%[Kk]}; u=1024 ;;
    *[Mm]) n=${v%[Mm]}; u=1048576 ;;
    *[Gg]) n=${v%[Gg]}; u=1073741824 ;;
    *)     n=$v;         u=1 ;;
  esac
  case "$n" in ''|*[!0-9]*) return 1 ;; esac
  echo $(( n * u ))
}

HB=/usr/local/bin/hb.sh
[ -x "$HB" ] || HB=$(command -v hb.sh || true)

echo "=== cap: tag=$TAG as=${AS:-none} hb=$([ "$NOHB" = 1 ] && echo off || echo on)"
echo "=== cap: start $(date -u '+%F %T UTC')  cmd=${CMD[*]}"

RUN=()
if [ -n "$AS" ]; then
  if BYTES=$(to_bytes "$AS"); then
    RUN=(prlimit --as="$BYTES" --)
    echo "=== cap: RLIMIT_AS = $AS = $BYTES bytes"
  else
    echo "=== cap: --as 解析失败: $AS" >&2; exit 2
  fi
fi
if [ -z "$(tail -n +2 /proc/cgroups 2>/dev/null)" ]; then
  echo "=== cap: 提示 /proc/cgroups 无控制器 ⇒ MemoryMax 不可用，本次只靠 RLIMIT_AS"
fi

"${RUN[@]}" "${CMD[@]}" &
CHILD=$!
sleep 0.3
if [ -n "$AS" ] && [ -r "/proc/$CHILD/limits" ]; then
  echo "=== cap: 实限 $(grep -i 'address space' "/proc/$CHILD/limits" | tr -s ' ')"
fi
[ "$NOHB" = 1 ] || "$HB" start "$TAG" "$INT" "$CHILD" >/dev/null 2>&1

wait "$CHILD"; rc=$?
[ "$NOHB" = 1 ] || "$HB" stop "$TAG" >/dev/null 2>&1

echo "=== cap: exit=$rc ($(date -u '+%F %T UTC'))"
LOG=${HB_LOGDIR:-/root/hb}
if [ -f "$LOG/$TAG.log" ]; then
  peak_rss=$(awk -F'rss=' '/tgt=/{split($2,a," "); if (a[1]+0>m) m=a[1]+0} END{print m+0}' "$LOG/$TAG.log")
  peak_vsz=$(awk -F'vsz=' '/tgt=/{split($2,a," "); if (a[1]+0>m) m=a[1]+0} END{print m+0}' "$LOG/$TAG.log")
  echo "=== cap: 峰值 tgt_rss=${peak_rss} kB  tgt_vsz=${peak_vsz} kB  样本=$(grep -c 'tgt=' "$LOG/$TAG.log")"
  echo "=== cap: 最后 3 行"
  tail -n 3 "$LOG/$TAG.log"
fi
if [ "$rc" != "0" ]; then
  echo "=== cap: 非 0 退出，最后 10 行心跳"
  [ -f "$LOG/$TAG.log" ] && tail -n 10 "$LOG/$TAG.log"
fi
exit $rc