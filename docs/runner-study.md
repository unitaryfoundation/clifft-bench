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
per dispatch. The second window used the GitHub-managed larger runner labeled
`ucc-benchmarks-8-core-U22.04`. It is an 8-vCPU Ubuntu 22.04 runner rather than
a self-hosted machine. The organization runner pool has a maximum concurrency
of one, and the workflow also sets `max-parallel: 1`, so the three replicas run
serially even if the pool is expanded later.

The third window targeted the Ubicloud-managed
`ubicloud-standard-4-ubuntu-2404` shape: four x86-64 vCPUs, 16 GiB of memory,
and an explicit Ubuntu 24.04 image family. Although the workflow requested the
standard label, all nine full jobs received premium AMD Ryzen 9 7950X3D
hardware because premium routing was enabled for the account. Ubicloud
documents the two hardware families and its account-wide premium routing in its
[runner documentation](https://www.ubicloud.com/docs/github-actions-integration/runner-types).
Every raw result records the CPU model, topology, memory, kernel, runner image
metadata, and selected CPU. The workflow serialized its three independent
ephemeral jobs with `max-parallel: 1`.

The workflow installs `requirements/runner-study.txt`, which exactly pins
Clifft and each performance-sensitive dependency named by its software
manifest. When changing Clifft, update that requirements file and the software
manifest together. Any intentional requirement change starts a new evidence
cohort. This keeps an unrelated dependency release from silently changing
software during a study.

The larger-runner evidence window collected seven full dispatches from August
13 through August 16, 2026. The Ubicloud window collected three full dispatches
from August 18 through August 19 after a one-replica commissioning run. A fourth
scheduled dispatch was canceled during collection in its first replica; it did
not produce a full dispatch, and the other two replicas never allocated. The
workflow was removed to stop the experiment.

A successful job uploads its schema-valid raw result, a per-pair CSV, and a
JSON summary. If an individual case fails, the job uploads the raw result and a
partial summary that identifies skipped pairs before retaining a failed job
status. Failures before result creation may have no benchmark artifact.

The three completed Ubicloud dispatches consumed approximately 125 premium
runner minutes using per-job whole-minute rounding, or about $0.50 at the
documented four-vCPU premium rate before any
[monthly credit](https://www.ubicloud.com/docs/about/pricing). This remains an
estimate until reconciled with provider billing.

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

## Completed runner decision

The standard runner remains suitable for correctness CI and exploratory
same-job paired comparisons. The larger runner is not the canonical performance
host: it supplied three CPU models across two image versions, did not reduce
dispatch-level A/A noise, and cost money without making absolute throughput
portable. Neither GitHub pool supports an unstratified official absolute
number. A separately selected and commissioned reference host is the next
candidate for infrequent release/tool comparisons and absolute results.

## Completed Ubicloud decision

All nine full jobs used the same Ryzen 9 7950X3D model, four-vCPU/two-core
topology, 16 GiB memory class, Ubuntu image, and kernel. Provisioning and job
completion were reliable, but performance stability did not commission the
runner:

- short-workload absolute dispatch A/A differences were 0.91%, 0.73%, and
  0.03%;
- long-workload differences were 2.80%, 4.07%, and 0.74%, exceeding the 1.1%
  provisional band in two of three dispatches;
- job-median absolute throughput spanned approximately 26% for the short
  workload and 32% for the long workload, while three-job dispatch medians
  still spanned about 10%.

Ubicloud therefore did not solve the absolute-throughput requirement, and its
paired A/A measurements did not improve on the free standard GitHub-hosted
cohort. The scheduled workflow was removed after the third full dispatch. A
separately controlled reference host remains the candidate for absolute
throughput, while standard GitHub-hosted runners remain suitable for
correctness CI and exploratory paired comparisons.
