#!/bin/bash
# install_ekho.sh —— 给 mimo 用户准备 ekho-server 的安装/引导脚本。
#
# 三种方式（按推荐顺序）：
#   1) 已经有打包好的 kit（最快，113 板推荐）：
#        bash install_ekho.sh --kit /path/to/kit-ekho
#        bash install_ekho.sh --tar /path/to/kit-ekho-aarch64.tar.gz
#      也可以从 GitHub Release 拉：
#        bash install_ekho.sh --release
#   2) 本机 Linux（x86_64/aarch64）从源码构建：
#        bash install_ekho.sh --build
#      需要 apt 包：build-essential autoconf automake libtool pkg-config \
#      libsndfile1-dev libespeak-ng-dev rsync curl xz-utils bzip2
#   3) 什么都不做，只检查当前是否已有：
#        bash install_ekho.sh --check
#
# 安装目标默认 $HOME/.local/ekho（可用 EKHO_ROOT 或 --root 改）。
# 装完后把下面的变量写进环境/服务文件：
#   export EKHO_ROOT=/path/to/ekho
#   export EKHO_SERVER=$EKHO_ROOT/bin/ekho-server
#
# 注意：ekho-server 是 ekho 的 Linux fork 里的 HTTP 服务（不是上游 hgneng/ekho）；
# 仓库地址：https://github.com/WCIS-Team/ekho-Linux-AIGC
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${EKHO_ROOT:-$HOME/.local/ekho}
REPO_URL=${EKHO_REPO_URL:-https://github.com/WCIS-Team/ekho-Linux-AIGC.git}
KIT_ASSET=${EKHO_KIT_ASSET:-kit-ekho-aarch64.tar.gz}
MODE=""
ARG_KIT=""
ARG_TAR=""
ARG_ROOT=""

die() { echo "错误：$*" >&2; exit 1; }
say() { echo "[ekho-install] $*"; }

usage() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --release) MODE=release ;;
    --build) MODE=build ;;
    --kit) shift; [ $# -gt 0 ] || die "--kit 需要路径"; ARG_KIT=$1; MODE=kit ;;
    --tar) shift; [ $# -gt 0 ] || die "--tar 需要路径"; ARG_TAR=$1; MODE=tar ;;
    --root) shift; [ $# -gt 0 ] || die "--root 需要路径"; ARG_ROOT=$1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
  shift
done
[ -n "$ARG_ROOT" ] && ROOT=$ARG_ROOT
ROOT=$(cd "$(dirname "$ROOT")" 2>/dev/null && pwd)/$(basename "$ROOT") || ROOT=$ROOT

check_ekho() {
  if [ -f "$ROOT/bin/ekho-server" ]; then
    say "已找到：$ROOT/bin/ekho-server"
    "$ROOT/bin/ekho-server" --help 2>&1 | head -3 | sed 's/^/  /'
    return 0
  fi
  if command -v ekho-server >/dev/null 2>&1; then
    say "PATH 里已有：$(command -v ekho-server)"
    return 0
  fi
  return 1
}

install_kit_dir() {
  local src=$1
  # 注意用 -f 而不是 -x：kit 是在 Windows 上打的 tar，POSIX 可执行位会丢
  # （tar -tvzf 显示 bin/ekho-server 是 -rw-rw-rw-）。存在即可，复制后下面 chmod +x。
  [ -f "$src/bin/ekho-server" ] || die "$src 里没有 bin/ekho-server"
  if [ "$(cd "$src" && pwd)" = "$(cd "$ROOT" 2>/dev/null && pwd)" ]; then
    say "kit 已在目标位置：$ROOT"
    return 0
  fi
  mkdir -p "$ROOT"
  say "复制 kit：$src -> $ROOT"
  cp -a "$src/." "$ROOT/"
  chmod +x "$ROOT"/bin/ekho* 2>/dev/null || true
  # 同样只用 -f：Windows/NTFS 上 chmod +x 可能不体现在 test -x 里，
  # 板端 Linux 上 chmod 一定生效；存在即可算装好。
  [ -f "$ROOT/bin/ekho-server" ] || die "复制后仍找不到 $ROOT/bin/ekho-server"
  say "完成：$ROOT/bin/ekho-server"
}

install_tar() {
  local tar=$1 tmp src
  [ -f "$tar" ] || die "tar 不存在：$tar"
  tmp=$(mktemp -d) || die "mktemp 失败"
  trap 'rm -rf "$tmp"' EXIT
  say "解包：$tar"
  # 用 stdin 喂 tar，绕开 GNU tar 的 "host:path" 远程解析
  # （Windows 上 S:/... 或 C:\... 会被当成远程主机；板端 Linux 同样安全）。
  tar -xzf - -C "$tmp" < "$tar" || die "tar 解包失败"
  src=$(find "$tmp" -type f -path '*/bin/ekho-server' -print 2>/dev/null | head -1 | sed 's|/bin/ekho-server$||')
  [ -n "$src" ] || die "tar 里没有 bin/ekho-server"
  install_kit_dir "$src"
  trap - EXIT
  rm -rf "$tmp"
}

download_release() {
  local tmp url
  # 首选 releases/latest/download 固定链接：不走 API，不踩匿名 403 限流。
  url="https://github.com/WCIS-Team/ekho-Linux-AIGC/releases/latest/download/$KIT_ASSET"
  if ! curl -sIL --max-time 60 "$url" | grep -qi '^HTTP/.* 200\|^HTTP/.* 302'; then
    # 兜底：查最新 Release 的资产名（匿名 API，可能被限流 403）
    command -v python3 >/dev/null || die "需要 python3 读 GitHub Release API"
    url=$(python3 - "$KIT_ASSET" <<'PY'
import json, os, sys, urllib.request
asset = sys.argv[1]
api = "https://api.github.com/repos/WCIS-Team/ekho-Linux-AIGC/releases/latest"
try:
    data = json.load(urllib.request.urlopen(api, timeout=30))
except Exception as exc:
    raise SystemExit("GitHub Release API 读取失败：%r" % (exc,))
for a in data.get("assets", []):
    name = a.get("name", "")
    if name == asset or ("kit-ekho" in name and name.endswith((".tar.gz", ".tgz"))):
        print(a["browser_download_url"])
        raise SystemExit(0)
raise SystemExit("没有找到 kit-ekho 资产（看 README 的“发布 kit”一节）")
PY
    ) || die "没有可下载的 kit：$url"
  fi
  tmp=$(mktemp -d) || die "mktemp 失败"
  trap 'rm -rf "$tmp"' EXIT
  say "下载：$url"
  curl -L --fail --retry 2 -o "$tmp/kit.tar.gz" "$url" || die "下载失败"
  install_tar "$tmp/kit.tar.gz"
  trap - EXIT
  rm -rf "$tmp"
}

apt_install_deps() {
  local pkgs="build-essential autoconf automake libtool pkg-config libsndfile1-dev libespeak-ng-dev rsync curl xz-utils bzip2"
  if ! command -v apt-get >/dev/null 2>&1; then
    say "不是 apt 系统；请自行安装：$pkgs"
    return 0
  fi
  if [ "$(id -u)" -eq 0 ]; then
    apt-get update && apt-get install -y $pkgs
  elif command -v sudo >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y $pkgs
  else
    die "需要 root 或 sudo 安装依赖：$pkgs"
  fi
}

build_from_source() {
  command -v git >/dev/null || die "需要 git"
  command -v curl >/dev/null || die "需要 curl"
  command -v make >/dev/null || die "需要 make"
  command -v pkg-config >/dev/null || die "需要 pkg-config"
  local src=${EKHO_SRC:-$ROOT/src}
  local build=${EKHO_BUILD:-$ROOT/build}
  local prefix=$ROOT/prefix
  mkdir -p "$ROOT" "$build"
  if [ ! -d "$src/.git" ]; then
    say "克隆：$REPO_URL -> $src"
    git clone --depth 1 "$REPO_URL" "$src" || die "git clone 失败"
  else
    say "复用源码树：$src"
  fi
  say "安装构建依赖（apt 系统）……"
  apt_install_deps
  say "拉取语音 wav（SourceForge ~87 MB）……"
  bash "$src/scripts/fetch-voice.sh" || die "fetch-voice 失败"
  say "构建 ekho / ekho-server……"
  SRC="$src" WORK="$build" PREFIX="$prefix" bash "$src/scripts/build-native.sh" ||
    die "build-native 失败（看 $build/*.log）"
  say "生成 48 kHz 语音包……"
  (
    set -e
    cd "$src"
    [ -f ekho-data/pinyin.index ] && mv ekho-data/pinyin.index ekho-data/pinyin.index.tracked || true
    printf '你好，世界。\n' > "$build/gen.txt"
    EKHO_DATA_PATH="$src/ekho-data" "$src/out/ekho" -v Mandarin -f "$build/gen.txt" -o "$build/gen.wav"
    [ -f ekho-data/pinyin.index ] && mv ekho-data/pinyin.index ekho-data/pinyin.index.44k || true
    [ -f ekho-data/pinyin.voice ] && mv ekho-data/pinyin.voice ekho-data/pinyin.voice.44k || true
    [ -f ekho-data/pinyin.index.tracked ] && mv ekho-data/pinyin.index.tracked ekho-data/pinyin.index || true
    cc -O2 -o "$build/convert-voice" scripts/convert-voice.c -lsndfile -lm
    "$build/convert-voice" ekho-data/pinyin.voice.44k ekho-data/pinyin.index.44k 48000 \
      ekho-data/pinyin.voice ekho-data/pinyin.index
    cc -O2 -o "$build/convert-alphabet" scripts/convert-alphabet.c -lsndfile -lm
    "$build/convert-alphabet" ekho-data/alphabet 48000
  ) || die "语音包生成失败"
  mkdir -p "$ROOT/bin" "$ROOT/share"
  cp -f "$src/out/ekho" "$src/out/ekho-server" "$src/out/ekho-stream" "$ROOT/bin/" 2>/dev/null || true
  cp -a "$src/ekho-data" "$ROOT/share/ekho-data"
  if [ -d /usr/share/espeak-ng-data ]; then
    ln -sfn /usr/share/espeak-ng-data "$ROOT/share/espeak-ng-data"
  elif [ -d "$src/ekho-data/espeak-ng-data" ]; then
    cp -a "$src/ekho-data/espeak-ng-data" "$ROOT/share/espeak-ng-data"
  fi
  [ -x "$ROOT/bin/ekho-server" ] || die "构建完成但没有 $ROOT/bin/ekho-server"
  say "完成：$ROOT/bin/ekho-server"
}

case "$MODE" in
  check)
    check_ekho || { say "没有找到 ekho-server"; exit 1; }
    ;;
  kit)
    install_kit_dir "$ARG_KIT"
    ;;
  tar)
    install_tar "$ARG_TAR"
    ;;
  release)
    download_release
    ;;
  build)
    build_from_source
    ;;
  "")
    if check_ekho; then
      exit 0
    fi
    cat <<EOF
没有找到 ekho-server。请三选一：

  1) 已有 kit（推荐；113 板用 aarch64 kit）：
       bash install_ekho.sh --kit /path/to/kit-ekho
       bash install_ekho.sh --tar /path/to/kit-ekho-aarch64.tar.gz

  2) 从 GitHub Release 拉 kit：
       bash install_ekho.sh --release
     （仓库：https://github.com/WCIS-Team/ekho-Linux-AIGC）

  3) 本机 Linux 从源码构建（耗时较长）：
       bash install_ekho.sh --build

装完后：
  export EKHO_ROOT=$ROOT
  export EKHO_SERVER=\$EKHO_ROOT/bin/ekho-server
  python3 asr/start_ekho_server.sh
EOF
    exit 1
    ;;
esac

say "EKHO_ROOT=$ROOT"
say "EKHO_SERVER=$ROOT/bin/ekho-server"
say "测试：EKHO_ROOT=$ROOT bash asr/start_ekho_server.sh"
say "然后在 mimo_voice.py / funasr-lite 里保持 TTS=ekho（默认）即可。"
