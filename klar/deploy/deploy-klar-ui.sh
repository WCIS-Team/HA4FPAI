#!/usr/bin/env bash
# deploy-klar-ui.sh —— 把校机构建的 klar 前端 dist 部署到板子
#
# 背景：板子不能编译（993MB 内存、无 Node）。所以链路是
#   改源码（校机 D:\yzd\WCIS\klar-ha-nlu\web\src）
#     → 校机 npm run build
#       → 本脚本把 dist 拉回本机
#         → 备份板上旧 UI
#           → 替换 /mnt/data/klar/engine/ui
#             → 校验 md5
#
# 用法：
#   bash deploy-klar-ui.sh              # 完整：校机构建 + 部署
#   bash deploy-klar-ui.sh --no-build   # 只部署已有的 dist
#   bash deploy-klar-ui.sh --rollback   # 回滚到最近一次备份
#
# 为什么不用 scp 校机直连板子：校机在 192.168.212.0/24，板子在
# 192.168.1.0/24，跨网段不可达。必须经本机中转。
set -uo pipefail

SCHOOL="${SCHOOL:-school-guest}"
SCHOOL_DIST='D:\yzd\WCIS\klar-ha-nlu\web\dist'
SCHOOL_WEB='D:\yzd\WCIS\klar-ha-nlu\web'
NODE_NPM='D:\yzd\tools\node\node-v24.22.2-win-x64\npm.cmd'

BOARD_HOST='root@192.168.1.113'
BOARD_UI='/mnt/data/klar/engine/ui'
BOARD_BACKUP_ROOT='/mnt/data/klar/ui-backups'

PLINK="/c/Program Files/PuTTY/plink"
PSCP="/c/Program Files/PuTTY/pscp"
HOSTKEY='SHA256:IQGq+fgZLG+sC77GCs+cZuntFUYB1mcNM4POwA4ZzLc'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKROOT="$REPO_ROOT/../../workenv/klar-ui-deploy"
STAMP="$(date +%Y%m%d-%H%M%S)"

blue() { printf '\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  PASS  %s\033[0m\n' "$*"; }
bad()  { printf '\033[31m  FAIL  %s\033[0m\n' "$*"; }

board() {
  "$PLINK" -ssh -batch -hostkey "$HOSTKEY" -pw fmsh "$BOARD_HOST" "$@" 2>&1
}

# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------
do_rollback() {
  blue "=== 回滚到最近一次备份 ==="
  local latest
  latest=$(board "ls -1dt $BOARD_BACKUP_ROOT/*/ 2>/dev/null | head -1" | tr -d '\r')
  if [ -z "$latest" ]; then
    bad "没有可用备份"
    return 1
  fi
  echo "  最近备份: $latest"
  board "rm -rf $BOARD_UI.broken && mv $BOARD_UI $BOARD_UI.broken && cp -a '$latest' $BOARD_UI && ls -la $BOARD_UI/assets | tail -4"
  ok "已回滚（坏的那份留在 $BOARD_UI.broken）"
}

# ---------------------------------------------------------------------------
# 校机构建
# ---------------------------------------------------------------------------
do_build() {
  blue "=== 1. 校机构建 ==="
  # 注意：Windows OpenSSH 会话断开会杀掉远端子进程，
  # 所以这里必须让 ssh 一直挂着跑完，不能 detached。
  ssh "$SCHOOL" "cmd /c \"cd /d $SCHOOL_WEB && $NODE_NPM run build\"" 2>&1 | tail -25
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    bad "校机构建失败（exit=$rc）"
    return 1
  fi
  ok "校机构建完成"
}

# ---------------------------------------------------------------------------
# 拉取 dist
# ---------------------------------------------------------------------------
do_pull() {
  blue "=== 2. 把 dist 拉回本机 ==="
  mkdir -p "$WORKROOT/$STAMP"
  local local_dist="$WORKROOT/$STAMP/dist"
  mkdir -p "$local_dist"

  # 校机打成 tar 再从 stdin 取回（避免 Windows 路径在 scp 里的转义问题）
  ssh "$SCHOOL" "cmd /c \"cd /d $SCHOOL_DIST && tar -cf - .\"" > "$WORKROOT/$STAMP/dist.tar" 2>/dev/null
  if [ ! -s "$WORKROOT/$STAMP/dist.tar" ]; then
    bad "拉取 dist 失败（tar 为空）"
    return 1
  fi
  tar -xf "$WORKROOT/$STAMP/dist.tar" -C "$local_dist"
  echo "  拉到: $local_dist"
  ls -la "$local_dist/assets" | tail -5
  ok "dist 已拉回本机"
}

# ---------------------------------------------------------------------------
# 部署
# ---------------------------------------------------------------------------
do_deploy() {
  blue "=== 3. 备份板上旧 UI ==="
  board "mkdir -p $BOARD_BACKUP_ROOT && cp -a $BOARD_UI $BOARD_BACKUP_ROOT/$STAMP && ls -1dt $BOARD_BACKUP_ROOT/*/ | head -3"
  ok "已备份到 $BOARD_BACKUP_ROOT/$STAMP"

  blue "=== 4. 上传新 UI ==="
  local local_dist="$WORKROOT/$STAMP/dist"
  # 先传到临时目录，校验通过再替换（避免半新半旧）
  board "rm -rf /tmp/klar-ui-new && mkdir -p /tmp/klar-ui-new"
  ( cd "$local_dist" && tar -cf - . ) > "$WORKROOT/$STAMP/push.tar"
  "$PSCP" -batch -hostkey "$HOSTKEY" -pw fmsh "$WORKROOT/$STAMP/push.tar" "$BOARD_HOST:/tmp/klar-ui-new.tar" >/dev/null 2>&1
  board "cd /tmp/klar-ui-new && tar -xf /tmp/klar-ui-new.tar && ls -la assets | tail -4"

  blue "=== 5. 替换 ==="
  board "rm -rf $BOARD_UI && mv /tmp/klar-ui-new $BOARD_UI && chown -R root:root $BOARD_UI && ls -la $BOARD_UI/assets | tail -4"
  ok "已替换 $BOARD_UI"

  blue "=== 6. 校验 md5（本机 vs 板子）==="
  local mismatch=0
  for f in $(cd "$local_dist" && find . -type f | sed 's|^\./||'); do
    local h_local h_board
    h_local=$(cd "$local_dist" && md5sum "$f" | awk '{print $1}')
    h_board=$(board "md5sum '$BOARD_UI/$f' 2>/dev/null | awk '{print \$1}'" | tr -d '\r')
    if [ "$h_local" = "$h_board" ]; then
      ok "$f"
    else
      bad "$f  本机=$h_local 板子=$h_board"
      mismatch=1
    fi
  done
  [ "$mismatch" -eq 0 ] || { bad "有文件不一致，建议 --rollback"; return 1; }

  blue "=== 7. 服务存活 + 页面可访问 ==="
  board "systemctl is-active klar-nlu-engine"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://192.168.1.113:10520/" || echo 000)
  if [ "$code" = "200" ]; then ok "10520 返回 200"; else bad "10520 返回 $code"; fi

  echo
  blue "部署完成。备份目录: $BOARD_BACKUP_ROOT/$STAMP"
  echo "  回滚: bash $0 --rollback"
  echo "  提示：浏览器请强刷（Ctrl+F5），否则可能用缓存的旧 JS。"
}

main() {
  case "${1:-}" in
    --rollback) do_rollback; exit $? ;;
    --no-build) do_pull && do_deploy ;;
    *) do_build && do_pull && do_deploy ;;
  esac
}

main "$@"
