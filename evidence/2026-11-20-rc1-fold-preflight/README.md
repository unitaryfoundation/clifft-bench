# RC1 fold preflight at the release call size

Both fold-transversal circuits completed their supported cases at 262,144 shots per public call on the Ubuntu 24.04 AWS `m7a.4xlarge`. The preflight selected 30 cases from the full release manifest at the [merged benchmark revision](https://github.com/unitaryfoundation/clifft-bench/commit/dd362f5977c178ad5da5d4588404841f59ae6e6c). All 18 supported cases passed correctness at the requested physical-core budget. Eight xtim cases lacked a verified raw reference, and four Clifft 0.13.0 packed cases used an unsupported endpoint. All 30 case outcomes matched the expected selection.

The smoke run used a full-size first call, the standard untimed correctness check, and one timed repetition of at least 0.001 seconds per supported case. Collection took 48 seconds. The highest recorded sample peak RSS was 0.883 GiB (Tsim, fold d5, packed output, eight cores) under the 32 GiB worker limit. These timings document call completion. The official release comparison uses three repetitions of at least 15 seconds.

| Fold d5 one-core case | First full call | Timed full call |
| --- | ---: | ---: |
| Clifft 0.14.0rc1 survivor counts | 0.072 s | 0.061 s |
| Tsim 1.0.0 survivor counts | 0.318 s | 0.113 s |
| Clifft 0.14.0rc1 packed output | 5.326 s | 5.318 s |
| Tsim 1.0.0 packed output | 6.327 s | 6.088 s |

The two Clifft 0.13.0 fold d5 survivor-count baselines were omitted by the release manifest. [Call-size investigation](https://github.com/unitaryfoundation/clifft-bench/tree/9edce397f5f229e8141c34a0ba0f1afe58bcbcf1/evidence/2026-11-20-fold-d5-sizing)

[Raw execution](raw.json) · [Case table](cases.csv)
