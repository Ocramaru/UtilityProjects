#!/usr/bin/env bash
# Sets up a shell, prompt, terminal tools and fonts on Ubuntu or macOS.
# Safe to run again: anything already in place is reported and left alone.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

REPOSITORY="https://github.com/Ocramaru/UtilityProjects.git"
CLONE="${UTILITY_PROJECTS:-$HOME/UtilityProjects}"
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"

# Run through curl there is no checkout beside the script; the dotfiles link into one, so it has to stay.
if [[ ! -d "$SETUP_DIR/dotfiles" ]]; then
  if [[ -d "$CLONE/.git" ]]; then
    echo "[info] Using the clone at $CLONE"
  elif [[ " $* " == *" --dry-run "* ]]; then
    temporary_clone="$(mktemp -d)"
    trap 'rm -rf -- "$temporary_clone"' EXIT
    echo "→ git clone $REPOSITORY $CLONE"
    echo "[info] The paths below point at a temporary copy; the real run links into $CLONE"
    git clone --quiet --depth 1 "$REPOSITORY" "$temporary_clone"
    bash "$temporary_clone/device-terminal-setup/install.sh" "$@"
    exit
  else
    command -v git >/dev/null || { echo "error: git is needed to clone $REPOSITORY" >&2; exit 1; }
    git clone --quiet "$REPOSITORY" "$CLONE"
    echo "✓ Cloned $REPOSITORY to $CLONE"
  fi
  exec bash "$CLONE/device-terminal-setup/install.sh" "$@"
fi

DOTFILES="$SETUP_DIR/dotfiles"
LOCAL_BIN="$HOME/.local/bin"
NERD_FONTS_VERSION="v3.4.0"
COMPONENTS=" packages starship mise uv dotfiles ohmyzsh shell fonts agents "
DRY_RUN=0
SKIP=" "

