# Benchmark contract

This contract governs the single-core QEC throughput campaigns and their
historical appendix workloads. The separate single-shot multicore experiment
has its own resource and timing contract in
[`qv-multicore.md`](qv-multicore.md); its results cannot be mixed into this
throughput schema.

## Benchmark question

For the same versioned circuit and output contract, how many independent
attempted shots per second can a pinned simulator execute in steady state on one
logical CPU, and how much do repeated paired measurements vary?

This question is narrower than "which simulator is best." Like-for-like
results require matching workload semantics, precision, thread and affinity
policy, runner identity, timed boundaries, and call/batch configuration.
Campaigns may also declare end-to-end comparisons between differently labeled
call/batch modes, but those answer a configured-throughput question rather than
an equal-granularity one.

## Logical work and output

One unit of logical work is one complete independent attempted shot of the
selected circuit. The simulator must return aggregate counts for:

- attempted shots,
- shots discarded by detector postselection,
- accepted shots, and
- logical errors for the manifest-selected observable among accepted shots.

For every QEC workload, each declared detector is a postselection check. The
Quantum Volume workloads declare no detectors and do not postselect. Every
benchmark workload selects observable 0 explicitly; this matters for the
distillation circuit, which declares five observables. Throughput always uses
attempted shots as the numerator, before detector rejection.

Every benchmark workload also declares `reference_convention` as
`raw-record-parity`. Detector events and logical errors are the XOR parity of
their declared measurement records, without XORing a noiseless reference
sample into either value. The convention is embedded in each result with the
workload definition. Reference-normalized variants are intentionally a
separate future benchmark profile so they cannot be mixed into the same result
series.

Materializing full measurement or detector arrays is not part of the logical
work. The Clifft and SymFT configurations in these campaigns expose
aggregate-count APIs, and any public-call overhead needed to produce those
counts remains inside the timed boundary.

## Software identity

Each implementation records its version, source commit SHA, source commit
datetime, and release datetime. `release_datetime` is the first published PyPI
artifact timestamp when the version has a PyPI release, and `null` for a pinned
unreleased commit such as the current SymFT baseline.

## Timed boundaries

| Phase | Timed as steady-state execution? | Contents |
|---|---:|---|
| Installation | No | Environment creation, download, source build |
| Import | No | Python and extension-module import |
| Setup | No, recorded separately | Parse, trace/plan, optimization, lowering, sampler creation |
| Warmup | No, recorded separately | One declared sampler call after setup |
| Correctness | No, recorded separately | Metadata and aggregate-count invariant check |
| Execution sample | Yes | Wall time around repeated public aggregate sampling calls |
| Result validation/write | No | Schema validation, JSON serialization, atomic write |

Each raw execution sample continues until its accumulated wall time meets the
profile's minimum interval. The final API call may make a sample longer than
that minimum. Individual samples are retained; median and median absolute
deviation are derived convenience fields, not replacements for raw data.
Every worker request also has a manifest-defined wall-clock deadline. If a
simulator stops responding, the runner terminates that isolated worker and
records a structured timeout rather than leaving the run indefinitely active.
The deadline must exceed the minimum sample interval and should include ample
margin for the slowest expected final public API call; profiles use a larger
margin for workloads whose single call may itself take substantial time.

## Resources and ordering

Cases and campaign runs execute serially. Each run uses an isolated Python
environment, so incompatible versions never share a process. The harness may
keep members of an explicitly declared in-run pair prepared in separate
workers, but only one worker samples at a time. Every worker requests the same
logical CPU and receives the same single-thread environment:

`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMEXPR_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, and `BLIS_NUM_THREADS` are 1;
adapter-specific restrictions are also captured in runtime metadata.

Linux affinity is applied with `sched_setaffinity` and verified. An unsupported
or failed affinity request is recorded, never silently treated as successful.
Within each pair, repetitions alternate forward and reverse order. Two
repetitions therefore produce `A/B/B/A`.

Each worker also receives the campaign's 12 GiB Linux address-space ceiling.
The requested value is embedded in every raw case, the applied `RLIMIT_AS` is
recorded after setup, and finalization rejects results that do not match the
campaign. An allocation failure is retained as a structured case error. The
ceiling follows the resource-limit pattern used by the original QV benchmark
while reserving 4 GiB of the reference host for the controller and operating
system; the separate QV campaign retains its 10 GiB limit.

The manifest seed must be at least 1. Warmup uses `seed - 1`, correctness uses
`seed`, and execution repetition `r` begins at
`seed + 10_000 + r * 100_000_000`; repeated public calls increment that value
by one. The worker enforces the 100-million-call stride before a stream
identifier can overlap the following repetition, and the harness rejects
repetition counts whose reserved ranges exceed the unsigned 32-bit seed space.
These non-overlapping ranges keep every phase deterministic for adapters that
expose per-call streams.

These fixed streams exist only to make performance runs replayable and
auditable. They are not a seeding recommendation for scientific simulation or
statistical inference, where independent seeds should be drawn from operating-
system entropy backed by hardware entropy when available.

## Batching

Two distinct quantities are recorded:

- `batch_size`: the simulator's internal number of shots processed together;
- `shots_per_call`: attempted shots requested by one public API call.

Clifft exposes no internal shot-batch choice in this API, so its batch size is
1. SymFT batch sizes are explicit manifest values, never hidden automatic
choices. A large-batch throughput result is not a single-circuit latency
result.
Cross-mode tool comparisons are intentional end-to-end configuration
comparisons, not claims of equal public-call granularity. Derived comparison
rows therefore carry both sides' mode, effective batch size, and shots per
call; reports and plots must display those dimensions.
SymFT uses native batch size 0 as a disabled sentinel on its single backend; the
harness records the effective logical batch size as 1 in that mode.
The Quantum Volume cases therefore request one shot per call with batching
disabled and are marked as latency measurements; their inverse latency is still
reported in the common attempted-shots-per-second field.

## Correctness

Correctness work occurs outside the timed region. The initial check verifies:

- parsed qubit, measurement, detector, and observable counts against the
  workload manifest;
- attempted = accepted + discarded;
- logical errors lie between zero and accepted shots; and
- non-postselected workloads discard no shots.

The real-adapter smoke test also runs deterministic detector and observable
sentinels whose raw and reference-normalized answers are exact opposites. This
detects an upstream change in either simulator's reference convention without
relying on statistical agreement.

These checks catch circuit or API mismatches but do not prove two independent
random streams are sample-for-sample equal. Later statistical distribution
checks can extend the versioned correctness check without changing timed work.

## Failures and incomparability

An adapter/workload combination may appear in a run manifest only if the
workload declares it compatible. Manifest validation rejects an incompatible
combination with a specific error before execution; it is never coerced into a
different task or emitted as a result case. Import, setup, warmup, timeout,
correctness, and sampling failures after validation are recorded as structured
case errors. Successful cases cannot omit raw samples or correctness evidence
under the result schema.

## Runs and campaigns

`manifests/run-smoke.v1.json` is a short CI/developer correctness check and has
no performance significance. A campaign groups one or more isolated run
manifests, exact environment locks, comparison declarations, a hardware epoch,
and the required placements/replicas. A run becomes official evidence only
after the manual host checks, complete finalization, and review of its results
PR.

Tsim remains covered by adapter and reference-convention smoke tests, but is
not part of the official CPU campaign. A future GPU campaign will define its
own hardware epoch and resource contract rather than mixing CPU fallback
results with this series.
