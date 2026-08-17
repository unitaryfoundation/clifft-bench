# clifft-bench

Public, reproducible benchmarks of [clifft](https://github.com/unitaryfoundation/clifft) and other near-Clifford quantum circuit simulators.

This repository tracks release-to-release single-core CPU performance, supports on-demand regression comparisons, and publishes the raw results and visualizations needed to understand how simulator performance changes over time.

The initial Phase 1 implementation compares Clifft and SymFT on an immutable,
representative circuit corpus. It records every timing sample and enough runner,
software, build, batching, affinity, and correctness metadata to decide whether
two results are actually comparable.

## Current scope

- CPU-only, one thread per simulator and one active benchmark case at a time.
- One logical CPU affinity where the operating system supports it.
- The eight QEC circuits used by `clifft-paper`: cultivation, distillation,
  coherent-noise surface-code, and pure-Clifford surface-code workloads.
- Fixed-seed Quantum Volume circuits at 10 and 20 qubits.
- Attempted-shot steady-state throughput as the primary metric.
- Setup/compilation, warmup, correctness, and sampling represented separately.
- Explicit SymFT batch sizes; Clifft's internal logical batch size is recorded as 1.
- Pair-local alternating order (`A/B`, then `B/A`) to reduce temporal and thermal bias.

The full contract is in [docs/benchmark-contract.md](docs/benchmark-contract.md).
Multicore and GPU experiments are intentionally outside Phase 1.

## Set up a checkout

Python 3.12 or newer is required.

This repository is run from a source checkout. It is not a library or CLI
distribution project; the Python project metadata exists only to create the
development environment and expose the checkout-local command.

```bash
uv sync --extra test
uv pip install 'clifft==0.7.0'
uv pip install 'git+https://github.com/haoliri0/SymFT_Test.git@9ec5790322f93140e78bdb6d6620a2a43eceba0b#subdirectory=python'
```

The SymFT source URL is pinned because SymFT 0.1.0 currently has no standalone
tagged release. The exact identity is also recorded in
[manifests/software.v1.json](manifests/software.v1.json).

CI and hosted-runner studies use x86-64 GitHub Linux, where the pinned SymFT CPU
extension is supported. The same extension does not compile on this project's
Apple Silicon development laptops because an upstream kernel header includes
x86 intrinsics. That is only a local-development limitation: on Apple Silicon,
validate the harness and run the Clifft case locally, while paired adapter
smoke checks run on GitHub Linux. The canonical host for official performance
measurements has not yet been selected.

## Use

Validate the manifests, circuit digests, schemas, and example result:

```bash
uv run clifft-bench validate
```

List the smoke cases without importing either simulator:

```bash
uv run clifft-bench list
```

Run the short correctness-oriented smoke profile:

```bash
uv run clifft-bench run --output results/local-smoke.json
```

Smoke results are explicitly classified as `smoke`; they are not performance
claims. Run the longer exploratory Phase 1 profile with:

```bash
uv run clifft-bench run \
  --run-manifest manifests/run-phase1.v1.json \
  --output results/phase1-current.json
```

On Linux, the harness chooses the first CPU in the process's allowed affinity
mask unless `--cpu` is supplied. Platforms without process-affinity support
still run, but record that the restriction was not applied and must not be used
for canonical comparisons.

## Isolated environments and candidate comparisons

Each implementation runs in an isolated long-lived Python worker. Set
`CLIFFT_BENCH_CLIFFT_PYTHON` or `CLIFFT_BENCH_SYMFT_PYTHON` to select a specific
environment. Additional candidate/baseline entries can use distinct environment
variables while retaining compiled state across the alternating sample order.

Only one member of a comparison pair samples at a time. Workers are restricted
to the same logical CPU and common BLAS/OpenMP/JAX thread settings.

## Repository layout

| Path | Purpose |
|---|---|
| `manifests/` | Versioned workload, software, and run selections |
| `workloads/circuits/` | Immutable circuit artifacts and applicable license |
| `schemas/` | JSON Schemas for manifests and raw results |
| `src/clifft_bench/` | CLI, scheduler, resource controls, and simulator adapters |
| `examples/` | Schema-valid example raw result |
| `tests/` | Contract, schema, scheduler, and isolated-worker tests |

## Development

```bash
uv run pytest
uv run ruff check .
```

The GitHub-hosted CI smoke check validates harness behavior and adapter
correctness. It does not publish or interpret hosted-runner timings.

## Runner variance study

The runner A/A workflow measured pinned Clifft against itself on short- and
long-call workloads. The initial cohort used standard GitHub-hosted jobs; a
second cohort used the existing serialized 8-vCPU GitHub larger runner. The
larger runner was neither fixed hardware nor more stable, so it is not a
canonical performance host. A bounded third cohort is commissioning an
explicit Ubicloud standard x64 shape as a possible reference host. See
[docs/runner-study.md](docs/runner-study.md) for the method and
[results/runner-study/](results/runner-study/README.md) for raw evidence,
derived summaries, cost accounting, and runner decisions.
