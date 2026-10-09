"""Tests for the vcode-bridge installer, with launchd, SSH and the Go build replaced by fakes.

Author: Marco Cassar (@Ocramaru)
"""

import subprocess

import pytest

import manage

OWN_BLOCK = "Host work\n  User someone\n"
HOST = {"host": "host-a", "ssh": "/usr/bin/ssh"}


def settings_from(ssh_config):
  """Fakes `ssh -G` by reading SetEnv lines straight from a test ssh config."""
  def ssh_settings(ssh_path, host):
    lines = ssh_config.read_text().splitlines() if ssh_config.exists() else []
    return [("setenv", line.split()[1]) for line in lines if line.strip().startswith("SetEnv")]
  return ssh_settings


@pytest.fixture
def mac(tmp_path, monkeypatch):
  """Points every installed file at a temporary directory and fakes launchd, SSH and the Go build.

  Prompts get Enter unless answers are queued, so a test only lists the answers it cares about.
  """
  paths = {"binary_path": tmp_path / "app" / "vcode-bridge", "config_path": tmp_path / "app" / "config.toml",
           "plist": tmp_path / "agents" / "bridge.plist", "logs": tmp_path / "logs", "sockets": tmp_path / "sockets"}
  for name, path in paths.items(): monkeypatch.setattr(manage.SETTINGS, name, path)
  monkeypatch.setattr(manage.SETTINGS, "code", "/bin/sh")
  monkeypatch.setattr(manage, "SSH_CONFIG", tmp_path / "ssh_config")
  monkeypatch.setattr(manage, "UNDO", [])
  monkeypatch.setattr(manage, "OPTIONS", manage.SimpleNamespace(yes=False))
  monkeypatch.setattr(manage.shutil, "which", lambda command: "/usr/bin/go")
  state = {"answers": [], "running": False, "starts": 0, "healthy": True}

  def call(*args, check=True, **kwargs):
    if args[0] == "go": args[args.index("-o") + 1].write_bytes(b"bridge binary")
    return subprocess.CompletedProcess(args, 0, stdout="")

  def ssh(entry, *command, batch=True, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="remoteuser\n" if command == ("id -un",) else "")

  def start():
    state["running"], state["starts"] = True, state["starts"] + 1

  monkeypatch.setattr(manage, "call", call)
  monkeypatch.setattr(manage, "ssh", ssh)
  monkeypatch.setattr(manage, "ssh_settings", settings_from(tmp_path / "ssh_config"))
  monkeypatch.setattr(manage, "install_command", lambda entry: False)
  monkeypatch.setattr(manage, "ask", lambda question, default="", hint="": state["answers"].pop(0) if state["answers"] else default)
  monkeypatch.setattr(manage, "running", lambda: state["running"])
  monkeypatch.setattr(manage, "start", start)
  monkeypatch.setattr(manage, "stop", lambda: state.update(running=False))
  monkeypatch.setattr(manage, "healthy", lambda entry: state["healthy"])
  monkeypatch.setattr(manage, "local_healthy", lambda entry: False)
  return state


def installed_hosts():
  return [entry["host"] for entry in manage.read_installed()[1]]


def test_adding_and_removing_hosts_never_restarts_the_others(mac):
  manage.install("host-a")
  bridge_id = manage.read_installed()[0]
  manage.install("host-b")
  manage.install("host-b")
  assert installed_hosts() == ["host-a", "host-b"]
  assert manage.read_installed()[0] == bridge_id

  manage.remove("host-b")
  assert installed_hosts() == ["host-a"]
  assert mac["running"] and mac["starts"] == 1


def test_remote_socket_matches_what_vcode_looks_for(mac):
  manage.install("host-a")
  bridge_id, [entry] = manage.read_installed()
  assert entry["remote_socket"] == f"/tmp/vcode-bridge-remoteuser-{bridge_id}.sock"
  assert '"/tmp/vcode-bridge-$(id -un)"' in (manage.SOURCE / "vcode").read_text()


def test_failed_install_restores_previous_config(mac, monkeypatch):
  manage.install("host-a")
  before = manage.SETTINGS.config_path.read_text()

  mac["healthy"] = False
  monkeypatch.setattr(manage.SETTINGS, "health_timeout_seconds", 0)
  with pytest.raises(RuntimeError, match="did not answer"):
    manage.install("host-b")
  assert manage.SETTINGS.config_path.read_text() == before


def test_one_bridge_block_reusing_the_existing_id(mac):
  stale_blocks = manage.ENV_BLOCK.format(bridge_id="first1") + manage.ENV_BLOCK.format(bridge_id="second2")
  manage.SSH_CONFIG.write_text(OWN_BLOCK + stale_blocks)
  bridge_id = manage.read_installed()[0]
  manage.ensure_bridge_env(HOST, bridge_id)
  manage.ensure_bridge_env(HOST, bridge_id)
  assert bridge_id == "first1"
  assert manage.SSH_CONFIG.read_text() == OWN_BLOCK + manage.ENV_BLOCK.format(bridge_id="first1")


