# release-clifft — fc091f89-b63e-4be6-9e72-87cc15fcd04a

Classification: **official**. Collected 2026-11-04T15:29:23.958328Z.
Source: `3c89445367581edba4d9b7ec5c72e53c4e68eddc`; dirty: False.
Host: AMD EPYC 9R14 (Linux, x86_64).

## Collection sources

Clifft and paired release changes use the primary execution above.
Peer throughput is reused from run `031a93c5-a1c7-4dc3-a045-3f8163cdc50b`, collected 2026-11-03T23:17:33.881427Z.
Peer source: `70bd59483489a5e6b3a0df840fbcb7ecf79e2f70`.
These are separate collections on matching reference hardware and protocols;
cross-collection throughput is not a fresh paired comparison.
The peer versions below retain their source measurements; peers were not rerun.

## Measured implementations

| Implementation | Exact version | Commit |
| --- | --- | --- |
| clifft-0.12.0rc1 | 0.12.0rc1 | `847757e2bc8c4f7439b9e92011550fd61719eb85` |
| clifft-0.13.0rc2 | 0.13.0rc2 | `7e83ec2823bf99c54aa1b90c54a092641c10c4b0` |
| symft-0.1.1-c89b985 | 0.1.1 | `c89b98514a919240b8afa53a271e08d926d3c987` |
| stim-1.16.0 | 1.16.0 | `e2fc1eca7fd21684d433aa5f10f4504ea4860d07` |
| xtim-3.2.2-edf0dd0 | 3.2.2 | `edf0dd0cff804ac0228d517889e49b0c286cb19a` |
| tsim-0.1.5-e9406ed | 0.1.5 | `e9406ed5bb49fcff1b04b65534cf56bbb756b3a6` |

## Outcomes

