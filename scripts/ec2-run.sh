#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "usage: $0 CAMPAIGN EXECUTION_DIR" >&2
  exit 2
fi
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
[[ "$(uname -s)" == Linux && "$(cat /sys/devices/virtual/dmi/id/sys_vendor)" == "Amazon EC2" ]] || {
  echo "the collection wrapper requires Amazon EC2 Linux" >&2; exit 1;
}
sudo shutdown -c >/dev/null 2>&1 || true
sudo shutdown -h +480
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo "the benchmark checkout must be clean" >&2; exit 1;
}
[[ ! -e "$2" ]] || { echo "refusing to overwrite existing execution: $2" >&2; exit 1; }
sudo apt-get update
sudo apt-get install --yes build-essential python3.12-dev python3.12-venv
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/clifft-bench bootstrap "$1"
exec timeout --signal=TERM --kill-after=30s 480m \
  .venv/bin/clifft-bench run "$1" --require-ec2 --output-dir "$2"
