"""Installs, lists and removes the SSH hosts that the vcode-bridge launch agent on this Mac serves.

A host reaches the Mac through its SSH session's agent channel, which a managed block in ~/.ssh/config points at the service's socket.
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
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).parent
VERSION = (SOURCE / "VERSION").read_text().strip()
VCODE_VERSION = re.compile(r'^version="([^"]+)"', re.MULTILINE)  # the line install_command stamps into vcode
settings = tomllib.loads((SOURCE / "config.toml").read_text())
for name, value in settings.items():
  if isinstance(value, str) and name != "label": settings[name] = Path(value).expanduser()
SETTINGS = SimpleNamespace(**settings)

SSH_CONFIG = Path.home() / ".ssh" / "config"
BLOCK = re.compile(r"# vcode-bridge begin.*?# vcode-bridge end\n\n?", re.DOTALL)  # with the blank line written after it
OLD_ENV_BLOCK = re.compile(r"\n?# vcode-bridge: tells remote vcode commands which Mac you came from\nHost \*\n  SetEnv LC_VCODE_BRIDGE=\w+\n")  # from 0.1
UNDO = []  # (message, step) for each change this run made, undone newest first after an error or Ctrl-C
OPTIONS = SimpleNamespace(yes=False)
SSH_OPTIONS = ("-S", "none", "-T", "-o", "ControlMaster=no", "-o", "ClearAllForwardings=yes", "-o", "RemoteCommand=none", "-o", "ConnectTimeout=10")
SERVICE = f"gui/{os.getuid()}/{SETTINGS.label}"
NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
ADDRESS = re.compile(r"[A-Za-z0-9._:-]+")
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
STYLES = {"question": "1", "hint": "2", "done": "32", "warn": "33", "error": "1;31", "block": "36"}
COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def call(*args, check=True, **kwargs):
  return subprocess.run([str(arg) for arg in args], check=check, **kwargs)


def ssh(host, command, *options, **kwargs):
  """Runs a command through sh on the host, over its own connection rather than a shared one, whatever its login shell is."""
  return call(SETTINGS.ssh, *SSH_OPTIONS, *options, host, "sh -c " + shlex.quote(command), **kwargs)


def paint(text, style):
  return f"\033[{STYLES[style]}m{text}\033[0m" if COLOR else text


def done(message):
  print(paint("✓", "done"), message)


def info(message):
  print(paint(f"[info] {message}", "hint"))


def warn(message):
  print(paint(message, "warn"))


def tilde(path):
  return str(path).replace(str(Path.home()), "~", 1)


def snapshot(path):
  return (path.read_bytes(), path.stat().st_mode) if path.exists() else None


def put_back(path, saved):
  """Puts a file back as snapshot saved it, or deletes it if it did not exist then."""
  if saved is None: return path.unlink(missing_ok=True)
  if path.is_symlink(): return path.write_bytes(saved[0])  # a dotfiles link stays a link
  temporary = path.with_name(path.name + ".restore")
  temporary.write_bytes(saved[0])
  temporary.chmod(saved[1])
  os.replace(temporary, path)  # a new file: macOS kills a signed binary that changes in place


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
  """Asks a yes/no question; --yes takes each question's default, so the risky ones, which default to no, stay no."""
  hint = "[Y/n]" if default else "[y/N]"
  if OPTIONS.yes:
    print(paint(question, "question"), paint(hint, "hint"), "y" if default else "n")
    return default
  answer = ask(question, hint=hint).lower()
  return default if not answer else answer in ("y", "yes")


def check_name(value, what="SSH host alias"):
  if not NAME.fullmatch(value) or value.startswith("-"): raise ValueError(f"invalid {what}: {value!r}")


## ~/.ssh/config: one managed block at the top, closed with Host * so your own settings after it still apply to every host

def write_ssh_config(text, message):
  """Replaces ~/.ssh/config with text, recording how to put the old one back."""
  previous = snapshot(SSH_CONFIG)
  SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
  SSH_CONFIG.write_text(text, newline="")
  SSH_CONFIG.chmod(0o600)
  UNDO.append((f"Restored {tilde(SSH_CONFIG)}", lambda: put_back(SSH_CONFIG, previous)))
  done(message)


