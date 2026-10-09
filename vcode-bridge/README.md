# VS Code SSH Bridge

Run `vcode .` in any SSH session and the folder opens in VS Code on your Mac, through Remote SSH. That includes a host you reached by hopping through another one: `ssh sparky`, then `ssh inner`, then `vcode .` on inner, and VS Code on the Mac connects straight to inner.

One launch agent on the Mac keeps an SSH tunnel open to each host you install, and reconnects any that drop. Nothing listens on a TCP port, and your own SSH sessions never own a tunnel.

## Install

On the Mac you need [uv](https://docs.astral.sh/uv/) (it fetches Python 3.15 by itself), Go, and VS Code with the Remote SSH extension and the `code` command (VS Code: **Shell Command: Install 'code' command in PATH**). Each host needs SSH key login from the Mac, plus bash and curl.

```bash
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/vcode-bridge/install.sh | bash -s -- sparky
```

`install.sh` downloads the latest version from GitHub every time and runs its installer, passing your arguments through. The installer:

1. Connects to the host once, so you can accept its key. If the Mac cannot reach it, it offers to add a `Host` block with `ProxyJump`, through one host or a chain like `sparky,inner`. A jump host the Mac cannot reach yet gets the same offer first, so any depth works.
2. Adds `SetEnv LC_VCODE_BRIDGE=<this Mac's id>` to `~/.ssh/config` once, after asking.
3. Copies the `vcode` command to `~/.local/bin/vcode` on the host, asking first unless an older copy of its own is already there.
4. Builds the bridge, starts or reloads the launch agent, and waits until the host can reach the Mac.

Running it again for the same host is safe. Other options go after `bash -s --` the same way (`--help` lists them):

| Option | Does |
|---|---|
| `--list` | prints the installed hosts |
| `--remove sparky` | stops serving one host |
| `--update` | reinstalls every host when a newer version is out |
| `--uninstall` | stops the service, deletes its files and logs, and removes the `SetEnv` block |
| `--version` | prints the latest version and the installed one |
| `-y`, `--yes` | answers yes to every prompt, so it runs without a terminal |

`--uninstall` asks before removing `vcode` from your hosts, since another Mac may still use it, and leaves `ProxyJump` blocks alone, since plain ssh uses them too.

## Versions

`VERSION` holds the current version. Bumping it on `main` is the release: `--update` compares it with the installed one and reinstalls when they differ. The installer stamps it into the bridge binary, the installed config and each host's `vcode`, so `vcode --version` on a host says which version it has.

To try local changes before pushing them, run the installer straight from a checkout: `uv run --python 3.15 manage.py sparky`.

## How it finds the right Mac

Each Mac gets a random id on its first install. Its tunnel to a host forwards `/tmp/vcode-bridge-<user>-<id>.sock` there, and deletes a stale copy of its own socket before reconnecting, so no sshd setting or sudo is needed.

ssh passes `LC_*` variables on through every hop, and Ubuntu's sshd accepts them by default, so `vcode` knows which Mac you came from even two hops in. If the variable does not arrive, `vcode` uses the one socket that answers, and asks you to set `LC_VCODE_BRIDGE` when more than one Mac is connected.

Inside tmux, `vcode` reads the id from the tmux session instead of the pane, so a session you reattach from another Mac follows you. That needs one line in `~/.tmux.conf`:

```tmux
set -ga update-environment LC_VCODE_BRIDGE
```

## Settings

`config.toml` holds where the Mac side lives (binary, launch agent, logs, local sockets), the default `ssh` and `code` commands for new hosts, and how long installs wait for a host. The installed host list and the Mac's id are in `~/Library/Application Support/VCode Bridge/config.toml`; the agent reloads it within a second of a change. Logs are in `~/Library/Logs/vcode-bridge*.log`.

## Checking a bridge

On the host, `vcode --healthy` checks that the Mac answers without opening anything; the installer runs it as its last step. `vcode .` prints `Opening <path> in VS Code`.
