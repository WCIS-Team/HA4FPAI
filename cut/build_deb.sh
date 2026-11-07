#!/bin/sh
# Build a .deb for ha-small from the release artifacts.
# Runs inside a Debian-based container that has dpkg-deb.
set -e

SRC=/relsrc              # unpacked source tree
ART=/artifacts           # wheel + source tarball
OUT=/out                 # output directory for the .deb
VER=0.1.0
PKG=ha-small
ARCH=all

STAGE=/tmp/debbuild/${PKG}_${VER}
rm -rf /tmp/debbuild
mkdir -p "${STAGE}/DEBIAN"
mkdir -p "${STAGE}/opt/ha-small"
mkdir -p "${STAGE}/etc/ha-small"
mkdir -p "${STAGE}/usr/bin"
mkdir -p "${STAGE}/lib/systemd/system"
mkdir -p "${STAGE}/usr/share/doc/${PKG}"

# payload: the wheel plus the scripts needed to install it offline-ish
cp "${ART}"/homeassistant-2026.10.4-py3-none-any.whl "${STAGE}/opt/ha-small/"
cp "${SRC}/examples/configuration.yaml" "${STAGE}/etc/ha-small/"
cp "${SRC}/examples/automations.yaml" "${STAGE}/etc/ha-small/"
cp "${SRC}/examples/scripts.yaml" "${STAGE}/etc/ha-small/"
cp "${SRC}/examples/scenes.yaml" "${STAGE}/etc/ha-small/"
cp "${SRC}/examples/secrets.example.yaml" "${STAGE}/etc/ha-small/"
cp "${SRC}/LICENSE.md" "${STAGE}/usr/share/doc/${PKG}/copyright"
cp "${SRC}/README.md" "${STAGE}/usr/share/doc/${PKG}/README.md"

cat > "${STAGE}/opt/ha-small/requirements-voice.txt" <<'EOF'
pymicro-vad==1.0.1
pyspeex-noise==1.0.2
EOF

cat > "${STAGE}/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VER}
Section: misc
Priority: optional
Architecture: ${ARCH}
Maintainer: WCIS Team <noreply@example.invalid>
Depends: python3.14 | python3 (>= 3.14)
Recommends: ffmpeg, libturbojpeg0
Description: Trimmed Home Assistant Core (deletion-only cut)
 ha-small is a deletion-only trim of Home Assistant Core 2026.10.4:
 128 integrations kept, 6096 upstream files byte-identical, zero patches.
 This package ships the built wheel plus a launcher; Python dependencies are
 installed by ha-small-setup into /opt/ha-small/venv (use HA_SMALL_WHEELHOUSE
 for an offline install).
EOF

cat > "${STAGE}/DEBIAN/conffiles" <<EOF
/etc/ha-small/configuration.yaml
/etc/ha-small/automations.yaml
/etc/ha-small/scripts.yaml
/etc/ha-small/scenes.yaml
EOF

cat > "${STAGE}/usr/bin/ha-small" <<'EOF'
#!/bin/sh
# Launcher for the trimmed Home Assistant build.
CONFIG="${HA_SMALL_CONFIG:-/etc/ha-small}"
VENV=/opt/ha-small/venv
if [ ! -x "${VENV}/bin/hass" ]; then
    echo "ha-small is not set up yet: run 'ha-small-setup' (needs network or a wheelhouse)." >&2
    exit 1
fi
exec "${VENV}/bin/hass" -c "${CONFIG}" "$@"
EOF
chmod 0755 "${STAGE}/usr/bin/ha-small"

cat > "${STAGE}/usr/bin/ha-small-setup" <<'EOF'
#!/bin/sh
# Create the runtime venv and install the packaged wheel into it.
# Offline install: set HA_SMALL_WHEELHOUSE=/path/to/wheels
set -e

VENV=/opt/ha-small/venv
WHL=$(ls /opt/ha-small/homeassistant-*.whl | head -1)

pick_python() {
    for cand in python3.14 python3.15 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,14) else 1)'; then
                echo "$cand"
                return 0
            fi
        fi
    done
    return 1
}

PY=$(pick_python) || {
    echo "ha-small needs Python >= 3.14; none found (tried python3.14, python3.15, python3)." >&2
    echo "Trimming files does not make this build run on Python 3.8/3.12." >&2
    exit 1
}
echo "using interpreter: $PY ($("$PY" --version 2>&1))"

if [ ! -d "${VENV}" ]; then
    "$PY" -m venv "${VENV}"
fi

if [ -n "${HA_SMALL_WHEELHOUSE}" ]; then
    echo "offline install from ${HA_SMALL_WHEELHOUSE}"
    "${VENV}/bin/pip" install --no-index --find-links "${HA_SMALL_WHEELHOUSE}" "${WHL}"
else
    "${VENV}/bin/pip" install "${WHL}"
fi

# Optional compiled voice dependencies (assist_pipeline needs them). They are not
# hard requirements of the wheel, and building them needs a C++ toolchain, so use
# prebuilt wheels from the wheelhouse when present and warn otherwise.
VOICE_REQ=/opt/ha-small/requirements-voice.txt
if [ -f "${VOICE_REQ}" ]; then
    if [ -n "${HA_SMALL_WHEELHOUSE}" ]; then
        "${VENV}/bin/pip" install --no-index --find-links "${HA_SMALL_WHEELHOUSE}" \
            -r "${VOICE_REQ}" || echo "warning: voice extras not installed (missing from wheelhouse)" >&2
    else
        "${VENV}/bin/pip" install -r "${VOICE_REQ}" \
            || echo "warning: voice extras not installed" >&2
    fi
fi

"${VENV}/bin/pip" check
echo "ha-small installed into ${VENV}; config in /etc/ha-small"
echo "start with: systemctl enable --now ha-small   (or: ha-small)"
EOF
chmod 0755 "${STAGE}/usr/bin/ha-small-setup"

cat > "${STAGE}/lib/systemd/system/ha-small.service" <<'EOF'
[Unit]
Description=ha-small (trimmed Home Assistant Core)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ha-small
Restart=on-failure
RestartSec=10
Environment=HA_SMALL_CONFIG=/etc/ha-small
# Refuse to start before ha-small-setup has built the venv.
ExecStartPre=/bin/sh -c 'test -x /opt/ha-small/venv/bin/hass || { echo "run ha-small-setup first" >&2; exit 1; }'

[Install]
WantedBy=multi-user.target
EOF

cat > "${STAGE}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    echo "Run 'ha-small-setup' to create the venv, then 'systemctl enable --now ha-small'."
fi
EOF
chmod 0755 "${STAGE}/DEBIAN/postinst"

mkdir -p "${OUT}"
( cd "${STAGE}" && dpkg-deb --build --root-owner-group . "${OUT}/${PKG}_${VER}_${ARCH}.deb" )
ls -l "${OUT}"
dpkg-deb --info "${OUT}/${PKG}_${VER}_${ARCH}.deb"
echo "--- contents:"
dpkg-deb --contents "${OUT}/${PKG}_${VER}_${ARCH}.deb"
