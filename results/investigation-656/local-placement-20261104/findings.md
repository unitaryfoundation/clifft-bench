## Predeclared address-pair test and preparation-history study

The prediction was [published before timing](https://github.com/unitaryfoundation/clifft/issues/656#issuecomment-6636166443). These are local fixed-work diagnostics on the EPYC 9554P/KVM VM, using the exact published RC1 wheel, CPU 0, one thread, ASLR disabled, Python hash seed 0, fixed seeds and the pinned harness's preparation/output semantics. No EC2 work was launched.

### Controlled prediction

Four fresh experimental workers were selected by creation order, plus one unmodified reference. Within each experimental worker, four coefficient configurations used preselected distinct guest PFNs from a newly faulted pool. No pages were selected using timing. N has four different PFN-modulo-16 digits; R matches the two real-array pages; I matches the two imaginary-array pages; X matches a real page to an imaginary page. R/I/X each changes one page relative to N. Both scratch pages, virtual addresses, bytes and code stay fixed.

Each worker completed two measurements of each configuration in balanced forward/reverse order; the reference ran once per round. Every pass used 100 calls x 20,000 shots. All 40 measured passes preserve exact counts, all configured PFNs remain stable before/after timing, and all counters ran at 100% scheduled time. Instruction counts range from 91.666364b to 91.666406b.

| Worker | R/N cycle change | I/N cycle change | X/N cycle change |
| --- | ---: | ---: | ---: |
| 0 | +7.69% | +8.18% | +2.99% |
| 1 | +11.54% | +9.38% | +1.51% |
| 2 | +8.44% | +11.13% | +0.69% |
| 3 | +5.64% | -0.50% | -9.23% |
| Median of worker contrasts | **+8.06%** | **+8.78%** | +1.10% |

The primary thresholds pass: each same-array contrast has median penalty >=3%, with positive direction in at least three of four workers. The negative-control threshold fails: median absolute X/N change is **2.25%**, above the predeclared 2% limit. Worker 3 is particularly informative: N is already slow, and X improves it substantially. The reference ranges from 34.230b to 35.207b cycles.

**Conclusion:** the targeted same-array pattern predicts a substantial tendency in fresh controlled backing sets. It is neither a complete speed classifier nor an established CPU mechanism. Cross-array relationships or other backing interactions can also matter. Guest PFNs do not expose host physical mappings. These four workers are not a release population estimate.

Audit caveat: an initial Python list-aliasing bug changed the probe's stored `original` list while configuring N. Consequently, the terminal phase named `restore-original` returned to N rather than the pre-pool coefficient frames. The originals remain correctly recorded in each pre-intervention `*-setup.predictions.json`. The initial setup event's `before` list is also affected. All timed N/R/I/X configurations, fixed scratch pages and byte checks are independently valid; all workers exited. The exact script, corrected reproduction script and explicit restoration audit are retained. No timing was replaced or discarded, and the earlier separate A/B/A/B experiment is unaffected.

### Preparation history

Eight direct and eight fully calibrated fresh workers ran in four DCCD blocks. A read-only breakpoint in a separate diagnostic call recorded the actual live State address/PFNs, with no page exchange. Both histories received the same extra calls. Each worker then completed two identical fixed-work measurements. All six calibration candidates completed in every calibrated worker, and all selected batch 1. Live addresses and frames agreed before and after the measurements for all 16 workers.

| History | Median worker cycles | Worker cycle range | Workers with a same-array modulo-16 match |
| --- | ---: | ---: | ---: |
| Direct batch 1 | 35.645b | 34.728–41.094b | 1/8 |
| Full calibration then batch 1 | 35.787b | 34.984–39.856b | 2/8 |

Calibrated median cycles are +0.40% relative to direct. Both histories have broad worker variation. The three same-array matches cost 36.160–41.094b cycles; the 13 without one cost 34.728–39.399b. Several slow workers lack the tested pattern, and an imaginary-array match occurs in a comparatively fast worker.

**Conclusion:** this run does not reproduce a material preparation-history median split. The simple pair rule does not account for the full fresh-worker spread. This observational study does not establish that calibration caused the original EC2 difference by changing backing; it also cannot disprove that explanation for other allocations or hosts. The causal backing result remains the separate controlled intervention.

### Follow-up decision

Validate the small metadata-hoisting patch independently. Hold the proposed huge-page allocator change: the observed address-bit rule is incomplete, and it does not yet justify predicting that contiguous guest backing will remove the spread. If allocator mitigation is revisited, measure actual backing, full API cost and memory use rather than treating a huge-page request as successful allocation. Keep the issue open for the remaining dependency and for reference-host confirmation. The local result does not change the maintainer's accepted release-risk decision.

## Normal-build metadata-hoisting validation

Parent: `7e83ec2823bf99c54aa1b90c54a092641c10c4b0`. Candidate: `7e1099bfe4024c1bfb11b3dc3afabfeece5f60f8` on `codex/hoist-high-pivot-metadata`. The only source change snapshots State size and Pauli X/Z masks in the serial AVX-512 high-pivot rotation template, covering both phase cases. Arithmetic and lane permutations are unchanged.

Matched builds use Clang 22.1.8, ThinLTO, lld, x86-64-v2 baseline, Release fast-math, nanobind 2.11.0, Python 3.12.14, NumPy 2.5.4 and the same OpenMP 18 runtime. Function alignments 16/32/64 provide three build perturbations. These change code generation/alignment; they are not independent randomized samples from every possible layout. They are local CMake installations, not shipped manylinux wheels.

There are 72 fresh timing workers: two per build/workload cell, two fixed-work passes per worker, 144 total passes. Parent/candidate order reverses between workers. All workers verify the exact extension hash, use AVX-512, CPU 0, one thread, fixed seeds and a 12 GiB ceiling. Preparation uses the pinned adapter and numeric batch settings; calibration is excluded. D5 uses 50 calls x 20,000 shots per pass. Other call/shot counts are retained in `normal-performance/protocol.json`. These shorter diagnostics are not the release harness's >=30-second samples. No compilation or correctness suite overlaps timing.

The table compares medians of worker medians within each alignment. Negative values mean less work/time in cycles.

| Workload | Instruction change across settings | Cycle change, alignment 16 | Alignment 32 | Alignment 64 |
| --- | ---: | ---: | ---: | ---: |
| Cultivation d5 | **-3.618%** | -3.896% | -7.849% | -0.241% |
| Coherent d5 | **-3.122%** | -2.008% | -3.316% | -1.613% |
| QV-10 | **-0.450%** | +0.445% | -1.864% | -1.096% |
| Draper control, active width 0 | +0.342% to +0.346% | -3.083% | -2.732% | -1.284% |
| Cultivation d3 control | approximately 0% | +0.209% | -0.497% | -2.326% |
| Surface d7 control, active width 0 | approximately 0% | -1.074% | +1.185% | -1.118% |

The width-zero rows do not execute the changed kernel. Their cycle movements, and Draper's small instruction movement, show why whole-build measurements must not all be attributed to the source edit. The robust claim is the target-workload instruction reduction backed by assembly, not a universal speedup or a release percentage. D5 cycle improvement is positive in all three settings but nearly flat in one; two workers per cell are too few to establish a population spread reduction. Preparation/backing sensitivity is not resolved.

Linker maps were regenerated and all six installed artifacts matched their original hashes. In each matched pair, the direct AVX-512 entry address is the same. Assembly shows X/Z descriptor reads and State width reads moved out of the high-pivot loops. Both real/imaginary phase loops benefit. Sibling AVX2 and other rotation paths contain related reload patterns, but this patch stays with the measured hot path; no performance claim is made for those siblings.

Validation:
- 1,245 C++ tests passed with assertions enabled, including existing direct-rotation SIMD/scalar comparisons.
- 778 Python compiler-conformance/sampling-integration tests passed; 25 skipped.
- 162 seeded full-output checks across six workloads and six builds agree exactly, covering scalar and configured packed batches, measurement/detector/observable bytes, expectation-value bytes and survivor counts.
- All 144 performance passes preserve their workload's exact aggregate counts across builds; all perf counters run at 100% scheduled time.
- Repository-required pre-commit checks pass. The committed diff exactly matches the measured candidate patch.
- All three studies' CSVs and JSON summaries regenerate byte-for-byte. Artifact and archive hashes are retained. Both probe audit notes are included; no timing was replaced or discarded.

Proceed with the scoped kernel PR. Keep allocator changes and the remaining causal mechanism in this issue. Before any source bisect for d5, require a repeated matched-build difference that survives preparation/backing controls; these backing interventions do not by themselves identify a source regression. A later reference-host check should first test whether the local observations generalize, then measure the actual release wheel with the pinned release harness.
