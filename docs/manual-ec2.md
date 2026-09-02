# Manual EC2 release collection

You launch, stop, and restart the reference instance in the AWS console. The
scripts require no IAM role or AWS credentials. They verify launch identity
through IMDSv2, install isolated tool environments, spool results outside the
checkout, and prepare a normal reviewable results change.

Every script arms an eight-hour shutdown guard. Set instance-initiated shutdown
behavior to **Stop** and stop the instance promptly after each placement.

## 1. Launch the reference host

Use these fixed choices:

- verified Canonical **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type**;
- **64-bit (x86)**, not Arm, Ubuntu Pro, Marketplace, or a community image;
- `m7a.xlarge`, shared tenancy, and On-Demand purchasing;
- one fixed region and availability zone for the execution;
- IMDS enabled with IMDSv2 required;
- one 16 GiB `gp3` root EBS volume at the default 3,000 IOPS;
- no S3 Files, EFS, FSx, extra volume, or IAM role; and
- SSH restricted to your current IP or an equivalent console connection.

The scripts record the exact instance, AMI, region, availability zone, and boot
ID. Before cloning, expect Ubuntu `VERSION_ID="24.04"` and Python `3.12.x`.

## 2. Clone and choose a data branch

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch main
git pull --ff-only
export CLIFFT_BENCH_EXECUTION="release-v1-$(date -u +%Y%m%d-%H%M%S)"
git switch -c "data/$CLIFFT_BENCH_EXECUTION"
```

Do not pull, edit tracked files, or change commits during collection.

## 3. Bootstrap the release campaign

```bash
export CLIFFT_BENCH_CAMPAIGN=release-v1
./scripts/ec2/bootstrap.sh "$CLIFFT_BENCH_CAMPAIGN"
```

Bootstrap installs the controller into `.venv`. Each implementation used by
the release manifest is installed into a separate ignored environment under
`.campaign-envs/` using the lock recorded with its software identity.
Installation and import checks are outside the timed region.

Inspect the collection settings before starting:

```bash
jq '.collection' campaigns/release-v1/run.v1.json
```

Every worker receives the manifest's 12 GiB Linux address-space ceiling. The
complete placement also has a wall-clock timeout with a 30-second forced-kill
fallback.

## 4. Collect the placement

Use the execution ID created with the data branch:

```bash
./scripts/ec2/run-placement.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION" \
  1
```

Run inside `tmux` if the SSH connection may close. The collector runs all cases
serially on one logical CPU and writes one raw file under
`~/clifft-bench-ec2-results/`. A case failure remains in that raw result; a
launcher timeout or invalid result leaves an `.incomplete-*` directory.

The recurring campaign declares one placement, so collection is complete after
placement 1 succeeds. Stop the instance after the result has been finalized and
pushed.

## 5. Finalize and push

```bash
./scripts/ec2/finalize.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION"
```

Finalization copies raw files into the repository and generates the index and
tables described in [data-format.md](data-format.md). Before committing, inspect
the unique comparison configurations and every selected calibrated batch size:

```bash
result_dir="results/$CLIFFT_BENCH_CAMPAIGN/$CLIFFT_BENCH_EXECUTION"
cut -d, -f6,8,16-19,27-29 "$result_dir/comparisons.csv" | sort -u
jq -r '
  .cases[]
  | select(.setup.runtime_metadata.batch_calibration != null)
  | [
      .variant_id,
      .workload.id,
      .setup.runtime_metadata.batch_calibration.selected_batch_size,
      .execution.batch_size
    ]
  | @tsv
' "$result_dir"/raw/*-raw.json
```

Confirm that `current-vs-previous` is scalar previous Clifft versus calibrated
current Clifft, `alternatives-vs-current` compares the two calibrated tools,
and `scalar-alternatives-vs-current` compares their scalar configurations.

Review before committing:

```bash
git diff --stat
git add "results/$CLIFFT_BENCH_CAMPAIGN/$CLIFFT_BENCH_EXECUTION"
git commit --no-gpg-sign -m \
  "data: add $CLIFFT_BENCH_CAMPAIGN execution $CLIFFT_BENCH_EXECUTION"
git push -u origin HEAD
```

A fine-grained GitHub token only needs repository contents write access. The
collector strips HTTP credentials from the recorded Git remote. Stop the
instance after confirming the branch is visible on GitHub.
