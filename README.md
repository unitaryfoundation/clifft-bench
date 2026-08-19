# clifft-bench

Reproducible, single-core CPU benchmarks for
[Clifft](https://github.com/unitaryfoundation/clifft) and comparable
near-Clifford circuit simulators.

This repository is a source checkout and evidence archive, not a distributed
Python package. Normal CI checks the harness and adapter contracts. Official
performance data is collected infrequently on a manually operated EC2
reference host and merged through reviewed results PRs.

## Questions this repository answers

1. Did a new Clifft release improve performance?
2. How does current Clifft compare with current alternatives?
3. How has Clifft changed across its release history?
4. What absolute throughput was observed on a named hardware epoch?

The checked-in campaigns currently prepare two evidence sets:

- `clifft-history-v1`: Clifft 0.1.0 through 0.8.0 on the shared QEC corpus;
- `current-tools-v1`: Clifft 0.7/0.8, SymFT single/batched, and Tsim 0.1.5.

The eight shared QEC workloads form the comparison core. QV10 and QV20 remain
available as historical appendix workloads but are not in the current
cross-tool campaigns.

## Repository layout

| Path | Purpose |
|---|---|
| `campaigns/` | Named collection plans and their tool/version run manifests |
| `environments/` | Direct requirements and resolved Linux environment locks |
| `manifests/` | Shared software catalog, workload catalog, and CI smoke run |
| `workloads/circuits/` | Immutable circuit artifacts and applicable license |
| `schemas/` | JSON Schemas for manifests, raw results, and execution indexes |
| `src/clifft_bench/` | CLI, scheduler, adapters, and result finalization |
| `scripts/ec2/` | Generic manual EC2 bootstrap, collection, and finalization |
| `results/` | Reviewed executions; raw JSON plus deterministic derived tables |

The measurement contract is in
[`docs/benchmark-contract.md`](docs/benchmark-contract.md), the manual procedure
is in [`docs/manual-ec2.md`](docs/manual-ec2.md), and the checked-in result
layout is in [`docs/data-format.md`](docs/data-format.md).

## Local development

Python 3.12 or newer is required:

```bash
uv sync --extra test
uv run clifft-bench validate
uv run pytest
uv run ruff check .
```

List the short smoke cases without importing a simulator:

```bash
uv run clifft-bench list
```

The real-adapter smoke check requires Clifft 0.8 and the pinned SymFT source.
Their native Linux extensions are tested in GitHub CI; Apple Silicon can run
the harness tests but cannot build the pinned SymFT extension.

## Add a circuit

1. Add the immutable circuit under `workloads/circuits/` with its license.
2. Add its digest, provenance, expected metadata, compatible adapters, and
   semantic contract to `manifests/workloads.v1.json`.
3. Add the workload ID to the relevant campaign run manifest.
4. Run `clifft-bench validate` and the adapter correctness tests.

Changing an existing circuit creates a new workload identity; historical raw
results continue to point at the old digest.

## Add a tool or release

1. Add the implementation identity to `manifests/software.v1.json`.
2. Add a direct `.in` requirement and resolved Linux `.txt` lock under
   `environments/`.
3. Add or update its adapter and correctness smoke coverage if its API is new.
4. Add an isolated run manifest and environment entry to the campaign.
5. Validate locally, then run the campaign through the manual EC2 playbook.

Each campaign run executes in its own Python environment. Runs may therefore
be collected independently on the same placement and compared during
finalization without installing incompatible tool versions together.

## Change hardware

Create a new `hardware_epoch` rather than appending unlike absolute numbers to
an existing series. Run at least one prior anchor version on both epochs so a
later plot can show or bridge the transition explicitly. The current reference
host decision is summarized in [`docs/reference-host.md`](docs/reference-host.md).

## Results

Raw result JSON is authoritative. Finalization also creates stable long-form
CSV tables and a compact summary for review and future plots. Derived files
must be reproducible from the raw files; no database, dashboard, Git LFS, or
Parquet layer is required at the current data volume.
