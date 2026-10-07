#!/usr/bin/env bash
# Sets up a shell, prompt, terminal tools and fonts on Ubuntu or macOS.
# Safe to run again: anything already in place is reported as ok and left alone.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dotfiles"
LOCAL_BIN="$HOME/.local/bin"
NERD_FONTS_VERSION="v3.4.0"
COMPONENTS=" packages starship mise uv dotfiles ohmyzsh shell fonts agents "
DRY_RUN=0
SKIP=" "

usage() {
  cat <<EOF
usage: $0 [--dry-run] [--skip LIST] [--only LIST]

  --dry-run    print every action and change nothing
  --skip LIST  leave out these comma separated components
  --only LIST  install just these comma separated components

components:$COMPONENTS
EOF
}

# Leaves the comma separated list in $1 space separated in LIST, or exits on an unknown name.
read_list() {
  [[ -n "$1" ]] || { usage >&2; exit 2; }
  LIST="${1//,/ } "
  local name
  for name in $LIST; do
    [[ "$COMPONENTS" == *" $name "* ]] || { echo "unknown component: $name" >&2; exit 2; }
  done
}

while (( $# )); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip) read_list "${2:-}"; SKIP+="$LIST"; shift ;;
    --only)
      read_list "${2:-}"
      for c in $COMPONENTS; do
        [[ " $LIST" == *" $c "* ]] || SKIP+="$c "
      done
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

wants() { [[ "$SKIP" != *" $1 "* ]]; }

# starship, mise and uv live here, and later steps look for them on PATH
export PATH="$LOCAL_BIN:$PATH"

step() { printf '\n== %s\n' "$*"; }
ok()   { printf 'ok     %s\n' "$*"; }
note() { printf 'note   %s\n' "$*"; }
die()  { printf 'error  %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

run() {
  if (( DRY_RUN )); then
    printf 'would  %s\n' "$*"
  else
    printf 'run    %s\n' "$*"
    "$@"
  fi
}

detect_platform() {
  step "Platform"
  OS="$(uname -s)"
  case "$OS" in
    Linux) have apt-get || die "this Linux has no apt-get; only Ubuntu is supported" ;;
    Darwin) ;;
    *) die "unsupported OS: $OS" ;;
  esac
  ok "$OS $(uname -m)"
  if (( DRY_RUN )); then note "dry run: nothing will change"; fi
  if [[ "$SKIP" != " " ]]; then note "skipping:$SKIP"; fi
}

installed() {
  if [[ "$OS" == Linux ]]; then
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
  else
    brew list --formula "$1" >/dev/null 2>&1
  fi
}

