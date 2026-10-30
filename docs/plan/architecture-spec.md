# Release benchmark architecture

## Purpose and scope

Make comparisons between current Clifft, the previous release, and other
simulators easy to collect, inspect, and publish. Keep enough evidence for later
analysis while allowing representative workloads and measurement methods to
evolve. The recurring workflow answers:

- What improved or regressed since the previous Clifft release?
- How does current Clifft compare with other tools on the same work?

Historical trends, Quantum Volume studies, and GPU studies are optional analyses.
They do not impose compatibility requirements on the recurring harness. This is
the target design; [the staged plan](staged-pr-plan.md) describes its implementation.

## Measurement contract

Each campaign measures previous and current Clifft together, with the relevant
other tools. A comparison requires identical circuit bytes, selection and output
semantics, attempted shots per public call, timing boundaries, and resource
limits. Internal batching is calibrated independently for each implementation.
Keep short-call and bulk comparisons distinct. Compare supported intersections;
a new workload without a previous-release baseline may appear in current
throughput results without a release-change value.

Preserve fresh worker processes, interpreter isolation, thread caps before
imports, CPU affinity, Linux memory limits, native-stdout isolation, and hard
worker deadlines. Preserve raw-record parity, explicit detector subsets, and
independent simulator correctness checks. Failures, unsupported combinations,
and combinations not selected for measurement must remain distinguishable.

The workload set may change between campaigns. Give a changed circuit or output
contract a new identity and record the actual definition in each run. A
permanent six-workload core and an unbroken historical release chain are not
requirements. Performance comparisons describe the measured release
configurations; attributing a change to an individual optimization needs a
separate controlled experiment.

## Configuration and environments

Use Python 3.12+ `tomllib` and small typed configuration objects. Replace the JSON
configuration manifests and their schemas with these files:

| File | Owns |
| --- | --- |
| `workloads.toml` | Circuit paths and digests, provenance and licenses, expected circuit metadata, output/selection semantics, compatible adapters, default call sizes, and reusable workload groups |
| `software.toml` | Active implementation identities, exact versions and source pins, display labels, imports, and build/install settings |
| `campaigns/release-v1.toml` | Reference host, resource and measurement settings, variants, workload selection, call-size overrides, and explicit baseline/candidate comparison groups |
| `campaigns/smoke.toml` | Short correctness and workflow checks using the same configuration model |

Groups and optional presets remove repeated lists and option dictionaries; they
do not introduce another configuration language. Validate required fields,
unknown keys, values, references, and selection masks when loading configuration.
Dataclasses organize validated values; they do not validate types automatically.
Keep validation direct rather than implementing a generic schema framework.

Simulator-specific settings travel in `options`, validated by the owning adapter.
Configuration listing must not import installed simulator packages. The core
owns shared measurement semantics, not a growing list of simulator knobs.

Resolve environments as `.campaign-envs/<implementation-id>/bin/python` and
locks as `environments/<implementation-id>.txt`. `bootstrap` installs the
campaign's selected implementations with their declared build settings, checks
dependencies and imports, and verifies identities. An explicit local interpreter
override may be useful for smoke work; official collection requires isolated,
verified environments. Retain dependency input files only where they serve the
maintained build workflow.

Use the standard library for the core and keep plotting/testing dependencies in
extras. Removing `jsonschema` should reduce code and maintenance rather than
replace it with an equally elaborate custom validator.

## One calibration policy

Maintain one bounded harness policy for new runs. Use a 60-second soft search
budget checked between candidates, warm each candidate, take three probes of at
least one second, select the best median attempted-shot rate, and break exact
ties toward the smaller batch. Keep hard setup/request deadlines and memory
limits: a preparation or public call can overrun the soft search budget.

Initial tool-specific candidate sets are:

- Clifft/SymFT: `1, 32, 64, 256, 1024, 2048`, restricted to the public call size.
- Tsim: `1, 32, 256, 1024, 8192, 65536`, capped at the public call size and deduplicated.
- Stim/xtim: output chunks `256, 1024, 4096, 16384, 65536`, capped at the public
  call size and deduplicated, plus the full public call size.

Apply the same policy to previous/current Clifft and freeze selected settings
before final measurement. Record candidate order, probes, failures, budget skips,
search time, memory evidence, and effective settings in raw results. Unsupported
batch sizes may fail while another candidate succeeds.

This is a chosen harness policy, not a claim of equivalence to native tuning or
of finding a global optimum. Remove `native_batch_choices()` and the parallel
`grid-v1`/`bounded-v2` execution paths. Review selected batches, failures, and
search costs during the first normal reference-host campaign; ongoing support
for both policies is unnecessary.

## Results and validation

Each new execution has a unique directory:

```text
results/<campaign>/<execution-id>/
  raw.json
  cases.csv
```

`raw.json` is the authoritative evidence. Retain a format identifier, collection
time, execution/source identity, host and resource metadata, the resolved
campaign and comparison definitions, exact software identities, workload bytes'
digests and semantics, effective options, calibration, setup/warmup/correctness
records, individual samples, and structured failure details. Keep compilation,
active-width, and memory evidence for later analysis even when no default figure
uses it. Recorded configurations must be sufficient to interpret a run without
consulting today's campaign files.

