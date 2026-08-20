# Manual EC2 campaign playbook

You launch, stop, and restart the instance in the AWS console. The scripts do
not install a persistent GitHub runner and require no IAM role or AWS
credentials. They verify the launch identity through IMDSv2, install isolated
tool environments, spool results outside the checkout, and prepare a normal
reviewable results commit.

Every script arms an eight-hour operating-system shutdown guard. This is a
backstop: set instance-initiated shutdown behavior to **Stop** and stop the
instance promptly after each placement.

## 1. Launch the reference host

Use these fixed choices:

- verified Canonical **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type**;
- **64-bit (x86)**, not Arm, Ubuntu Pro, Marketplace, or a community image;
- `m7a.xlarge`, shared tenancy, and On-Demand purchasing;
- one fixed region and availability zone for the entire execution;
- IMDS enabled with IMDSv2 required;
- one 16 GiB `gp3` root EBS volume at its default 3,000 IOPS;
- no S3 Files, EFS, FSx, extra volume, or IAM role;
- SSH restricted to your current IP or an equivalent console connection.

Record the exact AMI ID, region, and availability zone. Before cloning, expect
Ubuntu `VERSION_ID="24.04"` and Python `3.12.x`:

```bash
grep '^\(NAME\|VERSION_ID\)=' /etc/os-release
python3 --version
```

## 2. Clone and choose a data branch

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch -c data/current-tools-$(date -u +%Y%m%d)
```

Do not pull, edit tracked files, or change commits between placements.

## 3. Bootstrap one campaign

Choose `clifft-history-v1` or `current-tools-v1`:

```bash
export CLIFFT_BENCH_CAMPAIGN=current-tools-v1
./scripts/ec2/bootstrap.sh "$CLIFFT_BENCH_CAMPAIGN"
```

Bootstrap installs only the controller into `.venv`. Every declared
tool/version is installed into a separate ignored environment under
`.campaign-envs/` using its checked-in resolved lock. Installation and import
are outside the timed region. SymFT is compiled natively on this fixed host.

Inspect the declared number of placements before starting:

```bash
jq '.collection' "campaigns/$CLIFFT_BENCH_CAMPAIGN/campaign.v1.json"
```

The current-tools campaign gives each Tsim setup or request five minutes and
caps each complete tool run at 90 minutes. A Tsim timeout is retained as a
structured result: the campaign does not spend unbounded EC2 time trying to
turn an impractical circuit into a throughput number.

## 4. Collect a placement

Choose a unique execution ID and substitute the exact launch values:

```bash
export CLIFFT_BENCH_EXECUTION=current-tools-v1-202608
./scripts/ec2/run-placement.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION" \
  1 \
  ami-0123456789abcdef0 \
  us-east-1 \
  us-east-1c
```

Run inside `tmux` if the SSH connection may close. The collector executes each
campaign run and replica serially, pins every worker to one logical CPU, and
spools completed raw files under `~/clifft-bench-ec2-results/`. A failed case
or worker timeout remains in its schema-valid raw result and does not prevent
the other cases from running. A launcher-level timeout, interruption, or
invalid result leaves an `.incomplete-*` directory for diagnosis and is never
silently promoted to a completed placement.

When the command succeeds, stop the instance in the console. For a campaign
with additional placements, start the same EBS-backed instance again and run
the command with placement 2, then 3. The collector requires a distinct Linux
boot ID and one unchanged AMI/AZ/source identity.

## 5. Finalize and push

After the last placement succeeds:

```bash
./scripts/ec2/finalize.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION"
```

Finalization validates the complete execution, copies raw files into the
repository, and generates the index, tables, and summary described in
[`data-format.md`](data-format.md). Review the result before committing:

```bash
git diff --stat
git add "results/$CLIFFT_BENCH_CAMPAIGN/$CLIFFT_BENCH_EXECUTION"
git commit --no-gpg-sign -m \
  "data: add $CLIFFT_BENCH_CAMPAIGN execution $CLIFFT_BENCH_EXECUTION"
git push -u origin HEAD
```

A fine-grained GitHub token only needs repository contents write access for
the push. The collector strips HTTP credentials from the Git remote before
writing result metadata. Do not place AWS credentials on the benchmark
instance. Stop the instance immediately after confirming the branch is visible
on GitHub.
