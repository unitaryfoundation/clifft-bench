# Working in clifft-bench

Canonical doc owners (give each rule one home; keep design details out of `AGENTS.md`):
- `README.md`: reader overview and published figures.
- `docs/benchmark-contract.md`: measurement, fairness, and output semantics.
- `CONTRIBUTING.md`: maintainer, release, and EC2 workflows.

- **Active scope only:** Maintain current-vs-previous Clifft and current-vs-peer
  comparisons. Historical analysis and `experiments/` are optional and must not
  gate routine work.
- **Replace, do not shim:** Delete superseded pins, variants, adapter shims, and
  legacy readers rather than keeping backward-compatibility fallbacks.
- **No speculative plumbing:** Do not add dependencies, CLI flags, campaign
  variants, metadata keys, or CSV columns unless requested or consumed by an
  active check or default report. Prefer standard-library code and extending
  existing modules over new files, layers, or validation frameworks.
- **One harness:** Keep simulator options and their validation in adapters.
  Experiments may use small focused modules, not competing benchmark harnesses.
- **Stop instead of working around:** If a tool or change conflicts with the
  contract or expands maintained scope, stop and ask instead of adding parallel
  cohorts, workarounds, or documentation rationalizations.
- **Current state, not PR diaries:** Document current behavior only; do not
  append per-PR history, pilot narratives, or duplicated methodology.
- **Measurement integrity:** Preserve fresh worker processes, thread caps before
  simulator imports, CPU/memory limits, protocol isolation, correctness checks,
  and raw sample/identity evidence. Validate at input and report boundaries; do
  not replay calibration math or maintain redundant derived tables.
- **Test invariants, not snapshots:** Test behavior and structural relationships,
  not prose, version pins, exact case counts, or the absence of deleted code.

Use a feature or data branch, keep commits focused, and include an
`Assisted-by: AGENT_NAME (MODEL_NAME) <PROVIDER_NOREPLY_EMAIL>` trailer.
For Python changes run `uv run --extra test pytest` and
`uv run --extra test ruff check .`, plus relevant experiment or reporting checks.
For documentation-only changes check references and `git diff --check`.
