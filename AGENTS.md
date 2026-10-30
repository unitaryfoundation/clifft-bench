# clifft-bench Agent Instructions

This file records repository-specific constraints for `clifft-bench`. Use
`README.md`, `CONTRIBUTING.md`, and `docs/benchmark-contract.md` for project
orientation, methodology, and operational workflows.

## Source of Truth and Architecture Changes

Use `src/clifft_bench/`, `reporting/`, `workloads.toml`, `software.toml`,
`campaigns/`, and `docs/benchmark-contract.md` as the source of truth.

If a proposed change contradicts an architectural invariant below:

1. Stop instead of layering shims, adding duplicate schemas, or appending
   defensive prose to justify the change.
2. Explain the discrepancy and propose an explicit design change.
3. Wait for human confirmation before implementation.

## Architectural Invariants

- **Zero third-party runtime dependencies in the core harness:** `clifft_bench`
  uses the Python 3.12+ standard library (`tomllib`, `dataclasses`, `csv`,
  `json`, `subprocess`, `urllib.request`). Do not introduce `jsonschema`,
  `pydantic`, `psutil`, or external CLI frameworks into `[project.dependencies]`.
  Plotting (`matplotlib`) and testing (`pytest`, `ruff`, `stim`) live only in
  optional extras.
- **Process-isolated workers:** Every benchmark case runs in a fresh
  `clifft_bench.worker` subprocess using that implementation's isolated
  virtualenv (`.campaign-envs/<implementation-id>/bin/python`), with thread-cap
  environment variables applied before simulator imports, CPU affinity and
  Linux `RLIMIT_AS` applied before preparation, and file descriptor 1 isolated
  so native stdout cannot corrupt JSON-over-stdio messages.
- **Adapter-specific options stay in adapters:** Pass simulator-specific
  settings through `options = { ... }` in campaign TOML and validate them inside
  the target `Adapter`. Never add simulator-specific fields to global campaign
  dataclasses or dedicated columns in `cases.csv`; record rich adapter
  diagnostics in `raw.json` (`setup.runtime_metadata`).
- **Validate once at boundaries; do not re-audit internal producers:** Validate
  TOML configs when loading a campaign and validate simulator outputs (`Counts`
  and circuit metadata) in the worker. Downstream layers (`csv` export and
  `reporting/`) consume `raw.json` and `cases.csv` directly; do not re-simulate
  worker calibration math or cross-audit derived files against each other.
- **Single derived table (`cases.csv`):** Each finalized execution directory
  (`results/<campaign>/<execution-id>/`) contains `raw.json` (authoritative
  sample-level evidence) and `cases.csv` (derived long-form summary). Pairwise
  ratios and speedups are computed on demand from `cases.csv`; do not check in
  pre-joined `comparisons.csv` or `index.json` files.
- **Frozen historical releases:** `results/clifft-history-v1/` is frozen
  historical backfill for Clifft 0.1-0.9. Do not re-add runtime shims,
  lockfiles, or CI loops for Clifft versions older than the active
  `clifft-previous` baseline.
- **Experiments are single-script studies:** One-off or non-single-core studies
  under `experiments/<name>/` must remain self-contained scripts (~200 lines)
  plus their checked-in result CSVs, without duplicating multi-module runner,
  validator, or finalizer frameworks.

## Documentation and Test Anti-Bloat Rules

- **Three top-level docs only:** Keep documentation consolidated in `README.md`
  (reader overview and current figures), `docs/benchmark-contract.md` (present-tense
  methodology, timing boundaries, data format, and reference host spec), and
  `CONTRIBUTING.md` (linear maintainer checklist from local dev to EC2
  collection and figure refresh).
- **Write present-tense specs, not PR diaries:** Never append historical
  transition notes, GitHub issue numbers, temporary ablation counts, or local
  laptop pilot timings to permanent docs or code comments. State the current
  rule once in one file.
- **Test behavior, not prose or manifest snapshots:**
  - Never write tests that regex-match or substring-assert English prose in
    Markdown files or shell scripts, or assert that deleted features remain
    absent.
  - Never hardcode transient release-manifest state (such as current version
    strings, commit SHAs, or exact variant/case counts) in unit tests; validate
    structural invariants instead so routine release bumps do not require test
    edits.
- **Stable figure filenames:** Output stems under `reporting/figures/web/` must
  be version-independent (for example `release-change-{light,dark}.png`, not
  `v012-vs-v011-{light,dark}.png`).

## Git and AI-Assisted Contribution Rules

- Never commit directly to `main`; use a feature branch (`feat/...`, `fix/...`,
  `refactor/...`, `docs/...`) or a data branch (`data/<execution-id>`).
- Keep commits atomic with conventional prefixes (`feat:`, `fix:`, `refactor:`,
  `test:`, `docs:`, `data:`, `report:`).
- Run `uv run --extra test pytest` and `uv run --extra test ruff check .` before
  every commit (and `uv run --extra report clifft-bench report --check` when
  touching reporting or result files).
- Include an `Assisted-by:` trailer identifying the agent that actually
  assisted:

  ```text
  Assisted-by: AGENT_NAME (MODEL_NAME) <PROVIDER_NOREPLY_EMAIL>
  ```
