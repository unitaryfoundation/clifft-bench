# benchmark workload corpus

The checked-in `.stim` files are immutable benchmark inputs. Their SHA-256
digests, semantic contracts, compatible adapters, and source commits live in
[`manifests/workloads.v1.json`](../manifests/workloads.v1.json).

The benchmark corpus contains the eight QEC inputs shared by the original
Clifft and SymFT benchmark sets, two fixed Quantum Volume inputs, and a fixed-input
arithmetic circuit, plus an adapted 15-to-1 distillation protocol from xtim:

- distance-3 and distance-5 magic-state cultivation,
- 85-qubit color-code magic-state distillation,
- 15-to-1 Reed-Muller magic-state distillation with ideal output verification,
- coherent-noise surface-code circuits at `(d, rounds) = (3, 1), (3, 3),
  (5, 1), (5, 5)`,
- a distance-7, seven-round pure-Clifford surface-code memory circuit,
- fixed-seed Quantum Volume circuits at 10 and 20 qubits, and
- a 16-bit Draper adder on 32 qubits with fixed basis inputs.

The near-Clifford and Quantum Volume files use the extended Clifft/SymFT
Stim-like dialect. The pure surface-code file is a genuine Stim circuit.
Rotation arguments use half-turns, so `R_Z(0.02)` represents `0.02 * pi`
radians.

The eight QEC files are byte-for-byte copies from
[`unitaryfoundation/clifft-paper`](https://github.com/unitaryfoundation/clifft-paper)
commit `db7dc9f13a2c2854690e92390c779048a1ac1400`. The Quantum Volume
files were generated from that commit with Qiskit 2.5.1 and seed 42, then given
a terminal observable declaration so both aggregate-count adapters expose the
same output. The generated files are immutable inputs; Qiskit is not a runtime
benchmark dependency. The applicable Apache-2.0 license is included as
[`circuits/LICENSE-Clifft-paper`](circuits/LICENSE-Clifft-paper).

The Draper adder was generated with
[MQT Bench 2.3.0](https://github.com/munich-quantum-toolkit/bench/blob/v2.3.0/src/mqt/bench/benchmarks/draper_qft_adder.py)
and Qiskit 2.5.2 at optimization level 0. These generators are not runtime
dependencies.

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
