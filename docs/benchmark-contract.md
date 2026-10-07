# Benchmark contract

This contract governs the official single-core QEC campaigns under
`campaigns/`. One-off studies under `experiments/` define their own contracts.

## Question and logical work

For the same versioned circuit and output contract, how many independent
attempted shots per second can a simulator execute on one pinned logical CPU?
The default is steady-state sampling. For xtim, report the fixed-duration average
starting with an empty plan cache, as defined below.

One attempted shot is one complete circuit execution. Each simulator returns
aggregate counts for attempted shots, detector-postselected discards, accepted
shots, and observable-0 logical errors among accepted shots. Throughput always
uses attempted shots as the numerator.

Every workload declares `reference_convention` as `raw-record-parity`.
Detector events and logical errors are the XOR parity of their declared
measurement records without a noiseless-reference correction. Clifft and SymFT
return native aggregate counts. Stim and xtim's public detector samplers return packed
records, which the adapter reduces to the same counts; both materialization and
reduction are included in its sampling time. This is a comparison of available
aggregate endpoints, not identical internal work or a decoder benchmark.

## Release workload selection

The QEC cases in the recurring release campaign and figures cover cultivation
d3 and d5, 85q distillation, Clifford surface code d7/r7, and coherent surface code d3/r3
and d5/r5. The coherent memory workloads use repeated syndrome-extraction
rounds. One-off experiments define their own workload sets.

QEC reporting uses this same workload set across all plotted releases and
computes the median at each version.

The Clifft release comparison also includes `draper-adder-m16-basis`, a
fixed-input 16-bit addition on 32 qubits without noise or postselection.
Each attempted shot executes the entire addition. Its results describe prepared
basis inputs and are reported separately from the historical QEC figures and medians.

The campaign also includes `distillation-15to1-rm15-p1e-3` for both Clifft
releases, SymFT, and xtim. It retains xtim's 15-to-1 Reed-Muller preparation,
T-gate phase noise at p=0.001, and four postselected X checks. An ideal inverse-T
logical-X readout replaces expectation declarations so observable 0 measures
accepted logical phase errors under the shared raw-parity contract. Preparation,
checks, and verification are noiseless; the verification gates are timed.
Its measurements are included in the finalized result tables. Plot selection is
deferred until the next results are collected. See the
[corpus notes](../workloads/README.md) for provenance and validation.

Use the existing `cases.csv` compilation and throughput columns when reporting
the adder. For a stated shot count N, `compile_seconds + N / rate` estimates
compilation plus steady-state sampling, where rate is
`median_attempted_shots_per_second`. This estimate excludes parsing, calibration,
and warmup; `setup_seconds` records the full measured setup separately.

## Timed boundaries

| Phase | Timed as execution? |
|---|---:|
| Installation and import | No |
| Parse, plan, compile, and sampler setup | No; recorded separately |
| Lazy plan construction during sampling | Yes |
| Warmup | No; recorded separately |
| Correctness check | No; recorded separately |
| Repeated sampling calls, transfer and aggregate reduction | Yes |
| Validation and result writing | No |

Each sample runs until its accumulated public-call time reaches the manifest's
minimum interval. The final call may extend the sample beyond that interval.
Individual samples are retained; median and median absolute deviation are
derived conveniences.

A request deadline prevents a stalled simulator from blocking the campaign.
Setup, warmup, correctness, and sampling failures are recorded with their phase
instead of being converted into throughput values.

## Resources and ordering

Cases execute serially in manifest order. Every case gets a fresh worker
process using the Python environment recorded for its implementation. The
worker requests one logical CPU and receives a single-thread environment:
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMEXPR_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, and `BLIS_NUM_THREADS` are 1.

Linux affinity is applied with `sched_setaffinity` and the outcome is recorded.
Official workers receive the campaign's 12 GiB address-space ceiling, leaving
headroom on the 16 GiB reference host. The requested ceiling is embedded in the
raw result, the applied `RLIMIT_AS` is recorded after setup, and finalization
rejects a mismatch.

The manifest seed must be at least 1. Warmup uses `seed - 1`, correctness uses
`seed`, and execution repetition `r` begins at
`seed + 10_000 + r * 100_000_000`; repeated public calls increment that value
by one. The worker enforces the 100-million-call stride before a stream
identifier can overlap the following repetition, and the harness rejects
repetition counts whose reserved ranges exceed the unsigned 32-bit seed space.
These non-overlapping ranges keep every phase deterministic for adapters that
expose per-call streams.

Stim accepts its seed when compiling a sampler. Its adapter starts one continuous
native stream per phase or repetition and reuses that sampler across all timed
calls. Stream initialization is excluded and recorded as `stream_setup_seconds`;
per-call seed fields remain audit identifiers, not independent Stim seeds.

