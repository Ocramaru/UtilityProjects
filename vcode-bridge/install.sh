#!/usr/bin/env bash
# Downloads the latest vcode-bridge from GitHub and runs its installer with the arguments given.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

ARCHIVE="https://github.com/Ocramaru/UtilityProjects/archive/refs/heads/main.tar.gz"
INSTALLER="UtilityProjects-main/vcode-bridge/manage.py"

command -v uv >/dev/null || { echo "vcode-bridge needs uv: https://docs.astral.sh/uv/" >&2; exit 1; }
download="$(mktemp -d)"
trap 'rm -rf -- "$download"' EXIT
curl -fsSL --connect-timeout 10 --speed-limit 1024 --speed-time 30 "$ARCHIVE" | tar -xzf - -C "$download"  # gives up on a stalled download
uv run --no-project --python 3.15 "$download/$INSTALLER" "$@"
