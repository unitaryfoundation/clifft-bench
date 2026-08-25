# clifft-bench

Reproducible CPU benchmarks for
[Clifft](https://github.com/unitaryfoundation/clifft) and comparable
near-Clifford circuit simulators.

> [!NOTE]
> This is an evolving benchmark. We plan to add circuits, tools, and hardware
> backends, including GPU campaigns. Results from different hardware epochs or
> measurement contracts should not be compared directly.

This repository is both a benchmark harness and an evidence archive. Official
performance data is collected on named reference hosts and checked in with its
raw provenance and reproducible derived tables.

## Questions this repository answers

1. [Did a new Clifft release improve performance?](#current-single-core-qec-results)
2. [How does current Clifft compare with current alternatives?](#current-single-core-qec-results)
3. [How has Clifft changed across its release history?](#clifft-release-history)
4. [What absolute throughput was observed on a named hardware epoch?](#current-single-core-qec-results)
5. [How does Clifft compare and scale on wide, multicore circuits?](#quantum-volume-multicore-results)

## Current answers (August 2026)

### Current single-core QEC results

Clifft 0.9.0 has a higher median throughput than 0.8.0 on all eight workloads
in the current campaign. Across the 24 individual placement/workload pairs,
0.9.0 is faster in 23. SymFT's best measured mode is faster on seven
workloads; Clifft leads on distance-5 cultivation.

| Workload | Clifft 0.9 attempted shots/s | vs 0.8 | Fastest measured SymFT mode | Clifft / SymFT |
|---|---:|---:|---|---:|
| Coherent surface code d3, r1 | 1.88M | +37% | batch 2048 | 0.71x |
| Coherent surface code d3, r3 | 461k | +42% | batch 2048 | 0.93x |
| Coherent surface code d5, r1 | 14.6k | +1% | batch 32 | 0.48x |
| Coherent surface code d5, r5 | 7.0 | +10% | single | 0.11x |
| 85-qubit distillation | 576k | +6% | batch 2048 | 0.32x |
| Cultivation d3 | 1.20M | +8% | batch 2048 | 0.45x |
| Cultivation d5 | 182k | +33% | batch 32 | **1.33x** |
| Surface-code memory d7, r7 | 484k | +4% | batch 2048 | 0.14x |

These are medians over three fresh placements on one pinned core of an AWS
`m7a.xlarge` (`AMD EPYC 9R14`, Ubuntu 24.04). The fastest SymFT mode is selected
independently for each workload from the applicable single, batch-32, and
batch-2048 cases. A Clifft/SymFT ratio above 1 means Clifft is faster.

[Browse the execution index and raw results](results/current-tools-v1/current-tools-v1-20260824-r1/)
or use the complete derived
[`cases.csv`](results/current-tools-v1/current-tools-v1-20260824-r1/cases.csv)
and
[`comparisons.csv`](results/current-tools-v1/current-tools-v1-20260824-r1/comparisons.csv).

### Clifft release history

The largest broad improvement arrived in 0.8.0. On the shared corpus, 0.9.0 is
faster than 0.1.0 on all eight workloads, with a median per-workload speedup of
1.93x and a range of 1.18x to 16.89x.

| Release | Median speedup vs 0.1 | Range across workloads | Workloads faster than 0.1 |
|---|---:|---:|---:|
| 0.1.0 | 1.00x | 1.00x | baseline |
| 0.2.0 | 1.00x | 1.00x-1.03x | 6/8 |
| 0.3.0 | 0.98x | 0.92x-1.01x | 1/8 |
| 0.4.1 | 0.91x | 0.74x-1.01x | 1/8 |
| 0.5.0 | 0.97x | 0.82x-1.02x | 2/8 |
| 0.6.0 | 0.98x | 0.85x-1.02x | 2/8 |
| 0.7.0 | 0.94x | 0.84x-1.01x | 2/8 |
| 0.8.0 | 1.59x | 1.14x-15.43x | 8/8 |
| 0.9.0 | **1.93x** | **1.18x-16.89x** | **8/8** |

This campaign uses one placement on the same single-core QEC hardware epoch as
the current-tools campaign. See the
[`cases.csv`](results/clifft-history-v1/clifft-history-v1-20260825-r1/cases.csv)
and
[`comparisons.csv`](results/clifft-history-v1/clifft-history-v1-20260825-r1/comparisons.csv)
for every workload and release.

### Quantum Volume multicore results

![QV current-tool latency and Clifft strong scaling](figures/qv-multicore-v1-2026082.png)

At 16 physical cores, Clifft 0.9.0 has the lowest median single-shot latency of
the four measured tools at QV20 and QV22. Its paired median QV24 speedup is
10.17x from 1 to 16 physical cores. The relative ordering changes with circuit
width, so the full curve is more informative than one aggregate ranking.

This is an exploratory curated execution on an AWS `c8i.8xlarge` (`Intel Xeon
6975P-C`, Ubuntu 24.04), using three deterministic circuit seeds per point. See
the [campaign details and methodology](docs/qv-multicore.md) and the
[`cases.csv`](results/qv-multicore-v1/qv-multicore-v1-2026082/cases.csv).

### How to read these results

- QEC throughput counts attempted shots, including shots discarded by detector
  postselection.
- QEC campaigns are single-core throughput measurements. The QV campaign is a
  separate single-shot, multicore latency experiment.
- Raw JSON is authoritative. CSV tables and figures are derived views.
- Absolute values belong to their named hardware epoch; use an anchor run when
  moving to new hardware.

The eight shared QEC workloads are the comparison core. QV10 and QV20 remain as
historical appendix workloads but are not part of the current cross-tool QEC
campaigns. Tsim performance is deferred to a future GPU campaign on its
intended hardware.

## Repository layout

| Path | Purpose |
|---|---|
| `campaigns/` | Collection plans and tool/version run manifests |
| `environments/` | Direct requirements and resolved Linux locks |
| `manifests/` | Software, workload, and CI smoke manifests |
| `workloads/circuits/` | Immutable circuit artifacts and licenses |
| `schemas/` | Schemas for manifests and results |
| `src/clifft_bench/` | CLI, schedulers, adapters, and finalization |
| `scripts/ec2/` | Manual reference-host collection workflow |
| `results/` | Reviewed raw executions and derived tables |
| `figures/` | Reproducible result figures for quick review |

See the [measurement contract](docs/benchmark-contract.md),
[manual EC2 procedure](docs/manual-ec2.md), [data format](docs/data-format.md),
and [QV multicore procedure](docs/qv-multicore.md).

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

This is a source checkout, not a distributed Python package. CI tests the
harness and adapter contracts; official performance collection remains a
manual, reviewed workflow.

## Extending the benchmark

### Add a circuit

1. Add the immutable circuit under `workloads/circuits/` with its license.
2. Add its digest, provenance, expected metadata, compatible adapters, and
   semantic contract to `manifests/workloads.v1.json`.
3. Add the workload ID to the relevant run manifests.
4. Run validation and adapter correctness tests.

Changing a circuit creates a new workload identity so historical results keep
pointing to the old digest.

### Add a tool or release

1. Add the implementation to `manifests/software.v1.json`.
2. Add direct and resolved Linux requirements under `environments/`.
3. Add adapter and correctness coverage if the API is new.
4. Add an isolated run manifest and campaign environment.
5. Validate locally, then use the manual EC2 playbook.

Each campaign run has its own Python environment, allowing incompatible tool
versions to be collected independently on the same placement.

### Change hardware

Create a new `hardware_epoch` and run at least one prior anchor version on both
epochs. Do not append unlike absolute measurements to an existing series. See
the [reference-host policy](docs/reference-host.md).
