#!/usr/bin/env bash
# Sets up a shell, prompt, terminal tools and fonts on Ubuntu or macOS, and removes them again with --uninstall.
# Safe to run again: anything already in place is reported and left alone.
# Author: Marco Cassar (@Ocramaru)
set -euo pipefail

ARCHIVE="https://github.com/Ocramaru/UtilityProjects/archive/refs/heads/main.tar.gz"
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"

# Run through curl there is no checkout beside the script: download one, run from it, and delete it after.
if [[ ! -d "$SETUP_DIR/dotfiles" ]]; then
  download="$(mktemp -d)"
  trap 'rm -rf -- "$download"' EXIT
  curl -fsSL "$ARCHIVE" | tar -xzf - -C "$download"
  bash "$download/UtilityProjects-main/device-terminal-setup/install.sh" "$@"
  exit
fi

DOTFILES="$SETUP_DIR/dotfiles"
VERSION="$(cat "$SETUP_DIR/VERSION")"
MARKER="# Managed by device-terminal-setup"
YOURS="# ---- Your lines: everything below here is kept on update ----"
STATE="$HOME/.local/state/device-terminal-setup"
LOCAL_BIN="$HOME/.local/bin"
NERD_FONTS_VERSION="v3.4.0"
COMPONENTS=" packages starship mise uv dotfiles ohmyzsh shell fonts agents "
DRY_RUN=0
UNINSTALL=0
SKIP=" "

usage() {
  cat <<EOF
usage: $0 [--dry-run] [--uninstall] [--skip LIST] [--only LIST] [--version]

  --dry-run    print every command it would run and change nothing
  --uninstall  remove what the install added, for the chosen components
  --skip LIST  leave out these comma separated components
  --only LIST  act on just these comma separated components
  --version    print this version and the installed one

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
    --uninstall) UNINSTALL=1 ;;
    --skip) read_list "${2:-}"; SKIP+="$LIST"; shift ;;
    --only)
      read_list "${2:-}"
      for component in $COMPONENTS; do
        [[ " $LIST" == *" $component "* ]] || SKIP+="$component "
      done
      shift
      ;;
    --version) echo "device-terminal-setup $VERSION (installed: $(cat "$STATE/version" 2>/dev/null || echo none))"; exit ;;
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
  ok "$OS $(uname -m), device-terminal-setup $VERSION"
  if (( DRY_RUN )); then info "Dry run: nothing will change"; fi
  if [[ "$SKIP" != " " ]]; then info "Skipping:$SKIP"; fi
}

# Records what the install itself added, so --uninstall removes only that.
record() { if (( ! DRY_RUN )); then mkdir -p "$STATE" && printf '%s\n' "$*" >> "$STATE/installed"; fi; }
recorded() { grep -qxF "$*" "$STATE/installed" 2>/dev/null; }

## Dotfiles: copied in with the version stamped into their first line, keeping your lines below the YOURS line

managed() { [[ -f "$1" && ! -L "$1" ]] && head -n 1 "$1" | grep -q "^$MARKER "; }

stamped() { sed "1s/^$MARKER dev:/$MARKER $VERSION:/" "$1"; }

# The line number of the YOURS line in $1, or nothing when it has none.
yours_at() { grep -nxF "$YOURS" "$1" | head -n 1 | cut -d: -f1; }

# Prints the lines you added below the YOURS line.
your_lines() {
  local line
  line="$(yours_at "$1")"
  if [[ -n "$line" ]]; then tail -n "+$((line + 1))" "$1"; fi
}

# Prints the part an update replaces: everything up to and including the YOURS line.
setup_part() {
  local line
  line="$(yours_at "$1")"
  if [[ -n "$line" ]]; then head -n "$line" "$1"; else cat "$1"; fi
}

# Writes the stamped source to $2, followed by the lines you added below the YOURS line in the file it replaces.
copy_dotfile() {
  { stamped "$1"; if managed "$2"; then your_lines "$2"; fi; } > "$2.new"
  mv "$2.new" "$2"
}

# The first free name among $1.bak, $1.bak.1, $1.bak.2, ...
free_backup() {
  local backup="$1.bak" number=1
  while [[ -e "$backup" || -L "$backup" ]]; do backup="$1.bak.$number"; number=$((number + 1)); done
  printf '%s' "$backup"
}

# Copies a dotfile into place. Anything there that the setup did not write is moved aside first, and the move is recorded for --uninstall.
install_dotfile() {
  local source="$DOTFILES/$1" destination="$2" backup
  if managed "$destination" && cmp -s <(stamped "$source") <(setup_part "$destination"); then
    ok "$(tilde "$destination") is up to date"
    return 0
  fi
  [[ -d "$(dirname "$destination")" ]] || run "Created $(tilde "$(dirname "$destination")")" mkdir -p "$(dirname "$destination")"
  if managed "$destination"; then
    run "Updated $(tilde "$destination") to $VERSION, keeping your lines" copy_dotfile "$source" "$destination"
    return 0
  fi
  if [[ -e "$destination" || -L "$destination" ]]; then
    backup="$(free_backup "$destination")"
    run "Moved $(tilde "$destination") to $(tilde "$backup")" mv "$destination" "$backup"
    if (( ! DRY_RUN )); then mkdir -p "$STATE" && printf '%s\t%s\n' "$destination" "$backup" >> "$STATE/backups"; fi
  fi
  run "Installed $(tilde "$destination")" copy_dotfile "$source" "$destination"
}

