# Whole-machine CPU comparison

Collect the eight existing QEC circuits on one **m8a.16xlarge**, using all
**64 physical cores** and **256 GiB RAM**. This is a separate presentation
experiment with its own dependency lock and results. The release campaign
remains a single-core series.

The reference is shared-tenancy Linux On-Demand in **us-east-1**, running
Canonical Ubuntu Server **24.04 LTS**, x86-64, Python 3.12. AWS's public price
on September 9, 2026 was **$3.89504/hour**, excluding EBS. The Lambda
`gpu_1x_h100_sxm5` run cost **$4.29/hour**, as recorded by the operator.
The CPU instance is about **9.2% cheaper**: present the actual prices alongside
throughput and optionally attempted shots per dollar. This is a comparable-cost
whole-machine comparison; it does not claim identical hardware or identical cost.

Sources: [AWS M8a specifications](https://aws.amazon.com/ec2/instance-types/m8a/)
and [AWS public Linux On-Demand price feed, Northern Virginia](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20%28N.%20Virginia%29/Linux/index.json).
The price feed is live; the experiment records the stated collection price.

## Work and threading

| Tool | New cases | How the 64 cores are used |
|---|---:|---|
| Clifft 0.10.0rc1 | All 8 QEC circuits | One process, native `threads=64`, `thread_layout=(64,1)`: parallel shots |
| SymFT 0.1.1, c89b985 | All 8 QEC circuits | One process, native `threads=64`, both scalar and batch backends |
| Stim 1.16.0 | Clifford surface code d7/r7 | 64 persistent processes, one CPU each |
| Tsim 0.1.5, 44f64ba | Surface code d7/r7; coherent d3/r1 and d5/r1 | 64 persistent processes, one CPU each; assert compiled direct CPU path |

Each case runs alone. Native processes can use all 64 CPUs. Each Stim/Tsim
process is pinned to a distinct CPU **before importing the simulator or JAX**.
BLAS/OpenMP helper thread counts are one, JAX is CPU-only, and the environment
removes inherited affinity overrides. Affinity also confines any additional
runtime helper threads to that process's one CPU. Tsim receives distinct
32-bit seeds across workers and maintains a continuous stream within each
probe or final run. Clifft/SymFT/Stim receive separate per-worker and per-phase
streams. Compilation and worker startup occur outside the timed samples.

SymFT's returned `active_threads` must equal 64 on every call. Calls contain
at least four chunks per native thread. Clifft uses parallel shots because the
current circuits have small active widths; there is no intra-shot thread sweep.

Reuse the two successful **GPU-component** results for cultivation d3 and
distillation from
[`tsim-gpu-20260909-131821`](../tsim-gpu/results/tsim-gpu-20260909-131821/summary.json).
The three successful `direct-cpu` rows in that run are replaced by the new
64-process CPU measurements. Cultivation d5 and coherent d3/r3 and d5/r5 remain
missing Tsim results with their recorded compile/memory limits; they are not
zero throughput or proof that those circuits cannot run. This experiment does
not launch a GPU run. The prior CPU and GPU evidence hashes are recorded in
the new metadata.

## Keeping the machine responsive

No core is reserved. Linux can schedule SSH and monitoring while all 64 cores
are busy. Sampling workers use `nice +5` so ordinary interactive work has
priority. Run on a dedicated instance with no competing compute jobs.

Memory pressure is the greater responsiveness risk. The required systemd scope
limits **the whole collection to 224 GiB**, leaving roughly 32 GiB for the OS
and SSH, and disables swap for the collection. A process-tree RSS supervisor
also samples every 0.2 seconds and kills the entire candidate process group on
an excess, timeout, or interruption. The cgroup catches spikes between RSS
samples; an OOM can still kill a worker or controller, leaving an incomplete
attempt to resume. These limits protect the host, not a promise that every
batch will fit. There is no per-process virtual-address-space cap.

## Batch selection and measurement

Prior CPU winners are tried first, then a small sweep **at full 64-core load**:

- Clifft and SymFT: scalar, then batches 32, 256, 1024, and 2048, deduplicated
  with the prior winner. SymFT's chunk size uses the prior single-core rate,
  capped at 2048 shots and never below its batch size.
- Stim: detector-sampling chunks 256, 1024, 4096, 16384, 65536, and one unchunked
  public call. These are public API chunks, not comparable SIMD lane sizes.
- Tsim direct CPU: 65536, 8192, and 1048576 attempted shots per process per
  public call. GPU `batch_size` does not control this direct path.

Public-call sizes for Clifft/SymFT/Stim start from prior rates, then receive up
to six warm calibration calls, targeting 0.25–2 seconds per call while keeping
all workers busy. Capacity and memory limits can prevent reaching that target.
Each Tsim call shape gets its own warmup. Native candidates whose dense-state
lower bound exceeds 75% of the aggregate RAM budget are skipped; actual peak
memory may be larger. Tsim's returned arrays have a separate 16 GiB aggregate
allocation estimate limit. Every skipped or failed candidate is retained.

Select the highest median from **3 × at least 1 second** warm probes. Then
start fresh workers, prepare and warm the selected configuration, and collect
**5 × at least 30 seconds** independent final measurements. Probe timings are
never used as final throughput. Full API calls finish after the target interval;
the actual elapsed time, including overruns, is recorded.

Every process starts from a common monotonic-clock boundary. Throughput is
**total attempted shots / elapsed wall time enclosing all workers**, including
final result transfer and merging. It is never the sum of per-process rates or
a single-core rate multiplied by 64. Report the final median and median absolute
deviation. The denominator includes sampling and reduction to aggregate counts;
it excludes compilation, warmup, stream initialization, and result-file writing.

Preparation has 180 seconds per candidate; warmup has 120 seconds per call;
each probe/final interval has its target time plus 120 seconds. Each candidate,
including its measurements, has a 600-second total cap. One invocation has a
three-hour total budget and can resume remaining work. These are failure
budgets, not predicted runtimes. There is no automatic EC2 shutdown.

## Install and pilot

Use a clean committed checkout containing this directory. Do not use the
single-core `scripts/ec2` bootstrap or campaign runner. On the EC2 instance:

```bash
sudo apt-get update
sudo apt-get install --yes build-essential curl git python3.12-dev python3.12-venv tmux

# If uv is not already installed, install it in a small separate environment.
python3.12 -m venv "$HOME/clifft-uv"
"$HOME/clifft-uv/bin/pip" install uv
export PATH="$HOME/clifft-uv/bin:$PATH"

# From the repository checkout:
cd experiments/whole-machine
SYMFT_PY_NATIVE=1 SYMFT_PY_ENABLE_CUDA=0 uv sync --locked --python 3.12 --extra test
.venv/bin/python -m pytest -q
```

The lock pins the same Clifft/SymFT/Stim versions and Tsim commit used in the
existing results. SymFT builds for the host CPU with CUDA disabled; runtime
SIMD metadata is retained. JAX/JAXLIB and NumPy match the GPU experiment.
The standard published Clifft and Stim wheels are used, with their runtime
backend metadata recorded. No special Stim source-build optimization is added.

Start `tmux` before the pilot and collection. Keep the chosen spool path when
reconnecting. A short pilot exercises the real corpus and all four adapters at
the full worker count, including the widest SymFT case:

```bash
export CLIFFT_WHOLE_EXECUTION="whole-machine-$(date -u +%Y%m%d-%H%M%S)"
export CLIFFT_WHOLE_SPOOL="$HOME/clifft-whole-results/$CLIFFT_WHOLE_EXECUTION"

systemd-run --user --scope -p MemoryMax=224G -p MemorySwapMax=0 \
  .venv/bin/python -m whole_machine "${CLIFFT_WHOLE_SPOOL}-pilot" \
  --development --sample-seconds 2 --repetitions 2 \
  --workload surface-code-d7-r7-p1e-3 \
  --workload coherent-surface-d5-r5-p1e-3-rz2e-2
```

The pilot is deliberately marked development and cannot be finalized. Inspect
its `summary.json` and selected `raw/**/result.json`: successful final counts,
64 native threads or 64 one-CPU workers, no unintended Tsim components, and
acceptable RSS. A large SymFT batch failing under its memory limit is expected
and should not invalidate a successful scalar final run. Development on a
smaller machine requires explicit `--workers` and `--max-rss-gib` reductions.

The systemd user manager must be available in the SSH session. If
`systemd-run --user` reports a bus error, use a normal login as the Ubuntu user
and enable its user manager with `sudo loginctl enable-linger "$USER"`, then
reconnect. Do not remove the memory scope to bypass a preflight error.

## Collect, resume, and finalize

After checking the pilot, use a fresh production spool and default settings:

```bash
systemd-run --user --scope -p MemoryMax=224G -p MemorySwapMax=0 \
  .venv/bin/python -m whole_machine "$CLIFFT_WHOLE_SPOOL"
```

Production preflight checks the instance through IMDSv2, OS, physical core
count, CPU affinity, dependency pins, clean source, RAM budget, and hard cgroup
memory/swap limits. Keep source, dependencies, options, and the host placement
unchanged. Source/circuit hashes, versions, host identity, boot ID, seeds,
resource settings, per-worker records, and every attempt are retained.

Ctrl-C stops the candidate process group. Run the identical command to resume;
completed successes and failures are reused. `--retry-failures` explicitly
appends attempts for failed candidates while preserving earlier evidence.
Interrupted or incomplete attempts are retried automatically. A spool lock
prevents two controllers from collecting into the same directory. Changed
configuration, source, dependencies, host, or boot requires a new spool.

After all 20 cases have terminal outcomes, finalize from this directory:

```bash
.venv/bin/python -m whole_machine.finalize "$CLIFFT_WHOLE_SPOOL" \
  --execution-id "$CLIFFT_WHOLE_EXECUTION"
```

The finalizer checks the complete corpus and candidate coverage, selection,
worker resources, counts, timing, and derived rates against raw evidence. It
rejects development data, incomplete coverage, and reused output paths.
Failures remain explicit rows with blank rates. The output under
`results/EXECUTION_ID/` contains the raw evidence, metadata, summary, and
`cases.csv`, including attempted shots per dollar. Copy that directory off the
instance or commit it on a results branch before stopping the rental. Source
changes and data publishing can be reviewed separately.

## Presentation caveats

Keep the current aggregate-count framing: postselect all detectors using raw
record parity and report observable 0 among survivors. Clifft and SymFT can
avoid work using early exit. Stim samples the entire Clifford circuit and
materializes bit-packed detector/observable outputs before reduction. Tsim
also returns detector/observable arrays, with its own postselection behavior.
There is no new correction for these differences and no claim of identical
internal work. These are sampling throughputs, not decoder throughputs.

This is a new hardware series and a new full-machine measurement. It cannot
inherit the old single-core host's placement-variation bounds. One placement
and five repetitions capture within-run variation; they do not establish
cross-placement uncertainty. No linear scaling from the prior CPU run is
assumed, and small differences should not be overinterpreted.
