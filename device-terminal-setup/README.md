# Device terminal setup

This is my terminal setup, packaged so a new machine matches the old one: zsh with Oh My Zsh and starship, mise, uv, tmux, the usual CLI tools, and the ComicShannsMono and Symbols Nerd Fonts. It runs on Ubuntu (aarch64 and x86_64) and on macOS through Homebrew. Anyone is welcome to use it; fork it first if you want to change the dotfiles.

The files in `dotfiles/` are symlinked into place, so editing `~/.zshrc` edits the repo. Anything already in the way is moved to a numbered `.bak` first, never overwritten. Running it again leaves finished steps alone and says so.

## Running it

Preview, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/device-terminal-setup/install.sh | bash -s -- --dry-run
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/device-terminal-setup/install.sh | bash
```

The first real run clones this repo to `~/UtilityProjects` (set `UTILITY_PROJECTS` to put it elsewhere) and runs from there, since the dotfiles link into it; keep the clone. Later runs use that clone, and `git pull` in it updates the dotfiles. It needs git, and on macOS [Homebrew](https://brew.sh). From an existing clone, `device-terminal-setup/install.sh` does the same.

Options go after `bash -s --`: `--skip mise,fonts` leaves components out, `--only fonts` installs just those, and `--dry-run` lists every command without running it. The script uses sudo only for apt, and says so before it does. `chsh` asks for your password if the login shell is not zsh yet.

## Components

The managed `.zshrc` loads Oh My Zsh, starship and mise only when they are installed, so any combination leaves a working shell.

| Component | What it installs | Disk, roughly |
|---|---|---|
| `packages` | apt: zsh git curl jq gh tmux fontconfig xz-utils, or brew: git jq gh tmux | 90 MB, mostly git and gh, often already there |
| `starship` | starship into `~/.local/bin`, and its config | 10 MB |
| `mise` | mise into `~/.local/bin`, its config, and the tools it lists (just fzf) | 90 MB |
| `uv` | uv into `~/.local/bin` | 45 MB |
| `dotfiles` | `.zshrc`, `.tmux.conf`, and the Ghostty config on a Mac or wherever Ghostty is installed | none |
| `ohmyzsh` | Oh My Zsh, unattended, keeping the managed `.zshrc` | 18 MB |
| `shell` | `chsh` to zsh | none |
| `fonts` | Nerd Fonts v3.4.0 into `~/.local/share/fonts/nerdfonts` (then `fc-cache`) or `~/Library/Fonts` | 19 MB, a 5 MB download |
| `agents` | runs `~/.agents/bin/agent install`; skipped when `~/.agents` is not cloned | none |

Everything together is about 270 MB, and most of that is mise, uv and the apt packages. A new shell starts in under 0.1 s on my machine with all of it loaded.

## Machine-specific lines

The managed `.zshrc` sources `~/.zshrc.local` if it exists. Anything that belongs to one machine goes there, such as a ROS `source` line or a project's environment variables, and so do secrets like API keys. The script never creates or edits it, and it never ends up in the repo.

mise works the same way: tools for one machine go in `~/.config/mise/conf.d/local.toml`, which mise reads alongside the managed config. Node is not installed by default; to add it on a machine that needs it:

```bash
mise use --path ~/.config/mise/conf.d/local.toml node@20
```

Plain `mise use -g` would write into the managed config, which is a link into this repo.

## Not covered

The `vcode` bridge has its own installer: run it on the Mac from [`vcode-bridge/`](../vcode-bridge/), and it puts the `vcode` command on each host you pick.
