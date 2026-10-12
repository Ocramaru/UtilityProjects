"""Tests for the vcode-bridge installer, with launchd, SSH and the Go build replaced by fakes.

Where real ssh is installed, it reads the written ~/.ssh/config to check what each host ends up with.
Author: Marco Cassar (@Ocramaru)
"""

import shutil
import subprocess

import pytest

import manage

USER_CONFIG = "User everyone\nIdentityFile ~/.ssh/id_work\n\nHost work\n  HostName work.example\n"
KEYS = {"host-a": ["QQ==", "QTI="], "host-b": ["Qg=="], "work": ["Vw=="]}
REAL_SSH = shutil.which("ssh")


@pytest.fixture
def mac(tmp_path, monkeypatch):
  """Points every installed file at a temporary directory and fakes launchd, SSH, curl and the Go build.

  Prompts get Enter unless answers are queued, so a test only lists the answers it cares about.
  """
  paths = {"binary_path": tmp_path / "app" / "vcode-bridge", "config_path": tmp_path / "app" / "config.json",
           "vcode_path": tmp_path / "app" / "vcode", "plist": tmp_path / "agents" / "bridge.plist",
           "agent_socket": tmp_path / "agent.sock", "logs": tmp_path / "logs"}
  for name, path in paths.items(): monkeypatch.setattr(manage.SETTINGS, name, path)
  monkeypatch.setattr(manage.SETTINGS, "code", "/bin/sh")
  monkeypatch.setattr(manage, "SSH_CONFIG", tmp_path / "ssh_config")
  monkeypatch.setattr(manage, "UNDO", [])
  monkeypatch.setattr(manage, "OPTIONS", manage.SimpleNamespace(yes=False))
  monkeypatch.setattr(manage.shutil, "which", lambda command: "/usr/bin/go" if command == "go" else None)
  monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/key-agent.sock")
  state = {"answers": [], "running": False, "starts": 0, "healthy": True, "unreachable": set(), "vcode": {}, "ssh": [], "version": manage.VERSION}

  def call(*args, check=True, **kwargs):
    stdout = ""
    if args[0] == "go": args[args.index("-o") + 1].write_bytes(b"bridge binary " + state["version"].encode())
    if "-version" in args: stdout = f"vcode-bridge {state['version']}\n"
    if args[0] == "curl" and not state["running"]: return subprocess.CompletedProcess(args, 7, stdout="")
    return subprocess.CompletedProcess(args, 0, stdout=stdout)

  def ssh(host, command, *options, **kwargs):
    state["ssh"].append((host, command))
    if host in state["unreachable"] and not (manage.SSH_CONFIG.exists() and f"Host {host}\n" in manage.SSH_CONFIG.read_text()):
      return subprocess.CompletedProcess(command, 255, stdout="", stderr="unreachable")
    stdout, code = "", 0
    if "/probe" in command: stdout = "\n".join([KEYS[host][0]] + [f"ssh-ed25519 {key} root@{host}" for key in reversed(KEYS[host])])
    elif command.startswith("cat ~/.local/bin/vcode"): stdout = state["vcode"].get(host, "")
    elif command.startswith(("mkdir -p ~/.local/bin", "cat >")): state["vcode"][host] = kwargs["input"]
    elif command.startswith("rm -f ~/.local/bin/vcode"): state["vcode"].pop(host, None)
    elif "--healthy" in command:
      if state["healthy"] == "interrupt": raise KeyboardInterrupt
      code = 0 if state["healthy"] else 1
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")

  def start():
    state["running"], state["starts"] = True, state["starts"] + 1

  monkeypatch.setattr(manage, "call", call)
  monkeypatch.setattr(manage, "ssh", ssh)
  monkeypatch.setattr(manage, "ask", lambda question, default="", hint="": (state["answers"].pop(0) if state["answers"] else "") or default)  # Enter takes the default
  monkeypatch.setattr(manage, "running", lambda: state["running"])
  monkeypatch.setattr(manage, "start", start)
  monkeypatch.setattr(manage, "stop", lambda: state.update(running=False))
  return state


