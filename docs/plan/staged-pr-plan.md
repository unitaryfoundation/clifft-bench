# `clifft-bench` Staged PR Plan

Each PR below is independently reviewable, leaves CI green, and avoids mixing mechanical data migration with behavioral changes.

---

## PR 1: Add `AGENTS.md` and Purge Change-Detector / Snapshot Tests

**Goal:** Establish guardrails first so every subsequent PR follows the anti-bloat rules, and strip brittle prompt-echo tests before refactoring internals.

- **Add:** `AGENTS.md`
- **Delete:** `tests/test_ec2_playbook.py`
- **Edit:** `tests/test_manifests.py` — Replace hardcoded case counts (`108`), version strings (`0.12.1.dev26+...`), and commit SHAs with structural invariant checks (e.g., all referenced workloads/implementations exist, SHA-256 digests match `workloads/circuits/*.stim`, detector subset masks are valid, lockfiles exist).
- **Verify:** `uv run --extra test pytest && uv run --extra test ruff check .`

---

## PR 2: Drop Frozen Clifft 0.1-0.10 Lockfiles, Legacy Shims, and Duplicate Tuning Sweep

**Goal:** Shed dead historical baggage before migrating config formats.

- **Delete:**
  - `environments/clifft-0.1.0.{in,txt}` through `environments/clifft-0.10.0rc1.{in,txt}`
  - Superseded `environments/symft-0.1.0-9ec5790.{in,txt}` and `environments/xtim-3.1.4-2945407.{in,txt}`
  - Redundant `.in` files for single-line packages (retain `.in` header comments inside `.txt` where `uv pip compile` is used)
  - `campaigns/clifft-history-v1/` manifest (the archived evidence in `results/clifft-history-v1/` stays untouched)
  - `campaigns/release-v1/clifft-scheduled-execution.json`
- **Edit:**
  - `src/clifft_bench/adapters/clifft.py` — Remove pre-0.11 `set_num_threads`, `default_bytecode_pass_manager`, `active_k_history`, `_accepts_keyword` docstring-regex scraping, and `native_batch_choices()`.
  - `src/clifft_bench/calibration.py` & `worker.py` — Unify batch calibration onto the single bounded policy (60 s soft budget, 3 x 1 s warmed probes, `(1, 32, 64, 256, 1024, 2048)` for Clifft/SymFT, `(1, 32, 256, 1024, 8192, 65536)` for Tsim, `(256, 1024, 4096, 16384, 65536)` for Stim/xtim). Remove the 38 duplicate `*-tuned` cases from `release-v1` (resolves `#81`).
  - `.github/workflows/ci.yml` — Remove the 12-version `0.1.0..0.12.0rc1` install loop; test `clifft-previous` and `clifft-current` plus SymFT, xtim, Stim, and Tsim.
- **Verify:** `uv run --extra test pytest && uv run --extra test ruff check .`

---

## PR 3: Replace 5 JSON Schemas & 3 JSON Manifests with TOML + Dataclasses, `bootstrap`, and Direct `cases.csv`

**Goal:** Remove `jsonschema`, `schemas/`, `manifests/`, `comparisons.csv`, `index.json`, and the 40 custom env vars.

- **Add:**
  - `workloads.toml`, `software.toml`, `campaigns/release-v1.toml`, `campaigns/smoke.toml`
  - `src/clifft_bench/config.py` (`tomllib` + dataclass validation + `Adapter.validate_options`)
  - `src/clifft_bench/csv_export.py` (`raw.json` -> 25-column `cases.csv` + terminal comparison summary)
  - `scripts/ec2-run.sh` (~35-line EC2 wrapper)
- **Edit:**
  - `pyproject.toml` — Set `dependencies = []` (drop `jsonschema`).
  - `src/clifft_bench/adapters/*.py` — Read adapter-specific settings from `options: dict[str, Any]` and implement `validate_options()`.
  - `src/clifft_bench/system.py` — Add stdlib `urllib.request` IMDSv2 query behind `--require-ec2`; delete `CLOUD_METADATA_ENVIRONMENT` and `RUN_PROVENANCE_ENVIRONMENT`.
  - `src/clifft_bench/runner.py` & `cli.py` — Add `clifft-bench bootstrap` and `clifft-bench csv`; resolve `.campaign-envs/<id>/bin/python` by convention; write `raw.json` + `cases.csv` directly in `clifft-bench run`.
  - Migrate the 5 archived directories under `results/` (`raw/*-raw.json` -> `raw.json`, regenerate 25-column `cases.csv`, delete `comparisons.csv` and `index.json`).
