#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source "$repo_root/scripts/ec2/common.sh"
cd "$repo_root"

if (( $# != 1 )); then
  echo "usage: $0 CAMPAIGN_ID" >&2
  exit 2
fi

campaign_id="$1"
validate_identifier "campaign id" "$campaign_id"
campaign_path="$(campaign_manifest "$campaign_id")"

require_ec2_linux
arm_shutdown_guard
require_clean_checkout

[[ -r /etc/os-release ]] || fail "the selected image does not provide /etc/os-release"
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || \
  fail "select Canonical Ubuntu Server 24.04 LTS (found ${PRETTY_NAME:-unknown})"

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$python_version" == "3.12" ]] || \
  fail "Canonical Ubuntu Server 24.04 must provide Python 3.12 (found $python_version)"

sudo apt-get update
sudo apt-get install --yes build-essential curl jq python3.12-dev python3.12-venv tmux

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/clifft-bench validate "$campaign_path"

environment_root="$repo_root/.campaign-envs/$campaign_id"
software_relative="$(jq -er '.software_manifest' "$campaign_path")"
software_path="$(cd "$(dirname "$campaign_path")" && realpath "$software_relative")"
install_environment_query='.implementations[] | select(.id == $id) | (.environment.install_environment // {}) | to_entries[] | [.key,.value] | @tsv'
while IFS= read -r implementation_id; do
  environment_path="$environment_root/$implementation_id"
  requirements_relative="$(jq -er --arg id "$implementation_id" \
    '.implementations[] | select(.id == $id) | .environment.requirements' "$software_path")"
  requirements_path="$(dirname "$software_path")/$requirements_relative"
  echo "Installing isolated environment: $implementation_id"
  python3 -m venv "$environment_path"
  "$environment_path/bin/python" -m pip install --upgrade pip

  install_environment_tsv="$(
    jq -r --arg id "$implementation_id" \
      "$install_environment_query" \
      "$software_path"
  )"
  install_environment=()
  while IFS=$'\t' read -r key value; do
    [[ -n "$key" ]] || continue
    install_environment+=("$key=$value")
  done <<<"$install_environment_tsv"
  env "${install_environment[@]}" \
    "$environment_path/bin/python" -m pip install --no-deps \
      -r "$requirements_path"
  env "${install_environment[@]}" \
    "$environment_path/bin/python" -m pip check

  while IFS= read -r import_module; do
    echo "Checking import: $import_module"
    env "${install_environment[@]}" \
      "$environment_path/bin/python" -c \
      'import importlib, sys; importlib.import_module(sys.argv[1])' \
      "$import_module"
  done < <(
    jq -r --arg id "$implementation_id" \
      '.implementations[] | select(.id == $id) | .environment.import_modules[]' \
      "$software_path"
  )
done < <(jq -r '[.variants[].implementation_id] | unique[]' "$campaign_path")

echo
echo "Bootstrap complete for $campaign_id. The shutdown guard remains armed."
echo "Continue with scripts/ec2/run-placement.sh as documented in docs/manual-ec2.md."