Batch calibration uses the next four million stream identifiers, divided into
one range for each probe repetition and one for candidate warmup.

These fixed streams exist only to make performance runs replayable and
auditable. They are not a seeding recommendation for scientific simulation or
statistical inference, where independent seeds should be drawn from operating-
system entropy backed by hardware entropy when available.

## Batching

A throughput case may set `batch_size` to `"calibrate"`. During setup, the
worker:

1. keeps the candidates from `1`, `32`, `256`, `1024`, and `2048` that do not
   exceed `shots_per_call`, treating `1` as scalar execution;
2. prepares and warms each candidate;
3. runs three one-second probes and computes median attempted-shot throughput;
4. selects the highest median, breaking an exact tie toward the smaller size;
5. freshly prepares the selected configuration and uses it for all timed
   repetitions.

Clifft and SymFT use the same procedure. Stim calibrates public sampler chunks
of 256, 1024, 4096, 16384, and 65536 shots, capped by `shots_per_call`, plus an
unchunked call. Its compiled circuit detector sampler is reused across calls;
packed detectors/observables are reduced with vectorized NumPy operations.
Calibration is setup work and is not
included in final throughput samples. Raw results record candidate probes,
failures, the selected size, and total calibration duration in
`setup.runtime_metadata.batch_calibration`.

Successful results replace `"calibrate"` with the selected numeric `batch_size`.
They also record `batch_size_effective`, the maximum lanes available to one
public call after capping the selected capacity by `shots_per_call`. A fixed
numeric batch size remains supported for cases that do not request calibration.

For Clifft/SymFT, `batch_size` is the internal number of shots processed together.
For Stim it is the adapter's chunk size passed to the public detector sampler,
not a claim about Stim's SIMD width.
`shots_per_call` is the number requested from one public API call. Both are
recorded because changing either can change amortization.

The recurring campaign treats `shots_per_call` as a workload measurement
parameter, chosen to produce meaningful throughput samples and kept identical
across tools, releases, and batching modes. Batch calibration then selects an
internal optimization for that fixed public call. The active-wide coherent
d5/r5 workload intentionally retains one shot per call; its calibrated cases
therefore consider only candidate `1` and record a scalar selection.

Derived rows carry both sides' mode, effective batch size, and shots per call.

The recurring `current-vs-previous` comparison asks whether Clifft improved
using the capabilities available in each release. It applies calibration to
both releases, allowing an older release without batching support to select
scalar execution after unsupported candidates fail. The
`alternatives-vs-current` comparison applies the same calibration policy to
current Clifft and SymFT across the release workloads. The separate
`stim-anchor-vs-current` comparison pairs current Clifft with Stim on the
compatible surface-code workload. Keeping the anchor separate preserves the
existing Clifft/SymFT comparison identity and its downstream consumers. All
comparisons, including `xtim-vs-current`, reuse the same collected current-Clifft cases.

## Compiler configuration

Use the standard opt-in `ActiveWidthSchedulePass` after Clifft's default
pipeline. When pinning a scheduler-capable RC, set `clifft-current.execution`
to the checked-in
[`clifft-scheduled-execution.json`](../campaigns/release-v1/clifft-scheduled-execution.json).
Use the same settings across the release workloads. The four pass options must
be explicit; the benchmark supplies no defaults. Omit `clifft_scheduler` to
disable scheduling. Unsupported installations fail when the pass is requested.

With `execution` set to that JSON object, the public Python configuration is:

```python
manager = clifft.default_hir_pass_manager()
manager.add(clifft.ActiveWidthSchedulePass(**execution["clifft_scheduler"]))
manager.run(hir)
```

Raw execution records contain the explicit scheduler options. Runtime metadata
records whether the pass changed the schedule, its width/work estimates and
search counters, compilation time, and the final program's width. Finalization
requires scheduler statistics for successful cases that requested the pass.

## Stim compatibility and fast configuration

Only `surface-code-d7-r7-p1e-3` is Stim-compatible in this QEC corpus. All other
circuits contain non-Clifford instructions. The adapter passes the immutable
file directly to `stim.Circuit`: no twirling, gate removal, detector error model
conversion, or replacement circuit. Unsupported pairings are rejected by the
workload manifest before collection.

The compiled circuit detector sampler returns reference-relative flips. During
setup we convert its noiseless measurement reference to raw detector/observable
parities, then XOR these packed offsets into each sample before postselection.
Tests cover nonzero offsets, noise, observable indices beyond the first byte,
and chunk tails. The official circuit has deterministic detectors.

