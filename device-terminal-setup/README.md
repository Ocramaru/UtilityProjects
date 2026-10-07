# Device terminal setup

This is my terminal setup, packaged so a new machine matches the old one: zsh with Oh My Zsh and starship, mise, uv, tmux, the usual CLI tools, and the ComicShannsMono and Symbols Nerd Fonts. It runs on Ubuntu (aarch64 and x86_64) and on macOS through Homebrew. Anyone is welcome to use it; fork it first if you want to change the dotfiles.

The files in `dotfiles/` are symlinked into place, so editing `~/.zshrc` edits the repo. Anything already in the way is moved to a numbered `.bak` first, never overwritten. Running it again reports `ok` for every step already done.

## Running it

1. Install git. On Ubuntu that is `sudo apt-get install -y git`. On macOS, install [Homebrew](https://brew.sh) first.
2. Clone this repo anywhere and preview what it would do:

   ```bash
   git clone https://github.com/Ocramaru/UtilityProjects.git
   UtilityProjects/device-terminal-setup/install.sh --dry-run
   ```

3. Run it for real, leaving out anything you do not want:

   ```bash
   UtilityProjects/device-terminal-setup/install.sh --skip mise,fonts
   ```

The script uses sudo only for apt, and says so before it does. `chsh` asks for your password if the login shell is not zsh yet. Open a new terminal when it finishes. Keep the clone where it is afterwards, since the dotfiles link back into it.

## Components

`--skip a,b` leaves components out and `--only a,b` installs just those. The managed `.zshrc` loads Oh My Zsh, starship and mise only when they are installed, so any combination leaves a working shell.

| Component | What it installs | Disk, roughly |
|---|---|---|
| `packages` | apt: zsh git curl jq gh tmux fontconfig xz-utils, or brew: git jq gh tmux | 90 MB, mostly git and gh, often already there |
| `starship` | starship into `~/.local/bin`, and its config | 10 MB |
| `mise` | mise into `~/.local/bin`, its config, and the tools it lists (just fzf) | 90 MB |
| `uv` | uv into `~/.local/bin` | 45 MB |
| `dotfiles` | `.zshrc` and `.tmux.conf` | none |
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

## What it cannot do

Some of the setup lives on the Mac I connect from, so the script cannot install it:

- The Ghostty config, `~/.config/ghostty/config`. `export TERM=xterm-256color` in the managed `.zshrc` covers Ghostty's delete key over SSH.
- The `vcode` bridge. Run its installer on the Mac from [`vcode-bridge/`](../vcode-bridge/); it also puts the `vcode` command on each host you pick.
