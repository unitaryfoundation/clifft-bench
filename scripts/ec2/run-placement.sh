#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source "$repo_root/scripts/ec2/common.sh"
cd "$repo_root"

require_ec2_linux
arm_shutdown_guard

if (( $# != 6 )); then
  echo "usage: $0 CAMPAIGN_ID EXECUTION_ID PLACEMENT AMI_ID REGION AVAILABILITY_ZONE" >&2
  exit 2
fi

campaign_id="$1"
execution_id="$2"
placement="$3"
expected_image_id="$4"
expected_region="$5"
expected_zone="$6"
validate_identifier "campaign id" "$campaign_id"
validate_identifier "execution id" "$execution_id"
[[ "$placement" =~ ^[0-9]+$ ]] || fail "placement must be a positive integer"

campaign_path="$(campaign_manifest "$campaign_id")"
campaign_schema="$(jq -er '.schema_version' "$campaign_path")"
expected_instance_type="$(jq -er '.reference_host.instance_type' "$campaign_path")"
placement_count="$(jq -er '.collection.placements' "$campaign_path")"
replicas="$(jq -er '.collection.replicas_per_placement' "$campaign_path")"
timeout_minutes="$(jq -er '.collection.run_timeout_minutes' "$campaign_path")"
(( placement >= 1 && placement <= placement_count )) || \
  fail "placement must be between 1 and $placement_count"

[[ -x .venv/bin/clifft-bench ]] || fail "run scripts/ec2/bootstrap.sh $campaign_id first"
require_clean_checkout
.venv/bin/clifft-bench validate "$campaign_path"

spool_root="${CLIFFT_BENCH_EC2_SPOOL_ROOT:-$repo_root/../clifft-bench-ec2-results}"
execution_dir="$spool_root/$execution_id"
complete_dir="$execution_dir/placement-$(printf '%02d' "$placement")"
[[ ! -e "$complete_dir" ]] || fail "placement $placement is already complete"
if [[ -f "$execution_dir/campaign-id" ]]; then
  [[ "$(< "$execution_dir/campaign-id")" == "$campaign_id" ]] || \
    fail "execution id already belongs to another campaign"
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
  [[ "$actual" == "$expected" ]] || \
    fail "$label mismatch: expected '$expected', received '$actual'"
}

check_value "instance type" "$expected_instance_type" "$instance_type"
check_value "AMI" "$expected_image_id" "$image_id"
check_value "region" "$expected_region" "$region"
check_value "availability zone" "$expected_zone" "$availability_zone"
check_value "lifecycle" "on-demand" "$lifecycle"

source_commit="$(git rev-parse HEAD)"
source_branch="$(git branch --show-current)"
source_repository="$(sanitize_remote_url "$(git remote get-url origin)")"

shopt -s nullglob
existing_raw=("$execution_dir"/placement-*/raw/*-raw.json)
for path in "${existing_raw[@]}"; do
  check_value "existing execution campaign" "$campaign_id" \
    "$(jq -er '.run.profile_id' "$path")"
  check_value "existing execution source commit" "$source_commit" \
    "$(jq -er '.runner.suite_source.commit' "$path")"
  check_value "existing execution clean source" "false" \
    "$(jq -er '.runner.suite_source.dirty' "$path")"
  check_value "existing execution instance type" "$instance_type" \
    "$(jq -er '.runner.cloud.instance_type' "$path")"
  check_value "existing execution AMI" "$image_id" \
    "$(jq -er '.runner.cloud.image_id' "$path")"
  check_value "existing execution region" "$region" \
    "$(jq -er '.runner.cloud.region' "$path")"
  check_value "existing execution availability zone" "$availability_zone" \
    "$(jq -er '.runner.cloud.availability_zone' "$path")"
  check_value "existing execution lifecycle" "$lifecycle" \
    "$(jq -er '.runner.cloud.lifecycle' "$path")"
  [[ "$(jq -er '.runner.cloud.boot_id' "$path")" != "$boot_id" ]] || \
    fail "this boot ID is already represented; stop/start before a new placement"
done

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
export CLIFFT_BENCH_RUN_REF="$source_branch"
export CLIFFT_BENCH_RUN_SHA="$source_commit"
export CLIFFT_BENCH_RUNNER_NAME="$instance_id"
export CLIFFT_BENCH_RUNNER_OS=Linux
export CLIFFT_BENCH_IMAGE_OS="$ID-$VERSION_ID"
export CLIFFT_BENCH_IMAGE_VERSION="$image_id"

if [[ "$campaign_schema" == "clifft-bench/campaign/v1" ]]; then
  while IFS=$'\t' read -r variable environment_id; do
    environment_python="$repo_root/.campaign-envs/$campaign_id/$environment_id/bin/python"
    [[ -x "$environment_python" ]] || fail "missing environment $environment_id; bootstrap again"
    export "$variable=$environment_python"
  done < <(jq -r '.environments[] | [.python_executable_env,.id] | @tsv' "$campaign_path")
fi

work_dir="$execution_dir/.incomplete-p$(printf '%02d' "$placement")-${boot_id:0:8}"
[[ ! -e "$work_dir" ]] || fail "incomplete placement directory already exists: $work_dir"
mkdir -p "$work_dir/raw"
mkdir -p "$execution_dir"
printf '%s\n' "$campaign_id" > "$execution_dir/campaign-id"

if [[ "$campaign_schema" == "clifft-bench/qv-campaign/v1" ]]; then
  for (( replica = 1; replica <= replicas; replica++ )); do
    label="${campaign_id}-p$(printf '%02d' "$placement")-r$(printf '%02d' "$replica")"
    output="$work_dir/raw/$label-raw.json"
    export CLIFFT_BENCH_RUN_ID="$execution_id/$label"
    export CLIFFT_BENCH_RUN_ATTEMPT="$placement.$replica"
    echo "Running $label"
    set +e
    timeout --signal=INT --kill-after=30s "${timeout_minutes}m" \
      .venv/bin/clifft-bench qv-run \
        --campaign "$campaign_path" \
        --environment-root "$repo_root/.campaign-envs/$campaign_id" \
        --circuit-dir "$execution_dir/circuits" \
        --output "$output" \
        --execution-id "$execution_id" \
        --placement "$placement" \
        --replica "$replica"
    run_status=$?
    set -e
    if (( run_status != 0 && run_status != 1 )); then
      fail "$label terminated without a complete structured result (exit $run_status)"
    fi
    .venv/bin/clifft-bench validate "$output"
    if (( run_status == 1 )); then
      echo "Recorded one or more structured case failures in $label; continuing."
    fi
  done
else
  while IFS=$'\t' read -r run_id manifest_relative; do
    run_manifest="$(dirname "$campaign_path")/$manifest_relative"
    for (( replica = 1; replica <= replicas; replica++ )); do
      label="${run_id}-p$(printf '%02d' "$placement")-r$(printf '%02d' "$replica")"
      output="$work_dir/raw/$label-raw.json"
      export CLIFFT_BENCH_RUN_ID="$execution_id/$label"
      export CLIFFT_BENCH_RUN_ATTEMPT="$placement.$replica"
      echo "Running $label"
      set +e
      timeout --signal=TERM "${timeout_minutes}m" .venv/bin/clifft-bench run \
        --run-manifest "$run_manifest" \
        --output "$output"
      run_status=$?
      set -e
      if (( run_status != 0 && run_status != 1 )); then
        fail "$label terminated without a complete structured result (exit $run_status)"
      fi
      .venv/bin/clifft-bench validate "$output"
      if (( run_status == 1 )); then
        echo "Recorded one or more structured case failures in $label; continuing."
      fi
    done
  done < <(jq -r '.runs[] | [.id,.run_manifest] | @tsv' "$campaign_path")
fi

touch "$work_dir/COMPLETE"
mv "$work_dir" "$complete_dir"
echo "Completed $campaign_id placement $placement at $complete_dir"