Use the official pinned Stim 1.16.0 wheel and record the loaded `stim._stim_*`
native extension. Its x86 Python dispatch selects SSE2, even on AVX2-capable
hosts: upstream's [Python build](https://github.com/quantumlib/Stim/blob/v1.16.0/setup.py)
disables AVX2 pending [Stim issue #432](https://github.com/quantumlib/Stim/issues/432).
Retain upstream's build configuration and select the best measured packed-record
chunk size on the reference host. These measurements describe the official
release wheel; the recorded extension identifies the SIMD implementation used.

## xtim comparison

`xtim-vs-current` reuses the current Clifft cases on cultivation d3/d5,
Clifford surface code d7/r7, and 15-to-1 distillation. Unsupported rotations remain
outside xtim's domain.
Separate comparison identities preserve the paired ratios in the result tables.
The existing plots remain unchanged; presentation of the new results is deferred
until collection and review.

Use `compile_twirl_sampler(text, selfcheck=0, disk_cache=False)` and reseed
in place once per harness call. Internal chunks continue that call's stream
with `seed=None`. Do not use `Circuit.compile_detector_sampler()` for repeated
calls: the pinned version replays its compile seed. Reject gauge/anti detectors,
refused observables, and any declared observable not certified deterministic in
the noiseless circuit. The pinned twirl API also requires a detector channel.
`PAULI_EXPECTATION` is outside this suite's aggregate-count contract.

The sampler returns reference-relative bits. Setup obtains an exact noiseless
raw measurement trajectory from xtim and converts the declared record parities
using Stim's record converter. The converter sees a layout-only copy with the
extended unitary gates replaced by identities; all simulated physics comes from
the original immutable circuit in xtim. The packed offsets are XORed into each
sample before postselection. Metadata counts source qubits, with xtim's internal
ancilla-expanded count recorded separately as `native_num_qubits`.

Each repetition compiles a fresh sampler before timing, using the same
`begin_sample` boundary as Stim. No plan cache carries over from warmup,
correctness checks, calibration, or a previous repetition. Sampling retains the
cache across calls within the normal 30-second measurement window; lazy plan
construction is timed, while compilation is recorded as `stream_setup_seconds`.
These rates describe a fixed-duration run starting with an empty cache, not
established steady-state throughput. The final call can overrun the window.

xtim has no public cache eviction API. Retain the existing 12 GiB address-space
limit and check process peak RSS on the reference host. Raw samples record
`peak_rss_bytes`, the worker's lifetime high-water mark including setup and
calibration; `cases.csv` exposes its maximum. Resetting each repetition limits
cache lifetime but does not guarantee a particular memory footprint. Chunk
calibration uses the normal short probes and Stim's candidate sizes; its cache
warmup horizon is shorter than the release measurement window.

Pin xtim 3.1.4 to source commit `29454071fb20fbcffd258559ad0644676e44a8f9`.
Use `QECCORE_PORTABLE=1` (`-O3 -funroll-loops`, without `-march=native`) to
retain the portable build policy of Clifft's release wheels. Record
`CXXFLAGS=-include cstdint`, the pinned source's GCC missing-include workaround.
The adapter verifies the installed PEP 610 repository and commit before setup.

## Correctness and identity

Before timing, the worker checks circuit qubit, measurement, detector, and
observable counts plus these aggregate invariants:

- attempted = accepted + discarded;
- logical errors are between zero and accepted shots; and
- non-postselected workloads discard no shots.

Before calibration, the SymFT adapter verifies the installed package's PEP 610
Git commit and repository URL against the manifest and records both in runtime
metadata. A PyPI wheel with the same version string is rejected. The current
`haoliri0/SOFT` pin replaces a snapshot from `haoliri0/SymFT_Test`; those are
separate Git histories, so the two snapshots are identified by repository and
commit rather than treated as an ancestor/descendant update.

Each implementation records its exact package version, source commit, optional
release tag, release time, build features, and dependency versions. An optional
`display_version` provides the intended public release label for plots and
prose. It has no role in installation or runtime validation: a `0.10.0rc1`
candidate built from `v0.10.0rc1` can display as `0.10.0`, while the raw result
continues to identify and verify the RC version, tag, and commit. When omitted,
the exact version is also the display version. Each workload records an
immutable artifact digest, semantic contract, and source provenance. An
incompatible adapter/workload pairing is rejected before execution.

## Placements and official evidence

The release campaign emits one raw result per placement and replica, containing
all variants. The recurring release campaign uses one reference-host placement.
Finalization requires the declared coverage, one clean source commit, the
reference instance type, one reference instance, distinct boot IDs when a
campaign requests multiple placements, and the declared memory ceiling.

`manifests/run-smoke.v1.json` is only a short developer correctness check. A
release execution becomes official evidence after finalization and review of
its results change.
