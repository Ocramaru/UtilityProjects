#!/bin/zsh

set -euo pipefail

if (( $# != 1 )); then
  print -u2 "Usage: $0 <ssh-host-alias>"
  exit 2
fi

ssh_host=$1
app_dir="$HOME/Library/Application Support/VCode Bridge"
binary_path="$app_dir/vcode-bridge"
build_path="$app_dir/vcode-bridge.new"
socket_path="$app_dir/vcode-bridge.sock"
launch_agent="$HOME/Library/LaunchAgents/info.marcocassar.vcode-bridge.plist"
service="gui/$UID/info.marcocassar.vcode-bridge"

if [[ -z "$ssh_host" || "$ssh_host" == *[^A-Za-z0-9._-]* ]]; then
  print -u2 "SSH host aliases may contain only letters, numbers, dots, underscores, and hyphens."
  exit 2
fi
if ! command -v go >/dev/null 2>&1; then
  print -u2 "Go is not installed. Install it first with: brew install go"
  exit 1
fi
if [[ ! -x /usr/local/bin/code ]]; then
  print -u2 "VS Code's /usr/local/bin/code command is missing."
  print -u2 "In VS Code, run: Shell Command: Install 'code' command in PATH"
  exit 1
fi
mkdir -p "$app_dir" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
chmod 700 "$app_dir"
go build -trimpath -ldflags='-s -w' -o "$build_path" "${0:A:h}/vcode_bridge.go"
launchctl bootout "$service" >/dev/null 2>&1 || true
mv -f "$build_path" "$binary_path"

cat > "$launch_agent" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>info.marcocassar.vcode-bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>$binary_path</string>
    <string>-ssh-host</string>
    <string>$ssh_host</string>
    <string>-socket</string>
    <string>$socket_path</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>GOMAXPROCS</key>
    <string>1</string>
  </dict>
  <key>LowPriorityIO</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/vcode-bridge.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/vcode-bridge.log</string>
</dict>
</plist>
PLIST

chmod 755 "$binary_path"
chmod 644 "$launch_agent"
launchctl bootstrap "gui/$UID" "$launch_agent"
launchctl kickstart -k "$service"

print "Installed and started vcode-bridge for SSH host: $ssh_host"
print "Health check: curl --unix-socket '$socket_path' -fsS http://localhost/health"
