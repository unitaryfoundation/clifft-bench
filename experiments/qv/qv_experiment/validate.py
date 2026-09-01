"""Cross-check translated QV statevectors against Qiskit before collection."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from qv_experiment.generator import generate_qv_qasm
from qv_experiment.qasm_adapter import (
    to_cirq_circuit,
    to_clifft_stim,
    to_qulacs_circuit,
)


def _fidelity(reference, candidate) -> float:  # type: ignore[no-untyped-def]
    import numpy as np

    return float(abs(np.vdot(reference, candidate)) ** 2)


def _reverse_qubit_order(state, num_qubits: int):  # type: ignore[no-untyped-def]
    axes = tuple(reversed(range(num_qubits)))
    return state.reshape((2,) * num_qubits).transpose(axes).reshape(-1)


def validate_case(num_qubits: int, seed: int) -> dict[str, object]:
    import clifft
    import numpy as np
    import qsimcirq
    from qiskit.circuit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    from qulacs import QuantumState

    qasm = generate_qv_qasm(num_qubits, seed, measured=False)
    program = clifft.compile(to_clifft_stim(qasm))
    clifft_state = np.asarray(clifft.get_statevector(program))
    qiskit_state = np.asarray(
        Statevector.from_instruction(QuantumCircuit.from_qasm_str(qasm)).data
    )

    qulacs_circuit, qulacs_qubits = to_qulacs_circuit(qasm)
    qulacs_state = QuantumState(qulacs_qubits)
    qulacs_circuit.update_quantum_state(qulacs_state)

    cirq_circuit = to_cirq_circuit(qasm)
    qsim_state = np.asarray(
        qsimcirq.QSimSimulator().simulate(cirq_circuit).final_state_vector
    )
    qsim_state = _reverse_qubit_order(qsim_state, num_qubits)

    fidelities = {
        "clifft": _fidelity(qiskit_state, clifft_state),
        "qulacs": _fidelity(qiskit_state, np.asarray(qulacs_state.get_vector())),
        "qsim": _fidelity(qiskit_state, qsim_state),
    }
    probabilities = np.abs(clifft_state) ** 2
    median = float(np.median(probabilities))
    hop = float(np.sum(probabilities[probabilities > median]))
    return {
        "qubits": num_qubits,
        "seed": seed,
        "fidelities": fidelities,
        "heavy_output_probability": hop,
        "passed": all(value > 0.999 for value in fidelities.values()) and hop > 0.70,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qubits", default="4,6,8")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    widths = [int(value) for value in args.qubits.split(",") if value]
    results = [validate_case(width, args.seed) for width in widths]
    print(json.dumps(results, indent=2))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