def test_unreachable_jump_hosts_are_set_up_recursively(mac, monkeypatch):
  def ssh(entry, *command, batch=True, **kwargs):
    configured = manage.SSH_CONFIG.exists() and f"Host {entry['host']}\n" in manage.SSH_CONFIG.read_text()
    return subprocess.CompletedProcess(command, 0 if entry["host"] == "sparky" or configured else 255, stdout="")

  monkeypatch.setattr(manage, "ssh", ssh)
  mac["answers"] = ["", "middle", "", "sparky"]
  manage.reach({"host": "inner", "ssh": "/usr/bin/ssh"})
  text = manage.SSH_CONFIG.read_text()
  assert "Host middle\n  HostName middle\n" in text and "ProxyJump sparky\n" in text
  assert "Host inner\n  HostName inner\n" in text and "ProxyJump middle\n" in text
  assert text.index("Host middle") < text.index("Host inner")


def test_declining_vcode_copies_nothing(monkeypatch):
  copied = []

  def ssh(entry, *command, **kwargs):
    if "input" in kwargs: copied.append(command)
    return subprocess.CompletedProcess(command, 0, stdout="")

  monkeypatch.setattr(manage, "ssh", ssh)
  monkeypatch.setattr(manage, "ask", lambda question, default="", hint="": "n")
  assert not manage.install_command(HOST)
  assert not copied


def test_our_older_vcode_is_replaced_without_asking(monkeypatch, capsys):
  old_script = (manage.SOURCE / "vcode").read_text().replace('version="dev"', 'version="0.0.1"', 1)
  copied = []

  def ssh(entry, *command, **kwargs):
    if "input" in kwargs: copied.append(kwargs["input"])
    return subprocess.CompletedProcess(command, 0, stdout=old_script if command[0].startswith("cat ~") else "")

  def no_questions(question, default="", hint=""):
    raise AssertionError("asked: " + question)

  monkeypatch.setattr(manage, "ssh", ssh)
  monkeypatch.setattr(manage, "ask", no_questions)
  monkeypatch.setattr(manage, "UNDO", [])
  assert manage.install_command(HOST)
  assert f'version="{manage.VERSION}"' in copied[0]
  assert f"Updated vcode on host-a: 0.0.1 → {manage.VERSION}" in capsys.readouterr().out


def test_removing_last_host_stops_service_but_keeps_id(mac):
  manage.install("host-a")
  bridge_id = manage.read_installed()[0]
  manage.remove("host-a")
  assert not mac["running"]
  assert not manage.SETTINGS.binary_path.exists()
  assert manage.read_installed() == (bridge_id, [])


def test_uninstall_removes_everything_it_added_and_nothing_else(mac):
  manage.SSH_CONFIG.write_text(OWN_BLOCK)
  manage.install("host-a")
  mac["answers"] = ["y", "n"]
  manage.uninstall()
  assert not mac["running"]
  assert not manage.SETTINGS.binary_path.parent.exists()
  assert not manage.SETTINGS.plist.exists()
  assert manage.SSH_CONFIG.read_text() == OWN_BLOCK


def test_cancelling_a_first_install_undoes_the_ssh_config(mac, monkeypatch):
  manage.SSH_CONFIG.write_text(OWN_BLOCK)

  def interrupted(entry):
    raise KeyboardInterrupt

  monkeypatch.setattr(manage, "install_command", interrupted)
  with pytest.raises(KeyboardInterrupt):
    manage.install("host-a")
  manage.cancel()
  assert manage.SSH_CONFIG.read_text() == OWN_BLOCK


def test_cancelling_after_the_service_started_takes_it_back_down(mac, monkeypatch):
  real_ssh = manage.ssh

  def ssh(entry, *command, **kwargs):
    if "--healthy" in command[0]: raise KeyboardInterrupt
    return real_ssh(entry, *command, **kwargs)

  monkeypatch.setattr(manage, "install_command", lambda entry: True)
  monkeypatch.setattr(manage, "ssh", ssh)
  with pytest.raises(KeyboardInterrupt):
    manage.install("host-a")
  assert mac["running"]

  manage.cancel()
  assert not mac["running"]
  assert not manage.SETTINGS.binary_path.exists()
  assert not manage.SETTINGS.config_path.exists()


def test_yes_answers_every_question_without_a_terminal(mac, monkeypatch, capsys):
  def no_terminal(question, default="", hint=""):
    raise AssertionError("asked the terminal: " + question)

  monkeypatch.setattr(manage, "ask", no_terminal)
  monkeypatch.setattr(manage.sys, "argv", ["install.sh", "host-a", "--yes"])
  manage.main()
  assert installed_hosts() == ["host-a"]

  monkeypatch.setattr(manage.sys, "argv", ["install.sh", "--uninstall", "-y"])
  manage.main()
  assert not manage.SETTINGS.config_path.exists()
  assert "Uninstall vcode-bridge from this Mac? [y/N] y" in capsys.readouterr().out


def test_update_reinstalls_hosts_only_when_the_version_changed(mac, monkeypatch, capsys):
  manage.install("host-a")
  manage.update()
  assert f"vcode-bridge {manage.VERSION} is up to date" in capsys.readouterr().out

  installed = manage.installed_version()
  monkeypatch.setattr(manage, "VERSION", "9.9.9")
  manage.update()
  assert f"Updating vcode-bridge {installed} → 9.9.9" in capsys.readouterr().out
  assert manage.installed_version() == "9.9.9"
