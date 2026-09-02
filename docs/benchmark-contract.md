# Benchmark contract

This contract governs the official single-core QEC campaigns under
`campaigns/`. One-off studies under `experiments/` define their own contracts.

## Question and logical work

For the same versioned circuit and output contract, how many independent
attempted shots per second can a simulator execute in steady state on one
pinned logical CPU?

One attempted shot is one complete circuit execution. Each simulator returns
aggregate counts for attempted shots, detector-postselected discards, accepted
shots, and observable-0 logical errors among accepted shots. Throughput always
uses attempted shots as the numerator.

Every workload declares `reference_convention` as `raw-record-parity`.
Detector events and logical errors are the XOR parity of their declared
measurement records without a noiseless-reference correction. Materializing
full sample arrays is not part of the timed work.

## Timed boundaries

| Phase | Timed as execution? |
|---|---:|
| Installation and import | No |
| Parse, plan, compile, and sampler setup | No; recorded separately |
| Warmup | No; recorded separately |
| Correctness check | No; recorded separately |
| Repeated public aggregate sampling calls | Yes |
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

Clifft and SymFT use the same procedure. Calibration is setup work and is not
included in final throughput samples. Raw results record candidate probes,
failures, the selected size, and total calibration duration in
`setup.runtime_metadata.batch_calibration`.

Successful results replace `"calibrate"` with the selected numeric `batch_size`.
They also record `batch_size_effective`, the maximum lanes available to one
public call after capping the selected capacity by `shots_per_call`. A fixed
numeric batch size remains supported for cases that do not request calibration.

`batch_size` is the simulator's internal number of shots processed together.
`shots_per_call` is the number requested from one public API call. Both are
recorded because changing either can change amortization.

Cross-mode comparisons are configured-throughput comparisons, not claims of
equal public-call granularity. Derived rows therefore carry both sides' mode,
effective batch size, and shots per call.

The recurring `current-vs-previous` comparison asks whether Clifft improved
using the capabilities available in each release. It therefore compares the
previous release's scalar configuration with the current release's calibrated
configuration at the same `shots_per_call`. A separate scalar cross-tool
comparison retains visibility into non-batched behavior.

## Correctness and identity

Before timing, the worker checks circuit qubit, measurement, detector, and
observable counts plus these aggregate invariants:

- attempted = accepted + discarded;
- logical errors are between zero and accepted shots; and
- non-postselected workloads discard no shots.

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