install_packages() {
  step "System packages"
  local packages missing=() p
  if [[ "$OS" == Linux ]]; then
    packages=(zsh git curl jq gh tmux fontconfig xz-utils)
  else
    if ! have brew; then
      (( DRY_RUN )) || die "Homebrew is missing; install it from https://brew.sh first"
      note "Homebrew is missing; the real run stops here until it is installed"
      return 0
    fi
    packages=(git jq gh tmux)
  fi
  for p in "${packages[@]}"; do
    if installed "$p"; then ok "$p"; else missing+=("$p"); fi
  done
  (( ${#missing[@]} )) || return 0
  if [[ "$OS" == Linux ]]; then
    note "installing ${missing[*]} with apt, which uses sudo"
    run sudo apt-get update
    run sudo apt-get install -y "${missing[@]}"
  else
    run brew install "${missing[@]}"
  fi
}

# Moves an existing file aside to the first free .bak, .bak.1, ... and links the managed one in its place.
link() {
  local src="$DOTFILES/$1" dest="$2" bak n=1
  if [[ -L "$dest" && "$(readlink "$dest")" == "$src" ]]; then
    ok "$dest -> $src"
    return 0
  fi
  [[ -d "$(dirname "$dest")" ]] || run mkdir -p "$(dirname "$dest")"
  if [[ -e "$dest" || -L "$dest" ]]; then
    bak="$dest.bak"
    while [[ -e "$bak" || -L "$bak" ]]; do bak="$dest.bak.$n"; n=$((n + 1)); done
    run mv "$dest" "$bak"
  fi
  run ln -s "$src" "$dest"
}

# Runs an official installer (pointed at ~/.local/bin, so no sudo) unless the tool is already on PATH.
install_tool() {
  step "$1"
  if have "$1"; then
    ok "$1 ($(command -v "$1"))"
    return 0
  fi
  [[ -d "$LOCAL_BIN" ]] || run mkdir -p "$LOCAL_BIN"
  run sh -c "$2"
}

install_starship() {
  install_tool starship "curl -fsSL https://starship.rs/install.sh | sh -s -- -y -b '$LOCAL_BIN'"
  link starship.toml "$HOME/.config/starship.toml"
}

install_mise() {
  install_tool mise "curl -fsSL https://mise.run | MISE_INSTALL_PATH='$LOCAL_BIN/mise' sh"
  link mise.toml "$HOME/.config/mise/config.toml"
  if have mise && [[ -z "$(mise ls --missing 2>/dev/null)" ]]; then
    ok "mise tools"
  else
    run mise install
  fi
}

# UV_NO_MODIFY_PATH stops the installer from appending to .zshrc, which is a link into this repo.
install_uv() {
  install_tool uv "curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR='$LOCAL_BIN' UV_NO_MODIFY_PATH=1 sh"
}

link_dotfiles() {
  step "Dotfiles"
  link zshrc "$HOME/.zshrc"
  link tmux.conf "$HOME/.tmux.conf"
}

# Runs after the links, so --keep-zshrc keeps the managed .zshrc instead of the template.
install_oh_my_zsh() {
  step "Oh My Zsh"
  if [[ -d "$HOME/.oh-my-zsh" ]]; then
    ok "$HOME/.oh-my-zsh"
  else
    run sh -c "curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh | RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh -s -- --unattended --keep-zshrc"
  fi
}

set_login_shell() {
  step "Login shell"
  local zsh_path current
  if [[ "$OS" == Darwin ]]; then
    zsh_path=/bin/zsh
    current="$(dscl . -read "/Users/$USER" UserShell | awk '{print $2}')"
  else
    zsh_path="$(command -v zsh || echo /usr/bin/zsh)"
    current="$(getent passwd "$USER" | cut -d: -f7)"
  fi
  if [[ "$(basename "$current")" == zsh ]]; then
    ok "login shell is $current"
  else
    note "chsh asks for your password"
    run chsh -s "$zsh_path"
  fi
}

# Downloads one Nerd Fonts release archive and copies its font files into $2.
fetch_fonts() {
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/ryanoasis/nerd-fonts/releases/download/$NERD_FONTS_VERSION/$1.tar.xz" | tar -xJf - -C "$tmp"
  mkdir -p "$2"
  find "$tmp" -type f \( -name '*.otf' -o -name '*.ttf' \) -exec cp -n {} "$2/" \;
  rm -rf "$tmp"
}

install_fonts() {
  step "Fonts (Nerd Fonts $NERD_FONTS_VERSION)"
  local dir="$HOME/.local/share/fonts/nerdfonts" added=0 archive marker
  [[ "$OS" == Darwin ]] && dir="$HOME/Library/Fonts"
  # each archive, then one file that proves it is already installed
  for archive in ComicShannsMono:ComicShannsMonoNerdFont-Regular.otf NerdFontsSymbolsOnly:SymbolsNerdFont-Regular.ttf; do
    marker="${archive#*:}" archive="${archive%%:*}"
    if [[ -f "$dir/$marker" ]]; then
      ok "$archive"
    else
      run fetch_fonts "$archive" "$dir"
      added=1
    fi
  done
  if [[ "$OS" == Linux ]] && (( added )); then
    run fc-cache -f "$dir"
  fi
}

# agent install is idempotent itself, so it runs every time rather than being skipped.
install_agents() {
  step "Agent hooks and standards"
  local agent="$HOME/.agents/bin/agent" profile="$HOME/.agents/profile"
  if [[ ! -x "$agent" ]]; then
    note "skipped: ~/.agents is not cloned"
  elif [[ -d "$profile" ]]; then
    run "$agent" install --profile "$profile"
  else
    run "$agent" install
  fi
}

detect_platform
wants packages && install_packages
wants starship && install_starship
wants mise && install_mise
wants uv && install_uv
wants dotfiles && link_dotfiles
wants ohmyzsh && install_oh_my_zsh
wants shell && set_login_shell
wants fonts && install_fonts
wants agents && install_agents

step "Done"
if (( DRY_RUN )); then
  note "dry run finished; nothing changed"
else
  note "open a new terminal to pick up the shell changes"
fi
