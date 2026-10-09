#!/usr/bin/env bash
# Managed by device-terminal-setup dev: updates replace everything above the "Your lines" line at the end
# Starts zsh in a fresh container: links the setup on this volume into the local HOME and reinstalls the system packages the image lacks.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

SETUP_HOME="@HOME@"

# Folders created locally rather than linked, so what other tools write into them (pip --user, for one) stays in the pod.
LOCAL_FOLDERS=" .config .local .local/bin .local/share .local/state "

# Links each entry of $1 into $2, descending into folders that exist in both so the image's own files stay.
# Caches and history are skipped: they are written constantly, and writes to network storage are slow.
link_into() {
  local source="$1" target="$2" relative="$3" entry name
  for entry in "$source"/.[!.]* "$source"/*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="${entry##*/}"
    case "$name" in .cache|.zcompdump*|.zsh_history|start.sh|start.sh.bak*) continue ;; esac
    if [[ -d "$entry" && "$LOCAL_FOLDERS" == *" $relative$name "* ]]; then mkdir -p "$target/$name"; fi
    if [[ -L "$target/$name" ]]; then
      continue
    elif [[ -d "$target/$name" && -d "$entry" ]]; then
      link_into "$entry" "$target/$name" "$relative$name/"
    elif [[ ! -e "$target/$name" ]]; then
      ln -s "$entry" "$target/$name"
    fi
  done
}

# HOME stays on local disk; the setup on the volume is only read from there
if [[ "$HOME" != "$SETUP_HOME" ]]; then link_into "$SETUP_HOME" "$HOME" ""; fi
cd "@WORKDIR@"

if ! command -v zsh >/dev/null; then
  if (( EUID == 0 )); then
    as_root=()
  elif command -v sudo >/dev/null; then
    as_root=(sudo)
  else
    echo "start.sh: zsh is missing, and installing it needs root" >&2
    exit 1
  fi
  echo "start.sh: installing @PACKAGES@"
  "${as_root[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  "${as_root[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends @PACKAGES@ >/dev/null
fi

# Oh My Zsh is linked from the volume: its cache goes to local disk, its update check would write to the volume,
# and its permission check can stop to ask about linked folders
export ZSH_CACHE_DIR="$HOME/.cache/oh-my-zsh" DISABLE_AUTO_UPDATE=true ZSH_DISABLE_COMPFIX=true

# zsh starts once everything below has run, your lines included
trap 'exec zsh' EXIT

# ---- Your lines: everything below here is kept on update ----
