# Phase 1 benchmark contract

## Benchmark question

For the same versioned circuit and output contract, how many independent
attempted shots per second can a pinned simulator execute in steady state on one
logical CPU, and how much do repeated paired measurements vary?

This question is narrower than "which simulator is best." Results are only
comparable when workload semantics, batch configuration, precision, thread and
affinity policy, runner identity, and timed boundaries agree.

## Logical work and output

One unit of logical work is one complete independent attempted shot of the
selected circuit. The simulator must return aggregate counts for:

- attempted shots,
- shots discarded by detector postselection,
- accepted shots, and
- logical errors for the manifest-selected observable among accepted shots.

For cultivation workloads every declared detector is a postselection check.
The other QEC and Quantum Volume workloads do not postselect. Every Phase 1
workload selects observable 0 explicitly; this matters for the distillation
circuit, which declares five observables. Throughput always uses attempted shots
as the numerator, before detector rejection.

Materializing full measurement or detector arrays is not part of the logical
work. Clifft's `sample_survivors(..., keep_records=False)` and SymFT's compiled
counts sampler implement the same aggregate-count contract.

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

Cases run serially. The harness may keep the two members of a declared
comparison pair prepared in separate Python workers so different package
environments can coexist, but only one worker samples at a time. Both workers
belong to one process tree, request the same logical CPU, and receive the same
single-thread environment:

`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMEXPR_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, and `BLIS_NUM_THREADS` are 1;
JAX is CPU-only, x64-enabled, and has Eigen multithreading disabled.

Linux affinity is applied with `sched_setaffinity` and verified. An unsupported
or failed affinity request is recorded, never silently treated as successful.
Within each pair, repetitions alternate forward and reverse order. Two
repetitions therefore produce `A/B/B/A`.

The manifest seed must be at least 1. Warmup uses `seed - 1`, correctness uses
`seed`, and execution repetition `r` begins at
`seed + 10_000 + r * 1_000_000`; repeated public calls increment that value by
one. These non-overlapping ranges keep every phase deterministic without
reusing a stream identifier.

## Batching

Two distinct quantities are recorded:

- `batch_size`: the simulator's internal number of shots processed together;
- `shots_per_call`: attempted shots requested by one public API call.

Clifft exposes no internal shot-batch choice in this API, so its batch size is
1. SymFT batch sizes are explicit manifest values, never hidden automatic
choices. A large-batch throughput result is not a single-circuit latency result.
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

## Profiles

`run-smoke.v1.json` is a short CI/developer correctness check across the
cultivation, multi-observable distillation, and Quantum Volume gate regimes; it
has no performance significance. `run-phase1.v1.json` pairs Clifft and SymFT on
all ten workloads, using 30-second minimum samples and five repetitions. Neither
becomes canonical merely by running it; official publication also requires the
runner study, trusted workflow, review, and approval described in the project
plan.
