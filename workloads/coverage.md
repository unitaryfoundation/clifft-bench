# Supplemental sampling coverage

The `*-coverage` variants add repeated QV sampling, the unverified d7 cultivation
proxy, and full d5 cultivation to the normal release campaign. They share its
[endpoint and timing contract](../docs/benchmark-contract.md) and have no paired
previous-release baseline. `coverage-smoke.toml` exercises the same selections
with smaller calls.

Postselection on/off uses the same canonical circuit, digest, noise, and public
call size. Cases select the mode explicitly; on returns survivor counts and off
returns all packed detector/observable rows. These compare whole workflows and
do not isolate postselection cost. Unsupported pinned endpoints remain explicit
outcomes. Modes and supported circuit adapters are declared in `workloads.toml`.

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
receives it at sampling, then counts observable ones among native survivors.
SymFT supports all-detector selection. Stim has no native survivor endpoint.
xtim accepts detector masks, but its raw reference parity for this circuit is
unverified, so both modes report an unsupported outcome. Licenses and attribution are in
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

## Quantum Volume

The QV10/QV20 seed-42 workloads declare every terminal measurement as an
observable. These rows measure repeated packed sampling from a prepared sampler.
There are no selected detectors. They are distinct from the setup-plus-one-shot study
under `experiments/qv` and say nothing about heavy-output-generation success.

## Validation and collection

[`test_workload_coverage.py`](../tests/test_workload_coverage.py) checks the exact
selection pairs and mask, exercises scalar and packed subset selection against
an independent two-detector probability model, and compares repeated QV10
sampling against an independent dense U3/CX statevector. Raw-zero semantics are
tested with a selected detector that is always one, so reference normalization
or silently ignoring the mask fails the test. Finalization rejects a changed
mask, reference convention, or selection flag.

For the noiseless full-d5 control, the selected detectors should all be zero.
An ideal T-state Y readout has expectation magnitude `1/sqrt(2)`; the pinned
circuit's raw observable includes the negative static readout sign, so its
raw-one probability is `(1 + 1/sqrt(2))/2`, approximately 0.85355. The upstream
driver calibrates that sign before interpreting a corrected Y expectation;
this benchmark preserves it. This noiseless check does not turn noisy raw-one
counts into fidelity or decoded logical errors.

xtim excludes the d7 proxy because its records are nondeterministic without
noise, and excludes the coherent control because of its `R_Z` syntax. QV20's
32-shot public call bounds its cost.

The smoke profile uses small fixed calls and runs every declared combination,
including full d5 and QV20:

```bash
clifft-bench bootstrap campaigns/coverage-smoke.toml
clifft-bench run campaigns/coverage-smoke.toml \
  --memory-limit-gib 12 --output-dir /tmp/coverage-smoke
clifft-bench validate /tmp/coverage-smoke/raw.json
```

For a single local environment, pass its interpreter with `--python` and
select the matching cases with `--case clifft`, `--case tsim`, etc.
Linux CI runs these cases under the actual address-space ceiling. Official
sampling uses the full EC2 release sweep.
