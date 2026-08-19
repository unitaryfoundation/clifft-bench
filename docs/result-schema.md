# Raw result schema

JSON is the source result format. The current schema identifier is
`clifft-bench/result/v1`, implemented by
[`schemas/result-v1.schema.json`](../schemas/result-v1.schema.json).

A result contains one run envelope, one runner snapshot, and one record per
selected case. The envelope records both the campaign ID and the isolated run
ID within that campaign. Successful cases include setup provenance, affinity outcome,
warmup counts, an out-of-band correctness check, every raw timing sample, and a
robust summary. The effective request deadline is recorded with the execution
configuration. Error cases preserve their exact failure phase and message
without fabricating throughput.

When an external launcher supplies a complete cloud identity, the runner
snapshot also records provider, instance and image IDs, instance type,
region/AZ, lifecycle, and Linux boot ID. The benchmark harness never contacts a
cloud metadata service itself; the EC2 playbook verifies IMDSv2 and passes the
complete identity through an explicit environment.

Validate any result with:

```bash
clifft-bench validate path/to/result.json
```

Schema and benchmark-suite versions are distinct. A backward-compatible corpus
or adapter change increments the suite version; an incompatible JSON shape
introduces a new schema identifier.

Multiple raw results are grouped by a `clifft-bench/execution/v1` index during
finalization. See [`data-format.md`](data-format.md) for that layout and the
derived long-form tables.
