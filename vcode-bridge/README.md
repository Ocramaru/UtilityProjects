# VS Code SSH Bridge

Run `vcode .` inside a normal SSH session to open that remote folder in VS Code on your Mac. The bridge uses Unix sockets, so it does not occupy a TCP port.

Requires macOS, Go, VS Code Remote SSH, and the `code` command. In VS Code, install the command from **Shell Command: Install 'code' command in PATH**.

## Install

Run this on the Mac, replacing `mydevice` with the host alias from your `~/.ssh/config`:

```bash
zsh ./install.zsh mydevice
```

Add this inside the matching `Host mydevice` section of the Mac's `~/.ssh/config`:

```sshconfig
StreamLocalBindUnlink yes
RemoteForward /tmp/vcode-bridge-%r.sock "%d/Library/Application Support/VCode Bridge/vcode-bridge.sock"
ExitOnForwardFailure yes
```

Reconnect to that host, then add this function to the remote device's `~/.zshrc`:

```zsh
vcode() {
  emulate -L zsh

  local target=${1:-.}
  local remote_path=${target:A}
  local bridge_socket="/tmp/vcode-bridge-${USER}.sock"

  print -rn -- "$remote_path" |
    curl --unix-socket "$bridge_socket" -fsS --max-time 5 --data-binary @- http://localhost/open
}
```

Reload the shell:

```bash
source ~/.zshrc
```

Then use it from any remote folder:

```bash
vcode .
```
