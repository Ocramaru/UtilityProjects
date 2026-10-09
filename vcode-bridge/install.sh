#!/usr/bin/env bash
# Downloads the latest vcode-bridge from GitHub and runs its installer with the arguments given.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

ARCHIVE="https://github.com/Ocramaru/UtilityProjects/archive/refs/heads/main.tar.gz"
INSTALLER="UtilityProjects-main/vcode-bridge/manage.py"

command -v uv >/dev/null || { echo "vcode-bridge needs uv: https://docs.astral.sh/uv/" >&2; exit 1; }
download="$(mktemp -d)"
trap 'rm -rf -- "$download"' EXIT
curl -fsSL "$ARCHIVE" | tar -xzf - -C "$download"
uv run --no-project --python 3.15 "$download/$INSTALLER" "$@"
