#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source "$repo_root/scripts/ec2/common.sh"
cd "$repo_root"

if (( $# != 2 )); then
  echo "usage: $0 CAMPAIGN_ID EXECUTION_ID" >&2
  exit 2
fi

campaign_id="$1"
execution_id="$2"
validate_identifier "campaign id" "$campaign_id"
validate_identifier "execution id" "$execution_id"
campaign_path="$(campaign_manifest "$campaign_id")"
require_clean_checkout

spool_root="${CLIFFT_BENCH_EC2_SPOOL_ROOT:-$repo_root/../clifft-bench-ec2-results}"
execution_dir="$spool_root/$execution_id"
[[ -d "$execution_dir" ]] || fail "execution spool does not exist: $execution_dir"
[[ -f "$execution_dir/campaign-id" ]] || fail "execution spool is missing campaign identity"
check_campaign="$(< "$execution_dir/campaign-id")"
[[ "$check_campaign" == "$campaign_id" ]] || fail "execution belongs to $check_campaign"

placements="$(jq -er '.collection.placements' "$campaign_path")"
raw_paths=()
shopt -s nullglob
for (( placement = 1; placement <= placements; placement++ )); do
  placement_dir="$execution_dir/placement-$(printf '%02d' "$placement")"
  [[ -f "$placement_dir/COMPLETE" ]] || fail "placement $placement is not complete"
  placement_raw=("$placement_dir"/raw/*-raw.json)
  (( ${#placement_raw[@]} > 0 )) || fail "placement $placement contains no raw results"
  raw_paths+=("${placement_raw[@]}")
done

target="$repo_root/results/$campaign_id/$execution_id"
[[ ! -e "$target" ]] || fail "refusing to overwrite existing execution: $target"
mkdir -p "$(dirname "$target")"
stage="$(mktemp -d "$(dirname "$target")/.$execution_id.XXXXXX")"
mkdir "$stage/raw"
cp "${raw_paths[@]}" "$stage/raw/"

staged_raw=("$stage"/raw/*-raw.json)
.venv/bin/clifft-bench finalize \
  --campaign "$campaign_path" \
  --execution-id "$execution_id" \
  --output-dir "$stage" \
  "${staged_raw[@]}"
mv "$stage" "$target"

relative="results/$campaign_id/$execution_id"
echo "Prepared reviewable execution: $relative"
echo "Review it, then commit with:"
echo "  git add '$relative'"
echo "  git commit --no-gpg-sign -m 'data: add $campaign_id execution $execution_id'"
