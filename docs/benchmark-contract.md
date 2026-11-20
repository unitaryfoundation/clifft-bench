# Benchmark contract

This contract governs the recurring CPU campaigns under
`campaigns/`. One-off studies under `experiments/` define their own contracts.

## Question and logical work

For the same versioned circuit and output contract, how many independent
attempted shots per second can a simulator execute within its declared physical-core budget?
Report throughput after the first full sampling call, without assuming caches
have fully settled.

One attempted shot is one complete circuit execution. Throughput always uses
attempted shots as the numerator. Each canonical workload declares
`postselect_modes`; each case chooses `execution.postselect`:

- **Postselection on — survivor counts:** return attempted, accepted, discarded,
  and selected-observable logical-error counts. `postselect_all_detectors=true`
  selects every detector; otherwise `postselection_detector_indices` selects a
  nonempty sorted unique subset. Selected raw detector parities must be zero.
- **Postselection off — packed detector and observable samples:** return the native
  `(detectors, observables)` tuple of little-endian `b8` arrays, with `uint8` dtype,
  one row per attempted shot, and `ceil(num_columns / 8)` bytes per row. Ignore
  the workload's selection mask. No Python parity correction, filtering, or
  count reduction occurs in the timed call. Validate shapes/dtypes afterward.

Both endpoints use raw measurement-record parity, without reference correction.
They compare complete available workflows; their difference does not isolate
postselection cost. Clifft, SymFT, Tsim, and xtim have native counts endpoints.
Clifft, Tsim, and xtim support detector subsets. Clifft 0.14, Tsim, Stim,
and xtim support packed sampling. Stim and xtim require verified raw
reference parity for their reference-relative output.
An unavailable xtim reference circuit, unsupported endpoint, or unsupported
thread budget is recorded as `unsupported`. Other setup rejections fail the
command so unexpected coverage losses are visible in CI.

## Release workload selection

The report follows the resolved campaign's workload selections and comparison
groups. Match circuit bytes, selection semantics, public call sizes, measurement
settings, and resource limits within each comparison. A new workload without a
supported previous-release baseline is current coverage, with no release ratio.
The representative suite may change between campaigns.

The [corpus notes](../workloads/README.md) own circuit provenance and validation;
[coverage notes](../workloads/coverage.md) describe repeated QV sampling,
packed sampling and full d5 detector subsets, and identify the separate unverified d7 proxy.
Neither cultivation stress input estimates decoded logical error or fidelity.
Repeated QV sampling measures a different question from the optional
[setup-plus-one-shot QV study](../experiments/qv/README.md).

Reports distinguish recorded failures, unsupported adapter pairings, and
unmeasured combinations. Failed cases have no plotted throughput.

The throughput figure combines Clifft and peer rates, grouped into magic-state
preparation; surface-code memory and coherent noise; and arithmetic, code
switching; and Quantum Volume. Each theme has an independent logarithmic axis
with its own throughput ticks. Horizontal positions are comparable within a panel;
absolute labels remain comparable across panels. Within each theme, circuits follow a stable
family and size order, independent of measured speed. Each rate is directly
labeled; short circuit names and consistent simulator colors and shapes aid
comparison. Each postselection mode and physical-core budget has its own panels. The unverified
cultivation proxy and bulk-call variants stay in the detailed tables rather than the main figures.
Unsupported pairings have no marker; failed and unmeasured cases are labeled.
Every selected case and declared comparison remains in the review tables.
Call sizes and postselection modes are in `cases.csv`; exact masks remain in `raw.json`.

Release figures show changes within ±50% on a linear percentage axis and larger
changes on an adjacent logarithmic ratio axis sharing the same rows. This display
threshold keeps modest changes visible alongside large gains or losses; it is not
a significance threshold. The percentage panel shades the ±1.1% inconclusive band
defined below.
Both figures use the same circuit selection and ordering; the release figure
includes only measured pairs. New pairs automatically appear in their circuit's
thematic position when the recorded campaign includes compatible measurements.
Exact identities and unpaired outcomes remain in the review tables and `cases.csv`.

