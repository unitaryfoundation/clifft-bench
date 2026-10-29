# Supplemental sampling coverage

The `*-coverage` variants in the normal release campaign add 23 current-tool
cases. They use the existing `grid-v1` calibration policy; the 38 matched
policy-comparison cases from #81 remain a separate experiment. Results appear
in `cases.csv` and `coverage-alternatives-vs-current` comparison rows, outside
the historical QEC figures and medians. There is no previous-release baseline
for this new cohort. Tool pins, single-core restrictions, timed boundaries,
and the aggregate-count contract are otherwise unchanged.

| Workload | Attempted shots per public call | Eligible adapters |
|---|---:|---|
| Cultivation d3, selection off | 100,000 | Clifft, SymFT, Tsim, xtim |
| Surface d7/r7, selection off | 100,000 | Clifft, SymFT, Tsim, xtim, Stim |
| Coherent surface d3/r3, selection off | 100,000 | Clifft, SymFT, Tsim |
| QV10, seed 42, repeated sampling | 4,096 | Clifft, SymFT, Tsim |
| QV20, seed 42, repeated sampling | 32 | Clifft, SymFT, Tsim |
| Unverified d7 MSC performance proxy | 20,000 | Clifft, SymFT, Tsim |
| Full d5 cultivation, selective mask | 20,000 | Clifft, Tsim |

Eligibility applies to the pinned implementation and this repository's adapter,
not every capability of the underlying simulator. Failed collection attempts
remain explicit outcomes; they do not become zero throughput or disappear.

## Postselection controls

Each `-unselected` workload reuses its selected counterpart's exact circuit,
digest, observable, noise, and public call size. Only the semantic selection
flag changes. Selected rows already exist in the `*-current` variants. These
controls expose the cost of continuing rejected trajectories and whether
selection enables compiler optimizations. They count raw observable-0 ones
over all attempts; they are not decoded logical-error estimates.

SymFT uses its measurement-record path for these cases because its native
counts endpoint always discards detector-fired shots. The materialization and
parity reduction are timed. The selected and unselected rows therefore compare
the complete available adapter paths, including that endpoint difference.

## Cultivation sources and semantics

