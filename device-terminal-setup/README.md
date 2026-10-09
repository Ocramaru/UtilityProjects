# Device terminal setup

This is my terminal setup, packaged so a new machine matches the old one: zsh with Oh My Zsh and starship, mise, uv, tmux, the usual CLI tools, and the ComicShannsMono and Symbols Nerd Fonts. It runs on Ubuntu (aarch64 and x86_64) and on macOS through Homebrew. Anyone is welcome to use it; fork it first if you want to change the dotfiles.

The files in `dotfiles/` are copied into place with the version in their first line. Anything already in the way is moved to a numbered `.bak` first, never overwritten. Running it again leaves finished steps alone and says so. Nothing of the repo stays on the machine: a run through curl downloads it to a temporary folder and deletes it afterwards.

## Running it

Preview, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/device-terminal-setup/install.sh | bash -s -- --dry-run
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/device-terminal-setup/install.sh | bash
```

On macOS it needs [Homebrew](https://brew.sh). From a checkout, `device-terminal-setup/install.sh` does the same with the checkout's files. Options go after `bash -s --`:

| Option | Does |
|---|---|
| `--dry-run` | lists every command it would run, and changes nothing |
| `--skip mise,fonts` | leaves those components out |
| `--only fonts` | acts on just those components |
| `--uninstall` | removes what the install added, for the chosen components |
| `--version` | prints this version and the installed one |

The script uses sudo only for apt and, where sudo needs no password, to change the login shell; it says so before it does.

## Updating and uninstalling

Running the curl line again updates a machine: managed dotfiles from an older version are replaced, and anything already current is left alone. Each managed file ends with a `# ---- Your lines: everything below here is kept on update ----` line. Add your own lines below it and updates keep them; anything above it is replaced.

`--uninstall` removes only what the install added. It deletes the managed dotfiles, or moves one aside if you added lines below its "Your lines" line, and puts back the files it moved aside, and removes the tools, Oh My Zsh and fonts it installed and the login shell it changed, all from a record it keeps in `~/.local/state/device-terminal-setup/`. Anything that was there before, the apt and Homebrew packages, and the local files are left alone.

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

Lines below the "Your lines" line in any managed file stay on that machine. For `.zshrc` there is also `~/.zshrc.local`, which the managed `.zshrc` sources if it exists. Anything that belongs to one machine can go in either, such as a ROS `source` line or a project's environment variables, and so do secrets like API keys. Neither ever ends up in the repo, and the script never touches `~/.zshrc.local`.

mise works the same way: tools for one machine go in `~/.config/mise/conf.d/local.toml`, which mise reads alongside the managed config. Node is not installed by default; to add it on a machine that needs it:

```bash
mise use --path ~/.config/mise/conf.d/local.toml node@20
```

Plain `mise use -g` would write into the managed config, which the next update replaces.

## Not covered

The `vcode` bridge has its own installer: run it on the Mac from [`vcode-bridge/`](../vcode-bridge/), and it puts the `vcode` command on each host you pick.
