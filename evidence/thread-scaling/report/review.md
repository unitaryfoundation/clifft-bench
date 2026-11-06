# Native thread scaling pilot

For this workload and call size, how does throughput change with more threads, using the simulator's native configuration or tuning?

Optional smoke/validation evidence; not the recurring official throughput series.

Run: `8eef88d5-2fb7-4faf-9531-f5ca9194bb63`. Host: INTEL(R) XEON(R) PLATINUM 8573C (Linux).
Harness: `c36eaf901d2da9900ed2fcea59afd5815d24a57f`; dirty checkout: False.
Collection: 2026-11-06T19:03:34.865178Z to 2026-11-06T19:06:06.789049Z.
Selection: `--t(1|2)$`; overrides: `{}`.
Memory ceiling: 12 GiB per process, fixed across thread budgets. One logical CPU per physical core.
Core allocation (prefix used for each budget): `[{'core_id': 0, 'logical_cpu': 0, 'package_id': 0}, {'core_id': 1, 'logical_cpu': 2, 'package_id': 0}]`.

Rates use attempted shots. Speedup uses the same tool/workload's one-core rate in this run; efficiency is speedup divided by the allocated core count. Missing or failed one-core baselines have no ratio. MAD is sample dispersion.

CPU cores used is process CPU time divided by wall time over the sampling loop, including adapter reduction and loop bookkeeping. It measures average CPU consumption, not a thread count. Runtime threads is the resolved worker/pool limit; active threads is SymFT's observed minimum–maximum across calls. Other tools do not expose that count.

Clifft calibrates once per point with its native 0.25-second soft budget and reuses the selected batch/layout. SymFT and Tsim use native heuristics, with no measured calibration. Tsim's per-call automatic batch size is not exposed by its public API and remains unknown here. Setup includes native calibration; JIT/warmup costs are reported separately where available. Peak RSS is the process high-water mark and can include setup and tuning. These policies do not claim optimal configurations.

## distillation-color-code-85q-p5e-2

Attempted shots per call: 100000.

| Tool | Cores | Status | Shots/s ± MAD | Speedup | Efficiency | Runtime threads | Active threads | CPU cores used | Peak MiB |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | success | 2.147e+06 ± 2144 | 1× | 1 | 1 | — | 1 | 48.52 |
| Clifft | 2 | success | 4.155e+06 ± 6946 | 1.936× | 0.9678 | 2 | — | 1.961 | 49.25 |
| SymFT | 1 | success | 1.613e+06 ± 1968 | 1× | 1 | 1 | 1–1 | 1 | 47.24 |
| SymFT | 2 | success | 3.098e+06 ± 1.054e+04 | 1.92× | 0.9602 | 2 | 2–2 | 1.978 | 48.65 |
| Tsim | 1 | success | 6.909e+06 ± 1.529e+04 | 1× | 1 | 1 | — | 0.9997 | 593 |
| Tsim | 2 | success | 1.145e+07 ± 8.726e+04 | 1.657× | 0.8287 | 2 | — | 1.953 | 478.3 |

| Tool | Cores | Setup s | Native calibration s | Compile s | Kernel warmup s | Warmup s |
|---|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | 0.3442 | 0.2495 | 0.003249 | — | 0.04689 |
| Clifft | 2 | 0.3218 | 0.2499 | 0.003244 | — | 0.02392 |
| SymFT | 1 | 0.04183 | — | — | — | 0.06289 |
| SymFT | 2 | 0.04223 | — | — | — | 0.03273 |
| Tsim | 1 | 15.33 | — | 0.0446 | 14.47 | 0.2563 |
| Tsim | 2 | 1.128 | — | 0.04456 | 0.2567 | 1.287 |

Resolved native settings:

- distillation-color-code-85q-p5e-2--clifft-native--t1: batch=2048; chunk=—; layout=1 × 1; tuning stop=completed; sufficient measurements=True.
- distillation-color-code-85q-p5e-2--clifft-native--t2: batch=2048; chunk=—; layout=2 × 1; tuning stop=completed; sufficient measurements=True.
- distillation-color-code-85q-p5e-2--symft-native--t1: batch=1024; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- distillation-color-code-85q-p5e-2--symft-native--t2: batch=1024; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- distillation-color-code-85q-p5e-2--tsim-native--t1: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- distillation-color-code-85q-p5e-2--tsim-native--t2: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.

## coherent-surface-d5-r5-p1e-3-rz2e-2

Attempted shots per call: 256.

| Tool | Cores | Status | Shots/s ± MAD | Speedup | Efficiency | Runtime threads | Active threads | CPU cores used | Peak MiB |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | success | 1.017e+04 ± 7.392 | 1× | 1 | 1 | — | 0.9999 | 48.1 |
| Clifft | 2 | success | 1.911e+04 ± 78.14 | 1.88× | 0.9399 | 2 | — | 1.933 | 48.27 |
| SymFT | 1 | success | 35.31 ± 0.4207 | 1× | 1 | 1 | 1–1 | 0.9996 | 78.39 |
| SymFT | 2 | success | 35.33 ± 0.6075 | 1.001× | 0.5003 | 2 | 1–1 | 1 | 110.3 |
| Tsim | 1 | success | 1.205e+04 ± 197.5 | 1× | 1 | 1 | — | 0.9999 | 480.3 |
| Tsim | 2 | success | 2.216e+04 ± 58.26 | 1.84× | 0.92 | 2 | — | 1.727 | 483.1 |

