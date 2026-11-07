#!/bin/sh
# Reproducible install of ha-small on a Linux host with Python >= 3.14.2.
# Never point this at an existing production Home Assistant configuration.
set -eu

SRC=${1:?usage: install.sh <source-dir> <config-dir>}
CFG=${2:?usage: install.sh <source-dir> <config-dir>}
PY=${PYTHON:-python3.14}

command -v "$PY" >/dev/null 2>&1 || { echo "error: $PY not found (need >= 3.14.2)" >&2; exit 2; }
"$PY" - <<'EOF'
import sys
if sys.version_info[:2] < (3, 14):
    raise SystemExit('error: Python >= 3.14 required, found %s' % sys.version.split()[0])
EOF

[ -f "$SRC/cut/source-audit.json" ] || { echo "error: $SRC does not look like ha-small source" >&2; exit 2; }
"$PY" "$SRC/cut/verify_source.py" || { echo "error: source audit failed" >&2; exit 2; }

VENV="$CFG/.venv"
mkdir -p "$CFG"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install --disable-pip-version-check -q "$SRC"
"$VENV/bin/pip" check

for f in configuration.yaml; do
  [ -e "$CFG/$f" ] || cp "$SRC/examples/$f" "$CFG/$f"
done
for f in automations.yaml scripts.yaml scenes.yaml; do
  [ -e "$CFG/$f" ] || cp "$SRC/examples/$f" "$CFG/$f"
done

cat <<EOF
installed into $VENV
config dir:  $CFG
start with:  $VENV/bin/hass -c "$CFG"
EOF
