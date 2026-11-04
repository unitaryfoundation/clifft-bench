# EC2 backing sensitivity persists after the metadata-hoisting patch

The controlled backing effect reproduces on the clifft-bench EC2 reference instance. All four parent workers and all four patch workers use more cycles after changing one coefficient page to the predeclared same-array PFN-modulo-16 relationship. The median penalty is **8.43% before the patch and 7.86% with it**. The patch reduces instructions by **3.59%**, but leaves a substantial backing dependency.

The patch cohort has a modestly smaller penalty in all four chronological blocks. The median penalty decreases by 0.56 percentage points. This is consistent with partial mitigation, but the parent and patch processes do not share the same actual physical frames. The strongest causal result is the reversible within-worker effect; the between-build attenuation remains a small repeated-cohort comparison.

## Experiment

[Protocol published before EC2 timing](https://github.com/unitaryfoundation/clifft/issues/656#issuecomment-6638147165). Protocol SHA-256: `d9f7373c4571bceb06d5e3779bdb263f2c98cc059b6c5df7b7307091d7c84158`.

Host: existing m7a.xlarge reference instance, AMD EPYC 9R14, four vCPUs, Ubuntu 24.04, kernel 7.0.0-1014-aws. Perf 7.0.14 works. All workers use CPU 0, AVX-512, direct batch 1, thread limits set before import, ASLR disabled, Python hash seed 0 and a 12 GiB address-space ceiling.

Parent source: `7e83ec2823bf99c54aa1b90c54a092641c10c4b0`. Patch source: `7e1099bfe4024c1bfb11b3dc3afabfeece5f60f8`, from [Clifft #657](https://github.com/unitaryfoundation/clifft/pull/657). The normal function-alignment-16 binaries are copied from the preceding validated local study without rebuilding. The same Python 3.12.14, NumPy 2.5.4, OpenMP library, pinned benchmark adapter and d5 workload are transferred with them. All 5,512 input-file hashes match remotely. The host's libc/libstdc++ and loaded files are recorded separately.

The PR advanced to `5a012372a0bb9545338f2c7cbd04eb45ca5f912e` during this run. That follow-up adds one explanatory source comment; the archived GitHub comparison confirms no executable source change. The measured artifact identity remains the earlier commit above.

There are four fresh experimental workers per build, selected by creation order, and one unchanged reference worker per build. Each experimental worker receives a faulted anonymous pool. Frames are selected by address alone before timing; no worker or page is selected by speed. N uses four distinct coefficient-page PFN-modulo-16 digits [a,b,c,d]. R replaces only the upper real-array page with another full PFN whose low digit matches the lower real-array page, yielding [a,a,c,d]. Virtual addresses, current bytes, code and both scratch pages remain fixed within each worker. Each reversal returns to the exact selected frames.

The four chronological parent/patch blocks use NRRN, RNNR, NRRN and RNNR, with rotated block execution order and reversed build order on odd rounds. Each measured pass is 100 API calls x 20,000 attempted shots with seeds 20270819 through 20270918. Each experimental worker has two passes per configuration, and each reference has four passes. Total: **40 measured passes and 80 million attempted shots**. Individual measured passes last 9.10-10.22 seconds; these are fixed-work diagnostics, not the release harness's >=30-second samples.

## Results

The penalty is `100 * (median(R cycles) / median(N cycles) - 1)`. Positive values mean R needs more cycles for the same work.

| Chronological block | Parent penalty | Patch penalty | Patch minus parent |
| --- | ---: | ---: | ---: |
| 0 | +8.40% | +7.80% | -0.60 percentage points |
| 1 | +8.31% | +8.04% | -0.27 percentage points |
| 2 | +10.15% | +7.92% | -2.23 percentage points |
| 3 | +8.45% | +7.78% | -0.67 percentage points |
| Median of worker penalties | **+8.43%** | **+7.86%** | **-0.56 percentage points** |

Parent penalties range from 8.31% to 10.15%; patch penalties range from 7.78% to 8.04%. The cycle difference between the two repetitions of any single worker/configuration is at most 0.351%. Both configurations remain reproducible through the balanced reversals.

Median instruction counts are 92.869232369 billion for the parent and 89.539594440 billion for the patch, a **3.5853% reduction**. Instructions remain effectively unchanged between N and R within each build. This supports reduced instruction work independently of the remaining cycle penalty. These whole-process counter values are specific to this host/runtime and should not be treated as universal constants.

The unchanged parent reference varies by 0.421% across its four cycle measurements; the patch reference varies by 0.237%. Worker CPU/wall ratios are 0.999835-0.999891. The ten-second activity log has 43 snapshots over 420 seconds, with zero reported CPU-0 steal in every interval and more than 14.3 GiB MemAvailable. No sustained competing build/test workload appears in that log. These observations support a quiet run; finite snapshots do not prove perfect isolation or exclude invisible hypervisor effects.

## Validation and audit

- All 40 measured passes preserve the exact totals: 2,000,000 attempted shots, 287,417 accepted, 1,712,583 discarded and zero logical errors. Every separate diagnostic sample also preserves its expected totals.
- All cycles/instructions counters run at 100% scheduled time.
- All six State PFNs are checked before and after every measurement. A separate diagnostic call after each measurement confirms the actual live State address and frames. Both scratch frames stay original, and all repeat configurations use their expected frames.
- The eight experimental workers restore their original six frames, checked against independent pre-intervention records. All ten measured workers exit cleanly. The original perf/ptrace settings are restored.
- The measured cohort completes 54 exact seeded full-output checks at setup and after restoration. The two untimed smoke workers add 36 such checks, including each configured backing condition. Full measurement/detector/observable and expectation-value bytes and survivor counts match across builds.
- All derived summary JSON files and the sample CSV regenerate byte-for-byte on the local VM. The remote evidence archive and all 676 per-file hashes match after retrieval.
- The earlier local attempt is retained as interrupted evidence: eight passes completed before concurrent C++ test activity was detected. Those samples do not enter this EC2 comparison.
- The first EC2 launcher incorrectly restricted the controller to CPU 1. The child worker inherited that mask and the existing harness rejected CPU 0 before preparation. It produced zero measured samples. That failed start is retained under `setup-failure-affinity`; the corrected launcher leaves controller affinity unrestricted while workers select CPU 0. No measured EC2 observation is replaced or excluded.

The input archive was recompressed from gzip to xz for publication; every input-file hash is unchanged. The exact builds/runtime are included to avoid depending on a future rebuild. No SSH keys or AWS credentials are included.

## What this establishes and what remains

1. The backing dependency is relevant to the EC2 reference host under these normal builds. It is no longer solely an observation from the shared local VM.
2. #657 remains a useful reduction in instruction work. This cohort is consistent with a modest reduction in the tested backing penalty, while leaving approximately 8% more cycles in the unfavorable configuration. It is not a complete sensitivity fix.
3. This does not identify the CPU or VM mechanism. Guest PFNs do not reveal host physical mappings. Only the real-array relationship is tested here; the previous cross-array counterexample and other unexplained local behavior remain relevant.
4. This does not explain why preparation history reversed the earlier previous-release/RC comparison. Both builds here use direct batch 1, and the comparison is PR parent versus patch, not previous release versus RC.
5. These measurements do not establish a population spread reduction, a shipped-wheel percentage or a new release regression. The normal-build instruction claim and the maintainer's accepted release-risk decision remain separate from the unresolved mechanism.

Proceed with review/merge of #657 and keep #656 open. A further mechanism study can use this verified reversible condition as its starting point. Keep the huge-page allocator proposal on hold until an intervention addresses the demonstrated dependency and its full API/memory cost is measured. A source bisect still requires a repeated source effect that survives preparation/backing controls. No additional campaign is started by this result.

## Instance shutdown

The maintainer confirmed that instance-initiated shutdown is configured to Stop. A two-hour fallback was armed before the experiment. After validated evidence was copied off, `sudo shutdown -h now` returned successfully at **2026-11-04 18:16:02 UTC**. SSH access cannot establish the final AWS lifecycle state; the maintainer will confirm `stopped`. EBS data is retained under the confirmed Stop configuration.
