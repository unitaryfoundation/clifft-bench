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
Clifft configurations, then calibrated current Clifft and SymFT configurations.
A release without batching support selects scalar mode during calibration.

For a new Clifft release:

1. Add its identity and install environment to
   [`manifests/software.v1.json`](manifests/software.v1.json).
2. Add its direct requirement and resolved lock under [`environments/`](environments/).
3. Point `clifft-current` at the new implementation and `clifft-previous` at
   the prior release in the release manifest.
4. Keep each workload's `shots_per_call` aligned across every variant and
   release. Update those workload-level values only when measurement evidence
   requires it; batch size remains a separately calibrated implementation choice.
5. Validate locally, then follow the [EC2 collection procedure](docs/manual-ec2.md).

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
