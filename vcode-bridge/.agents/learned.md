---
name: vcode-bridge learned
description: Traps in the agent channel, ~/.ssh/config, VS Code and macOS, and why there is no standing tunnel
kind: learned
---

# Learned

<!-- map: generated. Edit the note, not this; after a manual edit run `agent remap` -->

| section | line |
|---|---|
| The agent channel | 22 |
| ~/.ssh/config | 33 |
| VS Code and macOS | 38 |
| Why there is no standing tunnel | 44 |
| Installer | 53 |

<!-- /map -->


## The agent channel

- **ssh forwards an agent connection only while a key agent runs on the client**, even when `ForwardAgent` names another socket; without `SSH_AUTH_SOCK` it skips forwarding silently. macOS always runs one.
- **ssh forwards nothing to a host whose key it did not verify.** With `StrictHostKeyChecking=no` it prints "Agent forwarding is disabled to avoid man-in-the-middle attacks", which `LogLevel=ERROR` hides.
- **OpenSSH 8.9+ writes a `session-bind@openssh.com` message into each forwarded connection before the payload**, one per hop, innermost last; a plain HTTP server on the socket chokes on it.
- **Only the first bind comes from the Mac's own ssh.** Any process at the far end can append a bind with another host's key, and checking signatures would not stop it, since a real hop binds the next host too.
- **An ssh older than 8.9 forwards without binding**, so through such a hop the last bound key is the hop's own.
- **Each hop negotiates its own host key type** from its own known_hosts, so the key the Mac saw is not always the one a hop binds.
- **A `ForwardAgent` path in `~/.ssh/config` outranks `ssh -A` and `-o ForwardAgent=yes`**; only `-o ForwardAgent=<path>` beats it. A path that does not exist yet makes ssh exit 255 under `ExitOnForwardFailure`.
- **A tmux pane keeps the environment it started with**; `tmux show-environment` gives the session's `SSH_AUTH_SOCK`, which tmux refreshes on every attach.

## ~/.ssh/config

- **A Host block has no end.** A block at the top of the file captures every top-level line after it, `Include` lines too, unless it ends with `Host *`.
- **The first value ssh finds wins**, so a block meant to override your settings, like a jump host's `User`, has to come above them.

## VS Code and macOS

- **`code --remote <authority> <path>` guesses file or folder from the name**: a dot in the last part means a file. `--folder-uri` and `--file-uri` with a `vscode-remote://` URI say which.
- **`launchctl bootstrap` right after `bootout` fails with "5: Input/output error"**: launchd unloads the job a moment after `bootout` returns. Wait until `launchctl print` fails before bootstrapping again.
- **macOS can kill a signed binary that changes in place**, since it caches the signature per file; write a new file and rename it over the old one. From Apple's guidance, not reproduced.

## Why there is no standing tunnel

Hosts that refused agent forwarding once got a reverse tunnel from the Mac to `/tmp/vcode-bridge-<user>-<mac id>.sock`. It was dropped because:

- **Nothing travels over an idle tunnel**, so a dead one needed pings, and rebuilding it from a `LocalCommand` on every ssh cut off VS Code's own connection.
- **`/tmp` is world-writable**, so another user on a shared host could take the socket name and serve a replacement `vcode` through the self-update.
- **Its `SetEnv LC_VCODE_BRIDGE` shadowed the user's own `SetEnv`**, since only the first match applies.
- **Its unattended `BatchMode` logins fail on MFA clusters**, the hosts most likely to refuse agent forwarding.

## Installer

- **`open("/dev/tty", "r+")` fails in Python even in a real terminal.** Text mode `r+` needs a seekable file and raises `io.UnsupportedOperation`, an `OSError`, which read as "no terminal". Open one handle to write and one to read.
