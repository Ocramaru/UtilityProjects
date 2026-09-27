# VS Code SSH Bridge

Run `vcode .` inside an SSH session to open the remote folder in VS Code on your Mac. The bridge uses Unix sockets and does not occupy a TCP port.

A macOS background service maintains one SSH tunnel. Terminal sessions and VS Code windows use the same bridge without owning its connection. Closing a terminal or VS Code window leaves the bridge running. The service starts at login and retries disconnected tunnels automatically.

## Requirements

- macOS with Go installed.
- VS Code with the Remote SSH extension and `/usr/local/bin/code`. Install the CLI through **Shell Command: Install 'code' command in PATH** in VS Code.
- An SSH host reachable from your Mac with unattended key authentication. The service uses `BatchMode=yes` and cannot prompt for passwords or key passphrases.
- zsh and curl on the remote host for the `vcode` function below.
- `StreamLocalBindUnlink yes` enabled on the remote SSH server.

Use your SSH host name or alias wherever the examples use `mydevice`.

## Setup

### 1. Mac SSH configuration

Use the SSH configuration that normally connects to your remote host. The bridge needs no additional `Host` settings. The selected host's effective configuration must not contain `RemoteForward`, `LocalForward`, or `DynamicForward` directives; the installer checks this because the service manages its own forwarding.

`ControlMaster` defaults to `no`, so an explicit `ControlMaster no` line is unnecessary unless overriding another matching configuration section. The bridge service always disables connection sharing for its own tunnel. Ordinary SSH sessions follow your SSH configuration; if that configuration enables sharing, `ControlPath none` disables it for a host.

### 2. Remote SSH server configuration

On the remote host, enable socket replacement in the SSH server configuration:

```text
StreamLocalBindUnlink yes
```

On Ubuntu systems where `/etc/ssh/sshd_config` includes `/etc/ssh/sshd_config.d/*.conf`:

```bash
echo 'StreamLocalBindUnlink yes' | sudo tee /etc/ssh/sshd_config.d/90-vcode-bridge.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep streamlocalbindunlink
```

The final command should print `streamlocalbindunlink yes`. Some systems name the service `sshd` instead of `ssh`.

This server setting lets a reconnecting tunnel replace a socket left behind after a disconnect. It replaces the path whether or not its listener is active, so only one Mac bridge service may own a given remote user/socket. The bridge uses `/tmp/vcode-bridge-<remote-user>.sock`.

### 3. Install on your Mac

From this directory:

```bash
zsh ./install.zsh mydevice
```

The installer builds the app, verifies SSH authentication and host trust interactively, and installs a launch agent that starts the service automatically. The service files live in `~/Library/Application Support/VCode Bridge`. Logs are written to `~/Library/Logs/vcode-bridge.log`.

The installation supports one configured SSH host. A local lock prevents duplicate service instances from owning the same local socket.

### 4. Remote shell command

Add this function to the remote host's `~/.zshrc`:

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

Reload the shell configuration:

```bash
source ~/.zshrc
```

Use `vcode .` from any remote project directory, or `vcode /path/to/project`. The function sends the folder path through the socket to the Mac service, which opens it using VS Code Remote SSH.

## Connection recovery

The service retries failed tunnels with delays from one to 30 seconds. SSH keepalives detect an unresponsive connection, normally within about 45 seconds while the Mac is awake. Recovery resumes when the Mac and remote host are reachable and authentication succeeds. A bridge tunnel failure does not terminate ordinary SSH sessions, although `vcode` can fail temporarily during reconnection.

Authentication failures require fixing the SSH credentials; retries cannot resolve those automatically.

## Health checks

On the remote host, check the complete connection to the Mac:

```bash
curl --unix-socket "/tmp/vcode-bridge-${USER}.sock" -fsS --max-time 5 http://localhost/health
```

A successful check returns no body. To inspect the remote listener:

```bash
ss -xlpn | grep -F "/tmp/vcode-bridge-${USER}.sock"
```

There should be one `LISTEN` entry for the bridge socket.

On the Mac, check the local service:

```bash
curl --unix-socket "$HOME/Library/Application Support/VCode Bridge/vcode-bridge.sock" -fsS --max-time 5 http://localhost/health
```

The local check confirms that the Mac service responds; the remote check also verifies the SSH tunnel. Connection errors are recorded in `~/Library/Logs/vcode-bridge.log`.

## Development

```bash
go test -race vcode_bridge.go vcode_bridge_test.go
go build -o /tmp/vcode-bridge vcode_bridge.go
```
