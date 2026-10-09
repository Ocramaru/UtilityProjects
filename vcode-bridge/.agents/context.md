---
name: vcode-bridge context
description: Rules vcode-bridge's parts share, and how to test a change
kind: context
---

# Context

- The remote socket is `/tmp/vcode-bridge-<user>-<mac id>.sock`, the same name on every host of one Mac. The Mac routes to a host alias by which local socket a request arrives on.
- The Mac id lives in the installed config and in exactly one `SetEnv LC_VCODE_BRIDGE=<id>` block in `~/.ssh/config` (`ENV_PATTERN`).
- Anything an install changes registers an undo step in `UNDO`, which Ctrl-C replays.
- `vcode` keeps a literal `version="dev"` line; the installer replaces it with `VERSION`.
- `install.sh` always runs GitHub's `main`. Try local changes with `uv run --python 3.15 manage.py <host>`.
- Python tests: `env -u PYTHONPATH uv run --no-project --with pytest pytest tests/`. Go and launchd run only on the Mac: `cd bridge && gofmt -l . && go vet ./... && go test ./...` there.