def update_ssh_config(hosts):
  """Writes the managed block for these hosts at the top of ~/.ssh/config, after showing it."""
  block = ""
  if hosts:
    lines = ["# vcode-bridge begin: written by its installer; changes inside are replaced",
             f"Host {' '.join(sorted(entry['host'] for entry in hosts))}", f"  ForwardAgent {tilde(SETTINGS.agent_socket)}", "Host *", "# vcode-bridge end"]
    block = "\n".join(lines) + "\n"
  existing = SSH_CONFIG.read_text(newline="") if SSH_CONFIG.exists() else ""
  rest = OLD_ENV_BLOCK.sub("", BLOCK.sub("", existing))
  text = block + ("\n" if block and rest else "") + rest
  if text == existing: return done(f"{tilde(SSH_CONFIG)} is up to date")
  if not block: return write_ssh_config(text, f"Removed vcode-bridge from {tilde(SSH_CONFIG)}")

  info(f"Setting at the top of {tilde(SSH_CONFIG)}, where ssh reads it first:")
  print(paint(block.strip("\n"), "block"))
  if not confirm("Write it?", default=True): raise ValueError(f"vcode needs this block in {tilde(SSH_CONFIG)}")
  write_ssh_config(text, f"Wrote to {tilde(SSH_CONFIG)}")


def add_jump_host(host):
  """Adds a ProxyJump block for the host under the managed block, first making sure every hop is itself reachable, to any depth."""
  jump = ask("Reach it through which SSH host?", hint="(a,b for a chain)")
  for hop in jump.split(","):
    check_name(hop)
    reach(hop)
  address = ask(f"Address of {host} as {jump.split(',')[-1]} sees it:", host)
  user = ask(f"User on {host}:", getpass.getuser())
  if not ADDRESS.fullmatch(address): raise ValueError(f"invalid address: {address!r}")
  check_name(user, "user")
  block = f"Host {host}\n  HostName {address}\n  User {user}\n  ProxyJump {jump}\nHost *\n"
  info(f"Adding near the top of {tilde(SSH_CONFIG)}, so it comes before your own settings:")
  print(paint(block.strip("\n"), "block"))
  if not confirm("Add it?", default=True): raise ValueError(f"cannot reach {host} from this Mac")

  existing = SSH_CONFIG.read_text(newline="") if SSH_CONFIG.exists() else ""
  split = managed.end() if (managed := BLOCK.match(existing)) else 0
  write_ssh_config(existing[:split] + block + "\n" + existing[split:], f"Wrote to {tilde(SSH_CONFIG)}")


def reach(host):
  """Connects once, so you can accept a new host key, offering a ProxyJump block when this Mac cannot reach the host."""
  if ssh(host, "true", check=False).returncode:
    if OPTIONS.yes or not confirm(f"{host} is not reachable from this Mac. Reach it through another SSH host?", default=True): raise ValueError(f"cannot reach {host} from this Mac")
    add_jump_host(host)
    ssh(host, "true")
  done(f"Connected to {host}")


