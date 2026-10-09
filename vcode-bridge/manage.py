#!/usr/bin/env python3
"""Installs, lists and removes the SSH hosts served by the one vcode-bridge launch agent on this Mac.

Every remote socket carries this Mac's id, so several Macs can serve one remote account side by side.
Author: Marco Cassar (@Ocramaru)
"""

import argparse
import filecmp
import getpass
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).parent
VERSION = (SOURCE / "VERSION").read_text().strip()
VCODE_VERSION = re.compile(r'^version="([^"]+)"', re.MULTILINE)  # the line install_command stamps into vcode
settings = tomllib.loads((SOURCE / "config.toml").read_text())
for name, value in settings.items():
  if isinstance(value, str) and name != "label": settings[name] = Path(value.format(uid=os.getuid())).expanduser()
SETTINGS = SimpleNamespace(**settings)

SSH_CONFIG = Path.home() / ".ssh" / "config"
ENV_BLOCK = "\n# vcode-bridge: tells remote vcode commands which Mac you came from\nHost *\n  SetEnv LC_VCODE_BRIDGE={bridge_id}\n"
ENV_PATTERN = re.compile(r"\n?# vcode-bridge: tells remote vcode commands which Mac you came from\nHost \*\n  SetEnv LC_VCODE_BRIDGE=(\w+)\n")
STALE_SOCKETS = ('for socket in /tmp/vcode-bridge-"$(id -un)"-*.sock; do [ -S "$socket" ] || continue; '
                 'curl --unix-socket "$socket" -fsS --max-time 2 http://localhost/health >/dev/null 2>&1 || { rm -f -- "$socket" && echo "$socket"; }; done')