| Case | Outcome | Detail |
| --- | --- | --- |
| msc-d3-inject-cultivate-p1e-3--clifft-previous | success | 5 samples; batch 2048 |
| msc-d5-inject-cultivate-p1e-3--clifft-previous | success | 5 samples; batch 1 |
| distillation-color-code-85q-p5e-2--clifft-previous | success | 5 samples; batch 1024 |
| surface-code-d7-r7-p1e-3--clifft-previous | success | 5 samples; batch 2048 |
| coherent-surface-d3-r3-p1e-3-rz2e-2--clifft-previous | success | 5 samples; batch 2048 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--clifft-previous | success | 5 samples; batch 1 |
| draper-adder-m16-basis-all-observables--clifft-previous | success | 5 samples; batch 1024 |
| distillation-15to1-rm15-p1e-3--clifft-previous | success | 5 samples; batch 1024 |
| reed-muller-measured--clifft-previous | success | 5 samples; batch 256 |
| msc-d3-inject-cultivate-p1e-3--clifft-current | success | 5 samples; batch 2048 |
| msc-d5-inject-cultivate-p1e-3--clifft-current | success | 5 samples; batch 1 |
| distillation-color-code-85q-p5e-2--clifft-current | success | 5 samples; batch 1024 |
| surface-code-d7-r7-p1e-3--clifft-current | success | 5 samples; batch 2048 |
| coherent-surface-d3-r3-p1e-3-rz2e-2--clifft-current | success | 5 samples; batch 2048 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--clifft-current | success | 5 samples; batch 1 |
| draper-adder-m16-basis-all-observables--clifft-current | success | 5 samples; batch 1024 |
| distillation-15to1-rm15-p1e-3--clifft-current | success | 5 samples; batch 2048 |
| bt27-direct-x--clifft-current | success | 5 samples; batch 1 |
| bt81-direct-x--clifft-current | success | 5 samples; batch 1024 |
| reed-muller-measured--clifft-current | success | 5 samples; batch 256 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--clifft-bulk | success | 5 samples; batch 1 |
| draper-adder-m16-basis-all-observables--clifft-bulk | success | 5 samples; batch 2048 |
| msc-d3-inject-cultivate-p1e-3-unselected--clifft-coverage | success | 5 samples; batch 2048 |
| surface-code-d7-r7-p1e-3-unselected--clifft-coverage | success | 5 samples; batch 2048 |
| coherent-surface-d3-r3-p1e-3-rz2e-2-unselected--clifft-coverage | success | 5 samples; batch 2048 |
| quantum-volume-q10-seed42-all-observables--clifft-coverage | success | 5 samples; batch 1 |
| quantum-volume-q20-seed42-all-observables--clifft-coverage | success | 5 samples; batch 1 |
| msc-d7-proxy-unverified-p1e-3--clifft-coverage | success | 5 samples; batch 1 |
| msc-full-d5-cultivation-selected-p1e-3--clifft-coverage | success | 5 samples; batch 256 |
| msc-d3-inject-cultivate-p1e-3--symft-current | success | 5 samples; batch 2048 |
| msc-d5-inject-cultivate-p1e-3--symft-current | success | 5 samples; batch 64 |
| distillation-color-code-85q-p5e-2--symft-current | success | 5 samples; batch 1024 |
| surface-code-d7-r7-p1e-3--symft-current | success | 5 samples; batch 2048 |
| coherent-surface-d3-r3-p1e-3-rz2e-2--symft-current | success | 5 samples; batch 32 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--symft-current | success | 5 samples; batch 1 |
| distillation-15to1-rm15-p1e-3--symft-current | success | 5 samples; batch 1024 |
| reed-muller-measured--symft-current | success | 5 samples; batch 256 |
| surface-code-d7-r7-p1e-3--stim-current | success | 5 samples; batch 4096 |
| msc-d3-inject-cultivate-p1e-3--xtim-current | success | 5 samples; batch 100000 |
| msc-d5-inject-cultivate-p1e-3--xtim-current | success | 5 samples; batch 20000 |
| surface-code-d7-r7-p1e-3--xtim-current | success | 5 samples; batch 100000 |
| distillation-15to1-rm15-p1e-3--xtim-current | success | 5 samples; batch 100000 |
| msc-d3-inject-cultivate-p1e-3--tsim-current | success | 5 samples; batch 8192 |
| msc-d5-inject-cultivate-p1e-3--tsim-current | success | 5 samples; batch 20000 |
| distillation-color-code-85q-p5e-2--tsim-current | success | 5 samples; batch 65536 |
| surface-code-d7-r7-p1e-3--tsim-current | success | 5 samples; batch 1024 |
| coherent-surface-d3-r3-p1e-3-rz2e-2--tsim-current | success | 5 samples; batch 8192 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--tsim-current | success | 5 samples; batch 1 |
| draper-adder-m16-basis-all-observables--tsim-current | success | 5 samples; batch 2048 |
| distillation-15to1-rm15-p1e-3--tsim-current | success | 5 samples; batch 8192 |
| reed-muller-measured--tsim-current | success | 5 samples; batch 8192 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--symft-bulk | success | 5 samples; batch 1 |
| coherent-surface-d5-r5-p1e-3-rz2e-2--tsim-bulk | success | 5 samples; batch 32 |
| draper-adder-m16-basis-all-observables--tsim-bulk | success | 5 samples; batch 1 |
| msc-d3-inject-cultivate-p1e-3-unselected--symft-coverage | success | 5 samples; batch 1024 |
| surface-code-d7-r7-p1e-3-unselected--symft-coverage | success | 5 samples; batch 1024 |
| coherent-surface-d3-r3-p1e-3-rz2e-2-unselected--symft-coverage | success | 5 samples; batch 256 |
| quantum-volume-q10-seed42-all-observables--symft-coverage | success | 5 samples; batch 256 |
| quantum-volume-q20-seed42-all-observables--symft-coverage | success | 5 samples; batch 1 |
| msc-d7-proxy-unverified-p1e-3--symft-coverage | success | 5 samples; batch 64 |
| msc-d3-inject-cultivate-p1e-3-unselected--tsim-coverage | success | 5 samples; batch 8192 |
| surface-code-d7-r7-p1e-3-unselected--tsim-coverage | success | 5 samples; batch 65536 |
| coherent-surface-d3-r3-p1e-3-rz2e-2-unselected--tsim-coverage | success | 5 samples; batch 8192 |
| quantum-volume-q10-seed42-all-observables--tsim-coverage | success | 5 samples; batch 4096 |
| quantum-volume-q20-seed42-all-observables--tsim-coverage | success | 5 samples; batch 1 |
| msc-d7-proxy-unverified-p1e-3--tsim-coverage | success | 5 samples; batch 20000 |
| msc-full-d5-cultivation-selected-p1e-3--tsim-coverage | success | 5 samples; batch 8192 |
| msc-d3-inject-cultivate-p1e-3-unselected--xtim-coverage | success | 5 samples; batch 100000 |
| surface-code-d7-r7-p1e-3-unselected--xtim-coverage | success | 5 samples; batch 16384 |
| surface-code-d7-r7-p1e-3-unselected--stim-coverage | success | 5 samples; batch 4096 |

