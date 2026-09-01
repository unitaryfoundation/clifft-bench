# Clifft 0.x release-history backfill

This one-off official campaign reruns one representative release from each
Clifft 0.x series on the current QEC workload, reference host, and measurement
contract. It answers how those release binaries compare when measured together
today; it does not reconstruct observations made at their original release
dates.

The selected versions are 0.1.0, 0.2.0, 0.3.0, 0.4.1, 0.5.0, 0.6.0, 0.7.0,
0.8.0, and 0.9.0. Version 0.4.1 represents the 0.4 series because it superseded
0.4.0. Every version runs the same eight immutable workloads with scalar
sampling, three timed repetitions, and the same public shots per call.
Comparisons are derived against both 0.1.0 and 0.9.0 without rerunning cases.

The general reference-host requirements and safety checks are documented in
[`docs/manual-ec2.md`](../../docs/manual-ec2.md). Use the same
`m7a.xlarge` instance and keep one checkout commit fixed across all placements.

## Collect on EC2

After this campaign has merged, create a data branch from the exact main commit
that will own the results:

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch -c data/clifft-history-$(date -u +%Y%m%d)

export CLIFFT_BENCH_CAMPAIGN=clifft-history-v1
export CLIFFT_BENCH_EXECUTION=clifft-history-v1-$(date -u +%Y%m%d)
./scripts/ec2/bootstrap.sh "$CLIFFT_BENCH_CAMPAIGN"
```

Bootstrap creates nine isolated environments from the checked-in locks and
verifies that every Clifft release imports. Run the first placement inside
`tmux`:

```bash
./scripts/ec2/run-placement.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION" \
  1
```

Stop the instance after a successful placement, start the same EBS-backed
instance again, and repeat for placements 2 and 3. Do not pull, edit tracked
files, or change commits between placements. Each placement must record a
different boot ID.

Finalize after all three placements:

```bash
./scripts/ec2/finalize.sh \
  "$CLIFFT_BENCH_CAMPAIGN" \
  "$CLIFFT_BENCH_EXECUTION"

git diff --stat
git add "results/$CLIFFT_BENCH_CAMPAIGN/$CLIFFT_BENCH_EXECUTION"
git commit --no-gpg-sign -m \
  "data: add $CLIFFT_BENCH_CAMPAIGN execution $CLIFFT_BENCH_EXECUTION"
git push -u origin HEAD
```

Review the raw JSON, `cases.csv`, `comparisons.csv`, and `index.json`
before opening the results PR. A failed or timed-out version remains structured
evidence and should not be silently replaced with a different build or circuit.
