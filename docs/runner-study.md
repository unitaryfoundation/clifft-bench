# GitHub-hosted runner A/A study

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

The `Runner A/A study` workflow starts three independent `ubuntu-24.04` jobs.
It installs `requirements/runner-study.txt`, which exactly pins Clifft and each
performance-sensitive dependency named by its software manifest. When changing
Clifft, update that requirements file and the software manifest together. Any
intentional requirement change starts a new evidence cohort. This keeps an
unrelated dependency release from silently changing software during a study.

During the initial evidence window it runs at 01:17 and 13:17 UTC each day,
away from GitHub's high-load start-of-hour window, and it can also be dispatched
manually. The scheduled path always uses the full profile defaults; manual
inputs can shorten a commissioning run.

A successful job uploads its schema-valid raw result, a per-pair CSV, and a
JSON summary. If an individual case fails, the job uploads the raw result and a
partial summary that identifies skipped pairs before retaining a failed job
status. Failures before result creation may have no benchmark artifact.

Collect six full dispatches over three days before the initial evaluation. The
three replicas within one dispatch are not a substitute for temporal coverage.
Remove the temporary schedule when that evidence set is complete; manual
dispatch remains useful for later spot checks.

## Analysis

Download raw JSON artifacts from multiple workflow runs, then run:

```bash
uv run clifft-bench analyze-aa results/runner-aa-*-raw.json \
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

## Other runners

No self-hosted runner is currently visible to this repository. If the UCC
runner becomes available, add a separately labeled job using this same manifest
and analyzer. Consider a dedicated cloud host only if the hosted-runner study
shows that paired ratios remain too noisy or hardware strata are too sparse for
release decisions.
