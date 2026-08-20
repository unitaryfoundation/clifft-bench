# benchmark workload corpus

The checked-in `.stim` files are immutable benchmark inputs. Their SHA-256
digests, semantic contracts, compatible adapters, and source commits live in
[`manifests/workloads.v1.json`](../manifests/workloads.v1.json).

The benchmark corpus contains the eight QEC inputs shared by the original
Clifft and SymFT benchmark sets, plus two fixed Quantum Volume inputs:

- distance-3 and distance-5 magic-state cultivation,
- 85-qubit color-code magic-state distillation,
- coherent-noise surface-code circuits at `(d, rounds) = (3, 1), (3, 3),
  (5, 1), (5, 5)`,
- a distance-7, seven-round pure-Clifford surface-code memory circuit, and
- fixed-seed Quantum Volume circuits at 10 and 20 qubits.

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
