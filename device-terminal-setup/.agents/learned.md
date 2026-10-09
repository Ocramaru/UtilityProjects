---
name: device-terminal-setup learned
description: What running the setup in Kubernetes pods on network storage showed
kind: learned
---

# Learned

<!-- map: generated. Edit the note, not this; after a manual edit run `agent remap` -->

| section | line |
|---|---|
| Containers on network storage | 19 |
| Installer | 27 |

<!-- /map -->


## Containers on network storage

- **`HOME` on CephFS makes zsh look hung.** Writing `.zcompdump` and Oh My Zsh's many small cache files took long enough to seem stuck. Keep `HOME` on the pod's local disk and link the setup in from the volume; reading it over the network started Oh My Zsh in 0.11s, measured in a pod.
- **Docker volumes are local disk.** Tests with them prove the setup runs and where it writes, never how fast it is on the cluster.
- **A linked folder carries every write into it.** Linking `.local` whole sent `pip install --user` onto the volume, and Oh My Zsh writes its cache inside `$ZSH`; create `.config` and `.local` locally and set `ZSH_CACHE_DIR`.
- **The image's conda needs nothing.** It already puts `/opt/conda/bin` on `PATH`; a conda hook in `.zshrc` only added startup time.
- **apt packages do not survive a new pod**, and pods run as root without `sudo`; `start.sh` reinstalls them with `DEBIAN_FRONTEND=noninteractive`.

## Installer

- **`curl | bash` reads the script while running it.** An early `exit` closes the pipe under curl (error 23), and a cut-off download would half run; the whole script sits in one `{ }` group so bash reads it first.
- **`--no-install-recommends` drops `ca-certificates`**, which only curl recommends; minimal images then fail every HTTPS download. It is listed explicitly.