# Removes a dotfile the setup wrote and puts back the file the install moved aside for it. A file with your lines in it is moved aside instead, so they are not lost.
uninstall_dotfile() {
  local destination="$1" backup
  if managed "$destination" && [[ -n "$(your_lines "$destination" | tr -d '[:space:]')" ]]; then
    backup="$(free_backup "$destination")"
    run "Moved $(tilde "$destination"), which has your lines, to $(tilde "$backup")" mv "$destination" "$backup"
  elif managed "$destination"; then
    run "Removed $(tilde "$destination")" rm "$destination"
  elif [[ -e "$destination" ]]; then
    ok "$(tilde "$destination") was not written by this setup; left alone"
    return 0
  fi
  backup="$(awk -F '\t' -v destination="$destination" '$1 == destination { backup = $2 } END { print backup }' "$STATE/backups" 2>/dev/null || true)"
  if [[ -n "$backup" && -e "$backup" ]]; then run "Restored $(tilde "$destination") from $(tilde "$backup")" mv "$backup" "$destination"; fi
}

## Install

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

# Runs an official installer (pointed at ~/.local/bin, so no sudo) unless the tool is already on PATH.
install_tool() {
  step "$1"
  if have "$1"; then
    ok "$1 already installed ($(tilde "$(command -v "$1")"))"
    return 0
  fi
  [[ -d "$LOCAL_BIN" ]] || run "Created ~/.local/bin" mkdir -p "$LOCAL_BIN"
  run "Installed $1 to ~/.local/bin" sh -c "$2"
  record "tool $1"
}

install_starship() {
  install_tool starship "curl -fsSL https://starship.rs/install.sh | sh -s -- -y -b '$LOCAL_BIN'"
  install_dotfile starship.toml "$HOME/.config/starship.toml"
}

install_mise() {
  install_tool mise "curl -fsSL https://mise.run | MISE_INSTALL_PATH='$LOCAL_BIN/mise' sh"
  install_dotfile mise.toml "$HOME/.config/mise/config.toml"
  if have mise && [[ -z "$(mise ls --missing 2>/dev/null)" ]]; then
    ok "mise tools already installed"
  else
    run "Installed mise tools" mise install
  fi
}

# UV_NO_MODIFY_PATH stops the installer from appending to .zshrc, which the setup manages.
install_uv() {
  install_tool uv "curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR='$LOCAL_BIN' UV_NO_MODIFY_PATH=1 sh"
}

install_dotfiles() {
  step "Dotfiles"
  install_dotfile zshrc "$HOME/.zshrc"
  install_dotfile tmux.conf "$HOME/.tmux.conf"
  # Ghostty runs on the machine you type on: the Mac, or a Linux desktop that has it
  if [[ "$OS" == Darwin ]] || have ghostty; then install_dotfile ghostty.config "$HOME/.config/ghostty/config"; fi
}

# Runs after the dotfiles, so --keep-zshrc keeps the managed .zshrc instead of the template.
install_oh_my_zsh() {
  step "Oh My Zsh"
  if [[ -d "$HOME/.oh-my-zsh" ]]; then
    ok "Oh My Zsh already installed"
  else
    run "Installed Oh My Zsh" sh -c "curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh | RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh -s -- --unattended --keep-zshrc"
    record ohmyzsh
  fi
}

current_shell() {
  if [[ "$OS" == Darwin ]]; then dscl . -read "/Users/$USER" UserShell | awk '{print $2}'; else getent passwd "$USER" | cut -d: -f7; fi
}

# Images with passwordless sudo often give the user no password for chsh to ask for.
change_shell() {
  if sudo -n true 2>/dev/null; then
    run "Login shell set to $1" sudo chsh -s "$1" "$USER"
  else
    info "chsh asks for your password"
    run "Login shell set to $1" chsh -s "$1"
  fi
}

