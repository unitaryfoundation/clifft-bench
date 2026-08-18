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

The third window uses the Ubicloud-managed
`ubicloud-standard-4-ubuntu-2404` shape: four x86-64 vCPUs, 16 GiB of memory,
and an explicit Ubuntu 24.04 image family. Ubicloud documents its standard x64
runners as dedicated resources on AMD EPYC 9454P processors in its
[runner documentation](https://www.ubicloud.com/docs/github-actions-integration/runner-types).
The study treats that as a claim to verify, not an assumption: every raw result
records the CPU model, topology, memory, kernel, runner image metadata, and
selected CPU. The workflow serializes its three independent ephemeral jobs with
`max-parallel: 1`.

The workflow installs `requirements/runner-study.txt`, which exactly pins
Clifft and each performance-sensitive dependency named by its software
manifest. When changing Clifft, update that requirements file and the software
manifest together. Any intentional requirement change starts a new evidence
cohort. This keeps an unrelated dependency release from silently changing
software during a study.

The larger-runner evidence window collected seven full dispatches from August
13 through August 16, 2026. Its workflow has been replaced by the Ubicloud
candidate. For an Ubicloud commissioning run, select one replica, one
repetition, and a one-second minimum sample. Commissioning output checks runner
allocation, installation, result validation, summarization, and artifact
upload; it is not evidence.

A successful job uploads its schema-valid raw result, a per-pair CSV, and a
JSON summary. If an individual case fails, the job uploads the raw result and a
partial summary that identifies skipped pairs before retaining a failed job
status. Failures before result creation may have no benchmark artifact.

The Ubicloud target is six scheduled dispatch attempts over three days at
01:17 and 13:17 UTC. Each scheduled dispatch uses three replicas and the full
profile defaults. A free GitHub-hosted gate counts prior scheduled attempts for
this workflow and prevents any further Ubicloud allocation after six; manual
dispatches remain available and do not count toward the cap. Remove the
temporary schedule when the resulting evidence and runner decision are
committed. The three replicas within one dispatch are not a substitute for
temporal coverage.

Based on the larger-runner job durations, six full Ubicloud dispatches should
consume about 252 runner minutes. At the documented $0.002/minute price for the
selected four-vCPU standard shape, that is approximately $0.50 before any
[monthly credit](https://www.ubicloud.com/docs/about/pricing). Record actual
usage after collection rather than treating the estimate as billing evidence.

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
summarize those estimates across dispatches. The same nesting also reports one
absolute-throughput center per dispatch and its distribution across
dispatches. This gives each replica equal weight and prevents a noisy
repetition from masquerading as independent evidence.

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

## Ubicloud decision questions

After the six-attempt window, evaluate the candidate without changing the
measurement design:

- Did every job receive the documented EPYC 9454P model with the same topology,
  memory class, and explicit Ubuntu image family?
- Is the three-job dispatch A/A estimator no worse than the provisional 1.1%
  inconclusive band in both workload regimes?
- Within the exact hardware stratum, are absolute throughput distributions
  stable enough to report a single reference-host result and detect hardware
  changes as new cohorts?
- Does actual cost and operational reliability support infrequent on-demand
  release and tool comparisons?

Passing the study commissions this runner configuration; it does not turn the
exploratory A/A measurements themselves into official benchmark results.
