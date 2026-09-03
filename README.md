# clifft-bench

Reproducible single-core CPU benchmarks for
[Clifft](https://github.com/unitaryfoundation/clifft) and comparable
near-Clifford circuit simulators.

The repository is both a small benchmark harness and an evidence archive.
Official results are collected on a named reference host, checked in as raw
JSON, and summarized in derived CSV tables.

## Regular release questions

The recurring release campaign answers two questions:

1. Did the new Clifft release improve performance over the previous release?
2. How does current Clifft compare with current near-Clifford alternatives?

### Current status

The final one-off 0.x history backfill now runs through the 0.10.0rc1 release
candidate. After that backfill, official collection uses only the recurring
release campaign: previous versus current Clifft and current alternatives
versus current Clifft. Longer-term history accumulates from those executions;
older releases are not rerun for every release.

[`campaigns/release-v1/run.v1.json`](campaigns/release-v1/run.v1.json) is the
single recurring campaign definition. It compares calibrated previous and
current Clifft configurations, then compares calibrated current Clifft and
SymFT configurations. A release without batching support selects scalar mode
during calibration. Update its current and previous Clifft variants for each
later release.

[`campaigns/clifft-history-v1/`](campaigns/clifft-history-v1/) is an optional,
one-off backfill that reruns representative 0.x releases together under the
current contract. It is not part of the recurring release workflow.

## Other questions and experiments

Less-frequent questions do not extend the recurring campaign format or core
CLI. Examples include:

- dense non-Clifford Quantum Volume comparisons,
- multicore strong scaling,
- GPU simulator comparisons, and
- focused checks of a proposed optimization or hardware backend.

These belong under [`experiments/`](experiments/) as self-contained,
purpose-built studies. They may reuse workloads or small harness utilities,
but they should carry their own execution contract and only become shared
infrastructure after repeated use demonstrates a common shape.

## Measurement contract

The release campaign measures attempted shots per second for one complete
circuit execution on one pinned logical CPU. It records setup, warmup, and
correctness separately from the timed samples. Each case runs in its own
Python process and may use its own locked environment, allowing incompatible
simulator versions to be measured in one serial execution.

Important comparison rules:

- QEC throughput counts attempted shots, including postselected discards.
- Raw JSON is authoritative; CSV tables are derived views.
- Absolute rates are comparable only within one hardware epoch.
- Internal batch size and public shots per call remain visible in comparisons.
- Cases run serially in manifest order in one reference-host placement.

See the full [benchmark contract](docs/benchmark-contract.md),
[data format](docs/data-format.md), and
[manual EC2 procedure](docs/manual-ec2.md).

## Repository layout

| Path | Purpose |
|---|---|
| `campaigns/release-v1/` | The single recurring release campaign |
| `campaigns/clifft-history-v1/` | Optional one-off 0.x history backfill |
| `manifests/` | Software identities, workloads, and the developer smoke run |
| `environments/` | Direct requirements and resolved Linux locks |
| `workloads/circuits/` | Immutable circuit artifacts and licenses |
| `src/clifft_bench/` | Serial runner, adapters, validation, and finalization |
| `scripts/ec2/` | Manual reference-host collection workflow |
| `results/` | Reviewed raw executions and derived tables |
| `experiments/` | Infrequent, self-contained performance studies |

## Local development

Python 3.12 or newer is required:

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
a manual, reviewed workflow.

## Preparing a release campaign

For a new Clifft release:

1. Add its identity and install environment to
   `manifests/software.v1.json`.
2. Add its direct requirement and resolved lock under `environments/`.
3. Point `clifft-current` at the new implementation and `clifft-previous` at
   the prior release in the release manifest.
4. Keep each workload's `shots_per_call` aligned across every variant and
   release. Update those workload-level values only when measurement evidence
   requires it; batch size remains a separately calibrated implementation
   choice.
5. Validate locally, then follow `docs/manual-ec2.md`.

For a release candidate, keep its exact prerelease value in `version` and its
environment lock, record the immutable `source_tag` and commit, and set
`display_version` to the intended final release label. Runtime checks and raw
evidence retain the exact candidate identity; derived tables expose the display
version for plots, summaries, and release docs.

One placement now produces one raw result containing every variant. The
finalizer checks placement coverage and calibration evidence, writes
`cases.csv`, `comparisons.csv`, and `index.json` beside the raw evidence.

## Adding a workload

1. Add the immutable circuit under `workloads/circuits/` with its license.
2. Record its digest, provenance, expected metadata, compatible adapters, and
   semantic contract in `manifests/workloads.v1.json`.
3. Add it to the applicable variants in the release manifest.
4. Run validation and adapter correctness tests.

Changing a circuit creates a new workload identity so checked-in results never
silently change meaning.
