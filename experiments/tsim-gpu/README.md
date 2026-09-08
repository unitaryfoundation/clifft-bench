# Tsim GPU QEC comparison

This maintained experiment complements the single-core release campaign. It
uses the same eight immutable QEC circuits and raw aggregate-count semantics,
with a separate GPU host, dependency lock, preparation budget, and public-call
size. It does not launch instances or modify the CPU campaign's resource rules.

The pinned implementation is **bloqade-tsim 0.1.5 at
44f64ba79c91e6ada77d6b609595ad0a3d767c11** (upstream main, August 12, 2026),
including changes after the PyPI 0.1.5 release. The lock also pins JAX/JAXLIB
0.11.1, pyzx-param 0.9.3, NumPy, and both optional CUDA dependency trees. The
existing CPU comparison uses SymFT 0.1.1 at c89b985 and Stim 1.16.0. Ticit is not
part of either collection.

## Reuse from the paper

The [paper's runner](https://github.com/unitaryfoundation/clifft-paper/blob/db7dc9f13a2c2854690e92390c779048a1ac1400/qec_bench/bench_common.py)
compiled once, warmed the full sampling shape, and sampled repeatedly. Its
[compile check](https://github.com/unitaryfoundation/clifft-paper/blob/db7dc9f13a2c2854690e92390c779048a1ac1400/qec_bench/tsim_compile_check.py)
is the model for subprocess deadlines. We retain those ideas, with these changes:

- Try current `cat5`, `cutting`, and `bss` strategies. `cat5` replaces the old
  `default` spelling. Try the five historically successful circuits first,
  with their successful strategy first where applicable; retry the three old
  failures under the new source and limits too.
- Choose batch size and strategy by **warm throughput probes**, excluding
  first-call JIT. Retain independent final measurements for every successful
  strategy; choose the summary strategy using the probe median alone.
- Record every timeout, error, memory-estimate skip, and exhausted budget. They
  are missing performance results with a stated resource limit, not zero rates
  or proof that a simulator cannot run the circuit.

## GPU host and installation

Use Linux, Python 3.12, one dedicated NVIDIA GPU, a working driver, and enough
local disk for CUDA wheels. A host with at least 16 GiB RAM accommodates the
12 GiB RSS guard plus the controller; 32 GiB gives more headroom. Record the
instance type, GPU/VRAM, and region in `--host-label`. There is no CPU affinity
pin: BLAS/OpenMP threads are one, while JAX may use the host CPUs. This is a
one-GPU experiment, not a single-CPU-plus-GPU resource-equivalence claim.

CUDA 13 requires Linux driver >=580 and GPU SM >=7.5. CUDA 12 is an alternative
for driver >=525. The current requirements and wheel installation approach are
in the [JAX installation guide](https://docs.jax.dev/en/latest/installation.html#nvidia-gpu).
Use one of the locked extras, not both. Avoid an `LD_LIBRARY_PATH` override of
the wheel-provided libraries. No local CUDA toolkit build should be needed.

Clone the branch/commit containing this experiment. After the implementation PR
is merged, use `main` and create a results branch before collection, just as for
the CPU campaign:

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch main
git pull --ff-only
export CLIFFT_TSIM_EXECUTION="tsim-gpu-$(date -u +%Y%m%d-%H%M%S)"
export CLIFFT_TSIM_SPOOL="$HOME/clifft-bench-gpu-results/$CLIFFT_TSIM_EXECUTION"
git switch -c "data/$CLIFFT_TSIM_EXECUTION"
cd experiments/tsim-gpu
nvidia-smi
uv sync --locked --python 3.12 --extra cuda13 --extra test
export CUDA_VISIBLE_DEVICES=0

# Validate the pinned public API, near-Clifford probabilities, and aggregation
# on this GPU before paying for the full sweep.
JAX_PLATFORMS=cuda .venv/bin/python -m pytest -q
```

For CUDA 12, replace `--extra cuda13` with `--extra cuda12`. Use a clean committed
checkout for collection. `--allow-dirty` is only for development; finalization
rejects it. Spool results outside the checkout so collection does not
change its source identity.
Do not pull, change commits, or edit tracked files while collecting. Keep the
same execution ID and spool path when reconnecting to resume a run.

## Pilot and collection

A small pilot exercises both the Clifford CPU path and real GPU components:

```bash
.venv/bin/python -m tsim_gpu "${CLIFFT_TSIM_SPOOL}-pilot" \
  --host-label 'PROVIDER / INSTANCE TYPE / REGION' \
  --workload surface-code-d7-r7-p1e-3 \
  --workload msc-d3-inject-cultivate-p1e-3 \
  --strategies cutting --batch-sizes 1024 8192 \
  --shots-per-call 8192 --sample-seconds 2 --repetitions 2
```

Inspect `summary.json`, `metadata.json`, and the per-strategy `worker.log` files.
Confirm that cultivation records `execution_path=gpu-components`, the intended
GPU appears in `jax_devices`, and both tests and sampling succeeded. Preflight
requires exactly one CUDA GPU and rejects CPU fallback before attempting circuit
compilation. Tsim's pure Clifford path intentionally remains `direct-cpu`.

The first GPU run is a diagnostic pilot, not publication evidence. Only start
the separate full collection after checking its device, counts, precision,
memory behavior, and timeout/recovery records.

Then run the full bounded experiment in `tmux`, in a new output directory:

```bash
timeout --signal=INT --kill-after=30s 4h \
  .venv/bin/python -m tsim_gpu "$CLIFFT_TSIM_SPOOL" \
  --host-label 'PROVIDER / INSTANCE TYPE / REGION'
```

Stop the rental after pushing the results to GitHub as described below. No
instance-management automation is included. If interrupted, rerun the **identical
command** to resume. Completed
successes and failures are reused; the controller will not retry known expensive
compiles automatically. A lock prevents simultaneous controllers in one output
directory. `--retry-failures` explicitly appends new attempts while keeping
successful strategies. Changed code, options, circuit hashes, dependencies,
host/device identity, or precision settings require a new output directory.
An interrupted worker without a completed result is tried again.

## Defaults and bounded work

| Setting | Default |
|---|---:|
| Circuit compilation per strategy | 120 s |
| Total compilation + tuning per circuit, across strategies | 600 s |
| Each public sampling/JIT call | 120 s |
| Imports/GPU initialization per worker | 60 s |
| Process-tree resident memory | 12 GiB |
| Returned unpacked detector + observable allocation | 256 MiB |
| Public attempted shots per call | 65536 |
| Batch candidates | 1024, 8192, 65536, plus Tsim's memory-based estimate capped at shots/call |
| Warm probes per candidate | 3 × at least 1 s |
| Independent final repetitions per successful strategy | 5 × at least 30 s |

All settings are CLI options (`--help`). Large batches above Tsim's conservative
memory estimate are skipped before JIT. There is no speculative T-count cutoff;
current compilation gets a bounded chance even on former failures. A separate
controller kills the entire worker process group on a deadline, so a native
compiler or JIT cannot defeat the timeout. RSS is sampled every 0.2 s; short
spikes can escape the guard, and monitor errors are retained in the raw result.
GPU OOMs are retained as failures; there is no promised hard VRAM ceiling.
CUDA does not inherit the CPU campaign's `RLIMIT_AS`, which would constrain its
virtual address reservations incorrectly.

Compilation and tuning reuse one sampler in one process per strategy. Different
batch shapes require separate warmups, but repetitions do not recompile the
circuit. Warmup forces the component path once with short-circuit postselection
disabled, so an all-discarded warmup cannot leave the component JIT until timing.
The normal postselection path is also warmed. If a hard timeout kills tuning
after smaller candidates completed their probes, the controller makes one
automatic recovery attempt restricted to those completed candidates. It does
not reintroduce the memory-estimated large shape. Recompilation and tuning for
both attempts are charged to the remaining 600-second circuit budget. The
original timeout and probes remain in the first attempt; only fresh, complete
final repetitions from the recovery can become throughput evidence. A recovery
timeout is retained without another automatic retry. Resuming a completed
recovery reuses it, including its cumulative preparation cost.

The 600-second budget excludes imports and final measurements. Eight circuits
allow at most 80 minutes of preparation; measuring all three successful
strategies on every circuit adds about 60 minutes plus final-call overruns.
The outer four-hour timeout bounds the entire collection independently.
Persistent JAX compilation caching is disabled; we cache result/configuration
and failure evidence, not serialized ZX programs. Repeat runs therefore do not
mislabel a disk-cached JIT as cold compilation.

## What is measured

Each timed interval includes `CompiledDetectorSampler.sample`, synchronization
and transfer implicit in its NumPy return, and NumPy reduction to attempted,
discarded, accepted, and observable-0 logical-error counts. It excludes compile,
JIT warmup, checkpoint writing, and validation. The numerator is attempted shots,
including direct-detector short-circuit discards. Both reference-sample flags
are explicitly false. After native postselection we reapply **all** detector
bits to include component-detector failures and exclude placeholder records.
No twirling, approximation threshold, custom ZX optimizer, or circuit rewrite is
introduced.

A single seeded Tsim stream advances across tuning and final repetitions; the
circuit/strategy seed is recorded. Independent final measurements mean they are
separate from tuning measurements, not that the sampler is recompiled/reseeded
for each repetition. The worker uses the pinned JAX defaults for x64, matmul
precision, and GPU allocation, stripping inherited overrides from the CPU
harness. The pinned Tsim parity products use float32 on binary inputs, and its
graph factors explicitly use complex64. Requesting x64 does not turn those
factors into complex128; there is no justification for imposing a stricter
global matmul policy for this comparison. The effective JAX settings, unset
allocator variables, and compiled array dtypes are recorded. JAX's default
preallocation reduces allocation overhead on this dedicated GPU; see the
[allocation guide](https://docs.jax.dev/en/latest/gpu_memory_allocation.html).

The CPU campaign retains its public call sizes for historical continuity
(including one shot for coherent d5/r5); Tsim uses large calls to amortize GPU
launches. Present absolute rates under each stated configuration on separate
hardware series. Do not quote a same-hardware speedup or decoder throughput.

## Collected evidence

- `metadata.json`: exact source/lock fingerprints, installed package versions,
  source URL, driver, GPU identity/VRAM, host CPU/RAM, environment, budgets.
- `raw/WORKLOAD/STRATEGY/attempt-NNN/`: request, phase checkpoint, full log, and
  final `result.json`, including raw count/timing samples and failures.
- `summary.json`: resumable derived selection/coverage view. Raw results are
  authoritative; never replace a timeout with a numerical rate.

## Finalize and push from the GPU host

Use the same GitHub authentication setup as for CPU collection. The GPU host
can commit and push its results directly; copying through your laptop is not
required. From `experiments/tsim-gpu`, after collection has stopped:

```bash
.venv/bin/python -m tsim_gpu.finalize "$CLIFFT_TSIM_SPOOL" \
  --execution-id "$CLIFFT_TSIM_EXECUTION"

git status --short
git add "results/$CLIFFT_TSIM_EXECUTION"
git diff --cached --stat
git commit --no-gpg-sign -m "data: add Tsim GPU execution $CLIFFT_TSIM_EXECUTION"
git push -u origin HEAD
```

The finalizer checks coverage of the declared workloads and strategies, clean
collection provenance, fingerprints, counts, timing summaries, and strategy
selection. It preserves timeouts and other completed failures, rejects an
actively running or incomplete collection, and copies the complete evidence
into `experiments/tsim-gpu/results/EXECUTION_ID/` without overwriting a previous
execution. The original spool remains available for recovery. No GPU is needed
to finalize or read results.

Review `summary.json` and every non-successful strategy before committing. The
summary's `no-success` workloads are valid evidence when all declared strategies
have completed or timed out. They are not numerical throughput measurements.
For an interrupted sweep, rerun the original collection command before
finalizing; completed results and failure records are reused.

After pushing, open a results PR in GitHub, or with the GitHub CLI:

```bash
gh pr create --base main \
  --title "data: add Tsim GPU execution $CLIFFT_TSIM_EXECUTION" \
  --body "Add the Tsim GPU execution, including hardware provenance, warm tuning, raw timing samples, and failure records."
```

Confirm the branch and results are visible in GitHub, then stop the rental.

## Local tests without CUDA

```bash
uv sync --locked --python 3.12 --extra test
JAX_PLATFORMS=cpu .venv/bin/python -m pytest -q
```

These test the real pinned Tsim API on tiny circuits plus process timeouts,
resume caching, strategy selection, and count reduction. They do not validate
CUDA installation or performance. There is no production CPU fallback option.
