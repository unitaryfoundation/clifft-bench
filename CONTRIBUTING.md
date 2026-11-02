# Contributing to clifft-bench

The maintained workflow compares current Clifft with the previous release and
with other simulators in a fresh paired campaign. Representative workloads may
evolve; comparisons within a campaign use matching circuit and output semantics,
public call sizes, and resource limits. Long-term trends and one-off studies
are optional analyses, not prerequisites for maintaining the release benchmarks.

## Local development

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required:

```bash
uv sync --extra test
uv run clifft-bench validate
uv run pytest
uv run ruff check .
```

List smoke cases without importing a simulator:

```bash
uv run clifft-bench list
```

CI tests the harness and adapter contracts. Official performance collection is
a manual, reviewed workflow. Before changing measurements or results, read the
[benchmark contract](docs/benchmark-contract.md) and
[data format](docs/data-format.md).

Keep mainline additions focused on maintained benchmarks, their correctness
checks, and input provenance. Temporary probes, local debugging output, and
discussion of deferred work belong in PRs or issues. Keep reproducible input
generators and official raw results with the benchmarking code they support.

## Preparing a release campaign

[`campaigns/release-v1.toml`](campaigns/release-v1.toml) is the
recurring campaign definition. It compares calibrated previous and current
Clifft configurations, then calibrated current Clifft and SymFT configurations,
with Stim in a separate `stim-anchor-vs-current` comparison on the unchanged
Clifford surface-code circuit.
It also includes `xtim-vs-current` on the compatible subset, with calibrated
packed-record chunks and the documented cache measurement policy.
It also includes `tsim-vs-current` with CPU active-statevector sampling and
trajectory sharing. `bulk-alternatives-vs-current` separately compares Clifft,
SymFT, and Tsim at 256 attempted shots per call on coherent d5/r5, preserving
the historical one-shot configuration. See the
[Tsim measurement contract](docs/benchmark-contract.md#tsim-active-statevector-cpu-comparison).
The active SymFT identity is pinned to upstream source after its PyPI 0.1.1 release.

The campaign currently pins development Clifft `63cd885` against measured
0.12.0 because BT27/BT81 need the newer compiler. Replace this source pin with
the next RC when preparing that release.

All calibrated cases use the [bounded harness policy](docs/benchmark-contract.md#batching).
Review its selected settings, failures, and search costs during the normal
reference-host campaign. Supplemental coverage follows the same policy; see
[coverage notes](workloads/coverage.md).

For a new Clifft release:

1. Add its identity and install environment to
   [`software.toml`](software.toml).
2. Add its direct requirement and resolved lock under [`environments/`](environments/).
   Update the previous-release and current-source installs in
   [CI](.github/workflows/ci.yml) to exercise both maintained implementations.
   Remove superseded software entries and environments after updating consumers.
3. Point `clifft-current`, `clifft-bulk`, and `clifft-coverage` at the
   new implementation and `clifft-previous` at the prior measured release in the release manifest.
   Update the Clifft smoke, coverage smoke and pilot cases and the `clifft-current` CI install
   to the same new implementation. Keep the intended scheduler settings in
   the shared `clifft-scheduled` option preset in `software.toml`.
4. Keep each workload's `shots_per_call` aligned across every variant and
   release within each comparison. Update those workload-level values only when measurement evidence
   requires it; batch size remains a separately calibrated implementation choice.
5. Validate locally, then follow the [EC2 collection procedure](CONTRIBUTING.md#reference-host-collection).

Choose and record the previous/current configurations for the new campaign.
Keep shared measurement inputs matched within each comparison; new workloads
without a supported previous-release baseline belong in current coverage.
Changing the representative suite does not require extending the historical
series or rerunning old releases.

For a release candidate, keep its exact prerelease value in `version` and its
environment lock, record the immutable `source_tag` and commit, and set
`display_version` to the intended final release label. Runtime checks and raw
evidence retain the exact candidate identity; derived tables expose the display
version for plots, summaries, and release docs.

One collection produces a raw result containing every selected variant, including
structured case failures. The finalizer checks coverage and calibration evidence,
then writes `cases.csv`, `comparisons.csv`, and `index.json` beside the raw evidence.

After the results are reviewed, follow the
[reporting instructions](reporting/README.md) to update the figures and README.
The current reporting tools also read checked-in historical tables and paired
release ratios. See [historical evidence](#historical-evidence) for the archived
collection workflow.

## Configuration and environments

`workloads.toml` owns circuit identities, provenance, expected metadata,
selection/output semantics, default `shots_per_call`, and ordered workload groups.
`software.toml` owns exact implementation identities, build settings, and shared
adapter option presets. `campaigns/*.toml` select cases, resource limits,
measurements, and explicit comparison groups.

A variant's `workloads` is either a group name or a list of workload selections.
Selections and explicit cases inherit the workload's default call size unless
they set `shots_per_call`; bulk and smoke call sizes stay explicit. A group's
members expand in their declared order. Simulator settings use either inline
`execution.options` or one `execution.options_preset` from `software.toml`, with
no merging or inheritance between presets. The owning adapter validates the
resolved options without importing the simulator. Unknown keys, invalid values,
and references are rejected before workers start.

`bootstrap`, `list`, and `run` default to `smoke`. Select the release campaign
explicitly:

```bash
uv run clifft-bench list release-v1 --json
uv run clifft-bench bootstrap release-v1
```

Bootstrap uses `environments/<implementation-id>.txt` and installs each selected
implementation once under `.campaign-envs/<implementation-id>/bin/python`, with
its declared build settings. It checks dependencies, imports, exact versions,
and source identity where required by the adapter. Official setup requires a
clean committed checkout.

For local smoke work, an explicitly selected interpreter can replace the isolated
environments. For example, the test extra contains Stim:

```bash
uv run --extra test clifft-bench run smoke --python .venv/bin/python \
  --case stim --min-sample-seconds 0.01 --repetitions 1 --output /tmp/stim-smoke.json
```

Use a fresh output path: collection refuses to overwrite existing evidence.
Official collection requires isolated, verified implementation environments.

## Reference-host collection

Launch the host declared by the release campaign: Canonical Ubuntu Server 24.04
LTS, x86-64 `m7a.xlarge`, On-Demand, with IMDSv2 enabled. Use a 16 GiB `gp3`
root volume, restrict SSH access, and set instance-initiated shutdown behavior to
**Stop**. No AWS credentials or IAM role are needed by the collector.

Clone the repository, select a clean data branch, and run inside `tmux` if the
connection may close:

```bash
git switch -c data/release-benchmark
./scripts/ec2-run.sh release-v1 "$HOME/clifft-bench-ec2-results/release-benchmark"
```

The wrapper arms an eight-hour shutdown guard, installs host prerequisites,
bootstraps selected environments, and collects `raw.json` outside the checkout.
Python checks the source, OS, architecture, instance type, On-Demand lifecycle,
available CPU, and memory configuration. Bounded IMDSv2 requests record instance,
AMI, region, availability zone, and boot identity. Every worker receives the
campaign's memory ceiling; worker deadlines and the overall campaign deadline
preserve completed evidence when a case fails or times out. The wrapper also
has an eight-hour outer deadline with a 30-second forced-kill fallback.

The execution directory must be new, including after an incomplete attempt.
Inspect `raw.json` and its failures before finalizing:

```bash
uv run clifft-bench validate "$HOME/clifft-bench-ec2-results/release-benchmark/raw.json"
mkdir -p results/release-v1/release-benchmark/raw
cp "$HOME/clifft-bench-ec2-results/release-benchmark/raw.json" \
  results/release-v1/release-benchmark/raw/release-raw.json
uv run clifft-bench finalize release-v1 --execution-id release-benchmark \
  --output-dir results/release-v1/release-benchmark \
  results/release-v1/release-benchmark/raw/release-raw.json
```

Review selected batches, search failures, correctness, memory evidence, and the
comparison tables before committing results or publishing figures. Stop the
instance promptly after retrieving the evidence.

## Historical evidence

The [legacy code, environments, and results at `da88d59`](https://github.com/unitaryfoundation/clifft-bench/tree/da88d59f7976f5d3c2c071190b80d539eacb5fca)
provide a fixed reference for reproducing or investigating earlier measurements.
Use that revision's [reporting instructions](https://github.com/unitaryfoundation/clifft-bench/blob/da88d59f7976f5d3c2c071190b80d539eacb5fca/reporting/README.md)
with its evidence. Active code need not retain compatibility with those formats
or old simulator APIs, and archived results need not be migrated.
See the archived [scalar/auto-packing caveat](https://github.com/unitaryfoundation/clifft-bench/blob/da88d59f7976f5d3c2c071190b80d539eacb5fca/docs/benchmark-contract.md#compatibility-with-newer-clifft-apis)
when interpreting older QV and adder results.

Historical plots may describe performance on an evolving representative suite.
Label workload, hardware, and methodology changes rather than imply a precise
same-task speedup across them. Historical refreshes are separate from the
recurring release workflow.

## Adding a workload

1. Add the immutable circuit under [`workloads/circuits/`](workloads/circuits/)
   with its license.
2. Record its digest, provenance, expected metadata, compatible adapters,
   default call size, and semantic contract in [`workloads.toml`](workloads.toml).
3. Add it to the applicable workload groups or variant selections.
4. Run validation and adapter correctness tests.

Changing a circuit creates a new workload identity so checked-in results never
silently change meaning.

For self-contained studies outside the recurring release campaign, see the
[experiment guidelines](experiments/README.md).
The [Tsim GPU study](experiments/tsim-gpu/README.md) has its own locked
environment, bounded preparation, and finalize/commit/push workflow for results.


## Tsim CPU adapter checks

The maintained Tsim CPU backend uses its pinned source environment:

```bash
uv run clifft-bench bootstrap tsim-smoke
uv run clifft-bench run tsim-smoke --min-sample-seconds 0.01 --repetitions 1 \
  --output /tmp/tsim-smoke.json
uv run clifft-bench validate /tmp/tsim-smoke.json
```

Workers apply thread restrictions before simulator imports. Linux CI also
exercises the campaign's 12 GiB address-space ceiling. Local timings are
installation/correctness diagnostics, not official performance results.
The optional [Tsim GPU study](experiments/tsim-gpu/README.md) remains independently pinned.

## Bounded three-tool CPU pilot

`campaigns/tsim-pilot.toml` reuses the release's bulk coherent d5/r5 and adder
cases and its 85q distillation control. Run on Linux to enforce the memory ceiling:

```bash
uv run clifft-bench bootstrap tsim-pilot
uv run clifft-bench run tsim-pilot --memory-limit-gib 12 --output /tmp/tsim-pilot.json
uv run clifft-bench validate /tmp/tsim-pilot.json
```

The pilot records diagnostics under the same bounded calibration policy as the
release campaign. Review reference-host evidence before making performance claims.