UNDO = []  # (message, step) for each change this run made, undone newest first after Ctrl-C
OPTIONS = SimpleNamespace(yes=False)
SSH_OPTIONS = ("-S", "none", "-T", "-o", "ControlMaster=no", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=10")
SERVICE = f"gui/{os.getuid()}/{SETTINGS.label}"
NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
ADDRESS = re.compile(r"[A-Za-z0-9._:-]+")
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
STYLES = {"question": "1", "hint": "2", "done": "32", "warn": "33", "error": "1;31", "block": "36"}
COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def call(*args, check=True, **kwargs):
  return subprocess.run([str(arg) for arg in args], check=check, **kwargs)


def ssh(entry, *command, batch=True, **kwargs):
  """Runs a command on the entry's host over its own connection, never a shared or forwarding one."""
  batch_options = ("-o", "BatchMode=yes") if batch else ()
  return call(entry["ssh"], *SSH_OPTIONS, *batch_options, entry["host"], *command, **kwargs)


def paint(text, style):
  return f"\033[{STYLES[style]}m{text}\033[0m" if COLOR else text


def done(message):
  print(paint("✓", "done"), message)


def info(message):
  print(paint(f"[info] {message}", "hint"))


def tilde(path):
  return str(path).replace(str(Path.home()), "~", 1)


def put_back(path, text):
  """Writes a file's earlier text back, or deletes it if it did not exist before."""
  if text is None: path.unlink(missing_ok=True)
  else: path.write_text(text)


def ask(question, default="", hint=""):
  """Reads from the terminal rather than stdin, so prompts work when the installer arrives through curl."""
  try:
    with open("/dev/tty", "w") as output, open("/dev/tty") as terminal:  # one "r+" handle fails: a terminal cannot seek
      hint = hint or (f"[{default}]" if default else "")
      output.write(paint(question, "question") + (" " + paint(hint, "hint") if hint else "") + " ")
      output.flush()
      return terminal.readline().strip() or default
  except OSError as error:
    raise ValueError(f"installing needs an interactive terminal ({error})") from error


def confirm(question, default=False):
  hint = "[Y/n]" if default else "[y/N]"
  if OPTIONS.yes:
    print(paint(question, "question"), paint(hint, "hint"), "y")
    return True
  answer = ask(question, hint=hint).lower()
  return default if not answer else answer in ("y", "yes")


def check_name(value, what="SSH host alias"):
  if not NAME.fullmatch(value) or value.startswith("-"): raise ValueError(f"invalid {what}: {value!r}")


def ssh_settings(ssh_path, host):
  """Returns the Mac's effective SSH options for the host as (key, value) pairs."""
  result = call(ssh_path, "-G", host, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
  return [tuple(line.split(" ", 1)) for line in result.stdout.splitlines() if " " in line]


def offer_ssh_config(block, replacing=None):
  """Shows a block for ~/.ssh/config and writes it once you agree, in place of any block matching `replacing`."""
  info(f"Adding to {tilde(SSH_CONFIG)}:")
  print(paint(block.strip("\n"), "block"))
  if not confirm("Add it?", default=True): return False
  SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
  previous = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else None
  kept = previous or ""
  if replacing: kept = replacing.sub("", kept)
  SSH_CONFIG.write_text(kept + ("\n" if kept and not kept.endswith("\n") else "") + block)
  SSH_CONFIG.chmod(0o600)
  UNDO.append((f"Restored {tilde(SSH_CONFIG)}", lambda: put_back(SSH_CONFIG, previous)))
  done(f"Wrote to {tilde(SSH_CONFIG)}")
  return True


def add_jump_host(host, ssh_path):
  """Adds a ProxyJump block for the host, first making sure every hop is itself reachable, to any depth."""
  jump = ask("Reach it through which SSH host?", hint="(a,b for a chain)")
  for hop in jump.split(","):
    check_name(hop)
    reach({"host": hop, "ssh": ssh_path})
  address = ask(f"Address of {host} as {jump.split(',')[-1]} sees it:", host)
  user = ask(f"User on {host}:", getpass.getuser())
  if not ADDRESS.fullmatch(address): raise ValueError(f"invalid address: {address!r}")
  check_name(user, "user")
  block = f"\nHost {host}\n  HostName {address}\n  User {user}\n  ProxyJump {jump}\n"
  if not offer_ssh_config(block): raise ValueError(f"cannot reach {host}")


def reach(entry):
  """Connects once interactively so you can accept a new host key, then checks the unattended login the service needs."""
  host = entry["host"]
  if ssh(entry, "true", batch=False, check=False).returncode:
    if not confirm(f"{host} is not reachable from this Mac. Reach it through another SSH host?", default=True): raise ValueError(f"cannot reach {host}")
    add_jump_host(host, entry["ssh"])
    ssh(entry, "true", batch=False)
  if ssh(entry, "true", check=False).returncode:
    raise ValueError(f"{host} asks for a password or passphrase; the bridge runs unattended, so set up key authentication")
  for key, value in ssh_settings(entry["ssh"], host):
    if key in ("remoteforward", "localforward", "dynamicforward"): raise ValueError(f"remove {key} {value} from Host {host}; the bridge owns forwarding")
  done(f"Connected to {host}")


def sends_bridge_env(entry, variable):
  return any(key == "setenv" and variable in value.split() for key, value in ssh_settings(entry["ssh"], entry["host"]))


def ensure_bridge_env(entry, bridge_id):
  """Makes the Mac's ssh send this Mac's id, which remote vcode commands use to find their way back."""
  variable = f"LC_VCODE_BRIDGE={bridge_id}"
  blocks = ENV_PATTERN.findall(SSH_CONFIG.read_text()) if SSH_CONFIG.exists() else []
  if sends_bridge_env(entry, variable) and len(blocks) <= 1:
    done(f"{tilde(SSH_CONFIG)} has this Mac's id")
    return
  if not offer_ssh_config(ENV_BLOCK.format(bridge_id=bridge_id), replacing=ENV_PATTERN):
    print(paint("Skipped: vcode works only while one Mac is connected", "warn"))
    return
  if not sends_bridge_env(entry, variable):
    print(paint(f"An earlier SetEnv for {entry['host']} in {tilde(SSH_CONFIG)} overrides {variable}", "warn"))


def install_command(entry):
  """Offers to copy the vcode script to ~/.local/bin on the host, returning whether the host has it afterwards."""
  host = entry["host"]
  script = (SOURCE / "vcode").read_text().replace('version="dev"', f'version="{VERSION}"', 1)
  current = ssh(entry, "cat ~/.local/bin/vcode 2>/dev/null || true", stdout=subprocess.PIPE, text=True).stdout
  if current == script:
    done(f"vcode on {host} is up to date")
    return True
  previous = VCODE_VERSION.search(current)
  if not previous:
    info(f"vcode . on {host} opens that folder in VS Code here")
    if current:
      if not confirm(f"{host} has a different ~/.local/bin/vcode. Replace it?"): return False
    elif not confirm(f"Install vcode to ~/.local/bin on {host}?", default=True): return False
  ssh(entry, "mkdir -p ~/.local/bin && cat > ~/.local/bin/vcode && chmod 755 ~/.local/bin/vcode", input=script, text=True)

  def put_back_command():
    if current: ssh(entry, "cat > ~/.local/bin/vcode", input=current, text=True)
    else: ssh(entry, "rm -f ~/.local/bin/vcode")

  UNDO.append((f"Restored vcode on {host}" if current else f"Removed vcode from {host}", put_back_command))
  if previous:
    done(f"Updated vcode on {host}: {previous.group(1)} → {VERSION}")
  elif ssh(entry, "\"$SHELL\" -ic 'command -v vcode'", check=False, **QUIET).returncode:
    print(paint(f"Installed vcode on {host}; ~/.local/bin is not on PATH there", "warn"))
  else:
    done(f"Installed vcode on {host}")
  return True


def read_installed():
  """Returns this Mac's bridge id and configured hosts, reusing an id already in ~/.ssh/config before making one."""
  data = tomllib.loads(SETTINGS.config_path.read_text()) if SETTINGS.config_path.exists() else {}
  written = ENV_PATTERN.search(SSH_CONFIG.read_text()) if SSH_CONFIG.exists() else None
  return data.get("id") or (written and written.group(1)) or uuid.uuid4().hex, data.get("hosts", [])


def clear_stale_sockets(entry):
  """Deletes this user's bridge sockets on the host that no Mac answers on; live ones belong to Macs still connected."""
  result = ssh(entry, "sh -c " + shlex.quote(STALE_SOCKETS), check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
  if result.returncode: print(paint(f"Could not reach {entry['host']} to clear dead sockets", "warn"))
  elif result.stdout.split(): done(f"Removed dead sockets on {entry['host']}: {len(result.stdout.split())}")


def installed_version():
  if not SETTINGS.config_path.exists(): return None
  return tomllib.loads(SETTINGS.config_path.read_text()).get("version", "unversioned")


def write_installed(path, bridge_id, hosts):
  lines = [f"id = {json.dumps(bridge_id)}", f"version = {json.dumps(VERSION)}"]
  for entry in sorted(hosts, key=lambda entry: entry["host"]):
    lines += ["", "[[hosts]]", *(f"{key} = {json.dumps(value)}" for key, value in entry.items())]
  with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
    os.fchmod(stream.fileno(), 0o600)
    stream.write("\n".join(lines) + "\n")
  os.replace(stream.name, path)


def entry_for(host, ssh_path, code, user, bridge_id):
  return {"host": host, "socket": str(SETTINGS.sockets / f"{host}.sock"),
          "remote_socket": f"/tmp/vcode-bridge-{user}-{bridge_id}.sock",
          "ssh": str(ssh_path), "code": str(code), "log": str(SETTINGS.logs / f"vcode-bridge-{host}.log")}


def agent_data():
  log = str(SETTINGS.logs / "vcode-bridge.log")
  return {"Label": SETTINGS.label, "ProgramArguments": [str(SETTINGS.binary_path), "-config", str(SETTINGS.config_path)],
          "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background", "LowPriorityIO": True,
          "ThrottleInterval": 10, "EnvironmentVariables": {"GOMAXPROCS": "1"},
          "StandardOutPath": log, "StandardErrorPath": log}


def running():
  return call("launchctl", "print", SERVICE, check=False, **QUIET).returncode == 0


def start():
  call("launchctl", "bootstrap", f"gui/{os.getuid()}", SETTINGS.plist)


def stop():
  """Stops the service if it is loaded, returning whether it was."""
  if not running(): return False
  call("launchctl", "bootout", SERVICE)
  return True


def local_healthy(entry):
  return call("curl", "--unix-socket", entry["socket"], "-fsS", "--max-time", "2", "http://localhost/health", check=False, **QUIET).returncode == 0


def healthy(entry):
  """Checks both ends: the Mac listener, and the forwarded socket as the remote host sees it."""
  if not local_healthy(entry): return False
  command = ("curl", "--unix-socket", entry["remote_socket"], "-fsS", "--max-time", "2", "http://localhost/health")
  return ssh(entry, *command, check=False, **QUIET).returncode == 0


def wait_until(condition, seconds, failure):
  deadline = time.monotonic() + seconds
  while not condition():
    if time.monotonic() > deadline: raise RuntimeError(failure)
    time.sleep(1)


def snapshot():
  paths = (SETTINGS.binary_path, SETTINGS.plist, SETTINGS.config_path)
  return {path: (path.read_bytes(), path.stat().st_mode) if path.exists() else None for path in paths}


def restore(saved):
  for path, previous in saved.items():
    if previous is None:
      path.unlink(missing_ok=True)
      continue
    path.write_bytes(previous[0])
    path.chmod(previous[1])


def prepare_directories():
  SETTINGS.binary_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  SETTINGS.plist.parent.mkdir(parents=True, exist_ok=True)
  SETTINGS.logs.mkdir(parents=True, exist_ok=True)
  SETTINGS.sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
  info = SETTINGS.sockets.stat()
  if info.st_uid != os.getuid() or info.st_mode & 0o077: raise ValueError(f"unsafe socket directory: {SETTINGS.sockets}")


def activate(bridge_id, hosts, target):
  """Builds the bridge, swaps in what changed, and waits for the target to answer from both ends, rolling back on failure."""
  prepare_directories()
  saved, was_running = snapshot(), running()
  with tempfile.TemporaryDirectory(dir=SETTINGS.binary_path.parent) as temp:
    stage = Path(temp)
    call("go", "build", "-trimpath", f"-ldflags=-s -w -X main.version={VERSION}", "-o", stage / "vcode-bridge", ".", cwd=SOURCE / "bridge")
    write_installed(stage / "config.toml", bridge_id, hosts)
    call(stage / "vcode-bridge", "-check-config", "-config", stage / "config.toml")
    done("Built bridge")
    restart = not was_running or not SETTINGS.binary_path.exists() or not filecmp.cmp(stage / "vcode-bridge", SETTINGS.binary_path, shallow=False)

    def roll_back():
      if restart: stop()
      restore(saved)
      if restart and was_running: start()

    try:
      if restart:
        stop()
        shutil.copy2(stage / "vcode-bridge", SETTINGS.binary_path)
        with SETTINGS.plist.open("wb") as stream: plistlib.dump(agent_data(), stream)
      write_installed(SETTINGS.config_path, bridge_id, hosts)  # a running service reloads it within a second
      if restart:
        start()
        if not running(): raise RuntimeError("launchd did not load the bridge service")
        done("Started bridge service")
      else:
        done("Updated bridge config")
      waiting = hosts if restart else [target]
      wait_until(lambda: all(healthy(entry) for entry in waiting), SETTINGS.health_timeout_seconds,
                 "the bridge did not answer for: " + ", ".join(entry["host"] for entry in waiting))
      done("Tested tunnel to " + ", ".join(entry["host"] for entry in waiting))
    except BaseException:  # Ctrl-C included, so an interrupted install still rolls back
      roll_back()
      raise
  UNDO.append(("Restored bridge service" if saved[SETTINGS.plist] else "Removed bridge service", roll_back))


def install(host):
  host = host or ask("SSH host alias:")
  check_name(host)
  bridge_id, hosts = read_installed()
  previous = installed_version()
  if previous and previous != VERSION: info(f"Updating vcode-bridge {previous} → {VERSION}")
  existing = next((entry for entry in hosts if entry["host"] == host), {})
  code = existing.get("code", SETTINGS.code)
  if not os.access(code, os.X_OK): raise ValueError(f"VS Code command is not executable: {code}")
  if not shutil.which("go"): raise ValueError("Go is not installed")

  connection = {"host": host, "ssh": str(existing.get("ssh", SETTINGS.ssh))}
  reach(connection)
  user = ssh(connection, "id -un", stdout=subprocess.PIPE, text=True).stdout.strip()
  check_name(user, "remote user")

  target = entry_for(host, connection["ssh"], code, user, bridge_id)
  clear_stale_sockets(target)
  ensure_bridge_env(target, bridge_id)
  has_command = install_command(target)
  activate(bridge_id, [entry for entry in hosts if entry["host"] != host] + [target], target)
  if has_command:
    health = ssh(target, f"LC_VCODE_BRIDGE={bridge_id} ~/.local/bin/vcode --healthy", check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if health.returncode: print(paint(f"vcode on {host} could not reach this Mac: {health.stdout.strip()}", "warn"))
    else: done(f"Tested vcode on {host}")
  print(paint(f"Ready: vcode . on {host}", "done"))


def remove(host):
  bridge_id, hosts = read_installed()
  target = next((entry for entry in hosts if entry["host"] == host), None)
  if target is None: raise ValueError(f"{host} is not installed")
  remaining = [entry for entry in hosts if entry["host"] != host]
  write_installed(SETTINGS.config_path, bridge_id, remaining)  # keeps the id, which ~/.ssh/config refers to
  if remaining:
    wait_until(lambda: not local_healthy(target), 10, f"the bridge for {host} did not stop")
  else:
    stop()
    SETTINGS.plist.unlink(missing_ok=True)
    SETTINGS.binary_path.unlink(missing_ok=True)
  clear_stale_sockets(target)
  done(f"Removed {host}")


def uninstall():
  """Removes the service, its files and the SetEnv block; ProxyJump blocks stay, since plain ssh uses them too."""
  bridge_id, hosts = read_installed()
  if not confirm("Uninstall vcode-bridge from this Mac?"):
    print(paint("Cancelled", "warn"))
    return
  names = ", ".join(entry["host"] for entry in hosts)
  if hosts and confirm(f"Also remove ~/.local/bin/vcode from {names}? Another Mac may still use it"):
    for entry in hosts:
      if ssh(entry, "rm -f ~/.local/bin/vcode", check=False, **QUIET).returncode: print(paint(f"Could not reach {entry['host']} to remove vcode", "warn"))
      else: done(f"Removed vcode from {entry['host']}")

  if stop(): done("Stopped bridge service")
  for entry in hosts: clear_stale_sockets(entry)
  SETTINGS.plist.unlink(missing_ok=True)
  SETTINGS.config_path.unlink(missing_ok=True)
  for log in SETTINGS.logs.glob("vcode-bridge*.log"): log.unlink()
  for directory in (SETTINGS.binary_path.parent, SETTINGS.sockets): shutil.rmtree(directory, ignore_errors=True)
  left = [path for path in (SETTINGS.plist, SETTINGS.config_path, SETTINGS.binary_path.parent, SETTINGS.sockets) if path.exists()]
  if left: print(paint("Could not remove: " + ", ".join(tilde(path) for path in left), "warn"))
  else: done("Removed bridge files")

  if SSH_CONFIG.exists() and ENV_PATTERN.search(SSH_CONFIG.read_text()):
    SSH_CONFIG.write_text(ENV_PATTERN.sub("", SSH_CONFIG.read_text()))
    done(f"Removed LC_VCODE_BRIDGE from {tilde(SSH_CONFIG)}")
  if not left: print(paint("Uninstalled vcode-bridge", "done"))


def update():
  """Reinstalls every host from this copy, which install.sh downloads fresh from GitHub each run."""
  previous, hosts = installed_version(), read_installed()[1]
  if not hosts: raise ValueError("no hosts are installed")
  if previous == VERSION:
    done(f"vcode-bridge {VERSION} is up to date")
    return
  for entry in hosts: install(entry["host"])


def cancel():
  """After Ctrl-C, offers to undo what this run changed, newest first."""
  print()
  try:
    if UNDO and confirm("Cancel the installation and undo changes?", default=True):
      for message, step in reversed(UNDO):
        try:
          step()
          done(message)
        except (OSError, subprocess.CalledProcessError) as error:
          print(paint(f"Could not undo ({message}): {error}", "warn"))
  except KeyboardInterrupt:
    print()
  print(paint("Cancelled", "warn"))


def main():
  parser = argparse.ArgumentParser(prog="install.sh", description="Opens folders from SSH hosts in VS Code on this Mac.")
  parser.add_argument("host", nargs="?", default="", help="SSH host alias to install or update (asked for when left out)")
  actions = parser.add_mutually_exclusive_group()
  actions.add_argument("--list", action="store_true", help="print the installed hosts")
  actions.add_argument("--remove", metavar="HOST", help="stop serving one host")
  actions.add_argument("--update", action="store_true", help="update every installed host to this version")
  actions.add_argument("--uninstall", action="store_true", help="remove vcode-bridge from this Mac")
  parser.add_argument("--version", action="version", version=f"vcode-bridge {VERSION} (installed: {installed_version() or 'none'})")
  parser.add_argument("-y", "--yes", "--assume-yes", dest="yes", action="store_true", help="assume yes to all prompts and run non-interactively")
  arguments = parser.parse_args()
  OPTIONS.yes = arguments.yes
  if arguments.host and (arguments.list or arguments.remove or arguments.update or arguments.uninstall): parser.error("give a host or an option, not both")

  if arguments.list:
    for entry in read_installed()[1]: print(entry["host"])
  elif arguments.remove: remove(arguments.remove)
  elif arguments.update: update()
  elif arguments.uninstall: uninstall()
  else: install(arguments.host)


if __name__ == "__main__":
  try:
    main()
  except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
    print(f"{paint('vcode-bridge:', 'error')} {error}", file=sys.stderr)
    sys.exit(1)
  except KeyboardInterrupt:
    cancel()
    sys.exit(130)
