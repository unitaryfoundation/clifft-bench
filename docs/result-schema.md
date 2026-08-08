# Raw result schema

JSON is the source result format. The current schema identifier is
`clifft-bench/result/v1`, implemented by
[`schemas/result-v1.schema.json`](../schemas/result-v1.schema.json).

A result contains one run envelope, one runner snapshot, and one record per
selected case. Successful cases include setup provenance, affinity outcome,
warmup counts, an out-of-band correctness check, every raw timing sample, and a
robust summary. Error cases preserve their failure phase and message without
fabricating throughput.

Validate any result with:

```bash
clifft-bench validate path/to/result.json
```

Schema and benchmark-suite versions are distinct. A backward-compatible corpus
or adapter change increments the suite version; an incompatible JSON shape
introduces a new schema identifier.