usage() {
  cat <<EOF
usage: $0 [--dry-run] [--skip LIST] [--only LIST]

  --dry-run    print every command it would run and change nothing
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
      for component in $COMPONENTS; do
        [[ " $LIST" == *" $component "* ]] || SKIP+="$component "
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

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\033[1m' DIM=$'\033[2m' GREEN=$'\033[32m' YELLOW=$'\033[33m' RED=$'\033[1;31m' CYAN=$'\033[36m' RESET=$'\033[0m'
else
  BOLD="" DIM="" GREEN="" YELLOW="" RED="" CYAN="" RESET=""
fi

step() { printf '\n%s== %s%s\n' "$BOLD" "$*" "$RESET"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
info() { printf '%s[info] %s%s\n' "$DIM" "$*" "$RESET"; }
warn() { printf '%s%s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
tilde() { printf '%s' "${1/#$HOME/\~}"; }

# Prints the command in a dry run; otherwise runs it and reports $1 once it succeeds.
run() {
  local message="$1"
  shift
  if (( DRY_RUN )); then
    printf '%s→ %s%s\n' "$CYAN" "$*" "$RESET"
  else
    "$@"
    ok "$message"
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
  if (( DRY_RUN )); then info "Dry run: nothing will change"; fi
  if [[ "$SKIP" != " " ]]; then info "Skipping:$SKIP"; fi
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
  local packages missing=() package
  if [[ "$OS" == Linux ]]; then
    packages=(zsh git curl jq gh tmux fontconfig xz-utils)
  else
    if ! have brew; then
      (( DRY_RUN )) || die "Homebrew is missing; install it from https://brew.sh first"
      warn "Homebrew is missing; the real run stops here until it is installed"
      return 0
    fi
    packages=(git jq gh tmux)
  fi
  for package in "${packages[@]}"; do
    if installed "$package"; then ok "$package already installed"; else missing+=("$package"); fi
  done
  (( ${#missing[@]} )) || return 0
  if [[ "$OS" == Linux ]]; then
    info "apt installs ${missing[*]} with sudo"
    run "Updated apt" sudo apt-get update
    run "Installed ${missing[*]}" sudo apt-get install -y "${missing[@]}"
  else
    run "Installed ${missing[*]}" brew install "${missing[@]}"
  fi
}

# Moves an existing file aside to the first free .bak, .bak.1, ... and links the managed one in its place.
link() {
  local source="$DOTFILES/$1" destination="$2" backup number=1
  if [[ -L "$destination" && "$(readlink "$destination")" == "$source" ]]; then
    ok "$(tilde "$destination") already linked"
    return 0
  fi
  [[ -d "$(dirname "$destination")" ]] || run "Created $(tilde "$(dirname "$destination")")" mkdir -p "$(dirname "$destination")"
  if [[ -e "$destination" || -L "$destination" ]]; then
    backup="$destination.bak"
    while [[ -e "$backup" || -L "$backup" ]]; do backup="$destination.bak.$number"; number=$((number + 1)); done
    run "Moved $(tilde "$destination") to $(tilde "$backup")" mv "$destination" "$backup"
  fi
  run "Linked $(tilde "$destination") to dotfiles/$1" ln -s "$source" "$destination"
}

# Runs an official installer (pointed at ~/.local/bin, so no sudo) unless the tool is already on PATH.
install_tool() {
  step "$1"
  if have "$1"; then
    ok "$1 already installed ($(tilde "$(command -v "$1")"))"
    return 0
  fi
  [[ -d "$LOCAL_BIN" ]] || run "Created ~/.local/bin" mkdir -p "$LOCAL_BIN"
  run "Installed $1 to ~/.local/bin" sh -c "$2"
}

install_starship() {
  install_tool starship "curl -fsSL https://starship.rs/install.sh | sh -s -- -y -b '$LOCAL_BIN'"
  link starship.toml "$HOME/.config/starship.toml"
}

install_mise() {
  install_tool mise "curl -fsSL https://mise.run | MISE_INSTALL_PATH='$LOCAL_BIN/mise' sh"
  link mise.toml "$HOME/.config/mise/config.toml"
  if have mise && [[ -z "$(mise ls --missing 2>/dev/null)" ]]; then
    ok "mise tools already installed"
  else
    run "Installed mise tools" mise install
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
  # Ghostty runs on the machine you type on: the Mac, or a Linux desktop that has it
  if [[ -f "$DOTFILES/ghostty.config" ]] && { [[ "$OS" == Darwin ]] || have ghostty; }; then
    link ghostty.config "$HOME/.config/ghostty/config"
  fi
}

# Runs after the links, so --keep-zshrc keeps the managed .zshrc instead of the template.
install_oh_my_zsh() {
  step "Oh My Zsh"
  if [[ -d "$HOME/.oh-my-zsh" ]]; then
    ok "Oh My Zsh already installed"
  else
    run "Installed Oh My Zsh" sh -c "curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh | RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh -s -- --unattended --keep-zshrc"
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
    ok "Login shell is already $current"
  elif sudo -n true 2>/dev/null; then
    # images with passwordless sudo often give the user no password for chsh to ask for
    run "Login shell set to $zsh_path" sudo chsh -s "$zsh_path" "$USER"
  else
    info "chsh asks for your password"
    run "Login shell set to $zsh_path" chsh -s "$zsh_path"
  fi
}

# Downloads one Nerd Fonts release archive and copies its font files into $2.
fetch_fonts() {
  local download
  download="$(mktemp -d)"
  curl -fsSL "https://github.com/ryanoasis/nerd-fonts/releases/download/$NERD_FONTS_VERSION/$1.tar.xz" | tar -xJf - -C "$download"
  mkdir -p "$2"
  find "$download" -type f \( -name '*.otf' -o -name '*.ttf' \) -exec cp -n {} "$2/" \;
  rm -rf "$download"
}

install_fonts() {
  step "Fonts (Nerd Fonts $NERD_FONTS_VERSION)"
  local directory="$HOME/.local/share/fonts/nerdfonts" added=0 archive marker
  [[ "$OS" == Darwin ]] && directory="$HOME/Library/Fonts"
  # each archive, then one file that proves it is already installed
  for archive in ComicShannsMono:ComicShannsMonoNerdFont-Regular.otf NerdFontsSymbolsOnly:SymbolsNerdFont-Regular.ttf; do
    marker="${archive#*:}" archive="${archive%%:*}"
    if [[ -f "$directory/$marker" ]]; then
      ok "$archive already installed"
    else
      run "Installed $archive to $(tilde "$directory")" fetch_fonts "$archive" "$directory"
      added=1
    fi
  done
  if [[ "$OS" == Linux ]] && (( added )); then
    run "Refreshed the font cache" fc-cache -f "$directory"
  fi
}

# agent install is idempotent itself, so it runs every time rather than being skipped.
install_agents() {
  step "Agent hooks and standards"
  local agent="$HOME/.agents/bin/agent" profile="$HOME/.agents/profile"
  if [[ ! -x "$agent" ]]; then
    info "Skipped: ~/.agents is not cloned"
  elif [[ -d "$profile" ]]; then
    run "Installed agent hooks with ~/.agents/profile" "$agent" install --profile "$profile"
  else
    run "Installed agent hooks" "$agent" install
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
  info "Dry run finished; nothing changed"
else
  printf '%sReady: open a new terminal%s\n' "$GREEN" "$RESET"
fi
