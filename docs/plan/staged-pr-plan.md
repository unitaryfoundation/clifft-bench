# Implementation plan

Implement [the release benchmark architecture](architecture-spec.md) in focused
PRs that leave the active workflow and CI usable. Change producers and consumers
together; remove an old input only after its remaining consumers are updated.
No stage requires migrating archived results or reproducing historical figures.

Keep these temporary planning documents on the planning branch, linked from
[#91](https://github.com/unitaryfoundation/clifft-bench/issues/91). Each PR lands
current behavior in its owning contract or workflow documentation.

## PR 1: Establish scope and focused checks

- Land the concise `AGENTS.md` and make the release-over-release/cross-tool scope
  clear in contributor guidance. Keep design details out of agent instructions.
- Replace prose and shell-substring assertions in `tests/test_ec2_playbook.py`
  with behavior checks where operational behavior needs coverage. Keep checks
  for installation settings, deadlines, provenance, and overwrite prevention
  when their implementations move; do not preserve brittle assertions merely
  because they already exist.
- Replace transient version pins and exact counts in `tests/test_manifests.py`
  with reference integrity, artifact digests, valid selection masks, matched
  comparison semantics, and environment availability checks.
- Record one pinned legacy code/results revision in the documentation. A frozen
  plot or numerical export is optional; leave existing evidence unmodified.

Verify documentation references, the existing test suite, and Ruff. Do not create
new tests that assert the wording or length of these planning documents.

## PR 2: Retire historical execution and consolidate calibration

- Remove the historical campaign, obsolete simulator environments, old adapter
  shims, and the historical-version CI loop. Keep support for active previous
  and current Clifft and the maintained comparison tools. Retain files still
  used by active consumers until those consumers change.
- Update `calibration.py`, `worker.py`, and the adapters to the single bounded
  policy in the specification. Remove native proposals and duplicate policy
  execution paths; retain candidate failure and budget evidence. Update the
  current writer/validator and tests in the same PR without changing the result
  layout consumed by the existing report yet.
- Remove the 38 duplicate tuned cases. At the starting manifest revision this
  leaves 70 of 108 cases, including all 23 coverage cases introduced in #90.
  Treat these as a migration inventory, not permanent counts in unit tests.
  Preserve selection-off controls, repeated QV sampling, cultivation stress
  inputs, full d5 detector subsets, and short-call/bulk distinctions.
- Update #81 to record the deliberate single-policy scope decision. Do not claim
  native-tuning equivalence or completion of the original comparative experiment.

Verify calibration selection, failures and soft-budget handling, plus real-adapter
smokes for maintained implementations. Use the next normal reference-host run to
review chosen settings and search costs; do not schedule a separate paid sweep
as a prerequisite to the architecture work.

## PR 3: Simplify configuration and environment setup

- Add `workloads.toml`, `software.toml`, `campaigns/release-v1.toml`, and
  `campaigns/smoke.toml`, with a small `config.py` loader and typed objects.
  Preserve explicit comparison groups and every retained case's executable
  contract. Represent all coverage cases, not only the older QEC group.
- Move simulator settings and their validation into adapter options. Preserve
  `selection.py`, `scripts/generate_full_d5.py`, generator pins, and circuit
  provenance/licenses.
- Add `bootstrap` and conventional implementation environments. Move reference
  host checks and bounded IMDSv2 discovery into Python; replace `scripts/ec2/`
  with the small collection wrapper once its behavior is covered.
- Update active manifest consumers together. Delete old configuration manifests,
  config schemas, redundant option files, and custom environment plumbing once
  unused. Keep any result schema/helper still required by the current writer or
  report until PR 4; do not build a permanent dual-configuration loader.

Compare the expanded case definitions before and after conversion, excluding
only intentional policy changes/removals. Exercise bootstrap, listing, option
validation, mocked host metadata, and smoke collection. Keep the existing result
writer/report usable during this stage.

## PR 4: Replace result production and release reporting together

- Implement deterministic `csv_export.py` and update the runner/CLI to write
  new executions as `raw.json` plus `cases.csv`. Include the resolved campaign,
  comparison definitions, and provenance in raw evidence so later reporting
  does not depend on the current configuration files.
- Add `clifft-bench report EXECUTION_DIR` and `--check` in `report.py`. Produce
  the three default light/dark figure pairs and a concise review summary from
  that execution alone. Use metadata for compatibility checks without replaying
  calibration or auditing a second stored comparison table.
- Update `tests/test_results.py`, `tests/test_reporting.py`, CLI consumers, and
  CI together. Then remove obsolete `results.py`/schema helpers, the `jsonschema`
  dependency, new-run `index.json`/`comparisons.csv` production, and the old
  recurring report/source-chain dependency. Historical folders are not converted.
- Update `README.md` references and publication instructions in the same PR.
  Retain existing published images as frozen evidence until reviewed new results
  replace them; smoke-generated images are verification artifacts, not published
  performance evidence. Coordinate renamed asset/link consumers in `clifft`.
- Keep history, QV, GPU, and adder-specific plotting optional. A history script
  may use a frozen numerical export if useful; it must not become a release or
  CI prerequisite. An aggregate-only export redraws history but cannot extend
  the old per-workload cumulative calculation by multiplying aggregate medians.

Verify a complete smoke run through CSV regeneration and reporting. Check paired
arithmetic, incompatible inputs, expected/missing cases, unsupported baselines,
recorded failures, and deterministic export. Inspect the three figure pairs.
Use focused fixtures for report behavior; exact legacy numerical/PNG equivalence
is not an acceptance criterion. This PR must work without archived executions.

## PR 5: Focus experiments and finish documentation

- Simplify `experiments/qv` and `experiments/tsim-gpu` where duplication or dead
  workflows remain. Keep useful module boundaries, subprocess deadlines,
  correctness checks, recovery, and recorded failures. Do not rewrite either
  study merely to hit a file or line count, or add a generic experiment framework.
- Consolidate recurring methodology in `docs/benchmark-contract.md` and the
  maintainer workflow in `CONTRIBUTING.md`; keep `README.md` reader-focused.
  Remove superseded data-format, host, EC2, and reporting instructions after
  moving useful content to its owner. Retain focused experiment instructions
  and necessary workload provenance instead of imposing a documentation quota.
- Update the companion `clifft` release instructions to publish the three
  recurring pairs. Historical, QV, GPU, and special-purpose figures remain
  separately maintained when useful.

Run relevant experiment checks and verify documentation/asset references.

## Validation and first release

For code changes run `uv run --extra test pytest` and
`uv run --extra test ruff check .`, plus the adapter, experiment, and end-to-end
checks relevant to each stage. Replace brittle tests with meaningful behavior
coverage; do not introduce tests for documentation wording or size targets.

The first normal reference-host campaign must demonstrate fresh previous/current
measurements, compatible cross-tool comparisons, preserved coverage, clear failure
outcomes, and auditable calibration and timing evidence. Review that evidence
before publishing new performance claims. Future release preparation should
require updating active software identities and deliberate campaign selections,
not historical environments, source chains, or unrelated experiments.
