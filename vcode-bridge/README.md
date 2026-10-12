# VS Code SSH Bridge

Run `vcode .` in any SSH session and the folder opens in VS Code on your Mac, through Remote SSH. That includes a host you reached by hopping through another one: `ssh sparky`, then `ssh -A inner`, then `vcode .` on inner, and VS Code on the Mac connects straight to inner.

The request travels back through the SSH session you are already in. Nothing stays connected to your hosts, nothing pings them, and nothing listens on a TCP port.

## Install

On the Mac you need [uv](https://docs.astral.sh/uv/) (it fetches Python 3.15 by itself), Go 1.27 or newer, and VS Code with the Remote SSH extension and the `code` command (VS Code: **Shell Command: Install 'code' command in PATH**). Each host needs SSH key or password login from the Mac, an sshd that allows agent forwarding (the default), and curl.

```bash
curl -fsSL https://raw.githubusercontent.com/Ocramaru/UtilityProjects/main/vcode-bridge/install.sh | bash -s -- sparky
```

`install.sh` downloads the latest version from GitHub every time and runs its installer, passing your arguments through. The installer:

1. Connects to the host once, so you can accept its key. If the Mac cannot reach it, it offers to add a `Host` block with `ProxyJump`, through one host or a chain like `sparky,inner`. A jump host the Mac cannot reach yet gets the same offer first, so any depth works.
2. Builds the bridge and starts it as a launch agent, or leaves it running when nothing changed.
3. Has the host call the bridge back through its SSH session, and records the host keys that session proved.
4. Writes a managed block at the top of `~/.ssh/config`, after showing it.
5. Copies the `vcode` command to `~/.local/bin/vcode` on the host, asking first unless an older copy of its own is already there, and runs `vcode --healthy` there.

If any step fails, it puts back everything it changed; Ctrl-C asks first. Running it again for the same host is safe. Other options go after `bash -s --` the same way (`--help` lists them):

| Option | Does |
|---|---|
| `--list` | prints the installed hosts |
| `--remove sparky` | stops serving one host |
| `--update` | updates the Mac; each host's `vcode` updates itself the next time it runs |
| `--uninstall` | stops the service, deletes its files and logs, and removes its `~/.ssh/config` block |
| `--version` | prints the latest version and the installed one |
| `-y`, `--yes` | runs without prompts, taking each one's default; the risky ones, like replacing a `vcode` the bridge did not install, default to no |

`--uninstall` asks before removing `vcode` from your hosts, since another Mac may still use it, and leaves `ProxyJump` blocks alone, since plain ssh uses them too.

## How it works

Every SSH session can forward an agent socket back to the computer it came from, and since OpenSSH 8.2 that socket can be any socket. The managed block points `ForwardAgent` at the bridge's socket for your installed hosts, so each session on them gets a `$SSH_AUTH_SOCK` that leads to the bridge. sshd creates it when you log in, lets only your user open it, and deletes it when you log out. `vcode` sends its request there, and the bridge opens the folder.

Since OpenSSH 8.9, ssh binds every forwarded connection to the host key it verified, one hop at a time, and the bridge opens folders on the host whose key comes last. A host you have not installed is refused. ssh forwards only to a host whose key it verified, and only while a key agent runs on the Mac, which macOS starts for every user.

Things to know:

- **Your keys stay on the Mac.** The socket leads to the bridge, not to your key agent, so no SSH key is reachable through it. A `ForwardAgent` path in `~/.ssh/config` also outranks `ssh -A`, so installed hosts stop getting your keys forwarded; `ssh -o ForwardAgent="$SSH_AUTH_SOCK" sparky` still forwards them when you want that.
- **Each hop must run OpenSSH 8.9 or newer.** An older ssh forwards without binding, so the request would look like it came from the hop. `vcode` sends its host's own public keys, and the bridge refuses a request whose last bound key is not among them, naming the cause.
- **A hop is trusted to say where it forwards.** A process on an installed host, running as you or as root, can claim to be another installed host and open a folder there. It reaches nothing on the Mac and no keys.
- **Open sessions keep the forwarding they started with.** Reconnect after installing, and close any ControlMaster connection first (`ssh -O exit sparky`).

Inside tmux, `vcode` reads `SSH_AUTH_SOCK` from the tmux session rather than the pane, so a session you reattach from another Mac follows you; tmux refreshes it on every attach by default.

## Versions

`VERSION` holds the current version. Bumping it on `main` is the release: `--update` rebuilds the Mac side, and every reply from the Mac carries its version, so a host's `vcode` notices a newer one and fetches the Mac's copy. `vcode --version` on a host says which version it has.

To try local changes before pushing them, run the installer straight from a checkout: `uv run --python 3.15 manage.py sparky`.

## Settings

`config.toml` holds where the Mac side lives (binary, launch agent, socket, logs) and the `ssh` and fallback `code` commands. The installed hosts and their keys are in `~/Library/Application Support/VCode Bridge/config.json`, which the bridge reads on every request. Logs are in `~/Library/Logs/vcode-bridge.log`.

## Checking a bridge

On the host, `vcode --healthy` checks that the Mac answers without opening anything; the installer runs it as its last step. `vcode .` prints `Opening <path> on <host> in VS Code`.
