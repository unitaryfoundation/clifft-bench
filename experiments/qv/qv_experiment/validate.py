"""Cross-check Clifft QV statevectors against Qiskit before collection."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from qv_experiment.generator import generate_qv_qasm
from qv_experiment.qasm_adapter import to_clifft_stim


def validate_case(num_qubits: int, seed: int) -> dict[str, object]:
    import clifft
    import numpy as np
    from qiskit.circuit import QuantumCircuit
    from qiskit.quantum_info import Statevector

    qasm = generate_qv_qasm(num_qubits, seed, measured=False)
    program = clifft.compile(to_clifft_stim(qasm))
    clifft_state = np.asarray(clifft.get_statevector(program))
    qiskit_state = np.asarray(
        Statevector.from_instruction(QuantumCircuit.from_qasm_str(qasm)).data
    )
    fidelity = float(abs(np.vdot(clifft_state, qiskit_state)) ** 2)
    probabilities = np.abs(clifft_state) ** 2
    median = float(np.median(probabilities))
    hop = float(np.sum(probabilities[probabilities > median]))
    return {
        "qubits": num_qubits,
        "seed": seed,
        "fidelity": fidelity,
        "heavy_output_probability": hop,
        "passed": fidelity > 0.999 and hop > 0.70,
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
