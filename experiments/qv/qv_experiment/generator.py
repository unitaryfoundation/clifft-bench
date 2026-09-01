"""Deterministic Quantum Volume circuit generation from clifft-paper."""

from __future__ import annotations


def generate_qv_qasm(num_qubits: int, seed: int, *, measured: bool = True) -> str:
    import qiskit.qasm2
    from qiskit.circuit.library import quantum_volume
    from qiskit.compiler import transpile

    circuit = quantum_volume(num_qubits, seed=seed)
    circuit = transpile(
        circuit,
        basis_gates=["cx", "u3"],
        optimization_level=0,
    )
    if measured:
        circuit.measure_all()
    return str(qiskit.qasm2.dumps(circuit))
