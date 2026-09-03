# Performance reporting

This directory is the reproducible source for the QEC and Quantum Volume figures
published by Clifft. The checked-in figures are downstream views of finalized
benchmark executions; no benchmark rows are copied into a second reporting data
set.

[`sources.json`](sources.json) selects the reviewed evidence:

- `history_execution` supplies scalar Clifft 0.1.0 through 0.9.0 QEC results.
- `release_executions` is an ordered chain of finalized recurring release runs.
  Each run extends the history with its paired `current-vs-previous` ratios, and
  the latest run supplies absolute Clifft throughput, Clifft/SymFT ratios, and
  the current-versus-previous release comparison.
- `qv_execution` supplies the four-tool Quantum Volume measurements.

The joins validate execution identities, workload coverage, equal per-workload
shot counts, the release chain, and reuse of the same current Clifft cases in
both release comparisons.

## Publication figures

The paper-style QEC figures remain in `figures/`. Generate them from the
repository root with:

```bash
uv run --extra report python reporting/qec.py
```

Pass `--pdf` to also write vector PDFs.

The web-ready, transparent light/dark figures are checked in under
`figures/web/`:

- `clifft-throughput-{light,dark}.png`: absolute attempted shots per second for
  the latest Clifft release configuration.
- `clifft-vs-symft-{light,dark}.png`: latest calibrated Clifft throughput
  relative to calibrated SymFT. This optional pair remains available here but
  is not consumed by Clifft's documentation.
- `performance-over-time-{light,dark}.png`: median Clifft speedup since v0.1.0.
- `v010-vs-v009-{light,dark}.png`: current release throughput relative to the
  previous release, retaining packed/scalar marker fill.
- `quantum-volume-{light,dark}.png`: median execution time for Clifft, Qiskit
  Aer, qsim, and Qulacs from the selected QV execution.

## Refresh after an RC campaign

First edit [`sources.json`](sources.json). Append the newly reviewed
`results/release-v1/<execution-id>` path to `release_executions` in release
order. Replace an entry only when it points to a corrected execution; do not
rename or alter stored result identities. Change `qv_execution` only when a new
QV execution has separately been reviewed.

From the repository root, validate the source selection and regenerate all QEC
web assets:

```bash
uv run --extra report python reporting/qec.py --check
uv run --extra report python reporting/qec.py --style web
```

Regenerate the QV pair from the exact `qv_execution` selected in
`sources.json`. For the currently selected execution, run:

```bash
cd experiments/qv
uv run --locked --extra plot python -m qv_experiment.plot \
  results/qv-0.10.0rc1-20260902 \
  --web-output-dir ../../reporting/figures/web
cd ../..
```

Then run the repository checks and review the rendered changes:

```bash
uv run --extra test pytest
uv run --extra test ruff check .
cd experiments/qv
uv run --locked --extra test pytest
cd ../..
git status --short reporting/sources.json reporting/figures/web
```

The QEC release-history calculation uses paired ratios between versions rather
than comparing absolute rates from different EC2 boots. Audit the latest
`current-vs-previous` rows before publishing to confirm they compare the best
current Clifft configuration with the supported previous configuration. Also
confirm that `alternatives-vs-current` compares the calibrated configuration of
each tool and reuses the current Clifft cases.

## Update the Clifft documentation copy

Clifft checks in rendered copies only. With `CLIFFT_CHECKOUT` set to a local
Clifft checkout, update the four consumed light/dark pairs (eight assets) with:

```bash
CLIFFT_CHECKOUT=/path/to/clifft
cp reporting/figures/web/clifft-throughput-{light,dark}.png \
  reporting/figures/web/performance-over-time-{light,dark}.png \
  reporting/figures/web/v010-vs-v009-{light,dark}.png \
  reporting/figures/web/quantum-volume-{light,dark}.png \
  "$CLIFFT_CHECKOUT/docs/assets/performance/"
```

The filenames are unchanged by the copy: each
`reporting/figures/web/<name>.png` maps directly to
`clifft/docs/assets/performance/<name>.png`. The guide reuses the README's
absolute-throughput figure; do not copy the optional `clifft-vs-symft` pair.
Review both light and dark variants
in the Clifft pull request. For a later release pair, update the release-specific
`v010-vs-v009` output stem and its Clifft references as part of that release's
report refresh.

## How the QEC history is combined

The historical execution provides the 0.1.0 through 0.9.0 points. Later points
are chained from recurring release runs. For example, each workload's 0.10.0
speedup is its historical 0.9.0/0.1.0 speedup multiplied by the corrected
release campaign's calibrated 0.10.0/0.9.0 ratio. This preserves common
workloads while allowing each newer release to use its best supported approach.
