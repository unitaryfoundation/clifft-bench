# benchmark workload corpus

The checked-in `.stim` files are immutable benchmark inputs. Their SHA-256
digests, semantic contracts, compatible adapters, and source identities live in
[`workloads.toml`](../workloads.toml).

The benchmark corpus contains:

- distance-3 and distance-5 magic-state cultivation,
- distance-3 and distance-5 fold-transversal CCZ cultivation,
- 85-qubit color-code magic-state distillation,
- 15-to-1 Reed-Muller magic-state distillation with ideal output verification,
- Cain 15-to-1 distillation at surface-code distances 5 and 9,
- coherent-noise surface-code circuits at `(d, rounds) = (3, 1), (3, 3),
  (5, 1), (5, 5)`,
- a distance-7, seven-round pure-Clifford surface-code memory circuit,
- fixed-seed Quantum Volume circuits at 10 and 20 qubits,
- a 16-bit Draper adder on 32 qubits with fixed basis inputs,
- BT27 and BT81 code switching with direct logical-X readout, and
- measured 15-qubit Reed-Muller preparation and transversal T.

The release campaign also includes postselection-off controls, repeated QV
sampling, an explicitly unverified d7 MSC performance proxy, and full d5
cultivation with a selective detector mask. See the [coverage notes](coverage.md)
for provenance, exact semantics, eligibility, reproduction, and validation.

The near-Clifford and Quantum Volume files use the extended Clifft/SymFT
Stim-like dialect. The pure surface-code file is a genuine Stim circuit.
Rotation arguments use half-turns, so `R_Z(0.02)` represents `0.02 * pi`
radians.

The original eight QEC files are byte-for-byte copies from
[`unitaryfoundation/clifft-paper`](https://github.com/unitaryfoundation/clifft-paper)
commit `db7dc9f13a2c2854690e92390c779048a1ac1400`. The Quantum Volume
files were generated from that commit with Qiskit 2.5.1 and seed 42, then given
one observable per terminal measurement to expose the complete terminal readout.
QV observable k is the measurement of qubit k. The benchmark still returns
aggregate observable-0 counts; declaring these outputs does not require every
adapter to materialize a full bitstring. The generated files are immutable inputs;
Qiskit is not a runtime benchmark dependency. The applicable Apache-2.0 license
is included as
[`circuits/LICENSE-Clifft-paper`](circuits/LICENSE-Clifft-paper).

The Draper adder was generated with
[MQT Bench 2.3.0](https://github.com/munich-quantum-toolkit/bench/blob/v2.3.0/src/mqt/bench/benchmarks/draper_qft_adder.py)
and Qiskit 2.5.2 at optimization level 0. Observable 0 is the parity of the two
sum MSBs; observables 1 through 32 expose every measurement record. These
generators are not runtime dependencies.

The 15-to-1 circuit comes from
[`ikim-quantum/xtim`](https://github.com/ikim-quantum/xtim/blob/29454071fb20fbcffd258559ad0644676e44a8f9/examples/distillation_15_1_3.stim),
with its [Apache-2.0 license](circuits/LICENSE-xtim). Preparation, feedback,
the fifteen T gates, independent `Z_ERROR(0.001)` faults, and four X checks
are retained. The two `PAULI_EXPECTATION` declarations are replaced by ideal
inverse transversal T gates and a logical-X measurement declared as observable 0.
This readout counts accepted logical phase errors, rather than computing state
expectations. It is included in every simulator's timed circuit execution.
The file contains 15 source qubits, 15 measurements, four detectors, and one
observable; xtim expands MPP operations into additional internal ancillas.

Its tests enumerate all 32,768 phase-fault patterns independently of the
simulators. The four check columns are the nonzero four-bit vectors: accepted
patterns have zero XOR syndrome, and odd weight means a logical error. No single
or double fault is accepted; there are 35 accepted weight-three faults, giving
the familiar leading `35 p^3` conditional error rate at small p. All three
adapters are checked against this distribution and representative exact faults.

The BT27/BT81 and measured Reed-Muller circuits come from Clifft's public
fixtures, with [Clifft/Merlin attribution](circuits/NOTICE-code-switching).
BT27 adds direct-X readout on nine decoded logical qubits; BT81 is unchanged;
Reed-Muller removes only the expectation-value declarations. All three sample
without postselection and count observable-0 ones, not logical errors.
Tests check probabilities 3/8 for BT and (1 − 1/√2)/2 for Reed-Muller; the BT
reference is a logical-model prediction, not an independent full-circuit simulation.

The regular release campaign runs BT27/BT81 on current Clifft, and Reed-Muller
on both Clifft versions, SymFT, and Tsim. BT requires the post-0.12 compiler;
peer tools are omitted where the smoke checks did not establish a usable run.
Results appear in the case table and their declared comparison groups.

The fold-transversal d3/d5 inputs use Wan and Zapirain's
[frozen native-T models](https://github.com/kh428/exact_ler_for_msc/tree/332bc0481843ebc9718e152e92f5ee10f8f0b148/data/alternatives/inputs)
of Sahay et al.'s protocol, under [Apache-2.0](circuits/LICENSE-exact-ler).
The d3 model is a terminated core without the noisy pre-escape round; d5 is an
explicit reconstruction, not an author-supplied noisy T circuit. Both retain
noiseless terminal code projection and omit escape/decoding. Clifft and Tsim pass the
native sampling checks. SymFT rejects the fold circuits at the first `CCZ`
instruction. xtim parses the circuits, but
lacks a verified raw reference for their non-Clifford gates in either mode.
Release campaigns do not yet include these inputs.

[The converter](generate_fold_transversal.py) verifies the source model hashes,
densely renumbers qubits, and preserves native gate/noise placement. Its
three-qubit depolarisation preserves the full joint distribution: identity
with probability `1-p`, each nonidentity Pauli with `p/63`. Observable 0 measures
logical `H_XY = (X+Y)/sqrt(2)` via ideal logical T† and X readout; its bit is the
logical error. All declared detectors select raw zero. Without noise, every
shot is accepted and observable 0 is zero.

The Cain inputs adapt the [published distillation archive](https://doi.org/10.5281/zenodo.15702016)
under [CC-BY-4.0](circuits/NOTICE-Cain). [The converter](generate_cain_distillation.py)
replaces the 15 factory-input S gates and the ideal readout ancilla's S with T,
preserving every other instruction. These are fixed circuits without decoded
feedforward, as in the archive; they are sampled with postselection off.
Observable 0 repeats the existing final logical-X readout on patch 0: Z parity
along its `x=d-1/2` data boundary after terminal H gates. Its unconditioned ideal bit
is 50/50, not a logical error flag. All source detectors, including the 32 raw
logical readouts, are retained. Clifft and Tsim support packed output.
SymFT lacks this paired detector/observable endpoint. xtim parses both Cain
circuits but cannot verify their raw reference parity. The registered
16,384-shot call sizes await the host pilot.

To reproduce either input, download `distillation.zip` from that record and run
(use `9` for d9):

```bash
python workloads/generate_cain_distillation.py --source distillation.zip \
  --distance 5 --output workloads/circuits/cain_15to1_d5_p1e-3.stim
```

The converter checks the archive member's SHA-256; the manifest records both
source and output hashes.
