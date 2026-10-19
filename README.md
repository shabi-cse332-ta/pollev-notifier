# PollEv notifier

Checks a public Poll Everywhere participant page every 30 seconds and sends an
[ntfy](https://ntfy.sh) phone notification when an open question or visible activity
appears. A visible activity can trigger an alert even if its open/closed state is
unknown. The participant URL is configured by `POLLEV_URL` in `pollev.py`.

## Linuxlab setup (no sudo)

Python **3.10 or newer is required**; use Python 3.11 for this setup. The system's
default `python3` may be Python 3.6. On WashU, install and test inside an allocated
compute session, using `qlogin` from an SSH access host.

This repository is public. Cloning over HTTPS does not require a GitHub account,
password, or token. The clone command below also skips cached credential helpers.

```bash
git -c credential.helper= clone https://github.com/shabi-cse332-ta/pollev-notifier.git
cd pollev-notifier
python3.11 --version
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
chmod 600 .env
nano .env
```

Set `NTFY_TOPIC` in `.env` to the topic subscribed to in your phone's ntfy app.
Use a long, hard-to-guess topic; `.env` stays out of Git. The commands below load
it into the environment; `--topic` can override it.

```bash
source .env
.venv/bin/python pollev.py --once          # Print page state; no notification
.venv/bin/python pollev.py --test-ntfy     # Send a test notification
```

### Repair an older Python environment

If `dataclasses` is missing, check `.venv/bin/python --version`. The module is
built into Python 3.7+, while current Playwright requires Python 3.10+. Rebuild
the environment with Python 3.11, keeping your existing `.env` configuration:

```bash
python3.11 --version  # Continue only if this command succeeds
mv .venv "$HOME/pollev-venv-backup-$(date +%Y%m%d-%H%M%S)"
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
source .env
.venv/bin/python pollev.py --test-ntfy
.venv/bin/python pollev.py --once
```

If `python3.11` is unavailable, install a user-owned interpreter with Micromamba.
WashU provides `/project/compute/bin/micromamba` on its ENGR cluster. The following
also supports Linux hosts without that shared binary; it downloads the official
Micromamba binary into your account. Run from the repository directory:

```bash
pollev_mamba="$(command -v micromamba || true)"
if [ -z "$pollev_mamba" ] && [ -x /project/compute/bin/micromamba ]; then
    pollev_mamba=/project/compute/bin/micromamba
fi
if [ -z "$pollev_mamba" ]; then
    mkdir -p "$HOME/.local/bin"
    curl -fLsS "https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest" |
        tar -xj -C "$HOME/.local/bin" --strip-components=1 bin/micromamba
    pollev_mamba="$HOME/.local/bin/micromamba"
fi
"$pollev_mamba" create -y -p "$HOME/.local/share/pollev-python311" --override-channels -c conda-forge python=3.11 pip
"$HOME/.local/share/pollev-python311/bin/python" --version
```

After the last command succeeds, move the old `.venv` as above, then create its
replacement with `"$HOME/.local/share/pollev-python311/bin/python" -m venv .venv`.
Continue from the pip installation command. Micromamba activation is unnecessary
when using these explicit interpreter paths. No sudo is needed for this Python
installation; Chromium's OS compatibility is still a separate requirement.

Python packages and browser downloads install in your account, but Chromium
still needs compatible system libraries. WashU documents a Rocky Linux 8 desktop,
which is outside Playwright's supported Debian/Ubuntu list. If Chromium reports
missing libraries or GLIBC errors, use an IT-supported browser/container environment
or a supported persistent host; pip cannot replace system libraries. No sudo-based
dependency installation is required by these instructions.

## WashU Linuxlab: run as a batch job

Submit from the repository directory, where `.venv` and `.env` are located:

```bash
sbatch linuxlab.sbatch
squeue -u "$USER"
tail -f pollev_notify.log
# Stop the job, replacing JOB_ID with the ID printed by sbatch:
scancel JOB_ID
```

`linuxlab.sbatch` requests one CPU, 2 GB RAM, and two hours. Adjust the time and
account to your class's permitted settings. The job runs after you disconnect,
but ends when its allocation expires or the host stops. A new allocation may
queue, so Linuxlab does not guarantee uninterrupted 24/7 monitoring. SSH access
hosts are for access; run the monitor on an allocated compute node.

## Persistent VM alternative: tmux

If your VM permits long-running processes and has tmux installed:

```bash
tmux new -s pollev
source .env
.venv/bin/python -u pollev.py --interval 30
```

Detach with **Ctrl+B, then D**. Reconnect to the same host with
`tmux attach -t pollev`; stop with **Ctrl+C**. tmux does not restart a crashed
process, survive a reboot, or extend a school allocation. Logs are written to
`pollev_notify.log`; rotate or clear them periodically. If Chromium dies while
Python stays alive, restart the monitor. Restarts reset notification deduplication.

The monitor needs outbound HTTPS to Poll Everywhere and ntfy, and no inbound port.
It sends alerts only: open the link on your phone to answer or complete any
location-based attendance check-in. It does not submit answers or verify attendance.

References: [WashU batch jobs](https://washu.atlassian.net/wiki/spaces/EIKB/pages/2289369323),
[WashU cluster](https://washu.atlassian.net/wiki/spaces/EIKB/pages/261751080),
[WashU Python environments](https://washu.atlassian.net/wiki/spaces/EIKB/pages/841384160),
[Micromamba installation](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html),
[Playwright requirements](https://playwright.dev/python/docs/intro#system-requirements).