def probe(host):
  """Has the host call the service back through its session's agent channel; returns the host's keys, the one that session proved first."""
  command = ('test -S "$SSH_AUTH_SOCK" || exit 3; command -v curl >/dev/null || exit 4; '
             'curl --unix-socket "$SSH_AUTH_SOCK" -fsS --max-time 5 http://localhost/probe || exit 5; cat /etc/ssh/ssh_host_*_key.pub 2>/dev/null || true')
  result = ssh(host, command, "-o", f"ForwardAgent={SETTINGS.agent_socket}", check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
  problem = result.stderr.strip()
  if result.returncode == 3: raise ValueError(f"{host} got no agent channel from this Mac; its sshd must allow agent forwarding (AllowAgentForwarding yes){': ' + problem if problem else ''}")
  if result.returncode == 4: raise ValueError(f"{host} has no curl, which vcode needs; install it there first")
  if result.returncode == 5: raise ValueError(f"curl on {host} could not reach this Mac through the agent channel: {problem}")
  if result.returncode: raise ValueError(f"ssh to {host} failed: {problem}")
  bound, *lines = result.stdout.splitlines()
  keys = [fields[1] for line in lines if len(fields := line.split()) > 1]
  return [bound] + [key for key in keys if key != bound] if bound in keys else [bound]


## The host side: the vcode script

def stamped_vcode():
  return (SOURCE / "vcode").read_text().replace('version="dev"', f'version="{VERSION}"', 1)


def install_command(host):
  """Offers to copy the vcode script to ~/.local/bin on the host, returning whether the host has it afterwards."""
  script = stamped_vcode()
  current = ssh(host, "cat ~/.local/bin/vcode 2>/dev/null || true", stdout=subprocess.PIPE, text=True).stdout
  if current == script:
    done(f"vcode on {host} is up to date")
    return True
  previous = VCODE_VERSION.search(current)
  if not previous:
    info(f"vcode . on {host} opens that folder in VS Code here")
    if current:
      if not confirm(f"{host} has a different ~/.local/bin/vcode. Replace it?"): return False
    elif not confirm(f"Install vcode to ~/.local/bin on {host}?", default=True): return False

  def put_back_command():
    if current: ssh(host, "cat > ~/.local/bin/vcode", input=current, text=True)
    else: ssh(host, "rm -f ~/.local/bin/vcode")

  UNDO.append((f"Restored vcode on {host}" if current else f"Removed vcode from {host}", put_back_command))  # before the copy, which Ctrl-C can cut short
  ssh(host, "mkdir -p ~/.local/bin && cat > ~/.local/bin/vcode && chmod 755 ~/.local/bin/vcode", input=script, text=True)
  if previous:
    done(f"Updated vcode on {host}: {previous.group(1)} → {VERSION}")
  elif ssh(host, "\"$SHELL\" -ic 'command -v vcode'", check=False, **QUIET).returncode:
    warn(f"Installed vcode on {host}; ~/.local/bin is not on PATH there")
  else:
    done(f"Installed vcode on {host}")
  return True


## The Mac side: the installed config, the service and its files

def installed():
  """Returns the installed config: the VS Code command and each host with its keys."""
  return json.loads(SETTINGS.config_path.read_text()) if SETTINGS.config_path.exists() else {"code": str(SETTINGS.code), "hosts": []}


def installed_version():
  if not SETTINGS.binary_path.exists(): return None
  output = call(SETTINGS.binary_path, "-version", check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.split()
  return output[-1] if output else "unversioned"


def write_installed(code, hosts):
  """Checks the installed config with the bridge itself, then swaps it in; the service reads it on every request."""
  data = {"vcode": str(SETTINGS.vcode_path), "code": code, "hosts": sorted(hosts, key=lambda entry: entry["host"])}
  with tempfile.NamedTemporaryFile("w", dir=SETTINGS.config_path.parent, delete=False) as stream:
    os.fchmod(stream.fileno(), 0o600)
    json.dump(data, stream, indent=2)
  check = call(SETTINGS.binary_path, "-check-config", "-config", stream.name, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
  if check.returncode:
    os.unlink(stream.name)
    raise ValueError(check.stderr.strip())
  previous = snapshot(SETTINGS.config_path)
  os.replace(stream.name, SETTINGS.config_path)
  UNDO.append(("Restored the installed hosts", lambda: put_back(SETTINGS.config_path, previous)))


def running():
  return call("launchctl", "print", SERVICE, check=False, **QUIET).returncode == 0


def start():
  call("launchctl", "bootstrap", f"gui/{os.getuid()}", SETTINGS.plist)


def stop():
  """Stops the service if it is loaded, returning whether it was; launchd unloads it a moment after bootout returns."""
  if not running(): return False
  call("launchctl", "bootout", SERVICE)
  deadline = time.monotonic() + SETTINGS.start_timeout_seconds
  while running():  # a bootstrap before the old job is gone fails with "5: Input/output error"
    if time.monotonic() > deadline: raise RuntimeError("launchd did not unload the bridge service")
    time.sleep(0.2)
  return True


def activate():
  """Builds the bridge and swaps in what changed, restarting the service only when its binary or launch agent changed."""
  SETTINGS.binary_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  SETTINGS.plist.parent.mkdir(parents=True, exist_ok=True)
  SETTINGS.logs.mkdir(parents=True, exist_ok=True)
  saved, was_running = {path: snapshot(path) for path in (SETTINGS.binary_path, SETTINGS.plist, SETTINGS.vcode_path)}, running()
  log = SETTINGS.logs / "vcode-bridge.log"
  plist = plistlib.dumps({"Label": SETTINGS.label, "ProgramArguments": [str(SETTINGS.binary_path), "-config", str(SETTINGS.config_path), "-socket", str(SETTINGS.agent_socket)],
                          "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background", "LowPriorityIO": True,
                          "ThrottleInterval": 10, "EnvironmentVariables": {"GOMAXPROCS": "1"}, "StandardOutPath": str(log), "StandardErrorPath": str(log)})

  with tempfile.TemporaryDirectory(dir=SETTINGS.binary_path.parent) as temp:
    built = Path(temp) / "vcode-bridge"
    call("go", "build", "-trimpath", f"-ldflags=-s -w -X main.version={VERSION}", "-o", built, ".", cwd=SOURCE / "bridge")
    done("Built bridge")
    restart = not was_running or saved[SETTINGS.plist] is None or saved[SETTINGS.plist][0] != plist or saved[SETTINGS.binary_path] is None or not filecmp.cmp(built, SETTINGS.binary_path, shallow=False)

    def roll_back():
      if restart: stop()
      for path, previous in saved.items(): put_back(path, previous)
      if restart and was_running: start()

    UNDO.append(("Restored bridge service" if saved[SETTINGS.plist] else "Removed bridge service", roll_back))
    SETTINGS.vcode_path.write_text(stamped_vcode())  # what hosts fetch when their vcode is out of date
    if restart:
      stop()
      os.replace(built, SETTINGS.binary_path)  # a new file: macOS kills a signed binary that changes in place
      SETTINGS.plist.write_bytes(plist)
      start()

  deadline = time.monotonic() + SETTINGS.start_timeout_seconds
  while call("curl", "--unix-socket", SETTINGS.agent_socket, "-fsS", "--max-time", "2", "http://localhost/health", check=False, **QUIET).returncode:
    if time.monotonic() > deadline: raise RuntimeError(f"the bridge service does not answer on {tilde(SETTINGS.agent_socket)}; see {tilde(log)}")
    time.sleep(0.5)
  done("Started bridge service" if restart else "Bridge service is running")


## Commands

def install(host):
  host = host or ask("SSH host alias:")
  check_name(host)
  hosts = installed()["hosts"]
  previous = installed_version()
  if previous and previous != VERSION: info(f"Updating vcode-bridge {previous} → {VERSION}")
  code = shutil.which("code") or str(SETTINGS.code)
  if not os.access(code, os.X_OK): raise ValueError(f"VS Code command is not executable: {code}; in VS Code, run Shell Command: Install 'code' command in PATH")
  if not shutil.which("go"): raise ValueError("Go is not installed")
  if not os.environ.get("SSH_AUTH_SOCK"): raise ValueError("no SSH key agent runs here, and ssh forwards nothing without one; open a new terminal")

  reach(host)
  activate()
  keys = probe(host)
  done(f"{host} reaches this Mac through its SSH session")
  if same := next((entry["host"] for entry in hosts if entry["host"] != host and set(entry["host_keys"]) & set(keys)), None):
    raise ValueError(f"{host} is the same machine as {same}; use {same}, or run install.sh --remove {same} first")
  hosts = [entry for entry in hosts if entry["host"] != host] + [{"host": host, "host_keys": keys}]
  write_installed(code, hosts)

  forwarding = next((line.split(" ", 1)[1] for line in call(SETTINGS.ssh, "-G", host, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.splitlines() if line.startswith("forwardagent ")), "no")
  if forwarding not in ("no", str(SETTINGS.agent_socket)): warn(f"{host} gets your key agent forwarded; after this it gets the bridge's socket, which holds no keys (ssh -o ForwardAgent=\"$SSH_AUTH_SOCK\" {host} still forwards them)")
  update_ssh_config(hosts)
  if not install_command(host): return warn(f"vcode is not installed on {host}")

  health = ssh(host, "~/.local/bin/vcode --healthy", check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
  if health.returncode: raise RuntimeError(f"vcode on {host} could not reach this Mac: {health.stdout.strip()}")
  done(f"Tested vcode on {host}")
  print(paint(f"Ready: vcode . on {host}", "done"))


def remove(host):
  config = installed()
  remaining = [entry for entry in config["hosts"] if entry["host"] != host]
  if len(remaining) == len(config["hosts"]): raise ValueError(f"{host} is not installed")
  update_ssh_config(remaining)
  if remaining:
    write_installed(config["code"], remaining)
  else:
    if stop(): done("Stopped bridge service, since no hosts are left")
    for path in (SETTINGS.plist, SETTINGS.binary_path, SETTINGS.config_path): path.unlink(missing_ok=True)
  done(f"Removed {host}")


def uninstall():
  """Removes the service, its files and its ~/.ssh/config block; ProxyJump blocks stay, since plain ssh uses them too."""
  hosts = installed()["hosts"]
  if not confirm("Uninstall vcode-bridge from this Mac?", default=True): return warn("Cancelled")
  names = ", ".join(entry["host"] for entry in hosts)
  if hosts and confirm(f"Also remove ~/.local/bin/vcode from {names}? Another Mac may still use it"):
    for entry in hosts:
      if ssh(entry["host"], "rm -f ~/.local/bin/vcode", check=False, **QUIET).returncode: warn(f"Could not reach {entry['host']} to remove vcode")
      else: done(f"Removed vcode from {entry['host']}")

  if stop(): done("Stopped bridge service")
  for path in (SETTINGS.plist, SETTINGS.agent_socket): path.unlink(missing_ok=True)
  for log in SETTINGS.logs.glob("vcode-bridge*.log"): log.unlink()
  shutil.rmtree(SETTINGS.binary_path.parent, ignore_errors=True)
  left = [path for path in (SETTINGS.plist, SETTINGS.binary_path.parent) if path.exists()]
  if left: warn("Could not remove: " + ", ".join(tilde(path) for path in left))
  else: done("Removed bridge files")

  update_ssh_config([])
  if not left: print(paint("Uninstalled vcode-bridge", "done"))


def update():
  """Updates the Mac side to this copy, which install.sh downloads fresh from GitHub each run.

  Hosts need no visit: each vcode compares its version with the Mac's on every run and fetches a newer one itself.
  """
  config = installed()
  if not config["hosts"]: raise ValueError("no hosts are installed; run install.sh with a host's SSH alias")
  if not shutil.which("go"): raise ValueError("Go is not installed")
  previous = installed_version()
  if previous != VERSION: info(f"Updating vcode-bridge {previous} → {VERSION}")
  update_ssh_config(config["hosts"])
  activate()
  write_installed(config["code"], config["hosts"])
  info("Each host's vcode updates itself the next time it runs")
  print(paint(f"Ready: vcode-bridge {VERSION}", "done"))


def undo():
  """Undoes what this run changed, newest first."""
  for message, step in reversed(UNDO):
    try:
      step()
      done(message)
    except (OSError, subprocess.CalledProcessError) as error:
      warn(f"Could not undo ({message}): {error}")
  UNDO.clear()


def main():
  parser = argparse.ArgumentParser(prog="install.sh", description="Opens folders from SSH hosts in VS Code on this Mac.")
  parser.add_argument("host", nargs="?", default="", help="SSH host alias to install or update (asked for when left out)")
  actions = parser.add_mutually_exclusive_group()
  actions.add_argument("--list", action="store_true", help="print the installed hosts")
  actions.add_argument("--remove", metavar="HOST", help="stop serving one host")
  actions.add_argument("--update", action="store_true", help="update this Mac to this version; hosts follow on their own")
  actions.add_argument("--uninstall", action="store_true", help="remove vcode-bridge from this Mac")
  parser.add_argument("--version", action="version", version=f"vcode-bridge {VERSION} (installed: {installed_version() or 'none'})")
  parser.add_argument("-y", "--yes", "--assume-yes", dest="yes", action="store_true", help="run without prompts, taking each one's default; risky steps default to no")
  arguments = parser.parse_args()
  OPTIONS.yes = arguments.yes
  if arguments.host and (arguments.list or arguments.remove or arguments.update or arguments.uninstall): parser.error("give a host or an option, not both")

  try:
    if arguments.list:
      for entry in installed()["hosts"]: print(entry["host"])
    elif arguments.remove: remove(arguments.remove)
    elif arguments.update: update()
    elif arguments.uninstall: uninstall()
    else: install(arguments.host)
  except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
    print(f"{paint('vcode-bridge:', 'error')} {error}", file=sys.stderr)
    undo()
    sys.exit(1)
  except KeyboardInterrupt:
    print()
    try:
      if UNDO and confirm("Cancel the installation and undo changes?", default=True): undo()
    except KeyboardInterrupt:
      print()
    warn("Cancelled")
    sys.exit(130)


if __name__ == "__main__":
  main()
