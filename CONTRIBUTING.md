# Contributing to clifft-bench

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

## Preparing a release campaign

[`campaigns/release-v1/run.v1.json`](campaigns/release-v1/run.v1.json) is the
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
A release without batching support selects scalar mode during calibration.

The campaign currently pins development Clifft `63cd885` against measured
0.12.0 because BT27/BT81 need the newer compiler. Replace this source pin with
the next RC when preparing that release.

For a new Clifft release:

1. Add its identity and install environment to
   [`manifests/software.v1.json`](manifests/software.v1.json).
2. Add its direct requirement and resolved lock under [`environments/`](environments/).
   Add the version to the adapter-smoke loop in [CI](.github/workflows/ci.yml)
   so the real-Clifft scheduler tests run against the published RC.
3. Point `clifft-current` and `clifft-bulk` at the new implementation and
   `clifft-previous` at the prior measured release in the release manifest.
   Update the Clifft smoke and pilot cases and the `clifft-current` CI install
   to the same new implementation. For a scheduler-capable RC, set
   `clifft-current.execution` to the checked-in
   [scheduler configuration](campaigns/release-v1/clifft-scheduled-execution.json).
4. Keep each workload's `shots_per_call` aligned across every variant and
   release within each comparison. Update those workload-level values only when measurement evidence
   requires it; batch size remains a separately calibrated implementation choice.
5. Validate locally, then follow the [EC2 collection procedure](docs/manual-ec2.md).

Preserve the previously reviewed current configuration when moving it to
`clifft-previous`, including optional compiler passes and per-workload shot
counts. This keeps the paired performance-history chain continuous.

For a release candidate, keep its exact prerelease value in `version` and its
environment lock, record the immutable `source_tag` and commit, and set
`display_version` to the intended final release label. Runtime checks and raw
evidence retain the exact candidate identity; derived tables expose the display
version for plots, summaries, and release docs.

One placement produces one raw result containing every variant. The finalizer
checks placement coverage and calibration evidence, then writes `cases.csv`,
`comparisons.csv`, and `index.json` beside the raw evidence.

After the results are reviewed, follow the
[reporting instructions](reporting/README.md) to update the figures and README.
The reporting layer extends the one-off
[historical backfill](campaigns/clifft-history-v1/README.md) with paired release
ratios; older releases are not rerun for every release.

## Adding a workload

1. Add the immutable circuit under [`workloads/circuits/`](workloads/circuits/)
   with its license.
2. Record its digest, provenance, expected metadata, compatible adapters, and
   semantic contract in [`manifests/workloads.v1.json`](manifests/workloads.v1.json).
3. Add it to the applicable variants in the release manifest.
4. Run validation and adapter correctness tests.

Changing a circuit creates a new workload identity so checked-in results never
silently change meaning.

For self-contained studies outside the recurring release campaign, see the
[experiment guidelines](experiments/README.md).
The [Tsim GPU study](experiments/tsim-gpu/README.md) has its own locked
environment, bounded preparation, and finalize/commit/push workflow for results.


## Tsim CPU adapter checks

Use the pinned source environment; the released 0.1.5 wheel lacks this backend:

```bash
uv venv --python 3.12 .campaign-envs/tsim
uv pip install --python .campaign-envs/tsim/bin/python -r environments/tsim-0.1.5-e9406ed.txt
uv pip install --python .campaign-envs/tsim/bin/python -e '.[test]'
NUMBA_NUM_THREADS=1 .campaign-envs/tsim/bin/python -m pytest tests/test_tsim_adapter.py tests/test_distillation_15to1.py -k 'tsim or hamming'
.campaign-envs/tsim/bin/python -m clifft_bench run \
  --run-manifest manifests/run-tsim-smoke.v1.json \
  --min-sample-seconds 0.01 --repetitions 1 --output /tmp/tsim-smoke.json
.campaign-envs/tsim/bin/python -m clifft_bench validate /tmp/tsim-smoke.json
```

The runner applies thread restrictions before starting each worker. Linux CI
also exercises the campaign's 12 GiB address-space ceiling. Local timings are
installation/correctness diagnostics, not official performance results. The
older `experiments/tsim-gpu` environment remains independently pinned.


## Bounded three-tool CPU pilot

`manifests/run-tsim-pilot.v1.json` reuses the release cases for coherent d5/r5
bulk sampling and the 85q distillation control across Clifft, SymFT, and Tsim.
It uses three samples of at least three seconds, with 180-second preparation
and 60-second request deadlines. The bulk calls contain 256 attempted shots;
all historical one-shot cases remain unchanged.

After installing the three pinned environments, set their
`CLIFFT_BENCH_CLIFFT_63CD885_PYTHON`, `CLIFFT_BENCH_SYMFT_C89B985_PYTHON`, and
`CLIFFT_BENCH_TSIM_E9406ED_PYTHON` variables to the corresponding Python
executables. Run on Linux to enforce the reference memory limit:

```bash
uv run clifft-bench run --run-manifest manifests/run-tsim-pilot.v1.json \
  --memory-limit-gib 12 --output /tmp/tsim-pilot.json
uv run clifft-bench validate /tmp/tsim-pilot.json
```

Review candidate failures, selected batch sizes, peak RSS, individual call
costs, and accepted/error counts. Calibration still chooses internal batch
sizes on the measurement host. The 256-shot bulk size was chosen after a local
pilot: 4096-shot SymFT calls took approximately 78–99 seconds, while 256-shot
calls took approximately 5–6 seconds and retained about 95% of Tsim throughput.
These are feasibility observations on an Apple M4 Pro, not reference-host
performance results.

macOS does not enforce this harness's Linux address-space ceiling or affinity.
In the local pilot, an external RSS watchdog stopped SymFT's 256-lane trial
above 8 GiB; smaller trials remained usable. Linux CI exercises the automatic
calibration path under the actual 12 GiB address-space ceiling and retains its
raw pilot artifacts. A killed local trial must remain recorded as a resource
failure; it must not be substituted for a successful calibration result.
