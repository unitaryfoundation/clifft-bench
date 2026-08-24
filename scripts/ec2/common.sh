#!/usr/bin/env bash

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"

fail() {
  echo "error: $*" >&2
  exit 1
}

require_ec2_linux() {
  [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || \
    fail "the manual campaign playbook requires x86-64 Linux"
  local system_vendor
  system_vendor="$(cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null || true)"
  [[ "$system_vendor" == "Amazon EC2" ]] || \
    fail "refusing to arm the shutdown guard outside Amazon EC2"
}

arm_shutdown_guard() {
  echo "Arming an eight-hour shutdown safety guard."
  sudo shutdown -c >/dev/null 2>&1 || true
  sudo shutdown -h +480
}

require_clean_checkout() {
  [[ -z "$(git status --porcelain --untracked-files=all)" ]] || \
    fail "the benchmark checkout must be clean"
}

campaign_manifest() {
  local campaign_id="$1"
  local standard="$repo_root/campaigns/$campaign_id/campaign.v1.json"
  local qv="$repo_root/campaigns/$campaign_id/qv-campaign.v1.json"
  if [[ -f "$standard" ]]; then
    [[ ! -f "$qv" ]] || fail "campaign has both standard and QV manifests: $campaign_id"
    printf '%s\n' "$standard"
  elif [[ -f "$qv" ]]; then
    printf '%s\n' "$qv"
  else
    fail "unknown campaign: $campaign_id"
  fi
}

validate_identifier() {
  local label="$1"
  local value="$2"
  [[ "$value" =~ ^[a-z0-9][a-z0-9._-]{2,63}$ ]] || fail "invalid $label: $value"
}

sanitize_remote_url() {
  sed -E 's#^(https?://)[^/@]*@#\1#' <<<"$1"
}
