"""Tests for the remote vcode script, run against a stand-in for the Mac's bridge socket.

The fallback to the one live socket is not tested: a real bridge on this machine would count as a second Mac.
Author: Marco Cassar (@Ocramaru)
"""

import http.server
import os
import pwd
import shutil
import socketserver
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "vcode"


class Bridge(socketserver.UnixStreamServer):
  """Answers /health and records each path /open is asked for, like the Mac service."""

  def __init__(self, socket_path):
    self.opened = []
    super().__init__(socket_path, BridgeHandler)

  def get_request(self):
    request, _ = super().get_request()
    return request, ("local", 0)


class BridgeHandler(http.server.BaseHTTPRequestHandler):
  def do_GET(self):
    self.send_response(204)
    self.end_headers()

  def do_POST(self):
    self.server.opened.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
    self.send_response(202)
    self.end_headers()

  def log_message(self, *args):
    pass


@pytest.fixture
def mac():
  """Serves a stand-in bridge on this user's socket for a fresh Mac id."""
  mac_id = uuid.uuid4().hex
  socket_path = f"/tmp/vcode-bridge-{pwd.getpwuid(os.getuid()).pw_name}-{mac_id}.sock"
  bridge = Bridge(socket_path)
  threading.Thread(target=bridge.serve_forever, daemon=True).start()
  yield mac_id, bridge
  bridge.shutdown()
  bridge.server_close()
  os.unlink(socket_path)


def run_vcode(*arguments, mac_id="", tmux="", cwd=None):
  environment = {key: value for key, value in os.environ.items() if key not in ("LC_VCODE_BRIDGE", "TMUX")}
  if mac_id: environment["LC_VCODE_BRIDGE"] = mac_id
  if tmux: environment["TMUX"] = tmux
  return subprocess.run([SCRIPT, *arguments], env=environment, cwd=cwd, capture_output=True, text=True)


def test_opens_the_folder_on_the_mac_its_id_names(mac, tmp_path):
  mac_id, bridge = mac
  assert run_vcode(".", mac_id=mac_id, cwd=tmp_path).returncode == 0
  assert bridge.opened == [str(tmp_path.resolve())]


def test_healthy_checks_the_mac_without_opening_anything(mac):
  mac_id, bridge = mac
  assert run_vcode("--healthy", mac_id=mac_id).returncode == 0
  assert bridge.opened == []


def test_a_mac_that_is_not_connected_is_named_in_the_error():
  result = run_vcode(".", mac_id="notconnected")
  assert result.returncode == 1
  assert "bridge notconnected" in result.stderr


@pytest.mark.skipif(not shutil.which("tmux"), reason="needs tmux")
def test_tmux_follows_the_mac_of_the_latest_attach(mac, tmp_path):
  mac_id, bridge = mac
  server = f"vcode-test-{uuid.uuid4().hex[:8]}"
  subprocess.run(["tmux", "-L", server, "-f", "/dev/null", "new-session", "-d", "-s", "test"], check=True)
  try:
    subprocess.run(["tmux", "-L", server, "set-environment", "-t", "test", "LC_VCODE_BRIDGE", mac_id], check=True)
    session = subprocess.run(["tmux", "-L", server, "display", "-p", "#{socket_path},#{pid},0"], check=True, capture_output=True, text=True).stdout.strip()
    assert run_vcode(".", mac_id="stalepane", tmux=session, cwd=tmp_path).returncode == 0
    assert bridge.opened == [str(tmp_path.resolve())]
  finally:
    subprocess.run(["tmux", "-L", server, "kill-server"], check=False)