## Paired comparisons

Ratios are candidate / baseline sample medians. Failed cases have no rate or ratio.
Bulk and short calls remain separate. Missing baselines are not regressions.

| Comparison | Workload | Candidate | Candidate / baseline | Outcome |
| --- | --- | --- | ---: | --- |
| current-vs-previous | msc-d3-inject-cultivate-p1e-3 | clifft-current | 1.02963× | baseline: measured; candidate: measured |
| current-vs-previous | msc-d5-inject-cultivate-p1e-3 | clifft-current | 1.00185× | baseline: measured; candidate: measured |
| current-vs-previous | distillation-color-code-85q-p5e-2 | clifft-current | 0.999688× | baseline: measured; candidate: measured |
| current-vs-previous | surface-code-d7-r7-p1e-3 | clifft-current | 1.00697× | baseline: measured; candidate: measured |
| current-vs-previous | coherent-surface-d3-r3-p1e-3-rz2e-2 | clifft-current | 1.04739× | baseline: measured; candidate: measured |
| current-vs-previous | coherent-surface-d5-r5-p1e-3-rz2e-2 | clifft-current | 0.994786× | baseline: measured; candidate: measured |
| current-vs-previous | draper-adder-m16-basis-all-observables | clifft-current | 1.08211× | baseline: measured; candidate: measured |
| current-vs-previous | distillation-15to1-rm15-p1e-3 | clifft-current | 222.993× | baseline: measured; candidate: measured |
| current-vs-previous | reed-muller-measured | clifft-current | 16.2169× | baseline: measured; candidate: measured |
| current-vs-previous | bt27-direct-x | clifft-current | — | baseline: not measured; candidate: measured |
| current-vs-previous | bt81-direct-x | clifft-current | — | baseline: not measured; candidate: measured |

The paired table uses only the primary execution. Peer cases in the
outcomes and throughput figures belong to the peer source above.
Each source raw/CSV retains its own call sizes, selection, and samples.

## Figures

Rates count attempted shots and exclude compilation; lazy planning during sampling
is included. The main figures share circuit selection and thematic ordering.
Throughput panels have independent logarithmic axes; compare horizontal positions
within a panel. Quantum Volume has its own panel.
Selection-off controls, the unverified cultivation proxy, and bulk-call variants
remain in the detailed results above. Colors and marker shapes identify simulators.
Unsupported pairings have
no marker; unmeasured and failed cases are labeled. Exact identities and outcomes
are recorded above. Each source cases.csv/raw.json retains call sizes and postselection.

Release changes within ±50% use a linear percentage axis; larger changes use
a separate logarithmic ratio panel. The shaded ±1.1% band is the contract's
inconclusive region, not a confidence interval. Unpaired workloads have no ratio.

### CPU simulator throughput

[Dark version](simulator-throughput-dark.png)

![CPU simulator throughput](simulator-throughput-light.png)

### Clifft release changes

[Dark version](release-change-dark.png)

![Clifft release changes](release-change-light.png)
