---
name: vcode-bridge todo
description: Optional ports of the VS Code side to Linux and Windows
kind: todo
---

# Todo

## Other platforms for the VS Code machine

Only `manage.py` is Mac-specific: launchd, `~/Library` paths, and `/usr/local/bin/code`. The Go service and the remote `vcode` script already run on Linux.

- [ ] **Linux desktop.** A systemd user unit in place of the launchd plist, XDG paths, and per-OS defaults in `config.toml`. About 40 to 60 lines in `manage.py`, Go unchanged. Sparky runs Ubuntu desktop, so it can be tested end to end there.
- [ ] **Windows, only once there is a Windows machine to test on.** Windows OpenSSH forwards its agent from a named pipe, so the bridge would need to serve one, plus a lock other than `flock`, a scheduled task in place of launchd, and a PowerShell installer. WSL does not help: Windows VS Code reads Windows' SSH config. A Windows remote host would also need a PowerShell `vcode`.
