#!/bin/sh
set -eu

usage() {
    printf '%s\n' 'Usage: sh install.sh [--no-model-download]' \
        'Install the adjacent lang-server arm64 deb and its APT dependencies.' \
        'Models download in the background unless explicitly disabled.'
}
no_download=0
for arg in "$@"; do
    case "$arg" in
        --no-model-download) no_download=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        echo 'Root privileges are required; install sudo or run as root.' >&2
        exit 1
    fi
    exec sudo -- sh "$script_dir/$(basename -- "$0")" "$@"
fi
if [ ! -r /etc/os-release ]; then
    echo 'Cannot identify this operating system.' >&2
    exit 1
fi
. /etc/os-release
if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != 20.04 ]; then
    echo 'This release requires Ubuntu 20.04.' >&2
    exit 1
fi
for tool in dpkg dpkg-deb apt-get df awk sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Required tool is missing: $tool" >&2
        exit 1
    }
done
if [ "$(dpkg --print-architecture)" != arm64 ] || [ "$(uname -m)" != aarch64 ]; then
    echo 'This release requires the Ubuntu arm64 / aarch64 architecture.' >&2
    exit 1
fi
set -- "$script_dir"/lang-server_*_arm64.deb
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo 'Expected exactly one lang-server_*_arm64.deb beside install.sh.' >&2
    exit 1
fi
package=$1
if [ ! -f "$script_dir/SHA256SUMS" ]; then
    echo 'Missing SHA256SUMS; use the complete release archive.' >&2
    exit 1
fi
(cd "$script_dir" && sha256sum --check --strict SHA256SUMS)
if [ "$(dpkg-deb -f "$package" Package)" != lang-server ] || \
        [ "$(dpkg-deb -f "$package" Architecture)" != arm64 ]; then
    echo 'The adjacent deb has an unexpected package name or architecture.' >&2
    exit 1
fi
# Reserve room for the unpacked payload, private venv and APT transactions.
installed_kb=$(dpkg-deb -f "$package" Installed-Size)
case "$installed_kb" in ''|*[!0-9]*) installed_kb=524288 ;; esac
required_kb=$((installed_kb * 2 + 1048576))
for location in /usr /var /etc; do
    available_kb=$(df -Pk "$location" | awk 'END {print $4}')
    if [ "$available_kb" -lt "$required_kb" ]; then
        echo "Insufficient free space at $location; require ${required_kb} KiB." >&2
        exit 1
    fi
done
# A persistent marker controls both postinst and the download unit at boot.
if [ ! -d /etc/lang-server ]; then
    install -d -o root -g root -m 0755 /etc/lang-server
fi
if [ "$no_download" -eq 1 ]; then
    if [ -L /etc/lang-server/no-model-download ]; then
        echo 'Refusing a symlink at the model download marker.' >&2
        exit 1
    fi
    : > /etc/lang-server/no-model-download
    chmod 0644 /etc/lang-server/no-model-download
else
    if [ -e /etc/lang-server/no-model-download ]; then
        rm -f -- /etc/lang-server/no-model-download
        # Re-enable a download unit previously disabled by this installer.
        if command -v systemctl >/dev/null 2>&1 && \
                [ -f /lib/systemd/system/lang-server-model-download.service ]; then
            systemctl enable lang-server-model-download.service
        fi
    fi
fi
apt-get update
# Use a relative path so APT treats this as a local package, not a repo name.
cd "$script_dir"
apt-get install -y "./$(basename -- "$package")"
printf '%s\n' 'Installation completed. Configure online ASR/LLM credentials before use.'