def run(monkeypatch, *arguments):
  """Runs the installer as install.sh would, returning its exit code."""
  monkeypatch.setattr(manage.sys, "argv", ["install.sh", *arguments])
  try:
    manage.main()
  except SystemExit as exit:
    return exit.code
  return 0


def effective(host, option):
  """Reads one option for a host from the written config, the way ssh itself resolves it."""
  output = subprocess.run([REAL_SSH, "-G", "-F", manage.SSH_CONFIG, host], capture_output=True, text=True, check=True).stdout
  return [line.split(" ", 1)[1] for line in output.splitlines() if line.startswith(option + " ")]


def test_install_records_the_hosts_keys_and_hands_your_settings_back(mac, capsys):
  manage.SSH_CONFIG.write_text(USER_CONFIG)
  mac["vcode"]["host-a"] = (manage.SOURCE / "vcode").read_text().replace('version="dev"', 'version="0.0.1"', 1)
  manage.install("host-a")
  assert manage.installed()["hosts"] == [{"host": "host-a", "host_keys": ["QQ==", "QTI="]}]
  text = manage.SSH_CONFIG.read_text()
  assert text.startswith("# vcode-bridge begin") and text.endswith("\n\n" + USER_CONFIG)
  assert f"Host host-a\n  ForwardAgent {manage.tilde(manage.SETTINGS.agent_socket)}\nHost *\n# vcode-bridge end\n" in text
  assert manage.SETTINGS.vcode_path.read_text() == mac["vcode"]["host-a"] == manage.stamped_vcode()
  output = capsys.readouterr().out
  assert f"Updated vcode on host-a: 0.0.1 → {manage.VERSION}" in output and "Ready: vcode . on host-a" in output
  if REAL_SSH:
    assert effective("elsewhere", "user") == ["everyone"] and "~/.ssh/id_work" in effective("elsewhere", "identityfile")
    assert effective("elsewhere", "forwardagent") == ["no"]


def test_hosts_come_and_go_without_restarting_the_service(mac, monkeypatch):
  original = b"Host work\r\n  User someone\r\n"
  manage.SSH_CONFIG.write_bytes(original)
  for host in ("host-a", "host-b", "host-b"): manage.install(host)
  monkeypatch.setitem(KEYS, "host-a-vpn", ["QTI="])
  with pytest.raises(ValueError, match="host-a-vpn is the same machine as host-a"): manage.install("host-a-vpn")
  manage.remove("host-b")
  assert [entry["host"] for entry in manage.installed()["hosts"]] == ["host-a"] and mac["running"] and mac["starts"] == 1

  manage.remove("host-a")
  assert not mac["running"] and not manage.SETTINGS.binary_path.exists() and not manage.SETTINGS.config_path.exists()
  assert manage.SSH_CONFIG.read_bytes() == original


@pytest.mark.parametrize(("healthy", "exit_code"), [(False, 1), ("interrupt", 130)])
def test_a_failed_or_cancelled_install_undoes_everything(mac, monkeypatch, healthy, exit_code):
  manage.SSH_CONFIG.write_text(USER_CONFIG)
  mac["healthy"] = healthy
  assert run(monkeypatch, "host-a") == exit_code
  assert manage.SSH_CONFIG.read_text() == USER_CONFIG and mac["vcode"] == {}
  assert not mac["running"] and not manage.SETTINGS.binary_path.exists() and not manage.SETTINGS.config_path.exists()


@pytest.mark.parametrize(("code", "stderr", "message"), [(3, "Agent forwarding is disabled to avoid man-in-the-middle attacks.", "AllowAgentForwarding yes.*man-in-the-middle"),
                                                    (255, "Connection reset", "ssh to host-a failed")])
