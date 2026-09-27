# VS Code SSH Bridge

Run `vcode .` inside any normal SSH session to open that remote folder in VS Code on your Mac. The bridge uses Unix sockets, so it does not occupy a TCP port.

The existing macOS background service owns one SSH tunnel and reconnects automatically after network outages, sleep, or a remote reboot. Ordinary terminal and VS Code SSH connections stay independent. Closing a terminal does not close the bridge, and no dedicated terminal or manual tunnel command is needed. The service starts at login and maintains the tunnel even when no interactive shell is open.

Requires macOS, Go, VS Code Remote SSH, and the `code` command. In VS Code, install the command from **Shell Command: Install 'code' command in PATH**. Unattended SSH key authentication must work; the daemon uses `BatchMode=yes` and cannot ask for passwords or key passphrases. It reads your usual SSH config but does not join a shared SSH master.

## Install or upgrade

### 1. Mac SSH configuration

Remove the old `RemoteForward` line from the host's `~/.ssh/config`, including any inherited forwarding directives. Remove bridge-specific `StreamLocalBindUnlink`, `ControlPath`, and `ControlPersist` settings. For independent shells, use:

```sshconfig
Host sparky
    ControlMaster no
```

Keep your existing `HostName`, `User`, identity, proxy, and other connection settings if present. The installer refuses legacy forwarding directives instead of silently starting conflicting forwards.

Close old SSH connections that own the previous bridge. If you used the earlier shared-master configuration, close that master from your Mac (this terminates its sessions):

```bash
ssh -S ~/.ssh/cm-%C -O exit sparky
```

It is fine if no master exists. The app will own the bridge from now on.

### 2. Enable remote socket replacement once

On **sparky**, set this in the SSH server configuration:

```text
StreamLocalBindUnlink yes
```

For Ubuntu systems whose `/etc/ssh/sshd_config` includes `/etc/ssh/sshd_config.d/*.conf`:

```bash
echo 'StreamLocalBindUnlink yes' | sudo tee /etc/ssh/sshd_config.d/90-vcode-bridge.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep streamlocalbindunlink
```

The final line should show `streamlocalbindunlink yes`. Some systems name the service `sshd`. This is a **server** setting; setting it on your Mac does not clean the remote socket. It makes a newly established tunnel replace a leftover socket after a disconnect or crash.

This option replaces an existing socket without checking whether it is live. Use one Mac bridge service per remote user/socket. Two Macs must not claim `/tmp/vcode-bridge-<remote-user>.sock` simultaneously. Local singleton locking prevents duplicate service instances using the same local socket.

### 3. Install the service on your Mac

From this directory:

```bash
zsh ./install.zsh sparky
```

The installer verifies SSH authentication and host trust interactively before starting the background service. Logs go to `~/Library/Logs/vcode-bridge.log`. The service retries failed tunnels with delays from one to 30 seconds. SSH keepalives detect an unresponsive connection, normally within about 45 seconds while the Mac is awake. A tunnel failure does not terminate your ordinary SSH sessions. `vcode` can temporarily fail while reconnection is in progress.

### 4. Remote shell command

Add this function to **sparky's** `~/.zshrc` (unchanged from the original bridge):

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

Reload with `source ~/.zshrc`, then use `vcode .` in any SSH shell. The function connects to the existing socket; it never creates a new listener.

## Verify

On **sparky**, check the complete tunnel to your Mac:

```bash
curl --unix-socket "/tmp/vcode-bridge-${USER}.sock" -fsS http://localhost/health
```

A successful check returns no body. Open two independent SSH shells, run `vcode .` in both, close one, and repeat in the other. The bridge should remain available.

On the **Mac**, the local-only health check is:

```bash
curl --unix-socket "$HOME/Library/Application Support/VCode Bridge/vcode-bridge.sock" -fsS http://localhost/health
```

This local check does not prove that the SSH tunnel is connected. To inspect failures, read `~/Library/Logs/vcode-bridge.log`. Authentication failures require fixing the SSH credentials; retries cannot resolve those automatically.

## Development

```bash
go test -race vcode_bridge.go vcode_bridge_test.go
go build -o /tmp/vcode-bridge vcode_bridge.go
```

Manual verification on 2026-09-27 (Mac to `sparky`): remote health check succeeded,
two independent SSH sessions opened different VS Code windows, and closing and
reopening sessions left one bridge service, one child SSH tunnel, and one remote
socket listener. Recovery after a forced tunnel failure or machine restart has
not yet been verified on that setup.
