---
name: vcode-bridge learned
description: Traps in routing vcode to the right Mac, socket recovery, and the installer
kind: learned
---

# Learned

<!-- map: generated. Edit the note, not this; after a manual edit run `agent remap` -->

| section | line |
|---|---|
| Finding the right Mac | 20 |
| Routing and recovery | 26 |
| Installer | 31 |

<!-- /map -->


## Finding the right Mac

- **A tmux pane keeps the environment it started with.** Reattaching from another Mac leaves `LC_VCODE_BRIDGE` stale in every open pane; `vcode` reads the session's value with `tmux show-environment`, which `set -ga update-environment LC_VCODE_BRIDGE` keeps current.
- **The first SetEnv that matches a host wins, and `ssh -G` prints all of them on one `setenv` line.** A new id per cancelled install left stale blocks that shadowed the current one; the installer reuses the id it finds in `~/.ssh/config` and keeps a single block.
- **`LC_*` is the one variable family that crosses hops without server changes.** Ubuntu's sshd has `AcceptEnv LANG LC_*` and its ssh client `SendEnv LANG LC_*`. Any other name needs `AcceptEnv` and sudo on each host.

## Routing and recovery

- **A remote Unix socket outlives its SSH forward, and a new forward cannot bind over it.** The Mac socket still answers, so local health proves nothing; the tunnel deletes its own remote socket before connecting, and installs check health from the host.
- **An inner host cannot use the outer host's socket.** An inner SSH session does not inherit the reverse forward, and a relayed request would open the outer alias. Each host gets its own tunnel from the Mac, through `ProxyJump` when needed.

## Installer

- **`open("/dev/tty", "r+")` fails in Python even in a real terminal.** Text mode `r+` needs a seekable file and raises `io.UnsupportedOperation`, an `OSError`, which read as "no terminal". Open one handle to write and one to read.
