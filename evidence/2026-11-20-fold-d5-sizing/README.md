# Fold-transversal d5 call size for 0.14.0rc1

Use 1,024 attempted shots per public call for fold d5 across the two release profiles. This retains a directly measured Clifft 0.13.0 baseline and gives the newer samplers more work per call than 256 shots. The 0.13.0 one-core fold d3 baseline completed its original 262,144-shot call in 0.78 seconds.

## Observations

The original 262,144-shot fold d5 call on Clifft 0.13.0 with one physical core was interrupted during first-call warm-up after approximately 10 minutes 50 seconds. It produced no completed sample. The partial execution preserves the interrupt. [Preflight raw evidence](preflight-raw.json)

The sizing check used Clifft 0.13.0, Clifft 0.14.0rc1 and Tsim 1.0.0. All 20 supported configurations across the two sizes passed correctness with a 32 GiB worker ceiling on the Ubuntu 24.04 AWS `m7a.4xlarge`. The host had restarted between the preflight and the sizing check. Both used clean harness commit `eee0ef9a6958c47cdc983659f71ed2b29e80e71b`.

Each configuration had one full first call, an untimed correctness check and one timed repetition of at least 0.001 seconds. The table gives seconds per public call, computed as timed duration divided by the number of full calls in that repetition. Fast-call timings serve as a sizing check. The official campaign uses three repetitions of at least 15 seconds. [Sizing raw evidence](raw.json) · [Case table](cases.csv)

| Implementation | Endpoint | Physical cores | 256 shots (s/call) | 1,024 shots (s/call) |
| --- | --- | ---: | ---: | ---: |
| clifft-0.13.0 | Survivor counts | 1 | 7.1456 | 26.582 |
| clifft-0.13.0 | Survivor counts | 8 | 9.2825 | 33.954 |
| clifft-0.14.0rc1 | Packed samples | 1 | 0.0051768 | 0.020653 |
| clifft-0.14.0rc1 | Packed samples | 8 | 0.0026904 | 0.0028279 |
| clifft-0.14.0rc1 | Survivor counts | 1 | 0.00013424 | 0.0003387 |
| clifft-0.14.0rc1 | Survivor counts | 8 | 0.0001307 | 0.00032702 |
| tsim-1.0.0 | Packed samples | 1 | 0.0096528 | 0.028484 |
| tsim-1.0.0 | Packed samples | 8 | 0.010357 | 0.015448 |
| tsim-1.0.0 | Survivor counts | 1 | 0.0012338 | 0.0020681 |
| tsim-1.0.0 | Survivor counts | 8 | 0.0013196 | 0.0047478 |

At 1,024 shots, the two Clifft 0.13.0 cases together would spend approximately 4 min 10 s on first calls and the three timed repetitions, assuming the observed call durations repeat. Setup and correctness are additional. For scale, a linear extrapolation of the 1,024-shot timed calls to 262,144 shots gives about 1.9 hours for one core and 2.4 hours for eight cores per call. These estimates do not enter benchmark results.
