# Physical-core host check for the 0.14 CPU campaign

Seven of the eight declared configurations passed the surface d7/r7 correctness checks and completed both timed repetitions at 65,536 attempted shots per public call. SymFT survivor counts and both xtim 4.1.2 endpoints reported the requested one or eight effective threads. Stim produced packed samples on one core. Its eight-core configuration returned `UnsupportedConfiguration` during setup, as specified in the campaign.

Every measured worker ran on the requested number of distinct physical cores under the 32 GiB address-space limit. The highest observed peak RSS was 86.9 MiB on the eight-core xtim packed endpoint. The companion pilot exercised Clifft and Tsim on the same instance. [Call-size pilot evidence](https://github.com/unitaryfoundation/clifft-bench/tree/9d2b3ded9020505163526c9a0419d73ba6571f9e/evidence/2026-11-20-call-size)

| Simulator | Endpoint | Cores requested | Effective threads | Median attempted shots/s | Peak RSS (MiB) |
| --- | --- | ---: | ---: | ---: | ---: |
| SymFT 2026.10.8 | Survivor counts | 1 | 1 | 3,630,304 | 51.6 |
| SymFT 2026.10.8 | Survivor counts | 8 | 8 | 25,715,626 | 80.4 |
| xtim 4.1.2 | Survivor counts | 1 | 1 | 13,339,839 | 82.6 |
| xtim 4.1.2 | Survivor counts | 8 | 8 | 42,239,974 | 82.8 |
| xtim 4.1.2 | Packed samples | 1 | 1 | 13,084,842 | 82.7 |
| xtim 4.1.2 | Packed samples | 8 | 8 | 62,273,408 | 86.9 |
| Stim 1.16.0 | Packed samples | 1 | 1 | 1,131,205 | 66.8 |
| Stim 1.16.0 | Packed samples | 8 | — | unsupported | — |

The host check used the same Ubuntu 24.04 `m7a.4xlarge` and boot as the four-circuit call-size pilot. Each case requested one or eight distinct physical cores and a 32 GiB address-space limit. The harness applied thread caps before simulator imports. Each supported case ran two repetitions of at least one second after the first full call. These short repetitions check the host configuration and native endpoints. [Predeclared host-check manifest](https://github.com/unitaryfoundation/clifft-bench/blob/93a0e1997e2ca491d95435b0228d97b071ba9f66/campaigns/physical-core-pilot.toml)

[Raw samples, worker affinity and thread settings](raw.json) · [Case summaries](cases.csv)
