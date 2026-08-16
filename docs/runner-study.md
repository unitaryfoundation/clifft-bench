# Runner A/A studies

The runner study measured identical Clifft work against itself before this
repository assigns performance meaning to small release-to-release changes. It
does not compare simulators and its exploratory outputs are not official
benchmark results. The raw cohorts, derived summaries, measurements, and final
runner decision are committed under
[`results/runner-study/`](../results/runner-study/README.md).

## Collection design

`manifests/run-runner-aa.v1.json` declares two A/A pairs using the same pinned
Clifft 0.7.0 implementation in both slots:

- the distance-3 cultivation workload represents high-throughput, short public
  sampling calls;
- the distance-5, five-round coherent workload represents long public sampling
  calls.

Each pair remains resident in two isolated workers. Samples run serially in
alternating `A/B`, then `B/A` order on one selected logical CPU. The default
profile records six 30-second samples per slot.

The first evidence window used three independent standard `ubuntu-24.04` jobs
per dispatch. The second window uses the GitHub-managed larger runner labeled
`ucc-benchmarks-8-core-U22.04`. It is an 8-vCPU Ubuntu 22.04 runner rather than
a self-hosted machine. The organization runner pool has a maximum concurrency
of one, and the workflow also sets `max-parallel: 1`, so the three replicas run
serially even if the pool is expanded later.

The workflow installs `requirements/runner-study.txt`, which exactly pins
Clifft and each performance-sensitive dependency named by its software
manifest. When changing Clifft, update that requirements file and the software
manifest together. Any intentional requirement change starts a new evidence
cohort. This keeps an unrelated dependency release from silently changing
software during a study.

The larger-runner evidence window collected seven full dispatches from August
13 through August 16, 2026. Its temporary twice-daily schedule has been removed;
the workflow remains manually dispatchable for later spot checks. For a
commissioning run, select one replica, one repetition, and a one-second minimum
sample. Commissioning output checks the runner label, installation, result
validation, summarization, and artifact upload; it is not evidence.

A successful job uploads its schema-valid raw result, a per-pair CSV, and a
JSON summary. If an individual case fails, the job uploads the raw result and a
partial summary that identifies skipped pairs before retaining a failed job
status. Failures before result creation may have no benchmark artifact.

The collection target was six full larger-runner dispatches over three days;
seven were collected before the temporary schedule was removed. The three
replicas within one dispatch are not a substitute for temporal coverage. The
completed larger-runner study used an estimated 294 billable minutes including
commissioning, or $6.47 at the recorded $0.022/minute rate. The calculation and
billing-reconciliation caveat are recorded with the evidence.

## Analysis

To regenerate either committed cohort, run:

```bash
uv run clifft-bench analyze-aa results/runner-study/free/raw/*-raw.json \
  --output-json results/runner-study/free/summary.json \
  --output-csv results/runner-study/free/pairs.csv
```

The analyzer rejects pairs whose workload, artifact, implementation, commit,
or execution settings differ within or across raw result files. Unsuccessful
pairs are listed under `skipped_pairs`; healthy pairs remain usable. It reports
absolute throughput, the B/A ratio, and the symmetric absolute pair difference.
Summaries include median, MAD, 90th and 95th percentiles, minimum, and maximum.
`pair_groups` are pooled repetition-level diagnostics. `groups` stratify them
by a hardware key derived from CPU model, topology, GiB-scale memory,
architecture, and runner image OS; exact observed memory remains in the raw
results and CSV.

`dispatch_estimates` are the comparison statistic. Within each independent job
the analyzer takes the median of `log(B/A)` across repetitions, then takes the
median of the three job centers from one workflow dispatch. `dispatch_groups`
summarize those estimates across dispatches. This nesting gives each replica
equal weight and prevents a noisy repetition from masquerading as independent
evidence.

Across both studied runner pools and workloads, the largest absolute
dispatch-level A/A estimate was 1.08%. Until a selected reference host has its
own commissioning evidence, 1.1% is the provisional inconclusive band for this
three-job design. Exceeding it warrants confirmation; it is not by itself an
official regression. Raw results remain authoritative; CSV and summary JSON
files are derived and reproducible.

## Runner decision

The standard runner remains suitable for correctness CI and exploratory
same-job paired comparisons. The larger runner is not the canonical performance
host: it supplied three CPU models across two image versions, did not reduce
dispatch-level A/A noise, and cost money without making absolute throughput
portable. Neither GitHub pool supports an unstratified official absolute
number. A separately selected and commissioned reference host is the next
candidate for infrequent release/tool comparisons and absolute results.
