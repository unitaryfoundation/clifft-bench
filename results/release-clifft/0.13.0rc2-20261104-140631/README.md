# Clifft 0.13.0rc2 release measurements

Official `release-clifft` collection on 2026-11-04, 14:06:31–15:29:24 UTC.
All 29 cases completed successfully: nine previous-release cases, eleven current
short-call cases, two current bulk cases, and seven current coverage cases.
There are 145 measured samples, five of at least 30 seconds per case.

The current published wheel is `clifft==0.13.0rc2` at
`7e83ec2823bf99c54aa1b90c54a092641c10c4b0`. The previous published wheel remains
`clifft==0.12.0rc1` at `847757e2bc8c4f7439b9e92011550fd61719eb85` for continuity.
Their original dependency locks are preserved: NumPy 2.5.4 for RC2 and NumPy
2.5.2 for the previous baseline. This is a comparison of those release
environments, not an experiment isolating the NumPy version.

Collection used the clean harness at
[`3c89445367581edba4d9b7ec5c72e53c4e68eddc`](https://github.com/unitaryfoundation/clifft-bench/tree/3c89445367581edba4d9b7ec5c72e53c4e68eddc),
with the committed
[`release-clifft` campaign](https://github.com/unitaryfoundation/clifft-bench/blob/3c89445367581edba4d9b7ec5c72e53c4e68eddc/campaigns/release-clifft.toml).
The host was an AWS `m7a.xlarge` in `us-east-1c`, Ubuntu 24.04, AMD EPYC 9R14,
Python 3.12.3. Fresh workers ran serially on logical CPU 0 with one thread and a
12 GiB address-space ceiling. The standard calibration, scheduler, seeds,
warmup, correctness, call sizes, and output semantics were retained.

## Results

The [generated review](report/review.md) contains all outcomes, selected batches,
and current/previous median-throughput ratios. RC2 measured 8.21% higher Draper
throughput, 2.96% higher d3 injection/cultivation throughput, and 0.18% higher d5
injection/cultivation throughput. The largest decrease was 0.52% for coherent
d5/r5. The 15-to-1 distillation and Reed–Muller cases measured 222.99× and 16.22×
the previous-release throughput, respectively.

Both d5 injection/cultivation cases selected batch 1 after full calibration.
Each configuration has one fresh measured worker in this serial campaign;
the five samples quantify within-worker variability, not variation across
independent workers or placements. This run does not establish behavior for a
different preparation history. No additional diagnostic repeat was performed.

The figures use the renderer at
[`48898bb3f9c1882e109197925b8cb2c5318e15c6`](https://github.com/unitaryfoundation/clifft-bench/tree/48898bb3f9c1882e109197925b8cb2c5318e15c6),
separately from the collection revision above:

- [Simulator throughput, light](report/simulator-throughput-light.png) and
  [dark](report/simulator-throughput-dark.png) show 14 circuit rows with
  RC2 Clifft and the retained peer measurements, grouped by workload theme with
  direct throughput labels. Bulk variants,
  selection-off controls, and the unverified d7 proxy remain in the detailed
  results rather than the main figure.
- [Release changes, light](report/release-change-light.png) and
  [dark](report/release-change-dark.png) show the nine measured release pairs
  in the same thematic order. Small changes use a percentage scale with a
  shaded ±1.1% inconclusive band; larger changes use a separate logarithmic
  ratio scale.

Peer throughput comes from the official
[2026-11-03 RC1 campaign](https://github.com/unitaryfoundation/clifft-bench/tree/1e16ed76602a1fac4623a1f38d163b049146eed0/results/release-v1/0.13.0rc1-20261103-183623):
run `031a93c5-a1c7-4dc3-a045-3f8163cdc50b`, collected 18:41:59–23:17:33 UTC.
Its 41 peer cases retain Stim 1.16.0, SymFT 0.1.1, xtim 3.2.2, and Tsim 0.1.5.
The [peer raw evidence](https://github.com/unitaryfoundation/clifft-bench/blob/1e16ed76602a1fac4623a1f38d163b049146eed0/results/release-v1/0.13.0rc1-20261103-183623/raw.json)
has SHA256 `5590e18927b75c15dba650ef0a9d6141aa027bbb1b22140b1d981b92342dad80`.

Peer tools were not rerun. The report validates matching hardware, protocol,
workload digests, selection semantics, and call sizes before reusing those
values. The companion review records both collection dates. Absolute rates
come from separate collections; all paired release changes use only the
RC2 execution. Each source keeps its original raw evidence and CSV. Figure
labels use intended final versions 0.13.0/0.12.0; raw evidence and the generated
review retain exact RC and peer identities. These are review artifacts, not an
update to published website figures.

## Evidence and validation

- [raw.json](raw.json) is authoritative; [cases.csv](cases.csv) is its exact
  deterministic projection.
- [environment.json](environment.json) records installed versions, dependency
  locks as installed, host metadata, wheel URLs/SHA256 hashes, and installed
  extension/METADATA hashes. Installed files matched the published wheels;
  both wheels are retained in the durable local collection backup.
- [console.log](console.log) and [exit-status.json](exit-status.json) record
  collection completion and exit code 0.
- Every copied remote evidence file, including both wheels, matched its local
  SHA256. [SHA256SUMS](SHA256SUMS) covers the files committed here.
- The pinned harness accepted the raw result and regenerated byte-identical
  CSV; `report --check` passed. All correctness, counts, sample seeds, CPU,
  thread, memory, scheduler, and exact-identity checks passed. The shortest
  measured sample was 30.00000149 seconds; maximum sample peak RSS was
  451,141,632 bytes. Both light and dark figures were visually reviewed.
- Calibration retained 158 candidate results: 157 successes and one guarded
  rejection. QV20 batch 32 exceeded Clifft's 64 MiB packed-state limit; its
  successful batch-1 candidate was selected and all five measured samples
  succeeded. This is retained in the raw calibration evidence.

To regenerate the review and figures, use the renderer revision linked above
with both committed source directories:

```bash
uv run --extra report clifft-bench report \
  results/release-clifft/0.13.0rc2-20261104-140631 \
  --peer-results results/release-v1/0.13.0rc1-20261103-183623
```

The original RC2 raw data, CSV, collection log, environment, and exit status are
byte-identical to the collected evidence. The peer execution is also unchanged.
The RC2 collection revision remains recorded in `raw.json` and the generated
review; the report identifies the peer collection separately.