set_login_shell() {
  step "Login shell"
  local current
  current="$(current_shell)"
  if [[ "$(basename "$current")" == zsh ]]; then
    ok "Login shell is already $current"
    return 0
  fi
  record "shell $current"
  if [[ "$OS" == Darwin ]]; then
    change_shell /bin/zsh
  else
    change_shell "$(command -v zsh || echo /usr/bin/zsh)"
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

font_directory() {
  if [[ "$OS" == Darwin ]]; then echo "$HOME/Library/Fonts"; else echo "$HOME/.local/share/fonts/nerdfonts"; fi
}

install_fonts() {
  step "Fonts (Nerd Fonts $NERD_FONTS_VERSION)"
  local directory added=0 archive marker
  directory="$(font_directory)"
  # each archive, then one file that proves it is already installed
  for archive in ComicShannsMono:ComicShannsMonoNerdFont-Regular.otf NerdFontsSymbolsOnly:SymbolsNerdFont-Regular.ttf; do
    marker="${archive#*:}" archive="${archive%%:*}"
    if [[ -f "$directory/$marker" ]]; then
      ok "$archive already installed"
    else
      run "Installed $archive to $(tilde "$directory")" fetch_fonts "$archive" "$directory"
      record "fonts $archive"
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

## Uninstall: each component reversed, leaving anything the setup did not put there

# Removes a tool, and any companion files given after it, only if this setup installed it.
uninstall_tool() {
  local name="$1"
  shift
  if ! recorded "tool $name"; then
    ok "$name was not installed by this setup; left alone"
    return 1
  fi
  run "Removed $name from ~/.local/bin" rm -f "$@"
}

uninstall_component() {
  case "$1" in
    packages)
      step "System packages"
      info "Left apt and Homebrew packages installed, since other software may use them"
      ;;
    starship)
      step "starship"
      uninstall_dotfile "$HOME/.config/starship.toml"
      uninstall_tool starship "$LOCAL_BIN/starship" || true
      ;;
    mise)
      step "mise"
      uninstall_dotfile "$HOME/.config/mise/config.toml"
      if uninstall_tool mise "$LOCAL_BIN/mise"; then
        run "Removed mise's tools, state and cache" rm -rf "$HOME/.local/share/mise" "$HOME/.local/state/mise" "$HOME/.cache/mise"
      fi
      ;;
    uv)
      step "uv"
      uninstall_tool uv "$LOCAL_BIN/uv" "$LOCAL_BIN/uvx" || true
      ;;
    dotfiles)
      step "Dotfiles"
      uninstall_dotfile "$HOME/.zshrc"
      uninstall_dotfile "$HOME/.tmux.conf"
      uninstall_dotfile "$HOME/.config/ghostty/config"
      ;;
    ohmyzsh)
      step "Oh My Zsh"
      if recorded ohmyzsh; then run "Removed Oh My Zsh" rm -rf "$HOME/.oh-my-zsh"; else ok "Oh My Zsh was not installed by this setup; left alone"; fi
      ;;
    shell)
      step "Login shell"
      local previous
      previous="$(awk '$1 == "shell" { shell = $2 } END { print shell }' "$STATE/installed" 2>/dev/null || true)"
      if [[ -z "$previous" ]]; then
        ok "Login shell was not changed by this setup; left alone"
      elif [[ "$(current_shell)" == "$previous" ]]; then
        ok "Login shell is already $previous"
      else
        change_shell "$previous"
      fi
      ;;
    fonts)
      step "Fonts"
      local directory removed=0
      directory="$(font_directory)"
      if recorded "fonts ComicShannsMono"; then run "Removed ComicShannsMono" rm -f "$directory"/ComicShannsMono*NerdFont*.otf; removed=1; fi
      if recorded "fonts NerdFontsSymbolsOnly"; then run "Removed NerdFontsSymbolsOnly" rm -f "$directory"/SymbolsNerdFont*.ttf; removed=1; fi
      if (( ! removed )); then ok "No fonts were installed by this setup; left alone"; fi
      if [[ "$OS" == Linux ]] && (( removed )); then run "Refreshed the font cache" fc-cache -f; fi
      ;;
    agents)
      step "Agent hooks and standards"
      info "Left agent hooks in place; ~/.agents/bin/agent manages them"
      ;;
  esac
}

detect_platform
if (( UNINSTALL )); then
  for component in agents fonts shell ohmyzsh dotfiles uv mise starship packages; do
    if wants "$component"; then uninstall_component "$component"; fi
  done
  if [[ "$SKIP" == " " ]]; then run "Removed $(tilde "$STATE")" rm -rf "$STATE"; fi
  step "Done"
  if (( DRY_RUN )); then info "Dry run finished; nothing changed"; else printf '%sUninstalled: open a new terminal%s\n' "$GREEN" "$RESET"; fi
  exit
fi

wants packages && install_packages
wants starship && install_starship
wants mise && install_mise
wants uv && install_uv
wants dotfiles && install_dotfiles
wants ohmyzsh && install_oh_my_zsh
wants shell && set_login_shell
wants fonts && install_fonts
wants agents && install_agents

step "Done"
if (( DRY_RUN )); then
  info "Dry run finished; nothing changed"
else
  mkdir -p "$STATE" && echo "$VERSION" > "$STATE/version"
  printf '%sReady: device-terminal-setup %s, open a new terminal%s\n' "$GREEN" "$VERSION" "$RESET"
fi