The [d7 source](https://github.com/haoliri0/SymFT_Test/blob/9ec5790322f93140e78bdb6d6620a2a43eceba0b/benchmark/circuit/msc_proxy_d7_unverified_p1e-3.stim)
is copied without modification. Its source explicitly labels it an unverified
performance proxy, built by generic growth from d5 with five superdense cycles.
It does not establish a validated d7 double-cat protocol. It contains 76 source
qubits, 310 measurements, and 299 detectors. All raw nonzero detectors reject
a shot. Cross-tool count agreement checks the literal circuit; it cannot
validate the protocol or establish a distance-7 logical error rate.

The full d5 input uses
[`make_d5_end2end(0.001)`](https://github.com/unitaryfoundation/clifft-paper/blob/db7dc9f13a2c2854690e92390c779048a1ac1400/magic_state_cultivation/convert_s_to_t.py)
with the source's vendored Gidney revision
`871e68ff6df2f75190b1bfd6351459d1b5a037e3`. It includes d15 escape, five growing
rounds and five end rounds: 463 source qubits, 2,863 measurements, 2,744 detectors,
and one observable. The sampling generator adds no `EXP_VAL` probes.

Its explicit 107-detector mask follows the pinned
[`run_end2end_bruteforce.py`](https://github.com/unitaryfoundation/clifft-paper/blob/db7dc9f13a2c2854690e92390c779048a1ac1400/magic_state_cultivation/run_end2end_bruteforce.py)
coordinate rule: select detectors with at most four coordinates or a fifth
coordinate equal to -9. The original driver normalizes references and performs
decoding. This benchmark uses raw parity, requires only those selected
detectors to be zero, and returns uncorrected observable-0 counts among
survivors. It performs no decoding, frame correction, expectation calculation,
or fidelity estimation. Escape detectors are sampled but do not reject a shot.

Clifft receives this same mask before HIR passes and during lowering. Tsim
receives it at sampling and the adapter reduces only selected detector columns.
SymFT, xtim, and Stim adapters currently reject explicit subsets instead of
silently selecting all or none. This narrower contract does not reopen the
end-to-end decoding study in #9. Licenses and attribution are in
[`NOTICE-cultivation-coverage`](circuits/NOTICE-cultivation-coverage).

To reproduce the d5 bytes and mask in a separate generator environment:

```bash
git clone https://github.com/unitaryfoundation/clifft-paper.git /tmp/clifft-paper
git -C /tmp/clifft-paper checkout db7dc9f13a2c2854690e92390c779048a1ac1400
uv venv --python 3.12 .campaign-envs/generator
uv pip install --python .campaign-envs/generator/bin/python \
  -r workloads/validation/generator-requirements.txt
.campaign-envs/generator/bin/python scripts/generate_full_d5.py \
  --source /tmp/clifft-paper
```

The script verifies the source revision, generated digest and exact detector
indices. `--noise 0 --output /tmp/full-d5-noiseless.stim` generates the diagnostic
noiseless control. Stim's substitution of T/T_DAG with I is used only to index
annotations, never to simulate or validate the non-Clifford output law.

## Quantum Volume and the optional 35-qubit input

QV reuses the immutable QV10/QV20 seed-42 circuits already in the corpus. These
rows measure repeated aggregate sampling from a prepared sampler. They are
distinct from the setup-plus-one-shot study under `experiments/qv` and say
nothing about heavy-output-generation success. There are no selected detectors;
the output is the raw parity of the declared final measured bit.

The public
[Tsim notebook pinned in #77](https://github.com/QuEraComputing/tsim/blob/e9406ed5bb49fcff1b04b65534cf56bbb756b3a6/docs/demos/magic_state_distillation.ipynb)
uses `ColorEncoder5`, producing the existing 85-qubit family. The same source
tree provides a seven-qubit `SteaneEncoder`, but the linked notebook is not an
immutable 35-qubit input. A substitution would need a specified readout and
validation of its stabilizer selection and the separate four-bit distillation
condition. No new 35-qubit workload is declared compatible here. The existing
85-qubit and 15-to-1 cases remain the distillation coverage for this release.

## Validation and collection

[`test_workload_coverage.py`](../tests/test_workload_coverage.py) checks the exact
selection pairs and mask, exercises scalar and packed subset selection against
an independent two-detector probability model, and compares repeated QV10
sampling against an independent dense U3/CX statevector. Raw-zero semantics are
tested with a selected detector that is always one, so reference normalization
or silently ignoring the mask fails the test. Finalization rejects a changed
mask, reference convention, or selection flag.

[`coverage-20261029.json`](validation/coverage-20261029.json) retains local
feasibility probes on an Apple M4 Pro using the pinned tools. Each child had a
120-second watchdog, an 8 GiB RSS watchdog, and single-thread environment
variables. Affinity and Linux address-space limits were not enforced. These are
diagnostics, not official throughput measurements:

- All three unselected controls returned every attempt on each eligible tool.
- d7's three-seed accepted totals were **236/49,152** for Clifft,
  **242/49,152** for SymFT, and **238/49,152** for Tsim. Raw observable-one counts
  were 117, 100 and 108. The rare acceptance required longer probes than smoke.
- Full d5's noiseless control accepted all 6,144 attempts on both Clifft and
  Tsim. Noisy acceptance was 755/6,144 and 738/6,144, respectively. These support
  the raw-zero mask; noisy uncorrected observable rates are not fidelity.
- QV20 at 32 shots and capacity 32 took about 74 seconds per call on SymFT and 3.5 seconds
  on Tsim with capacity 32. Clifft's capacity-32 candidate hit its 64 MiB packed
  state guard; scalar preparation and sampling succeeded. Calibration retains
  such candidate failures and can select scalar execution.
- xtim rejects the d7 proxy's nondeterministic noiseless records under the
  adapter contract. It also rejects the coherent circuit's `R_Z` syntax.

The normal release collector calibrates on the reference host; these probes do
not hard-code per-circuit winning batches. The smoke profile uses small fixed
calls and runs every declared combination, including full d5 and QV20:

```bash
clifft-bench run --run-manifest manifests/run-coverage-smoke.v1.json \
  --memory-limit-gib 12 --output /tmp/coverage-smoke.json
clifft-bench validate /tmp/coverage-smoke.json
```

Set the implementation-specific Python variables as for the release campaign,
or use `--case clifft`, `--case tsim`, etc. in the respective environment.
Linux CI runs these cases under the actual address-space ceiling. Official
sampling remains part of the next full EC2 release sweep.
