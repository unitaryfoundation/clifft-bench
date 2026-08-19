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

The fourth window used one manually operated On-Demand `m7a.xlarge` in
`us-east-1c`, with four physical AMD EPYC 9R14 cores, 16 GiB of memory, and the
recorded Canonical Ubuntu 24.04 AMI. Three stop/start boots each collected three
serial replicas. Distinct Linux boot IDs establish distinct boots; EC2 does not
expose enough information to claim distinct physical hosts. The checkout,
instance, AMI, region, availability zone, kernel, Python, dependency, affinity,
and thread identities remained fixed across all nine results.

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

The EC2 window collected three complete placements and nine results on August
19, 2026. Installation and instance control remained outside the benchmark;
the reference-host scripts retained raw data outside the checkout and prepared
one normal result commit after validating the full cohort.

## Analysis

To regenerate a committed cohort, run:

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
summarize those estimates across dispatches. The same nesting also reports one
absolute-throughput center per dispatch and its distribution across
dispatches. This gives each replica equal weight and prevents a noisy
repetition from masquerading as independent evidence.

Across the GitHub runner pools and workloads, the largest absolute
dispatch-level A/A estimate was 1.08%; the EC2 candidate's maximum was 0.71%.
Because its three boots were collected in one afternoon, 1.1% remains the
conservative provisional inconclusive band for this three-replica design until
time-separated release campaigns add evidence. Exceeding it warrants
confirmation on another boot; it is not by itself an official regression. Raw
results remain authoritative; CSV and summary JSON files are derived and
reproducible.

## Completed runner decision

The standard runner remains suitable for correctness CI and exploratory
same-job paired comparisons. The larger runner is not the canonical performance
host: it supplied three CPU models across two image versions, did not reduce
dispatch-level A/A noise, and cost money without making absolute throughput
portable. Neither GitHub pool supports an unstratified official absolute
number. The manual EC2 cohort below evaluates the separately controlled
reference-host approach.

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
cohort. The scheduled workflow was removed after the third full dispatch. The
manual EC2 cohort supplied the separately controlled candidate, while standard
GitHub-hosted runners remain suitable for correctness CI and exploratory paired
comparisons.

## Completed EC2 decision

All nine EC2 results validated, all cases succeeded, and every case applied
one-core affinity. The three-replica placement estimates were:

| Workload regime | Placement A/A differences | Placement throughput centers | Symmetric min/max span |
|---|---:|---:|---:|
| Short public calls | 0.714%, 0.041%, 0.057% | 788,811; 791,667; 791,408 shots/s | 0.36% |
| Long public calls | 0.009%, 0.051%, 0.192% | 0.3860; 0.3782; 0.3761 shots/s | 2.60% |

Within one boot, the largest min/max span among the three replica centers was
0.35% for the short workload and 0.53% for the long workload. This is materially
better absolute stability than either managed candidate and commissions the
stopped `m7a.xlarge` provisionally for infrequent reference measurements.

The long workload still exposes individual-sample granularity: each 30-second
sample completed only 10–14 public calls, and pooled raw sample throughput had
an 8.29% relative MAD. Paired aggregation was stable, but an official absolute
number for a slow workload must report the median and range across placements,
not one sample or an over-precise scalar.

Use one boot with three serial replicas for routine paired release or tool
comparisons. Treat changes at or below 1.1% as inconclusive, and confirm a
larger candidate change on another boot. For absolute results, use three
stop/start placements and publish their median and range. The first real
time-separated release campaigns also serve as continued commissioning
evidence; this one-afternoon study does not establish month-to-month stability.
