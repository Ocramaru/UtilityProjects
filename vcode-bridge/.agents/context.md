---
name: vcode-bridge context
description: Rules vcode-bridge's parts share, and how to test a change
kind: context
---

# Context

- A request's host is the last host key ssh bound the agent connection to (`session-bind@openssh.com`), matched against `host_keys`, which hold every `/etc/ssh/ssh_host_*_key.pub` of the host; when `vcode` sends its host's own keys, the bound key must be among them. Unknown keys are refused, and a connection with no bind is a process on the Mac, which may only `GET /health`. `/probe` answers only a connection with exactly one bind, the one the Mac's own ssh wrote.
- The service answers every key-agent request with no keys and never connects to the real agent.
- The service reads `config.json` on every request, so nothing signals it. Its socket path is a launch agent argument, so a change to it restarts the service.
- `~/.ssh/config` holds one managed block at the top, between `# vcode-bridge begin` and `# vcode-bridge end` and closed with `Host *`. Jump-host blocks go right under it, each closed with `Host *` too.
- Each change a run makes registers an undo step in `UNDO` before it is made; an error replays them, and Ctrl-C asks first.
- Remote commands run through `sh -c`, since login shells differ.
- `vcode` keeps a literal `version="dev"` line; the installer replaces it with `VERSION`, and hosts fetch the Mac's copy when it is newer.
- `install.sh` always runs GitHub's `main`. Try local changes with `uv run --python 3.15 manage.py <host>`.
- Python tests: `env -u PYTHONPATH uv run --no-project --with pytest pytest tests/`; one builds the real bridge when Go is on PATH. Go, in `bridge/`: `gofmt -l .; go vet ./... && go test -race ./...`.