| Tool | Cores | Setup s | Native calibration s | Compile s | Kernel warmup s | Warmup s |
|---|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | 0.08635 | 1.817e-06 | 0.01452 | — | 0.02677 |
| Clifft | 2 | 0.07533 | 2.193e-06 | 0.01444 | — | 0.01345 |
| SymFT | 1 | 0.07175 | — | — | — | 7.147 |
| SymFT | 2 | 0.08731 | — | — | — | 7.156 |
| Tsim | 1 | 1.441 | — | 0.3695 | 0.2506 | 0.02904 |
| Tsim | 2 | 1.422 | — | 0.3485 | 0.2578 | 0.01892 |

Resolved native settings:

- coherent-surface-d5-r5-p1e-3-rz2e-2--clifft-native--t1: batch=1; chunk=—; layout=1 × 1; tuning stop=single_candidate; sufficient measurements=False.
- coherent-surface-d5-r5-p1e-3-rz2e-2--clifft-native--t2: batch=1; chunk=—; layout=2 × 1; tuning stop=single_candidate; sufficient measurements=False.
- coherent-surface-d5-r5-p1e-3-rz2e-2--symft-native--t1: batch=1; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d5-r5-p1e-3-rz2e-2--symft-native--t2: batch=1; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d5-r5-p1e-3-rz2e-2--tsim-native--t1: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d5-r5-p1e-3-rz2e-2--tsim-native--t2: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.

## coherent-surface-d3-r3-p1e-3-rz2e-2

Attempted shots per call: 1.

| Tool | Cores | Status | Shots/s ± MAD | Speedup | Efficiency | Runtime threads | Active threads | CPU cores used | Peak MiB |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | success | 1.152e+05 ± 191 | 1× | 1 | 1 | — | 1 | 47.45 |
| Clifft | 2 | success | 1.15e+05 ± 19.61 | 0.9982× | 0.4991 | 1 | — | 1 | 47.48 |
| SymFT | 1 | success | 9.437e+04 ± 33.72 | 1× | 1 | 1 | 1–1 | 1 | 46.44 |
| SymFT | 2 | success | 9.366e+04 ± 25.22 | 0.9924× | 0.4962 | 2 | 1–1 | 1 | 47.42 |
| Tsim | 1 | success | 3222 ± 4.642 | 1× | 1 | 1 | — | 1 | 461.3 |
| Tsim | 2 | success | 3197 ± 5.551 | 0.9923× | 0.4961 | 2 | — | 0.9998 | 463.4 |

| Tool | Cores | Setup s | Native calibration s | Compile s | Kernel warmup s | Warmup s |
|---|---:|---:|---:|---:|---:|---:|
| Clifft | 1 | 0.04646 | 1.205e-06 | 0.001433 | — | 0.0003185 |
| Clifft | 2 | 0.04723 | 1.072e-06 | 0.001463 | — | 0.0002935 |
| SymFT | 1 | 0.04096 | — | — | — | 0.0006406 |
| SymFT | 2 | 0.04099 | — | — | — | 0.0006775 |
| Tsim | 1 | 1.096 | — | 0.02429 | 0.2505 | 0.004644 |
| Tsim | 2 | 1.096 | — | 0.02393 | 0.2542 | 0.004839 |

Resolved native settings:

- coherent-surface-d3-r3-p1e-3-rz2e-2--clifft-native--t1: batch=1; chunk=—; layout=1 × 1; tuning stop=single_candidate; sufficient measurements=False.
- coherent-surface-d3-r3-p1e-3-rz2e-2--clifft-native--t2: batch=1; chunk=—; layout=1 × 1; tuning stop=single_candidate; sufficient measurements=False.
- coherent-surface-d3-r3-p1e-3-rz2e-2--symft-native--t1: batch=256; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d3-r3-p1e-3-rz2e-2--symft-native--t2: batch=256; chunk=2048; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d3-r3-p1e-3-rz2e-2--tsim-native--t1: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.
- coherent-surface-d3-r3-p1e-3-rz2e-2--tsim-native--t2: batch=—; chunk=—; layout=— × —; tuning stop=not applicable; sufficient measurements=—.

## Software

- clifft-0.13.0: `0.13.0` at [9b9091c8267321e2a2fd967f0674a65e4baf0e38](https://github.com/unitaryfoundation/clifft/commit/9b9091c8267321e2a2fd967f0674a65e4baf0e38); precision=complex-fp64.
- symft-2026.10.8: `2026.10.8` at [81d28499b6da8c0b84b7567482c4692f509bd416](https://github.com/haoliri0/SOFT/commit/81d28499b6da8c0b84b7567482c4692f509bd416); precision=complex-fp64.
- tsim-1.0.0: `1.0.0` at [136bcc6bec7edba88bbb28152da05e33d17f77d0](https://github.com/QuEraComputing/tsim/commit/136bcc6bec7edba88bbb28152da05e33d17f77d0); precision=complex-fp64.