def test_probe_failures_say_what_went_wrong(monkeypatch, code, stderr, message):
  monkeypatch.setattr(manage, "ssh", lambda host, command, *options, **kwargs: subprocess.CompletedProcess(command, code, stdout="", stderr=stderr))
  with pytest.raises(ValueError, match=message): manage.probe("host-a")


def test_jump_hosts_are_set_up_recursively_above_your_settings(mac):
  manage.SSH_CONFIG.write_text(USER_CONFIG)
  mac["unreachable"] = {"inner", "middle"}
  mac["answers"] = ["", "middle", "", "sparky", "", "", "", "", "tester"]
  manage.reach("inner")
  text = manage.SSH_CONFIG.read_text()
  assert "Host middle\n  HostName middle\n" in text and "ProxyJump sparky\n" in text
  assert "Host inner\n  HostName inner\n  User tester\n  ProxyJump middle\nHost *\n" in text and text.endswith(USER_CONFIG)
  if REAL_SSH:
    assert effective("inner", "user") == ["tester"] and effective("inner", "proxyjump") == ["middle"]
    assert effective("elsewhere", "user") == ["everyone"]


def test_yes_runs_without_a_terminal_and_keeps_no_for_risky_steps(mac, monkeypatch, capsys):
  monkeypatch.setattr(manage, "ask", lambda question, default="", hint="": pytest.fail("asked the terminal: " + question))
  mac["vcode"]["host-a"] = "#!/bin/sh\necho mine\n"
  assert run(monkeypatch, "host-a", "--yes") == 0
  assert [entry["host"] for entry in manage.installed()["hosts"]] == ["host-a"] and mac["vcode"]["host-a"] == "#!/bin/sh\necho mine\n"

  assert run(monkeypatch, "--uninstall", "-y") == 0
  output = capsys.readouterr().out
  assert "Replace it? [y/N] n" in output and "Another Mac may still use it [y/N] n" in output
  assert not manage.SETTINGS.config_path.exists() and "host-a" in mac["vcode"]


def test_uninstall_removes_everything_it_added_and_nothing_else(mac):
  manage.SSH_CONFIG.write_text(USER_CONFIG)
  manage.install("host-a")
  mac["answers"] = ["y", "y"]
  manage.uninstall()
  assert not mac["running"] and not manage.SETTINGS.binary_path.parent.exists() and not manage.SETTINGS.plist.exists()
  assert manage.SSH_CONFIG.read_text() == USER_CONFIG and mac["vcode"] == {}


def test_update_touches_only_the_mac(mac, capsys):
  manage.install("host-a")
  mac["ssh"].clear()
  mac["version"] = "0.0.9"  # the binary installed earlier reports this
  manage.update()
  output = capsys.readouterr().out
  assert mac["ssh"] == [] and f"Updating vcode-bridge 0.0.9 → {manage.VERSION}" in output
  assert f'version="{manage.VERSION}"' in manage.SETTINGS.vcode_path.read_text()


@pytest.mark.skipif(not shutil.which("go"), reason="needs Go")
def test_the_bridge_accepts_what_the_installer_writes(tmp_path, monkeypatch):
  binary = tmp_path / "vcode-bridge"
  subprocess.run(["go", "build", "-o", binary, "."], cwd=manage.SOURCE / "bridge", check=True)
  monkeypatch.setattr(manage.SETTINGS, "binary_path", binary)
  monkeypatch.setattr(manage.SETTINGS, "config_path", tmp_path / "config.json")
  monkeypatch.setattr(manage, "UNDO", [])
  manage.write_installed("/bin/sh", [{"host": "host-a", "host_keys": KEYS["host-a"]}, {"host": "work", "host_keys": KEYS["work"]}])
  with pytest.raises(ValueError, match="share a host key"):
    manage.write_installed("/bin/sh", [{"host": "host-a", "host_keys": KEYS["host-a"]}, {"host": "host-a-vpn", "host_keys": ["QTI="]}])
  assert [entry["host"] for entry in manage.installed()["hosts"]] == ["host-a", "work"]
