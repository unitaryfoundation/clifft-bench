# GitHub runner A/A evidence

These results measure pinned Clifft 0.7.0 against itself. They characterize
measurement noise and runner allocation; they are not simulator comparisons or
official throughput claims.

## Cohorts

Both cohorts used two workloads, six 30-second alternating repetitions per A/A
slot, and three independent jobs per workflow dispatch. Every result validated,
both A/A pairs succeeded, and no observation was skipped.

| Cohort | Window (UTC) | Dispatches | Jobs | CPU models | Runner images | Paired observations |
|---|---:|---:|---:|---:|---:|---:|
| Standard `ubuntu-24.04` | 2026-08-10–12 | 6 | 18 | 5 | 2 | 216 |
| 8-vCPU `ucc-benchmarks-8-core-U22.04` | 2026-08-13–16 | 7 | 21 | 3 | 2 | 252 |

The standard cohort used workflow runs
[31393394827](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31393394827),
[31394687395](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31394687395),
[31450804215](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31450804215),
[31497996881](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31497996881),
[31555272611](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31555272611),
and
[31603372323](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31603372323).
Its 18 jobs were allocated 11 AMD EPYC 7763, four AMD EPYC 9V74, and one
each of three Intel Xeon models. Sixteen jobs used image `20260720.247.2` and
two used `20260810.271.1`.

The larger-runner cohort used workflow runs
[31659297813](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31659297813),
[31706863022](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31706863022),
[31762167063](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31762167063),
[31806330052](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31806330052),
[31857548257](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31857548257),
[31887227514](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31887227514),
and
[31920595656](https://github.com/unitaryfoundation/clifft-bench/actions/runs/31920595656).
Its 21 jobs were allocated three AMD EPYC 7763, ten AMD EPYC 9V74, and eight
Intel Xeon Platinum 8573C CPUs. Eleven jobs used image `20260720.234.2` and ten
used `20260810.260.1`.

## Relative-measurement noise

The individual statistic is the symmetric absolute difference for every
alternating A/B repetition. It is useful for diagnosing raw sample noise, but it
is not the release-decision statistic.

For each independent job and workload, the decision statistic first takes the
median of `log(B/A)` across its repetitions. It then takes the median of those
centers across the three jobs in a dispatch and converts the result back to a
symmetric percent difference. This gives each independent job equal weight and
prevents one unusually noisy repetition from dominating a dispatch.

| Cohort | Workload regime | Individual median | Individual p95 | Individual max | 3-job dispatch median | 3-job dispatch p95 | 3-job dispatch max |
|---|---|---:|---:|---:|---:|---:|---:|
| Standard | short public calls | 0.44% | 1.61% | 3.62% | 0.13% | 0.32% | 0.34% |
| Standard | long public calls | 0.60% | 6.42% | 14.08% | 0.28% | 0.48% | 0.52% |
| Larger | short public calls | 0.23% | 1.93% | 2.46% | 0.14% | 0.49% | 0.54% |
| Larger | long public calls | 0.54% | 5.71% | 20.93% | 0.22% | 0.94% | 1.08% |

The larger runner did not improve the statistic that matters for comparisons.
The maximum dispatch-level A/A difference was larger in both workload regimes,
although both cohorts are small. A conservative provisional rule for these two
regimes is therefore to treat an absolute three-job result of 1.1% or less as
inconclusive. A result outside that band is a candidate change to confirm, not
an automatic performance claim. A future reference host needs its own A/A
commissioning data before adopting a tighter band.

## Absolute throughput and hardware allocation

Neither GitHub pool supplied fixed hardware. On the larger runner, the median
short-call throughput was approximately 506,129 attempted shots/s on AMD EPYC
7763, 792,172 on AMD EPYC 9V74, and 673,350 on Intel Xeon Platinum 8573C. The
fastest CPU stratum was therefore about 57% above the slowest. For the long-call
workload, the corresponding medians were 0.359, 0.373, and 0.306 attempted
shots/s, about a 22% fastest-to-slowest spread.

CPU-name normalization would not repair this: it would change the question from
an absolute reference result to a model-dependent estimate, and several free
runner CPU strata contain only one job. Absolute results from these pools must
be reported by exact hardware stratum. A deliberately selected reference host
is the cleaner source of a single official throughput number.

Hardware grouping includes architecture, CPU name, topology, runner image OS,
and memory rounded to the nearest GiB. The raw and CSV observations retain exact
memory. This avoids splitting an otherwise identical runner because Linux
reported a few pages more or less memory; each group reports the observed
minimum and maximum exact values.

## Larger-runner cost

The completed job timestamps imply 294 estimated billable minutes using
per-job whole-minute rounding: one commissioning minute plus 293 minutes for
the seven full dispatches. At the Linux 8-core rate recorded for the study of
$0.022 per minute, the estimate is **$6.47** (`294 * $0.022 = $6.468`). This
should be reconciled with the repository-filtered billing report; the API token
used for the study did not have billing access.

## Decision

- Keep standard GitHub-hosted runners for correctness CI and, when useful,
  exploratory same-job paired comparisons.
- Do not use the paid larger runner as a canonical performance host. It was
  neither fixed hardware nor measurably more stable in this study.
- Do not interpret unstratified GitHub-hosted throughput as an absolute number.
- Select and commission a separate, explicitly described reference host for
  infrequent official comparisons and absolute throughput results.

## Files and reproduction

`free/raw/` and `larger/raw/` contain the schema-valid workflow results and are
authoritative. Each cohort's `pairs.csv` and `summary.json` are derived with:

```bash
uv run clifft-bench analyze-aa results/runner-study/free/raw/*-raw.json \
  --output-json results/runner-study/free/summary.json \
  --output-csv results/runner-study/free/pairs.csv

uv run clifft-bench analyze-aa results/runner-study/larger/raw/*-raw.json \
  --output-json results/runner-study/larger/summary.json \
  --output-csv results/runner-study/larger/pairs.csv
```

The summary's `pair_groups` contain pooled repetition diagnostics;
`dispatch_estimates` and `dispatch_groups` contain the release-style estimator;
and `groups` retain hardware-stratified throughput and pair distributions.
