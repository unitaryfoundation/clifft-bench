#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "error: the EC2 reference-host playbook requires x86-64 Linux" >&2
  exit 1
fi

system_vendor="$(cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null || true)"
if [[ "$system_vendor" != "Amazon EC2" ]]; then
  echo "error: refusing to arm the shutdown guard outside Amazon EC2" >&2
  exit 1
fi

echo "Arming a three-hour shutdown safety guard."
sudo shutdown -c >/dev/null 2>&1 || true
sudo shutdown -h +180

if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "error: start from a clean checkout before bootstrapping" >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "error: the selected image does not provide /etc/os-release" >&2
  exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "error: select Canonical Ubuntu Server 24.04 LTS (found ${PRETTY_NAME:-unknown})" >&2
  exit 1
fi

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$python_version" != "3.12" ]]; then
  echo "error: Canonical Ubuntu Server 24.04 must provide Python 3.12 (found $python_version)" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install --yes curl jq python3.12-venv tmux
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e . -r requirements/runner-study.txt
.venv/bin/clifft-bench validate manifests/run-runner-aa.v1.json

echo
echo "Bootstrap complete. The shutdown guard remains armed."
echo "Continue with the run-placement command in docs/ec2-playbook.md."