### Throughput widget data

`report/simulator-throughput.json` is a derived presentation of the same validated
throughput view as the PNGs, including explicitly selected retained peers. Its
`format` is `clifft-bench/throughput/v2`. It is regenerated from raw evidence;
consumers must not treat it as a second authoritative result or join campaigns
themselves. This is the data interface for the
[Clifft docs widget](https://github.com/unitaryfoundation/clifft/issues/659).

- `panels` and their `rows` preserve the figure's selection and order. Each panel
  has its own logarithmic `x_limits`. Rows identify the workload and baseline
  variant, display label (including multi-core budgets), circuit digest, and
  selection `semantics` and explicit `postselect` endpoint.
- Each row's `values` follows the simulator order in `series`. Values include
  the attempted-shot rate median and median absolute deviation (both in shots/s),
  sample count, shots per call, effective batch size, `threads_requested`,
  `threads_effective`, `compile_seconds`, `first_call_warmup_seconds`, and peak
  active width where recorded. For each measured eight-core point,
  `eight_core_speedup` divides its median attempted shots/s by the matching
  one-core median in the same source execution. Both cases must succeed and
  uniquely match in implementation, circuit, output, call size, batching,
  simulator options, memory limit, and measurement settings. Other values are null.
  MAD describes sample dispersion. It does not estimate a confidence interval.
- `outcome` is `measured`, a recorded failure status, `unsupported`, or
  `not measured`. Failed and absent cases have null rates and dispersion; missing
  metadata is null rather than zero. Unsupported cases remain in the export for
  accessible explanation but have no plotted marker. Recorded failures retain
  their error detail. Missing cases have null case/source identifiers.
- `series` supplies variant and exact simulator identities, semantic marker
  names (`circle`, `square`, `diamond`, `triangle`, filled `plus`), and light/dark
  colors. `themes` supplies the matching background, foreground, and grid colors.
- Every recorded value references `sources.primary` or `sources.peers`. Each
  source retains its run identity, collection times, classification, selection,
  overrides, harness revision, host, hardware epoch, measurement settings, and
  resource limits. A smoke or partial source remains validation evidence.
  Separate collections are not represented as a fresh paired run.

The timing boundaries below also apply to this export. Publication supplies
immutable evidence links for each source run; the export does not guess evidence URLs.

## Timed boundaries

Each case uses one fresh worker and one measurement sampler:

1. `compile_seconds`: parse in-memory circuit text and compile the sampler.
   Imports, file reads, installation checks, and reference-parity validation are
   excluded; `setup_seconds` retains the full preparation duration.
2. `first_call_warmup_seconds`: time the first full `shots_per_call` call,
   including lazy planning, JIT/cache loading, and sampling. No earlier sampling
   call uses the measurement sampler. Retain this result separately from throughput.
3. Check correctness, then measure repeated calls using the same sampler.
   Each repetition accumulates calls until its minimum sampling interval elapses;
   the final call can overrun it. Retain individual samples and derive median/MAD.

Compilation, first-call time, call size, and subsequent throughput are reported
separately; do not extrapolate finite-shot turnaround. Native output allocation
and the on endpoint's count reduction remain inside sampling timers. Correctness checks are untimed for
throughput. Deadlines apply to every phase; failures retain their phase and
completed evidence without a throughput value.

The worker keeps in-memory caches across calls. xtim's plan cache is in memory.
Tsim retains its upstream Numba disk cache. These are process-fresh measurements,
not guaranteed cold-disk builds or fully converged steady state.

## Resources and ordering

The reference host is an On-Demand AWS `m7a.4xlarge` with shared tenancy,
Ubuntu 24.04 on x86-64, and 16 physical cores. A single placement supplies all
fresh release and peer measurements. The worker uses one or eight physical cores.
The remaining cores provide headroom for collection and the operating system.
The existing ±1.1% reporting band comes from the earlier
[host commissioning study](https://github.com/unitaryfoundation/clifft-bench/pull/19)
on the `m7a.xlarge`. The new placement has its own timing dispersion.

Do not combine absolute rates from different hardware epochs without labeling
the boundary or measuring a bridge. Shared CI hosts provide correctness and
exploratory evidence, not the official throughput series. Host setup and
collection commands belong in the [maintainer workflow](../CONTRIBUTING.md#reference-host-collection).

Cases execute serially in manifest order. Every case gets a fresh worker
process using the Python environment recorded for its implementation.
`execution.threads` is 1 (default) or 8. Before imports, workers set
`OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` to that budget; BLAS, NumExpr,
Accelerate, and XLA remain serial. Clifft, Tsim, xtim and SymFT's legacy counts backend
support both budgets. Stim supports one core. Unsupported thread budgets are
recorded before resource allocation.

Before imports, Linux workers pin one logical CPU per distinct `(socket, core)`
within the allowed mask, starting at `--cpu`. Setup fails if topology is missing
or too few cores are available. Eight-core runs require applied affinity;
single-core smoke runs may record unavailable affinity.

Thread settings are recorded per worker: environment, CPU mask, physical-core
identities, and requested/effective counts. Effective counts describe the native
pool or plan, or are null when unreported (including multi-core Clifft 0.13.0).
Release/peer ratios require equal core budgets.

Official workers receive a 32 GiB address-space ceiling on the 64 GiB reference host. The requested ceiling is embedded in the
raw result, the applied `RLIMIT_AS` is recorded after setup, and input validation
rejects a mismatch.

The manifest seed must be at least 1. Warmup uses `seed - 1`, correctness uses
`seed`, and execution repetition `r` begins at
`seed + 10_000 + r * 100_000_000`; repeated public calls increment that value
by one. The worker enforces the 100-million-call stride before a stream
identifier can overlap the following repetition, and the harness rejects
repetition counts whose reserved ranges exceed the unsigned 32-bit seed space.
These non-overlapping ranges keep every phase deterministic for adapters that
expose per-call streams.

Stim and Tsim are seeded at compilation with `seed - 1` and advance one native
stream through the first call, correctness, and repetitions. Their per-call seed
fields are audit identifiers. Other adapters retain their public per-call reseeding.

These fixed streams exist only to make performance runs replayable and
auditable. They are not a seeding recommendation for scientific simulation or
statistical inference, where independent seeds should be drawn from operating-
system entropy backed by hardware entropy when available.

## Batching

Maintained campaigns set `batch_size="auto"`; there is no harness search.
Adapters request Clifft's `"auto"`, Tsim's `None`, and SymFT's `batch=True`,
`batch_size=0`, `sample_chunk_shots=0`. Stim and xtim receive each full public call
without harness chunking. Positive integer overrides remain available for focused
checks; they specify internal capacity for Clifft/SymFT/Tsim and output chunks for
xtim's counts endpoint. Its packed endpoint receives the complete public call. SymFT uses `cpu_backend="legacy"` for both core budgets.

`batch_size_effective` records a reported native capacity, capped at the public
call size, or null if unavailable. Automatic capacity is not inferred from the
request. The campaign uses one `shots_per_call` per circuit across both outputs,
physical-core budgets and compared implementations.

## Compiler configuration

Clifft 0.14 uses its public default compiler, including automatic scheduling and
reconstruction. Survivor counts compile with the raw-zero selection mask and
call `sample_survivors(..., keep_records=False)`. Packed sampling compiles
with no selected detectors and calls `sample(..., keep_records="syndromes",
bit_packed=True)`. The adapter records peak width and reconstruction from
the sampling plan for the requested outputs. The first full call supplies the
native executor, sharing decision, batch capacity, and shot and intra-shot
worker counts. Their product is the effective thread count for that call.
The smaller correctness call can select a different configuration.

The 0.13 baseline uses the opt-in `ActiveWidthSchedulePass` after its default
pipeline, with the `clifft-scheduled` preset from
[`software.toml`](../software.toml).
Use the same settings across the release workloads. The four pass options must
be explicit; the benchmark supplies no defaults. Scheduling is disabled when the
resolved `execution.options` has no `scheduler` table. Unsupported installations
fail when the pass is requested.

The 0.13 adapter sets the HIR `postselection_mask` before optimization and
supplies the same mask to lowering. Its runtime metadata includes
`postselection_before_optimization`, `batch_control`, and, when available,
`optimized_t_gates`. The 0.14 adapter records the output-specific plan and
the worker layout reported by the first full call.

With `execution` containing the resolved options, the public Python configuration is:

```python
manager = clifft.default_hir_pass_manager()
manager.add(clifft.ActiveWidthSchedulePass(**execution["options"]["scheduler"]))
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

The compiled detector sampler returns reference-relative flips. Setup converts
its noiseless measurement reference to raw detector/observable parities. Any
nonzero parity makes the case unsupported. With zero references, return the
native packed tuple directly, using one persistent sampler and one full call.
Stim has no native postselection endpoint, so on is unsupported. It requires
`batch_size="auto"`; explicit capacities are rejected rather than ignored.

Use the official pinned Stim 1.16.0 wheel and record the loaded `stim._stim_*`
native extension. Its x86 Python dispatch selects SSE2, even on AVX2-capable
hosts: upstream's [Python build](https://github.com/quantumlib/Stim/blob/v1.16.0/setup.py)
disables AVX2 pending [Stim issue #432](https://github.com/quantumlib/Stim/issues/432).
Retain upstream's build configuration. These measurements describe the official
release wheel; the recorded extension identifies the SIMD implementation used.

## xtim comparison

Compile xtim 4.1.2 with `compile_twirl_sampler(text, p_factor=1.0, selfcheck=0)`.
The sampler owns its in-memory plans and receives a fresh seed and the worker's
thread budget per call. Its first full call reports the effective worker count.

For survivor counts, every selected detector and the counted observable must
have deterministic zero raw parity in the noiseless circuit. Packed sampling
requires this for every detector and observable. The adapter checks the
participating channels against xtim's public `record_report()`.

Stim-compatible inputs use Stim's noiseless reference sample and raw
record-to-detector/observable converter. For MSC d3, MSC d5, and RM15, the
workload semantics declare `raw_zero_reference` per endpoint, with Clifft as
the independent sign check. CI samples raw parities from the noiseless circuit
with postselection disabled. The declaration applies to the artifact SHA-256;
a digest mismatch or loss of a declared reference fails the run. Extended
circuits without a verified reference for the requested endpoint report
`unsupported`.

Counts use xtim's native Boolean selection mask. Validate attempts, passes,
discards and early rejections and count the selected observable over survivors.
Packed sampling returns the native little-endian detector and observable arrays.

The source pin and portable build settings live in `software.toml` and its
locked environment. Verify the installed PEP 610 repository and commit before
setup.

## Correctness and identity

The worker checks circuit metadata and validates each returned result outside
the sampling timer. Counts require attempted = accepted + discarded and logical
errors between zero and accepted. Packed output requires the declared byte
widths, uint8 dtype, and every attempted row; raw evidence retains per-call
shapes instead of invented counts.

Before compilation, the SymFT adapter verifies the installed package's PEP 610
Git commit and repository URL against the manifest and records both in runtime
metadata. A PyPI wheel with the same version string is rejected. SymFT uses its
legacy CPU counts executor with native automatic sizing. Its opt-in compiled CPU
executor changes the effective batch capacity and is outside this configuration.

Each implementation records its exact package version, source commit, optional
release tag, build settings, actual worker interpreter, and dependency versions.
An optional `display_version` provides the intended public release label for plots and
prose. It has no role in installation or runtime validation: a `0.10.0rc1`
candidate built from `v0.10.0rc1` can display as `0.10.0`, while the raw result
continues to identify and verify the RC version, tag, and commit. When omitted,
the exact version is also the display version. Each workload records an
immutable artifact digest, semantic contract, and source provenance. An
incompatible adapter/workload pairing is rejected before execution.

## Placements and official evidence

The release campaign emits one raw result containing all variants from one
reference-host collection. Persisted official evidence requires complete case
coverage, one clean source commit, the reference instance type, applied CPU
affinity, and the declared memory ceiling.

`campaigns/smoke.toml` is only a short developer correctness check. A
release execution becomes official evidence after validation and review of
its results change.


## Tsim active-statevector CPU comparison

`tsim-vs-current` uses the Tsim 1.0.0 source identified in
[`software.toml`](../software.toml). The adapter checks the installed Git URL
and complete commit, so installing a same-version release wheel fails
verification. Dependencies, including Numba/LLVM and zxparam, are locked in
[`environments/tsim-1.0.0.txt`](../environments/tsim-1.0.0.txt).

Compilation explicitly requests `strategy="active_statevector"`; trajectory
sharing is always enabled upstream. This is a complex128 CPU implementation;
it does not need CUDA. The adapter verifies that Numba's pool matches the case's
thread budget. Resource limits and preparation/request deadlines also apply.
There is no fallback to a different Tsim strategy when preparation or sampling fails.

Tsim requests little-endian packed output with both reference-sample flags false.
On passes the workload's native detector mask, then counts the selected observable
bit over survivors inside the timer. Off passes no mask and returns the native
tuple directly. Both paths retain the requested attempted-shot denominator.

Tsim's automatic batch capacity limits attempted shots per thread; rejection and
sharing can reduce statevector work. `batch_size_applicable` records whether the
compiled program has statevector components. Raw metadata also records component
widths, strategy, precision, seed policy, and threads. No preparation sample is
performed; first-use kernel compilation/cache loading belongs to the first call.

## Result evidence and comparisons

A new execution contains `raw.json` in format `clifft-bench/raw/v2` and one derived
`cases.csv`. Raw evidence records the resolved selected campaign and comparison
definitions, effective measurement/resource settings, collection time and source,
exact software identities, workload digests and semantics, setup/compilation,
first-call/correctness outcomes, individual samples, and structured
failures. Local selection and overrides are recorded. Results do not depend on
current configuration files for interpretation. Interrupted or failed workers
retain completed samples and diagnostics; incomplete nonterminal evidence cannot
be reported as a completed execution.

CSV regeneration validates raw evidence and computes each successful case's
sample median and median absolute deviation. Failed rows retain sample counts and
error/setup/memory diagnostics while throughput summaries stay blank. Detailed
adapter options stay in raw JSON. The report regenerates
this table from raw evidence, so a stale CSV cannot change a performance claim.
There is no separately stored comparison table, execution index, or release chain.

A Clifft-only refresh may explicitly reuse unchanged peers from one prior
execution for the throughput figure. Both raw files remain separate and
self-contained; no synthetic combined execution or pooled samples are written.
The primary execution supplies every Clifft value and all paired release ratios.
The peer source supplies only non-Clifft values from its declared Clifft
comparisons. Missing, unsupported, and failed peer outcomes remain distinct.
Reuse requires matching classification, hardware epoch/reference host, host CPU,
OS/Python/thread settings, measurement protocol, seeds, and resource limits.
Compared workloads must also pass the digest, selection, and call-size checks
below. Incompatible or ambiguous sources are rejected. Reports identify the peer
run, collection time, source revision, and exact software versions in the
companion review. These cross-collection absolute rates are
not a fresh paired comparison and do not acquire the paired 1.1% interpretation.

Paired ratios use candidate median divided by baseline median. Pairing requires
matching workload digest and selection semantics, call size, measurement settings,
and resource limits. The endpoint (`postselect`) must also match, including for
explicit peer reuse. The raw format fixes raw-record parity and attempted-shot
throughput for every case. Internal batches may differ. Declared
comparison groups pair measurements within each output mode and core budget.
Only successful compatible pairs receive ratios. Missing expected case records
are errors; recorded failures, unsupported adapters, and intentionally unmeasured
combinations remain distinct. A new current workload without a previous baseline
can appear in throughput plots without a release-change value.

Archived files retain their original formats and use the
[pinned legacy tools](../CONTRIBUTING.md#historical-evidence). They are not migrated
or required by the recurring report. Hardware or workload changes must be labeled
in any optional long-term analysis.
