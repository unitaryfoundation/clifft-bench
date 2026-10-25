# Performance reporting

This directory is the reproducible source for the QEC and Quantum Volume figures
published by Clifft. The checked-in figures are downstream views of finalized
benchmark executions; no benchmark rows are copied into a second reporting data
set.

[`sources.json`](sources.json) selects the reviewed evidence:

- `history_execution` supplies scalar Clifft 0.1.0 through 0.9.0 QEC results.
- `release_executions` is an ordered chain of finalized recurring release runs.
  Each run extends the history with its paired `current-vs-previous` ratios, and
  the latest run supplies absolute throughput for Clifft, SymFT, xtim, and Stim,
  Clifft/SymFT ratios, and the current-versus-previous release comparison.
  Adder compile times and peak active widths come from that run's `cases.csv`.
- `qv_execution` supplies the four-tool Quantum Volume measurements.

The joins validate execution identities, workload coverage, equal per-workload
shot counts, the release chain, and reuse of the same current Clifft cases in
all cross-tool comparisons. Adder compiler evidence must join to the exact
cases used for its paired throughput comparison. Missing expected measurements
are rejected; unsupported combinations are left absent.

QEC figures use the recurring campaign's
[workload set](../docs/benchmark-contract.md#release-workload-selection) for
historical medians: the same six workloads at every version, now through 0.12.0.
Current figures cover those six plus 15-to-1 distillation. The prepared-basis
adder has a separate figure and never enters the QEC median or release-change
axis. Source executions may contain retired workloads; reporting selects its
defined sets without modifying the archived evidence.

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
  the latest Clifft release configuration, used by Clifft's README.
- `simulator-throughput-{light,dark}.png`: Clifft, SymFT, xtim, and Stim absolute
  attempted-shot rates on one logarithmic axis. Workloads follow descending
  Clifft throughput order; each tool has its own marker shape and color. This
  replaces the former `clifft-symft-throughput` pair.
- `clifft-vs-symft-{light,dark}.png`: latest calibrated Clifft throughput
  relative to calibrated SymFT. This optional pair remains available here but
  is not consumed by Clifft's documentation.
- `performance-over-time-{light,dark}.png`: median Clifft speedup since v0.1.0.
- `v012-vs-v011-{light,dark}.png`: current QEC throughput changes from the
  previous release, on a linear percentage axis with packed/scalar marker fill.
- `draper-adder-{light,dark}.png`: previous/current Clifft throughput for the
  fixed-input 16-bit adder, with compilation time and peak active width.
- `quantum-volume-{light,dark}.png`: median execution time for Clifft, Qiskit
  Aer, qsim, and Qulacs from the selected QV execution.

The combined figure reads absolute rates directly from the latest
`alternatives-vs-current`, `xtim-vs-current`, and `stim-anchor-vs-current` rows.
All reuse the current Clifft cases in `current-vs-previous`. Per-workload rates
are medians across the selected rows; no rates are reconstructed from rounded
labels or ratios. The optional Clifft/SymFT figure retains its paired-ratio
statistic. xtim's four measured workloads and Stim's single surface-code anchor
are shown without inventing values for other combinations.

These are available aggregate-count endpoints on one pinned logical CPU.
xtim samples begin with an empty plan cache and time lazy plan construction;
its plotted rate is a fixed-duration average. The exact pinned builds and
measurement boundaries remain in the [contract](../docs/benchmark-contract.md).
The separately measured Tsim GPU and Quantum Volume experiments retain their
own evidence and figures.

## Refresh after an RC campaign

Finalized CSVs record the
[compiler configuration](../docs/benchmark-contract.md#compiler-configuration)
and compilation costs; retain the exact RC identity in the evidence.

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
uv run --extra report python reporting/qec.py
```

The QV pair remains based on the reviewed 0.10.0rc1 execution; the QEC release
campaign does not provide new Quantum Volume measurements. To regenerate that
pair from the exact `qv_execution` selected in `sources.json`, run:

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
confirm that every cross-tool comparison uses calibrated configurations and
reuses the current Clifft cases. Small release changes on a single host do not
establish a release-wide speedup.

## Update the Clifft documentation copy

Clifft checks in rendered copies only. With `CLIFFT_CHECKOUT` set to a local
Clifft checkout, copy the six light/dark pairs (twelve assets) for the updated
README and performance guide with:

```bash
CLIFFT_CHECKOUT=/path/to/clifft
cp reporting/figures/web/clifft-throughput-{light,dark}.png \
  reporting/figures/web/simulator-throughput-{light,dark}.png \
  reporting/figures/web/performance-over-time-{light,dark}.png \
  reporting/figures/web/v012-vs-v011-{light,dark}.png \
  reporting/figures/web/draper-adder-{light,dark}.png \
  reporting/figures/web/quantum-volume-{light,dark}.png \
  "$CLIFFT_CHECKOUT/docs/assets/performance/"
```

The filenames are unchanged by the copy: each
`reporting/figures/web/<name>.png` maps directly to
`clifft/docs/assets/performance/<name>.png`. The README retains the Clifft-only
absolute-throughput figure, while the guide uses the four-tool combined figure
and a separate adder section. Update the old `clifft-symft-throughput` and
`v011-vs-v010` references and remove those superseded copies in the Clifft PR. Do not
copy the optional relative-only `clifft-vs-symft` pair.
Review both light and dark variants
in the Clifft pull request. For a later release pair, update the release-specific
`v012-vs-v011` output stem and its Clifft references as part of that release's
report refresh.

## How the QEC history is combined

The historical execution provides the 0.1.0 through 0.9.0 points. Later points
are chained from recurring release runs. For example, each workload's 0.10.0
speedup is its historical 0.9.0/0.1.0 speedup multiplied by the corrected
release campaign's calibrated 0.10.0/0.9.0 ratio. This preserves common
workloads while allowing each newer release to use its best supported approach.


## Tsim CPU and bulk comparison tables

The recurring release finalizer now produces `tsim-vs-current` rows for the
existing current-Clifft inputs and `bulk-alternatives-vs-current` rows for
coherent d5/r5 at 4096 attempted shots per call. Select the comparison ID
explicitly; do not combine the bulk rows with the historical one-shot series.
`cases.csv` includes the sampling backend, trajectory-sharing setting,
compilation time, kernel-warmup time, full-call warmup time, and active width.
The raw setup metadata retains source verification, Numba threads, seed policy,
and complete calibration evidence. Kernel warmup is also part of total setup,
so do not add it again to that total.

Existing published figures continue to use reviewed evidence. The current-tool
chart includes Tsim when the selected release has complete `tsim-vs-current`
coverage, verifying that it reuses the exact current-Clifft baseline cases.
It labels the source commit as well as the distribution version and never
reads bulk rows. Select new evidence only after matched reference-host results
are collected and reviewed; local smoke/probe timings are not publication data.
Bulk charts and broader finite-shot reporting are tracked in issue #79.
