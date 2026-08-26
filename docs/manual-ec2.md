# Manual EC2 campaign playbook

You launch, stop, and restart the instance in the AWS console. The scripts do
not install a persistent GitHub runner and require no IAM role or AWS
credentials. They verify the launch identity through IMDSv2, install isolated
tool environments, spool results outside the checkout, and prepare a normal
reviewable results commit.

Every script arms an eight-hour operating-system shutdown guard. This is a
backstop: set instance-initiated shutdown behavior to **Stop**. A fully
successful placement requests an immediate poweroff, which stops the instance;
an unexpected termination or structured case failure leaves it running for
inspection under the guard.

## 1. Launch the reference host

For `clifft-history-v1` and `current-tools-v1`, use these fixed choices:

- verified Canonical **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type**;
- **64-bit (x86)**, not Arm, Ubuntu Pro, Marketplace, or a community image;
- `m7a.xlarge`, shared tenancy, and On-Demand purchasing;
- one fixed region and availability zone for the entire execution;
- IMDS enabled with IMDSv2 required;
- one 16 GiB `gp3` root EBS volume at its default 3,000 IOPS;
- no S3 Files, EFS, FSx, extra volume, or IAM role;
- SSH restricted to your current IP or an equivalent console connection.

The scripts record the exact instance, AMI, region, and availability zone from
IMDS and require them to remain fixed across placements. Before cloning,
expect Ubuntu `VERSION_ID="24.04"` and Python `3.12.x`:

```bash
grep '^\(NAME\|VERSION_ID\)=' /etc/os-release
python3 --version
```

The less-frequent `qv-multicore-v1` campaign has a separate 16-core host and
launch checklist in [`qv-multicore.md`](qv-multicore.md). Keeping two stopped,
EBS-backed instances is preferred to changing one instance between CPU
families: it preserves each native build and makes accidental use of the wrong
host easier to detect.

## 2. Clone and choose a data branch

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch -c data/current-tools-$(date -u +%Y%m%d)
```

Do not pull, edit tracked files, or change commits between placements.

## 3. Bootstrap one campaign

Choose `clifft-history-v1`, `current-tools-v1`, or `qv-multicore-v1`:

```bash
export CLIFFT_BENCH_CAMPAIGN=current-tools-v1
./scripts/ec2/bootstrap.sh "$CLIFFT_BENCH_CAMPAIGN"
```

Bootstrap installs only the controller into `.venv`. Every declared
tool/version is installed into a separate ignored environment under
`.campaign-envs/` using its checked-in resolved lock. Installation and import
are outside the timed region. SymFT is compiled natively on this fixed host.

The QV bootstrap builds Clifft 0.9.0 and installs the generator plus three
isolated external-simulator environments. This setup is outside every timed
case and persists while the QV instance is stopped.

Inspect the declared number of placements before starting:

```bash
jq '.collection' campaigns/"$CLIFFT_BENCH_CAMPAIGN"/*campaign.v1.json
```

The single-core QEC campaigns give every worker a declared 12 GiB Linux
address-space ceiling. The request is embedded in each raw case and the
applied ceiling is recorded after setup. Each complete run also has a campaign
wall-clock timeout with a 30-second forced-kill fallback. These bounds protect
the 16 GiB host while leaving memory for the controller and operating system.
The separate QV campaign retains its own 10 GiB per-worker ceiling.

### Benchmark a Clifft release candidate

Use the final candidate campaign as a release gate rather than waiting for the
stable package. Development pilots remain `exploratory`; the frozen candidate
may be `official` because that classification describes collection quality,
not publication status.

1. Freeze an immutable Clifft candidate tag such as `v0.10.0rc1` and build it
   with the release wheel workflow.
2. Add a distinct implementation ID, exact candidate version and source SHA to
   `manifests/software.v1.json`. Set `release_datetime` to the candidate
   package publication time, for example its TestPyPI upload time.
3. Pin the exact candidate wheel URL and SHA-256 fragment in its environment
   lock, for example `clifft @ https://...whl#sha256=<digest>`. Add isolated
   single-shot and batched runs as applicable.
4. Collect the complete campaign from one unchanged clifft-bench commit and
   leave its results PR in draft during release review.
5. If executable code or build configuration changes, assign the next
   candidate a new implementation ID and start a new execution; never combine
   placements from different candidates.
6. After publishing the accepted candidate as the stable release, use the
   stable version in README prose, tables, and figures. Preserve the candidate
   version and provenance in the raw evidence.

## 4. Collect a placement

Choose a unique execution ID:

```bash
export CLIFFT_BENCH_EXECUTION=current-tools-v1-202608
./scripts/ec2/run-placement.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION" \
  1
```

Always start a new execution ID after changing the campaign or harness. Tool
environments from an earlier attempt can be reused after bootstrap verifies
them, but incomplete raw results from a different source commit cannot be
mixed into the new execution.

Run inside `tmux` if the SSH connection may close. The collector executes each
campaign run and replica serially, pins every worker to one logical CPU, and
spools completed raw files under `~/clifft-bench-ec2-results/`. A failed case
or worker timeout remains in its schema-valid raw result and does not prevent
the other cases from running. A launcher-level timeout, interruption, or
invalid result leaves an `.incomplete-*` directory for diagnosis and is never
silently promoted to a completed placement.

When the command completes without case failures, it marks the placement
complete and requests a poweroff. With instance-initiated shutdown behavior set
to **Stop**, wait for AWS to report that the instance has stopped. For a
campaign with additional placements, start the same EBS-backed instance again
and run the command with placement 2, then 3. The collector requires a distinct
Linux boot ID and one unchanged instance/AMI/region/AZ/source identity.

If the launcher crashes, times out without a structured result, or records a
structured case failure, it leaves the instance running so logs and the
external spool can be inspected; the eight-hour shutdown guard remains armed.
Set `CLIFFT_BENCH_AUTO_STOP=0` before running a placement to keep the instance
up after success during deliberate debugging. After the final successful
placement, start the stopped instance once more to finalize and push the
results.

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
