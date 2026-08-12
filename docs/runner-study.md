# Runner A/A studies

The runner study measures identical Clifft work against itself before this
repository assigns performance meaning to small release-to-release changes. It
does not compare simulators and its exploratory outputs are not official
benchmark results.

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

During an evidence window the workflow runs at 01:17 and 13:17 UTC each day,
away from GitHub's high-load start-of-hour window, and it can also be dispatched
manually. The scheduled path always uses three replicas and the full profile
defaults. For the initial larger-runner commissioning run, select one replica,
one repetition, and a one-second minimum sample. Commissioning output checks
the runner label, installation, result validation, summarization, and artifact
upload; it is not part of the evidence set.

A successful job uploads its schema-valid raw result, a per-pair CSV, and a
JSON summary. If an individual case fails, the job uploads the raw result and a
partial summary that identifies skipped pairs before retaining a failed job
status. Failures before result creation may have no benchmark artifact.

Collect six full larger-runner dispatches over three days before evaluating it
against the standard-runner evidence. The three replicas within one dispatch
are not a substitute for temporal coverage. Remove the temporary schedule when
that evidence set is complete; manual dispatch remains useful for later spot
checks.

The repository has a $10 GitHub Actions budget with paid usage stopped at the
limit. After collection, calculate the study cost from the completed job usage
and reconcile it with the repository-filtered GitHub billing report. Queued
time is not billed. Record the billed minutes, per-minute runner rate, and total
cost in the evidence PR before removing the temporary schedule.

## Analysis

Download raw JSON artifacts from multiple workflow runs, then run:

```bash
uv run clifft-bench analyze-aa results/*runner-aa-*-raw.json \
  --output-json results/runner-study-summary.json \
  --output-csv results/runner-study-pairs.csv
```

The analyzer rejects pairs whose workload, artifact, implementation, commit,
or execution settings differ within or across raw result files. Unsuccessful
pairs are listed under `skipped_pairs`; healthy pairs remain usable. It reports
absolute throughput, the B/A ratio, and the symmetric absolute pair difference.
Summaries include median, MAD, 90th and 95th percentiles, minimum, and maximum,
grouped by workload and a hardware key derived from CPU model, topology,
memory, architecture, and runner image OS.

The analyzer intentionally does not declare a regression threshold. The
evidence PR will select an inconclusive region only after reviewing multiple
jobs and hardware strata. Raw results remain authoritative; CSV and summary
JSON files are derived and reproducible.

## Runner decision

Compare the standard and larger-runner cohorts only after the six-dispatch
larger-runner window is complete. The decision should consider paired-ratio
noise, absolute-throughput variation, hardware consistency, operating cost,
and operational reliability. Consider a dedicated cloud host only if neither
GitHub-hosted option provides enough repeatability for release decisions.
