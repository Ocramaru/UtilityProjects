"""Tests for the remote vcode script, run against a stand-in for the Mac's service on a Unix socket.

Author: Marco Cassar (@Ocramaru)
"""

import glob
import http.server
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "vcode"


class Bridge(socketserver.UnixStreamServer):
  """Answers like the Mac service: /health, /open and /vcode, each reply carrying the Mac's version."""

  def __init__(self, socket_path, version="dev", status=None, script=""):
    self.opened, self.host_keys, self.version, self.status, self.script = [], [], version, status, script
    super().__init__(socket_path, BridgeHandler)

  def get_request(self):
    request, _ = super().get_request()
    return request, ("local", 0)


class BridgeHandler(http.server.BaseHTTPRequestHandler):
  def reply(self, status, body=b""):
    self.send_response(status)
    self.send_header("X-Vcode-Bridge-Version", self.server.version)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self):
    self.server.host_keys.append(self.headers.get("X-Vcode-Host-Keys"))
    if self.path == "/vcode": self.reply(200, self.server.script.encode())
    elif self.server.status: self.reply(self.server.status, b"this host is not installed on the Mac; run install.sh with its SSH alias there\n")
    else: self.reply(204)

  def do_POST(self):
    path = self.rfile.read(int(self.headers["Content-Length"])).decode()
    if self.server.status: return self.do_GET()
    self.server.opened.append(path)
    self.reply(202, f"Opening {path} on host-a in VS Code\n".encode())

  def log_message(self, *args):
    pass


@pytest.fixture
def short_directory():
  directory = Path(tempfile.mkdtemp(dir="/tmp"))  # socket paths stop at 104 bytes
  yield directory
  shutil.rmtree(directory)


@pytest.fixture
def serve(short_directory):
  """Starts stand-in bridges on sockets in short_directory, returning each with its socket path."""
  bridges = []

  def start(**options):
    socket_path = short_directory / f"{uuid.uuid4().hex[:8]}.sock"
    bridge = Bridge(str(socket_path), **options)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    bridges.append(bridge)
    return bridge, socket_path

  yield start
  for bridge in bridges:
    bridge.shutdown()
    bridge.server_close()


def run_vcode(*arguments, agent_socket="", tmux="", cwd=None, script=SCRIPT):
  environment = {key: value for key, value in os.environ.items() if key not in ("SSH_AUTH_SOCK", "TMUX")}
  if agent_socket: environment["SSH_AUTH_SOCK"] = str(agent_socket)
  if tmux: environment["TMUX"] = tmux
  return subprocess.run([script, *arguments], env=environment, cwd=cwd, capture_output=True, text=True, check=False)


def test_folders_go_with_a_trailing_slash_and_files_without_along_with_this_hosts_keys(serve, tmp_path):
  bridge, socket_path = serve()
  (tmp_path / "my.site").mkdir()
  (tmp_path / "Makefile").write_text("")
  for name in ("my.site", "Makefile"): assert run_vcode(name, agent_socket=socket_path, cwd=tmp_path).returncode == 0
  folder = str(tmp_path.resolve())
  assert bridge.opened[0] == f"{folder}/my.site/" and bridge.opened[1] == f"{folder}/Makefile"
  assert run_vcode(agent_socket=socket_path, cwd="/").returncode == 0 and bridge.opened[2] == "/"
  assert run_vcode("--healthy", agent_socket=socket_path).returncode == 0 and len(bridge.opened) == 3
  keys = " ".join(Path(file).read_text().split()[1] for file in sorted(glob.glob("/etc/ssh/ssh_host_*_key.pub")))
  assert (bridge.host_keys[-1] or "").split() == keys.split()


def test_paths_that_cannot_be_sent_as_they_are_are_refused(serve, tmp_path):
  bridge, socket_path = serve()
  (tmp_path / "a\nb").mkdir()
  result = run_vcode("a\nb", agent_socket=socket_path, cwd=tmp_path)
  assert result.returncode == 1 and "control characters" in result.stderr
  assert run_vcode("one", "two", agent_socket=socket_path).returncode == 2
  assert "usage: vcode" in run_vcode("--help").stdout and run_vcode("--nope").returncode == 2
  missing = run_vcode("/no/such/folder", agent_socket=socket_path)
  assert missing.returncode == 1 and "no such file or folder" in missing.stderr
  assert bridge.opened == []


def test_a_session_without_the_bridge_says_so(short_directory, serve):
  assert "has no agent channel" in run_vcode(".").stderr

  key_agent = socket.socket(socket.AF_UNIX)
  key_agent.bind(str(short_directory / "key-agent.sock"))
  key_agent.listen()
  threading.Thread(target=lambda: key_agent.accept()[0].close(), daemon=True).start()
  result = run_vcode(".", agent_socket=short_directory / "key-agent.sock")
  key_agent.close()
  assert result.returncode == 1 and "vcode-bridge does not answer" in result.stderr

  _, socket_path = serve(status=403)
  result = run_vcode(".", agent_socket=socket_path)
  assert result.returncode == 1 and "not installed on the Mac" in result.stderr


def stamped(version):
  return SCRIPT.read_text().replace('version="dev"', f'version="{version}"', 1)


@pytest.mark.parametrize(("installed", "status", "updated"), [("0.1.0", 403, True), ("9.9.10", None, False)])  # an error reply still updates; a newer copy stays
def test_vcode_takes_the_macs_copy_only_when_it_is_newer(serve, short_directory, tmp_path, installed, status, updated):
  _, socket_path = serve(version="9.9.9", status=status, script=stamped("9.9.9"))
  script = short_directory / "vcode"
  script.write_text(stamped(installed))
  script.chmod(0o755)
  result = run_vcode("--healthy", agent_socket=socket_path, script=script)
  assert ("vcode: updated to 9.9.9" in result.stdout) == updated
  assert script.read_text() == stamped("9.9.9" if updated else installed) and os.access(script, os.X_OK)


@pytest.mark.skipif(not shutil.which("tmux"), reason="needs tmux")
def test_tmux_follows_the_session_of_the_latest_attach(serve, tmp_path):
  bridge, socket_path = serve()
  server = f"vcode-test-{uuid.uuid4().hex[:8]}"
  subprocess.run(["tmux", "-L", server, "-f", "/dev/null", "new-session", "-d", "-s", "test"], check=True)
  try:
    subprocess.run(["tmux", "-L", server, "set-environment", "-t", "test", "SSH_AUTH_SOCK", str(socket_path)], check=True)
    session = subprocess.run(["tmux", "-L", server, "display", "-p", "#{socket_path},#{pid},0"], check=True, capture_output=True, text=True).stdout.strip()
    assert run_vcode(".", agent_socket="/tmp/stale-pane-agent.sock", tmux=session, cwd=tmp_path).returncode == 0
    assert bridge.opened == [f"{tmp_path.resolve()}/"]
  finally:
    subprocess.run(["tmux", "-L", server, "kill-server"], check=False)
