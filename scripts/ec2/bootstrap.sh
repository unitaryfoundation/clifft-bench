#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "error: the EC2 reference-host playbook requires x86-64 Linux" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "error: start from a clean checkout before bootstrapping" >&2
  exit 1
fi

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$python_version" != "3.12" ]]; then
  echo "error: the selected Ubuntu image must provide Python 3.12 (found $python_version)" >&2
  exit 1
fi

echo "Arming a three-hour shutdown safety guard."
sudo shutdown -c >/dev/null 2>&1 || true
sudo shutdown -h +180

sudo apt-get update
sudo apt-get install --yes curl jq python3.12-venv tmux
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e . -r requirements/runner-study.txt
.venv/bin/clifft-bench validate manifests/run-runner-aa.v1.json

echo
echo "Bootstrap complete. The shutdown guard remains armed."
echo "Continue with the run-placement command in docs/ec2-playbook.md."
