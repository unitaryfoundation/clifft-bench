# Manual EC2 reference-host playbook

This playbook commissions a deliberately selected EC2 shape without installing
a persistent GitHub runner or giving AWS control to GitHub Actions. You launch
and stop the instance in the AWS console. The repository scripts verify the
launch identity, collect three serial replicas per boot, retain results outside
the checkout, and prepare one reviewable result commit after three boots.

The commissioning profile measures pinned Clifft 0.7.0 against itself. Its
outputs are exploratory runner evidence, not an official simulator comparison.

## Expected time and operator involvement

Each placement takes approximately 45–55 minutes. Three sequential placements
take approximately 2.5–3 hours, with a few minutes of attention at launch and
between stop/start cycles. Every placement refreshes a three-hour operating
system shutdown guard before benchmarking.

The shutdown guard is a backstop, not the normal stopping procedure. Confirm
that **instance-initiated shutdown behavior is `Stop`** during launch, and stop
the instance from the EC2 console promptly after each placement.

## 1. Launch the instance

Create one instance manually with these fixed choices:

- Ubuntu Server 24.04 LTS, x86-64; record the exact AMI ID shown by the console.
- `m7a.xlarge` (4 vCPUs and 16 GiB) as the initial candidate.
- On-Demand purchasing, not Spot and not a burstable `t` family.
- One fixed region and availability zone for all placements.
- Default/shared tenancy for this first test.
- IMDS enabled with IMDSv2 required.
- At least 16 GiB of `gp3` EBS storage.
- Instance-initiated shutdown behavior set to `Stop`.
- SSH restricted to your current IP, or an equivalent AWS console connection.
- No IAM role is required.

Keep the instance ID, instance type, AMI ID, region, and availability zone from
the console. The collection script obtains those values independently from the
EC2 Instance Metadata Service and exits before benchmarking if they do not
match the expected command-line values.

## 2. Clone the playbook branch

Connect to the instance and run:

```bash
git clone --branch codex/ec2-reference-playbook \
  https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch -c data/ec2-m7a-$(date -u +%Y%m%d)
```

After the playbook is merged, use `main` instead of the temporary playbook
branch. Do not pull, change branches, or edit tracked files between placements.
The collector requires one clean source commit across the cohort.

## 3. Bootstrap once

```bash
./scripts/ec2/bootstrap.sh
```

Bootstrap verifies x86-64 Linux and Python 3.12, creates `.venv`, installs the
exact performance-sensitive dependencies in `requirements/runner-study.txt`,
validates the run manifest, and arms the shutdown guard. Installation and
validation are outside the timed benchmark region.

Choose one cohort name and keep it unchanged. For example:

```bash
export CLIFFT_EC2_COHORT=m7a-xlarge-ubuntu2404-202608
```

The name is only a local playbook selector; the raw results contain the exact
source and host identities.

## 4. Collect placement 1

Replace the example AMI, region, and availability zone with the console values:

```bash
./scripts/ec2/run-placement.sh \
  "$CLIFFT_EC2_COHORT" \
  1 \
  m7a.xlarge \
  ami-0123456789abcdef0 \
  us-east-1 \
  us-east-1a
```

Leave the SSH session open. If you need to disconnect, start the command inside
`tmux` first so an SSH disconnect cannot terminate it. Output is spooled by
default under
`~/clifft-bench-ec2-results/`, outside the checkout, so every replica records a
clean source tree. A completed placement contains three raw result files, a
derived summary and CSV, and a `COMPLETE` marker.

When the command succeeds, stop the instance in the EC2 console.

## 5. Collect two fresh boots

Start the same instance again, reconnect, and run the same command with
`--placement 2`. Stop/start once more and collect `--placement 3`.

The collector refuses to represent the same Linux boot ID twice. AWS normally
moves an EBS-backed instance to a different underlying host after stop/start,
although this is not guaranteed. The three boot IDs therefore measure normal
on-demand placement behavior without claiming physical-host identity.

Do not rerun a successful placement number. If collection fails, preserve the
`.incomplete-*` directory for diagnosis, stop/start, and retry the same
placement number on the fresh boot. Only completed placement directories are
eligible for finalization.

## 6. Prepare the repository result tree

After placement 3 succeeds, while the instance is still running:

```bash
./scripts/ec2/finalize.sh "$CLIFFT_EC2_COHORT"
```

Finalization validates every raw result, checks that placements 1–3 contain
distinct boot IDs and one fixed launch/source identity, regenerates the combined
analysis, and creates:

```text
results/runner-study/ec2/<cohort>/
├── pairs.csv
├── raw/
└── summary.json
```

Raw JSON is authoritative. The summary includes both the nested dispatch-level
A/A difference and boot-level throughput distributions. Review the summary and
`git diff --stat`, then commit without signing:

```bash
git add "results/runner-study/ec2/$CLIFFT_EC2_COHORT"
git commit --no-gpg-sign -m "data: add EC2 cohort $CLIFFT_EC2_COHORT"
git push -u origin HEAD
```

The HTTPS push requires GitHub authentication. If you do not want credentials
on the benchmark instance, create the commit there, make a Git bundle, copy it
to your laptop, and push from the laptop instead:

```bash
git bundle create "/tmp/$CLIFFT_EC2_COHORT.bundle" HEAD
```

Copy that file with `scp`, fetch the branch from the bundle locally, and push it
from an already authenticated checkout.

## What the pilot decides

- Stable A/A and stable boot-level throughput commissions this EC2
  configuration for infrequent comparisons and absolute results.
- Stable A/A but variable boot-level throughput supports paired release/tool
  comparisons on one boot, while absolute results require multiple placements
  and a reported distribution.
- Noise within a placement points to the benchmark or host behavior; changing
  orchestration to RunsOn would not address it.

The first cohort should remain on shared On-Demand EC2. Dedicated tenancy or a
managed EC2 runner layer is a follow-up only if the ordinary fixed shape does
not meet the required stability or the manual workflow becomes burdensome.
