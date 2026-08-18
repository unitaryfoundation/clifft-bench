#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: $0 COHORT" >&2
  exit 2
fi

cohort="$1"
if [[ ! "$cohort" =~ ^[a-z0-9][a-z0-9._-]{2,63}$ ]]; then
  echo "error: invalid cohort name" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
if [[ ! -x .venv/bin/clifft-bench ]]; then
  echo "error: run scripts/ec2/bootstrap.sh first" >&2
  exit 1
fi

spool_root="${CLIFFT_BENCH_EC2_SPOOL_ROOT:-$repo_root/../clifft-bench-ec2-results}"
cohort_dir="$spool_root/$cohort"
raw_paths=()
for placement in 1 2 3; do
  placement_dir="$cohort_dir/placement-0$placement"
  if [[ ! -f "$placement_dir/COMPLETE" ]]; then
    echo "error: placement $placement is incomplete or missing" >&2
    exit 1
  fi
  shopt -s nullglob
  placement_raw=("$placement_dir"/raw/*-raw.json)
  if (( ${#placement_raw[@]} != 3 )); then
    echo "error: placement $placement must contain exactly three raw results" >&2
    exit 1
  fi
  boot_count="$(jq -s '[.[].runner.cloud.boot_id] | unique | length' "${placement_raw[@]}")"
  if [[ "$boot_count" != "1" ]]; then
    echo "error: placement $placement contains more than one boot ID" >&2
    exit 1
  fi
  raw_paths+=("${placement_raw[@]}")
done

if ! jq -e -s '
  ([.[].runner.cloud | del(.instance_id, .boot_id)] | unique | length) == 1 and
  ([.[].runner.cloud.boot_id] | unique | length) == 3 and
  ([.[].runner.suite_source] | unique | length) == 1 and
  ([.[].runner.suite_source.dirty] | all(.[]; . == false))
' "${raw_paths[@]}" >/dev/null; then
  echo "error: placements do not share one fixed launch and clean source identity" >&2
  exit 1
fi

target="$repo_root/results/runner-study/ec2/$cohort"
if [[ -e "$target" ]]; then
  echo "error: refusing to overwrite existing result cohort: $target" >&2
  exit 1
fi
mkdir -p "$(dirname "$target")"
stage="$(mktemp -d "$(dirname "$target")/.$cohort.XXXXXX")"
mkdir "$stage/raw"
cp "${raw_paths[@]}" "$stage/raw/"

staged_raw=("$stage"/raw/*-raw.json)
.venv/bin/clifft-bench validate "${staged_raw[@]}"
.venv/bin/clifft-bench analyze-aa "${staged_raw[@]}" \
  --output-json "$stage/summary.json" \
  --output-csv "$stage/pairs.csv"
mv "$stage" "$target"

relative="results/runner-study/ec2/$cohort"
echo "Prepared reviewable cohort: $relative"
echo "Review it, then commit with:"
echo "  git add '$relative'"
echo "  git commit --no-gpg-sign -m 'data: add EC2 cohort $cohort'"
