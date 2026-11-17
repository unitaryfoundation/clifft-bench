# Benchmark contract

This contract governs the recurring CPU campaigns under
`campaigns/`. One-off studies under `experiments/` define their own contracts.

## Question and logical work

For the same versioned circuit and output contract, how many independent
attempted shots per second can a simulator execute within its declared physical-core budget?
Report throughput after the first full sampling call, without assuming caches
have fully settled.

One attempted shot is one complete circuit execution. Each simulator returns
aggregate counts for attempted shots, detector-postselected discards, accepted
shots, and observable-0 logical errors among accepted shots. Throughput always
uses attempted shots as the numerator.

`postselect_all_detectors=true` requires every raw detector parity to be zero.
With that flag false, optional `postselection_detector_indices` selects a
nonempty, sorted, unique list of zero-based detectors; only those parities must
be zero. Without a subset, false disables rejection. Subsets and the all-detector
flag are mutually exclusive. The raw result retains the exact indices and records
`execution.postselection=true` for either selection mode. Input and report
validation check the selection fields and execution flag. Clifft and Tsim
implement subsets; other adapters explicitly reject them. Tsim's subset-column materialization
and count reduction are included in the sampling time.

All supported workloads use raw-record parity; this is a fixed harness contract.
Detector events and logical errors are the XOR parity of their declared
measurement records without a noiseless-reference correction. Clifft returns
native aggregate counts. SymFT uses native counts for selected or detector-free
workloads. For unselected workloads containing detectors, it samples packed
measurement records and reduces the selected observable's raw parity across all
shots. SymFT's counts API always excludes detector-fired shots; its
`postselect_detectors` argument only enables early termination. The raw-record
path records its backend and materialization
in runtime metadata, and includes record allocation and parity reduction in the
timed call. Its batch API ignores `sample_chunk_shots`; metadata records that
distinction and the requested value. Stim and xtim's public detector samplers return packed
records, which the adapter reduces to the same counts; both materialization and
reduction are included in its sampling time. This is a comparison of available
aggregate endpoints, not identical internal work or a decoder benchmark.

## Release workload selection

The report follows the resolved campaign's workload selections and comparison
groups. Match circuit bytes, selection semantics, public call sizes, measurement
settings, and resource limits within each comparison. A new workload without a
supported previous-release baseline is current coverage, with no release ratio.
The representative suite may change between campaigns.

