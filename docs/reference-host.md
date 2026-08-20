# Reference host decision

Official measurements currently use a manually operated, shared-tenancy,
On-Demand AWS `m7a.xlarge` running Canonical Ubuntu Server 24.04 LTS on x86-64.
The hardware epoch is `aws-m7a-xlarge-ubuntu2404-v1`.

The commissioning evidence was merged in
[PR #19](https://github.com/unitaryfoundation/clifft-bench/pull/19). Across
three stop/start placements, paired identical-software differences stayed
below 0.72%. Placement-level absolute throughput spanned 0.36% for the short
workload and 2.60% for the slow workload. The latter completed few public calls
per sample, so absolute results must report the median and range across
placements rather than one over-precise scalar.

Use these rules until real campaigns provide enough evidence to revise them:

- treat paired changes at or below 1.1% as inconclusive;
- use one placement for broad historical trends where small drift is acceptable;
- use three stop/start placements for current absolute throughput;
- record exact AMI, region/AZ, CPU model, kernel, dependencies, and boot ID;
- never combine absolute numbers from different hardware epochs without an
  explicit boundary or a bridge measurement.

GitHub-hosted, GitHub larger, and Ubicloud runners remain suitable for
correctness CI or exploratory work, but were not stable enough to define the
absolute-throughput reference series. Their raw commissioning files remain
available in repository history rather than the active tree.
