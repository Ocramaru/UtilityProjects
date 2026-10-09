---
name: vcode-bridge todo
description: Optional ports of the VS Code side to Linux and Windows
kind: todo
---

# Todo

## Other platforms for the VS Code machine

Only `manage.py` is Mac-specific: launchd, `~/Library` paths, and `/usr/local/bin/code`. The Go service and the remote `vcode` script already run on Linux.

- [ ] **Linux desktop.** A systemd user unit in place of the launchd plist, XDG paths with sockets in `/run/user/<uid>`, `code` found on PATH, and per-OS defaults in `config.toml`. About 60 to 80 lines in `manage.py`, Go unchanged. Sparky runs Ubuntu desktop, so it can be tested end to end there.
- [ ] **Windows, only once there is a Windows machine to test on.** Needs a lock other than `flock`, a forward to a local TCP port with a token (Windows OpenSSH may not forward to a local Unix socket), a scheduled task or service in place of launchd, and a PowerShell installer. WSL does not help: Windows VS Code reads Windows' SSH config. A Windows remote host would also need a PowerShell `vcode`.