- **Delete:**
  - `schemas/` (all 5 `.schema.json` files)
  - `manifests/` (all `.v1.json` files, including `run-smoke`, `run-coverage-smoke`, `run-tsim-*`, `workloads`, `software`)
  - `campaigns/release-v1/run.v1.json`
  - `examples/result.v1.json`
  - `src/clifft_bench/schema.py`, `src/clifft_bench/manifest.py`, `src/clifft_bench/results.py`
  - `scripts/ec2/` (`common.sh`, `bootstrap.sh`, `run-placement.sh`, `finalize.sh`; retain `scripts/generate_full_d5.py`)
- **Verify:** Run smoke suite end-to-end, verify all 5 migrated `results/*/*/cases.csv` tables, and run `pytest` + `ruff`.

---

## PR 4: Unify & Slim `reporting/` (`clifft-bench report`)

**Goal:** Cut reporting from ~1,560 lines across two subprojects down to one ~450-line module reading `cases.csv` directly.

- **Add:**
  - `reporting/sources.toml`
  - `src/clifft_bench/report.py` — Compute release ratios and cross-tool comparisons directly from `cases.csv` (no `comparisons.csv` or `_case_map()` cross-auditing), fold in `quantum-volume-{light,dark}.png`, rename `v012-vs-v011-{light,dark}.png` to `release-change-{light,dark}.png`, and wire `clifft-bench report`.
- **Delete:**
  - `reporting/qec.py`, `reporting/sources.json`, `reporting/__init__.py`
  - Unused paper figures (`reporting/figures/*.png`) and unused `reporting/figures/web/clifft-vs-symft-{light,dark}.png`
  - Old `reporting/figures/web/v012-vs-v011-{light,dark}.png`
  - `experiments/qv/qv_experiment/plot.py`
- **Edit:** `README.md` (update `v012-vs-v011` image reference to `release-change`) and `tests/test_reporting.py`.
- **Verify:** `uv run --extra report clifft-bench report` and verify rendered PNGs match existing charts on the reviewed v0.12 data.

---

## PR 5: Slim `experiments/qv` and `experiments/tsim-gpu` into Single-Script Studies

**Goal:** Collapse ~2,500 lines of duplicate experiment mini-harnesses into two ~200-line scripts while preserving their checked-in result CSVs.

- **Edit / Replace:**
  - `experiments/qv/`: Replace the 8-file `qv_experiment/` package (`run_benchmark.py`, `worker.py`, `system.py`, `validate.py`, `generator.py`, `qasm_adapter.py`) with a single concise `run_qv.py` (accepting any installed Clifft version rather than hardcoding `0.10.0rc1`) + minimal smoke test; keep `experiments/qv/results/qv-0.10.0rc1-20260902/`.
  - `experiments/tsim-gpu/`: Replace the 6-file `tsim_gpu/` package (`run.py`, `worker.py`, `finalize.py`, `common.py`) with a single concise `run_tsim_gpu.py` + minimal smoke test; keep `experiments/tsim-gpu/results/tsim-gpu-20260909-131821/`.
- **Verify:** Run unit/smoke tests in main repo and `experiments/`.

---

## PR 6: Consolidate Documentation into 3 Crisp Files

**Goal:** Replace 9 overlapping, PR-diary Markdown files with 3 clean, non-repetitive documents.

- **Rewrite concisely:**
  - `README.md` — Reader overview and current figures.
  - `docs/benchmark-contract.md` — ~150-line present-tense specification of workload semantics (`raw-record-parity`), timed boundaries, calibration policy, `raw.json` + `cases.csv` format, and the `m7a.xlarge` reference host.
  - `CONTRIBUTING.md` — Single linear maintainer checklist: local development, adding a workload, preparing an RC, running `scripts/ec2-run.sh`, running `clifft-bench report`, and copying the 6 light/dark figure pairs to `clifft`.
- **Delete:**
  - `docs/data-format.md`
  - `docs/result-schema.md`
  - `docs/reference-host.md`
  - `docs/manual-ec2.md`
  - `docs/plan/` *(temporary refactor planning docs)*
  - `reporting/README.md`
  - `workloads/README.md` and `workloads/coverage.md` *(fold circuit provenance and selection semantics summary table into `docs/benchmark-contract.md`)*
- **Companion follow-up in `clifft`:**
  - Update `docs/development/releasing.md` to link to `clifft-bench`'s `CONTRIBUTING.md` instead of `reporting/README.md`, and update `v012-vs-v011-{light,dark}.png` to `release-change-{light,dark}.png` on the next performance refresh.
