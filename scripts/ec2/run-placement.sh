#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "error: placement runs require x86-64 Linux" >&2
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

if (( $# != 6 )); then
  echo "usage: $0 COHORT PLACEMENT INSTANCE_TYPE AMI_ID REGION AVAILABILITY_ZONE" >&2
  exit 2
fi

cohort="$1"
placement="$2"
expected_instance_type="$3"
expected_image_id="$4"
expected_region="$5"
expected_zone="$6"

if [[ ! "$cohort" =~ ^[a-z0-9][a-z0-9._-]{2,63}$ ]]; then
  echo "error: invalid cohort name" >&2
  exit 2
fi
if [[ ! "$placement" =~ ^[123]$ ]]; then
  echo "error: placement must be 1, 2, or 3" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
if [[ ! -x .venv/bin/clifft-bench ]]; then
  echo "error: run scripts/ec2/bootstrap.sh first" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "error: the benchmark checkout must be clean" >&2
  exit 1
fi

spool_root="${CLIFFT_BENCH_EC2_SPOOL_ROOT:-$repo_root/../clifft-bench-ec2-results}"
cohort_dir="$spool_root/$cohort"
complete_dir="$cohort_dir/placement-0$placement"
if [[ -e "$complete_dir" ]]; then
  echo "error: placement $placement is already complete" >&2
  exit 1
fi

token="$(curl --fail --silent --show-error --request PUT \
  --header 'X-aws-ec2-metadata-token-ttl-seconds: 300' \
  http://169.254.169.254/latest/api/token)"
identity="$(curl --fail --silent --show-error \
  --header "X-aws-ec2-metadata-token: $token" \
  http://169.254.169.254/latest/dynamic/instance-identity/document)"
lifecycle="$(curl --fail --silent --show-error \
  --header "X-aws-ec2-metadata-token: $token" \
  http://169.254.169.254/latest/meta-data/instance-life-cycle)"

instance_id="$(jq -er '.instanceId' <<<"$identity")"
instance_type="$(jq -er '.instanceType' <<<"$identity")"
image_id="$(jq -er '.imageId' <<<"$identity")"
region="$(jq -er '.region' <<<"$identity")"
availability_zone="$(jq -er '.availabilityZone' <<<"$identity")"
boot_id="$(< /proc/sys/kernel/random/boot_id)"

check_value() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "error: $label mismatch: expected '$expected', received '$actual'" >&2
    exit 1
  fi
}

check_value "instance type" "$expected_instance_type" "$instance_type"
check_value "AMI" "$expected_image_id" "$image_id"
check_value "region" "$expected_region" "$region"
check_value "availability zone" "$expected_zone" "$availability_zone"
check_value "lifecycle" "on-demand" "$lifecycle"

source_commit="$(git rev-parse HEAD)"
source_branch="$(git branch --show-current)"
source_repository="$(git config --get remote.origin.url)"

shopt -s nullglob
existing_raw=("$cohort_dir"/placement-*/raw/*-raw.json)
if (( ${#existing_raw[@]} > 0 )); then
  expected_cloud="aws|$instance_type|$image_id|$region|$availability_zone|$lifecycle"
  for path in "${existing_raw[@]}"; do
    observed_cloud="$(jq -r \
      '.runner.cloud | [.provider,.instance_type,.image_id,.region,.availability_zone,.lifecycle] | join("|")' \
      "$path")"
    check_value "existing cohort launch configuration" "$expected_cloud" "$observed_cloud"
    check_value "existing cohort source commit" "$source_commit" \
      "$(jq -er '.runner.suite_source.commit' "$path")"
    check_value "existing cohort clean source" "false" \
      "$(jq -r '.runner.suite_source.dirty' "$path")"
    if [[ "$(jq -er '.runner.cloud.boot_id' "$path")" == "$boot_id" ]]; then
      echo "error: this boot ID is already represented; stop/start first" >&2
      exit 1
    fi
  done
fi

source /etc/os-release
export CLIFFT_BENCH_CLOUD_PROVIDER=aws
export CLIFFT_BENCH_CLOUD_INSTANCE_ID="$instance_id"
export CLIFFT_BENCH_CLOUD_INSTANCE_TYPE="$instance_type"
export CLIFFT_BENCH_CLOUD_IMAGE_ID="$image_id"
export CLIFFT_BENCH_CLOUD_REGION="$region"
export CLIFFT_BENCH_CLOUD_AVAILABILITY_ZONE="$availability_zone"
export CLIFFT_BENCH_CLOUD_LIFECYCLE="$lifecycle"
export CLIFFT_BENCH_CLOUD_BOOT_ID="$boot_id"
export CLIFFT_BENCH_RUN_PROVIDER=aws-ec2-manual
export CLIFFT_BENCH_RUN_REPOSITORY="$source_repository"
export CLIFFT_BENCH_RUN_WORKFLOW=scripts/ec2/run-placement.sh
export CLIFFT_BENCH_RUN_ID="$instance_id/$boot_id"
export CLIFFT_BENCH_RUN_ATTEMPT="$placement"
export CLIFFT_BENCH_RUN_REF="$source_branch"
export CLIFFT_BENCH_RUN_SHA="$source_commit"
export CLIFFT_BENCH_RUNNER_NAME="$instance_id"
export CLIFFT_BENCH_RUNNER_OS=Linux
export CLIFFT_BENCH_IMAGE_OS="$ID-$VERSION_ID"
export CLIFFT_BENCH_IMAGE_VERSION="$image_id"

work_dir="$cohort_dir/.incomplete-p0$placement-${boot_id:0:8}"
if [[ -e "$work_dir" ]]; then
  echo "error: incomplete placement directory already exists: $work_dir" >&2
  exit 1
fi
mkdir -p "$work_dir/raw"

raw_paths=()
for replica in 1 2 3; do
  output="$work_dir/raw/ec2-aa-p0$placement-r0$replica-raw.json"
  timeout --signal=TERM 45m .venv/bin/clifft-bench run \
    --run-manifest manifests/run-runner-aa.v1.json \
    --min-sample-seconds 30 \
    --repetitions 6 \
    --output "$output"
  .venv/bin/clifft-bench validate "$output"
  raw_paths+=("$output")
done

.venv/bin/clifft-bench analyze-aa "${raw_paths[@]}" \
  --output-json "$work_dir/summary.json" \
  --output-csv "$work_dir/pairs.csv"
date -u +"Completed %Y-%m-%dT%H:%M:%SZ" > "$work_dir/COMPLETE"
mv "$work_dir" "$complete_dir"

echo "Placement complete: $complete_dir"
echo "Stop the instance from the EC2 console before the next placement."