`cases.csv` is a deterministic projection for reporting and review. Include case,
variant, workload, and implementation identity; status and error
information; call size and effective batching; sample counts and throughput
summaries; and useful setup/compilation/memory diagnostics. Pick columns for
actual consumers, with no fixed count or blanket ban on useful adapter-derived
diagnostics. Keep detailed adapter settings in raw metadata. Retain failed rows
and leave their throughput summaries empty.

Compute ratios on demand. New executions do not need `comparisons.csv`,
`index.json`, or a multi-placement finalization framework. Preserve completed
raw evidence on worker failure, write results safely, and refuse accidental
overwrite of an existing execution.

Validate at meaningful boundaries: configuration loading, simulator outputs,
loading persisted evidence, and forming comparisons. Reject duplicate or missing
expected cases, incompatible comparison inputs, and invalid numerical values.
Use the run's recorded comparison definitions to distinguish intentional absence
from incomplete collection. Do not replay calibration arithmetic or cross-audit
multiple stored representations of the same derived result.

## CLI and reference-host collection

| Command | Responsibility |
| --- | --- |
| `clifft-bench bootstrap [CAMPAIGN]` | Prepare and verify selected simulator environments |
| `clifft-bench list [CAMPAIGN] [--json]` | Expand selected cases without running simulators |
| `clifft-bench run [CAMPAIGN] --output-dir DIR [--require-ec2]` | Collect isolated cases and write raw evidence plus the derived table |
| `clifft-bench csv RAW_JSON [--output CASES_CSV]` | Regenerate the table for the supported result format |
| `clifft-bench report EXECUTION_DIR [--output-dir DIR] [--check]` | Validate one execution's comparisons and render its release report |

Keep practical run controls for execution identity, case selection, CPU, memory,
sample duration, and repetitions. Record overrides and identify partial or smoke
runs clearly. `--require-ec2` checks the clean source checkout and reference-host
requirements using a bounded IMDSv2 query in Python, including instance type,
architecture, OS, lifecycle, and memory configuration. Preserve cloud provenance.

Replace shell environment-variable plumbing with a small `scripts/ec2-run.sh`
wrapper for host prerequisites, the shutdown guard, bootstrap, and collection.
Honor the campaign's overall deadline in addition to worker timeouts. Shell size
is not a target. Keep `scripts/generate_full_d5.py`, its dependency pins, circuit
licenses, provenance, and detector-selection support.

## Release reporting

The default report reads one execution's `cases.csv` and the run metadata needed
to interpret and validate its comparisons. It requires neither old executions
nor a `sources.toml` release chain. Use stable light/dark filenames:

- `clifft-throughput-{light,dark}.png`
- `simulator-throughput-{light,dark}.png`
- `release-change-{light,dark}.png`

Include a concise review summary with exact measured versions and failures.
Only form ratios from compatible successful pairs. Show missing baselines and
unsupported/unmeasured combinations honestly; never replace failures with zero
throughput or silently select only successful outcomes.

Adder diagnostics, Quantum Volume plots, GPU figures, and long-term trends may
have separate scripts or explicit optional report paths. They are not inputs to
the default report or mandatory release outputs. Retire unused styles and plots
when their consumers have been updated; preserve useful underlying measurements.

## Historical analysis

Do not migrate archived executions into the new format or keep old readers,
adapters, lockfiles, and CI jobs solely to support them. Record a pinned Git
revision for the legacy code and evidence. Old result folders may remain
untouched or leave the active tree when their consumers no longer need them.

A frozen figure or small numerical export with source references is optional,
not a prerequisite for this refactor. Aggregate points suffice to redraw an old
chart. Extending the former cumulative calculation requires per-workload values:
chain each workload's ratios before aggregating, not the medians themselves.

Future trends can tell the story of performance on an evolving representative
suite. Record and label changes in workloads, tuning, timing, and hardware; do
not imply a precise same-task speedup across those changes. Separate segments or
occasional milestone reruns are acceptable. Historical plots need not be refreshed
every release and must not gate the regular report or CI.

## Experiments, documentation, and completion

Keep QV and GPU studies focused on their own questions. Remove duplicated
infrastructure where that reduces complexity, while preserving process deadlines,
correctness checks, recovery where needed, and failure evidence. Small modules
are allowed; neither single-file scripts nor line-count targets are requirements.
Avoid a second general-purpose harness or a new framework just to share helpers.

Use `README.md` for the reader overview, `docs/benchmark-contract.md` for
measurement semantics and data meaning, and `CONTRIBUTING.md` for the maintainer
workflow. Remove duplicated instructions and PR diaries. Focused experiment or
provenance documentation may remain useful; there is no file-count quota or
filename-based approval gate. Keep `AGENTS.md` short and link to these owners.

Completion means one command collects a reviewable campaign, one command produces
release and cross-tool comparisons, evidence explains what ran and what failed,
and routine release updates touch few places. Preserve the current workload
coverage during conversion, test behavior and structural invariants, and make
historical regeneration optional. See the staged plan for transition checks.
