# Working in clifft-bench

Read `README.md`, `CONTRIBUTING.md`, and `docs/benchmark-contract.md` for
orientation, workflows, and measurement semantics. Keep this file focused on
working rules; put design details in their owning documentation.

- Prioritize current-versus-previous release and current-versus-other-tool
  comparisons. Historical analysis is optional and must not gate routine work.
- Preserve process isolation, thread caps before simulator imports, CPU and
  memory limits, protocol isolation, and simulator correctness checks.
- Retain raw sample evidence and exact workload, software, and measurement
  identities. Validate inputs and comparison compatibility at their boundaries;
  do not replay calibration decisions or maintain redundant derived tables.
- Keep simulator options and their validation in adapters. Prefer simple typed
  configuration and standard-library code over another validation framework.
- Maintain one implementation of each recurring workflow. Experiments may use
  focused modules, but must not grow competing general-purpose harnesses.
  Discuss a new framework or expanded maintained scope before implementing it.
- Give each documented rule one canonical home. Write current behavior, avoid
  PR diaries and duplicated methodology, and keep experiment notes specific to
  that study. File counts and module sizes are not architectural requirements.
- Test behavior and structural invariants, not prose, transient version pins,
  exact campaign counts, or the continued absence of deleted features.

Use a feature or data branch, keep commits focused, and include an
`Assisted-by: AGENT_NAME (MODEL_NAME) <PROVIDER_NOREPLY_EMAIL>` trailer.
For Python changes run `uv run --extra test pytest` and
`uv run --extra test ruff check .`, plus relevant experiment or reporting checks.
For documentation-only changes check references and `git diff --check`.
