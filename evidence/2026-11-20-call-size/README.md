# Public call sizes for the Clifft 0.14 CPU campaign

| Circuit | Selected attempted shots per public call | Decision evidence |
| --- | ---: | --- |
| Fast cultivation, MSC d3 | 262,144 | Eight-core median throughput rose from 97.0M to 120.8M shots/s for Clifft and from 17.8M to 22.9M for Tsim. |
| Surface code d7/r7 | 262,144 | Eight-core medians rose from 44.2M to 49.3M for Clifft and from 38.8M to 42.3M for Tsim. The packed output was 10.75 MiB per larger call. |
| Coherent surface d5/r5 | 16,384 | The slowest first full call at this size was 1.41 s. Peak RSS reached 1.32 GiB. |
| Cain 15-to-1 d9 | 16,384 | Each packed call returned 28.98 MiB, and peak RSS stayed below 3.65 GiB. With eight cores allocated, Clifft reported two effective threads at 4,096 and eight at 16,384. |

All 32 declared cases passed correctness and completed all five repetitions in a single execution. On both fast circuits, every eight-core repetition at 262,144 exceeded every repetition at 65,536 for the same simulator. Both wide circuits fit the time, memory, and output budgets at 16,384. Each selected size applies across endpoints, simulators, and core counts. Quantum Volume q20 was outside the pilot and retains the provisional 32-shot exception. [Decision rules and prior q20 measurement](https://github.com/unitaryfoundation/clifft-bench/issues/124)

Changing call size preserved the Clifft/Tsim ordering at each fixed core count for all four circuits. Changing the core count did change the ordering for surface d7/r7 and Cain d9. Tsim led at one core and Clifft led at eight cores at both call sizes on those circuits.

## Measurements

The pilot used Clifft development commit `f1bb0d883495b73ba3e834be6d61d9c440ddfef5` (OpenMP, x86-64-v2) and Tsim 1.0.0. Each case ran in a fresh worker with one or eight pinned physical cores on an Ubuntu 24.04 `m7a.4xlarge` and a 32 GiB address-space limit. Both tools used native automatic internal batching. Each pair used the same circuit bytes, raw-parity convention, endpoint and public call size. [Pilot manifest](https://github.com/unitaryfoundation/clifft-bench/blob/1ec3ce492a376cc9b0dc4982d477cf3d93651df0/campaigns/call-size-pilot.toml) · [Clifft source](https://github.com/unitaryfoundation/clifft/commit/f1bb0d883495b73ba3e834be6d61d9c440ddfef5) · [Tsim source](https://github.com/QuEraComputing/tsim/commit/136bcc6bec7edba88bbb28152da05e33d17f77d0)

Each rate is the median attempted shots/s over five repetitions of at least 30 seconds after the first full call. MAD is the median absolute deviation of those five rates. Peak RSS is the highest resident-memory measurement among the repetitions. Packed MiB/call counts the detector and observable byte arrays together. MSC d3 and coherent d5/r5 returned survivor counts. Surface d7/r7 and Cain d9 returned packed detector and observable samples.

[Raw samples, worker settings and circuit identities](raw.json) · [Case summaries](cases.csv)

### `msc-d3-inject-cultivate-p1e-3`

| Physical cores | Shots/call | Simulator | Effective threads | Median shots/s | MAD shots/s | Compile (s) | First full call (s) | Peak RSS (MiB) | Packed MiB/call |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 65,536 | Clifft | 1 | 16,608,603.0 | 2,298.5 | 0.02652 | 0.008796 | 39.1 | — |
| 1 | 65,536 | Tsim | 1 | 12,583,954.0 | 4,187.8 | 0.07732 | 0.8478 | 349.0 | — |
| 1 | 262,144 | Clifft | 1 | 16,627,142.2 | 711.9 | 0.0063 | 0.01697 | 38.9 | — |
| 1 | 262,144 | Tsim | 1 | 16,123,213.5 | 3,717.6 | 0.03575 | 0.2444 | 353.0 | — |
| 8 | 65,536 | Clifft | 8 | 97,014,609.5 | 41,236.1 | 0.0063 | 0.002183 | 43.3 | — |
| 8 | 65,536 | Tsim | 8 | 17,814,145.6 | 52,744.0 | 0.03607 | 0.8528 | 374.3 | — |
| 8 | 262,144 | Clifft | 8 | 120,802,430.1 | 9,162.2 | 0.006206 | 0.003703 | 43.1 | — |
| 8 | 262,144 | Tsim | 8 | 22,876,260.9 | 125,173.5 | 0.0357 | 0.7768 | 389.9 | — |

### `surface-code-d7-r7-p1e-3`

| Physical cores | Shots/call | Simulator | Effective threads | Median shots/s | MAD shots/s | Compile (s) | First full call (s) | Peak RSS (MiB) | Packed MiB/call |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 65,536 | Clifft | 1 | 6,867,404.0 | 335.6 | 0.01337 | 0.02149 | 48.1 | 2.688 |
| 1 | 65,536 | Tsim | 1 | 7,958,105.4 | 577.4 | 0.1926 | 0.1959 | 343.4 | 2.688 |
| 1 | 262,144 | Clifft | 1 | 6,807,612.7 | 57.4 | 0.01339 | 0.0523 | 56.0 | 10.750 |
| 1 | 262,144 | Tsim | 1 | 8,094,053.5 | 538.1 | 0.1946 | 0.2182 | 352.1 | 10.750 |
| 8 | 65,536 | Clifft | 8 | 44,165,808.9 | 9,481.7 | 0.01343 | 0.01215 | 48.4 | 2.688 |
| 8 | 65,536 | Tsim | 8 | 38,776,394.0 | 13,643.1 | 0.1936 | 0.1921 | 351.9 | 2.688 |
| 8 | 262,144 | Clifft | 8 | 49,281,870.3 | 10,648.6 | 0.01348 | 0.01967 | 56.3 | 10.750 |
| 8 | 262,144 | Tsim | 8 | 42,332,343.7 | 1,368.2 | 0.1917 | 0.1936 | 359.7 | 10.750 |

### `coherent-surface-d5-r5-p1e-3-rz2e-2`

| Physical cores | Shots/call | Simulator | Effective threads | Median shots/s | MAD shots/s | Compile (s) | First full call (s) | Peak RSS (MiB) | Packed MiB/call |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 4,096 | Clifft | 1 | 675,084.6 | 207.2 | 0.05525 | 0.006804 | 42.7 | — |
| 1 | 4,096 | Tsim | 1 | 14,059.0 | 46.4 | 0.281 | 0.5378 | 586.1 | — |
| 1 | 16,384 | Clifft | 1 | 677,482.7 | 451.6 | 0.05424 | 0.02596 | 42.7 | — |
| 1 | 16,384 | Tsim | 1 | 14,250.9 | 13.2 | 0.2763 | 1.414 | 914.3 | — |
| 8 | 4,096 | Clifft | 8 | 3,481,505.0 | 744.3 | 0.05431 | 0.002798 | 55.7 | — |
| 8 | 4,096 | Tsim | 8 | 72,752.5 | 203.8 | 0.2727 | 0.276 | 627.3 | — |
| 8 | 16,384 | Clifft | 8 | 4,745,968.5 | 2,607.2 | 0.05402 | 0.005301 | 55.2 | — |
| 8 | 16,384 | Tsim | 8 | 74,153.6 | 12.2 | 0.2734 | 0.4833 | 1,351.6 | — |

### `cain-15to1-d9-p1e-3`

| Physical cores | Shots/call | Simulator | Effective threads | Median shots/s | MAD shots/s | Compile (s) | First full call (s) | Peak RSS (MiB) | Packed MiB/call |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 4,096 | Clifft | 1 | 220,527.9 | 101.0 | 1.031 | 0.2489 | 266.6 | 7.246 |
| 1 | 4,096 | Tsim | 1 | 332,884.7 | 352.2 | 13.53 | 0.239 | 3,725.9 | 7.246 |
| 1 | 16,384 | Clifft | 1 | 205,533.6 | 121.1 | 1.027 | 0.3219 | 266.5 | 28.984 |
| 1 | 16,384 | Tsim | 1 | 341,519.1 | 136.5 | 13.57 | 0.2762 | 3,725.5 | 28.984 |
| 8 | 4,096 | Clifft | 2 | 382,888.2 | 29.7 | 1.036 | 0.2294 | 266.0 | 7.246 |
| 8 | 4,096 | Tsim | 8 | 305,490.3 | 2,473.6 | 13.63 | 0.2418 | 3,732.5 | 7.246 |
| 8 | 16,384 | Clifft | 8 | 1,052,160.5 | 3,289.8 | 1.04 | 0.2486 | 268.7 | 28.984 |
| 8 | 16,384 | Tsim | 8 | 762,016.5 | 115.3 | 13.62 | 0.2512 | 3,731.1 | 28.984 |
