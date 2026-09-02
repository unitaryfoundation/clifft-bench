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
- use one placement for broad one-off historical trends;
- use three stop/start placements for current absolute throughput;
- cap each single-core worker at 12 GiB of address space, leaving host headroom;
- record exact AMI, region/AZ, CPU model, kernel, dependencies, and boot ID;
- never combine absolute numbers from different hardware epochs without an
  explicit boundary or a bridge measurement.

GitHub-hosted and other shared CI runners remain suitable for correctness or
exploratory work, but do not define the absolute-throughput reference series.
Their commissioning data remains available in repository history rather than
the active tree.