The [corpus notes](../workloads/README.md) own circuit provenance and validation;
[coverage notes](../workloads/coverage.md) describe repeated QV sampling,
selection-off controls, the unverified d7 proxy, and full d5 detector subsets.
Observable-0 counts on unselected circuits are raw ones, not logical errors.
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
comparison. Selection-off controls, the unverified cultivation proxy, and
bulk-call variants stay in the detailed tables rather than the main figures.
Unsupported pairings have no marker; failed and unmeasured cases are labeled.
Every selected case and declared comparison remains in the review tables.
Call sizes are in `cases.csv`; postselection settings remain in `raw.json`.

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
`format` is `clifft-bench/throughput/v1`. It is regenerated from raw evidence;
consumers must not treat it as a second authoritative result or join campaigns
themselves. This is the data interface for the
[Clifft docs widget](https://github.com/unitaryfoundation/clifft/issues/659).

- `panels` and their `rows` preserve the figure's selection and order. Each panel
  has its own logarithmic `x_limits`. Rows identify the workload and baseline
  variant, display label (including multi-core budgets), circuit digest, and
  selection/output `semantics`.
- Each row's `values` follows the simulator order in `series`. Values include
  the attempted-shot rate median and median absolute deviation (both in shots/s),
  sample count, shots per call, effective batch size, `threads_requested`,
  `threads_effective`, `compile_seconds`, `first_call_warmup_seconds`, and peak
  active width where recorded. MAD describes sample dispersion, not a confidence
  interval.
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
   including lazy planning, JIT/cache loading, and sampling. No earlier call uses
   the measurement sampler. Retain this result separately from throughput.
3. Check correctness, then measure repeated calls using the same sampler.
   Each repetition accumulates calls until its minimum sampling interval elapses;
   the final call can overrun it. Retain individual samples and derive median/MAD.

Compilation, first-call time, call size, and subsequent throughput are reported
separately; do not extrapolate finite-shot turnaround. Allocation and count
reduction remain inside sampling timers. Correctness checks are untimed for
throughput. Deadlines apply to every phase; failures retain their phase and
completed evidence without a throughput value.

The worker keeps in-memory caches across calls. xtim's disk cache is disabled;
Tsim's upstream Numba disk cache is retained. These are process-fresh measurements,
not guaranteed cold-disk builds or fully converged steady state.

## Resources and ordering

Official results use the campaign's declared hardware epoch: a shared-tenancy,
On-Demand AWS `m7a.xlarge` with Ubuntu 24.04 on x86-64. Use one placement for a
fresh paired release comparison. The [host commissioning study](https://github.com/unitaryfoundation/clifft-bench/pull/19)
found paired identical-software differences below 0.72% across three placements;
absolute throughput varied by up to 2.60% on the slower workload. Treat paired
changes at or below 1.1% as inconclusive. One execution does not re-estimate
placement variability; studies that need that estimate use independent
stop/start placements.

Do not combine absolute rates from different hardware epochs without labeling
the boundary or measuring a bridge. Shared CI hosts provide correctness and
exploratory evidence, not the official throughput series. Host setup and
collection commands belong in the [maintainer workflow](../CONTRIBUTING.md#reference-host-collection).

Cases execute serially in manifest order. Every case gets a fresh worker
process using the Python environment recorded for its implementation.
`execution.threads` is 1 (default) or 8. Before imports, workers set
`OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` to that budget; BLAS, NumExpr,
Accelerate, and XLA remain serial. Clifft, Tsim, and SymFT's legacy counts backend
support both budgets. Stim, pinned xtim, and SymFT's unselected measurement path
support only 1; unsupported requests are recorded before resource allocation.

Before imports, Linux workers pin one logical CPU per distinct `(socket, core)`
within the allowed mask, starting at `--cpu`. Setup fails if topology is missing
or too few cores are available. Eight-core runs require applied affinity;
single-core smoke runs may record unavailable affinity.

Thread settings are recorded per worker: environment, CPU mask, physical-core
identities, and requested/effective counts. Effective counts describe the native
pool or plan, or are null when unreported (including multi-core Clifft 0.13.0).
Release/peer ratios require equal core budgets.

Official workers receive the campaign's 12 GiB address-space ceiling, leaving
headroom on the 16 GiB reference host. The requested ceiling is embedded in the
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
Stim/xtim. SymFT uses `cpu_backend="legacy"` for both core budgets.

`batch_size_effective` records a reported native capacity, capped at the public
call size, or null if unavailable. Automatic capacity is not inferred from the
request. Every comparison matches `shots_per_call`; short and bulk calls remain
separate workloads in the report.

## Compiler configuration

Each release uses its own default HIR pipeline before the optional scheduler.
Both maintained Clifft implementations use the scheduled configuration.
Release-over-release results include their compiler defaults; they are not a
same-version pass ablation.

Use the standard opt-in `ActiveWidthSchedulePass` after Clifft's default
pipeline. When pinning a scheduler-capable RC, set
`clifft-current.execution.options_preset` to `clifft-scheduled` from
[`software.toml`](../software.toml).
Use the same settings across the release workloads. The four pass options must
be explicit; the benchmark supplies no defaults. Scheduling is disabled when the
resolved `execution.options` has no `scheduler` table. Unsupported installations
fail when the pass is requested.

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

The compiled circuit detector sampler returns reference-relative flips. During
setup we convert its noiseless measurement reference to raw detector/observable
parities, then XOR these packed offsets into each sample before postselection.
Tests cover nonzero offsets, noise, observable indices beyond the first byte,
and chunk tails. The official circuit has deterministic detectors.

Use the official pinned Stim 1.16.0 wheel and record the loaded `stim._stim_*`
native extension. Its x86 Python dispatch selects SSE2, even on AVX2-capable
hosts: upstream's [Python build](https://github.com/quantumlib/Stim/blob/v1.16.0/setup.py)
disables AVX2 pending [Stim issue #432](https://github.com/quantumlib/Stim/issues/432).
Retain upstream's build configuration. These measurements describe the official
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
calls: the pinned version replays its compile seed. Use the 3.2.2 `record_report()`
API and require every detector and declared observable to be classified
deterministic in the noiseless circuit. The newer engine can serve additional
record classes, but those are outside this comparison's eligibility contract.
`PAULI_EXPECTATION` is outside this suite's aggregate-count contract.

The sampler returns reference-relative bits. Setup obtains an exact noiseless
raw measurement trajectory from xtim and converts the declared record parities
using Stim's record converter. The converter sees a layout-only copy with the
extended unitary gates replaced by identities; all simulated physics comes from
the original immutable circuit in xtim. The packed offsets restore raw parities.
Metadata counts source qubits, with xtim's internal
ancilla-expanded count recorded separately as `native_num_qubits`.

For postselected workloads with all-zero raw detector references, pass an all-true
boolean detector mask to `TwirlSampler.sample(..., postselect=mask)`. This enables
native early rejection before plan work when possible; the returned packed rows
contain only accepted shots. Read and validate the returned `attempts`, `passed`,
`discarded`, and `early` counters for each chunk. Throughput still counts every
attempted shot, including early rejects. Apply observable reference offsets and
reduce observable ones over accepted rows inside the sampling timer.

If any raw detector reference is one, retain unmasked sampling followed by raw
parity conversion and detector filtering in the adapter: the native mask selects
zero reference-relative bits and cannot express that raw-zero condition.
Unselected workloads also use unmasked sampling. Metadata records the actual
`postselection_backend` (`native-twirl-sampler`, `adapter-raw-record-parity`, or
`disabled`) and whether packed records contain only accepted shots. The four
enabled xtim workloads have zero raw detector references and use native filtering.
Regressions compare their counts with same-seed unmasked samples filtered afterward.
Do not use `record_report().used` as attempts or accepted shots; native early
rejection and later filtering give that counter different semantics.

Native postselection changes the timed work and cache population. Collect new
throughput measurements for this path and identify it through the metadata above;
existing results retain the sampling behavior of their recorded harness revision.

The measurement sampler is compiled once and reused across all calls. Reference
parities come from a separate noiseless sampler with a private cache during
untimed validation. Its sampling does not warm the measurement sampler.

The pinned engine's 512 MiB plan-payload budget is not a process memory ceiling;
plan headers and other allocations are additional. Raw samples record cumulative
process peak RSS, including preparation; `cases.csv` exposes its maximum.

Pin xtim 3.2.2 to source commit `edf0dd0cff804ac0228d517889e49b0c286cb19a`.
The previous 3.1.4 pin restarted a per-shot coin counter on each unseeded call,
so chunked sampling could repeat random outcomes (#82). Regressions require
chunk-invariant counts and the correct noisy selected/unselected law. Historical
3.1.4 identities and measurements remain preserved; new collections use 3.2.2.
Use `QECCORE_PORTABLE=1` (`-O3 -funroll-loops`, without `-march=native`) to
retain the portable build policy of Clifft's release wheels. Record
`CXXFLAGS=-include cstdint`, retained from the earlier pin's missing-header
workaround on GCC. Whether 3.2.2 still needs this flag has not been tested;
the recorded build keeps it for continuity.
The adapter verifies the installed PEP 610 repository and commit before setup.

## Correctness and identity

Before timing, the worker checks circuit qubit, measurement, detector, and
observable counts plus these aggregate invariants:

- attempted = accepted + discarded;
- logical errors are between zero and accepted shots; and
- non-postselected workloads discard no shots.

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

Tsim returns raw detector and observable arrays. Both reference-sample flags
are explicitly false. The adapter passes the workload's detector selection mask,
allowing native early rejection. With a mask, only surviving rows are returned;
without one, every attempted row is returned. The adapter validates matching
boolean array shapes and counts the selected observable on returned rows.
Accepted shots equal the row count, while attempted shots remain the requested
count, including when every shot is rejected. Dense boolean array materialization
and NumPy aggregate counting are inside execution time. Clifft returns native
aggregate counts, so the tools can spend different fractions of their call time producing
the shared aggregate endpoint. This is the same timing boundary used for Stim
and xtim. Any reported postprocessing percentage must come from a separate,
reproducible profile specifying the workload, shots per call, batch capacity,
hardware, and software versions; it is not a universal adapter overhead.

Tsim's automatic batch capacity limits attempted shots per thread; rejection and
sharing can reduce statevector work. `batch_size_applicable` records whether the
compiled program has statevector components. Raw metadata also records component
widths, strategy, precision, seed policy, and threads. No preparation sample is
performed; first-use kernel compilation/cache loading belongs to the first call.

### Matched bulk sampling

`bulk-alternatives-vs-current` compares `clifft-bulk`, `symft-bulk`, and
`tsim-bulk` on the unchanged coherent d5/r5 circuit at 256 attempted shots per
call. Each tool uses native automatic sizing under the same resource ceiling.
The manifest validator rejects unequal public call sizes
within a comparison. Failed cases retain their diagnostics.

The short-call comparisons use one shot per call for d5/r5. Keep those rows
separate from the bulk comparison: sharing work across shots changes the
question, even on the same circuit and host.

### Compatibility with newer Clifft APIs

On Clifft versions exposing a writable HIR `postselection_mask`, the adapter
sets the workload's raw-parity mask before running compiler passes and supplies
it again to lowering. Earlier versions retain selection at lowering. Sampling
uses the case's thread budget and batch setting. Runtime metadata
records batching and the point at which postselection is applied. Development/VCS
builds also verify the full installed source URL and commit; release wheels retain
version validation.
The mask is part of compilation: an unselected variant must compile with an
empty mask, rather than reuse a program optimized assuming detector selection.
Runtime metadata records `postselection_before_optimization`, `batch_control`
and, when available, `optimized_t_gates`.


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
and resource limits. The raw format fixes aggregate counts, raw-record parity,
and attempted-shot throughput for every case. Internal batches may differ. Declared
comparison groups keep short calls, bulk calls, and supplemental coverage separate.
Only successful compatible pairs receive ratios. Missing expected case records
are errors; recorded failures, unsupported adapters, and intentionally unmeasured
combinations remain distinct. A new current workload without a previous baseline
can appear in throughput plots without a release-change value.

Archived files retain their original formats and use the
[pinned legacy tools](../CONTRIBUTING.md#historical-evidence). They are not migrated
or required by the recurring report. Hardware or workload changes must be labeled
in any optional long-term analysis.
