from __future__ import annotations

import math

import pytest

from qv_experiment.qasm_adapter import _safe_eval, parse_qasm, to_clifft_stim
from qv_experiment.run_benchmark import schedule_cases
from qv_experiment.system import select_physical_cpus


def test_qasm_adapter_preserves_paper_gate_mapping() -> None:
    qasm = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg meas[2];
u3(pi/2,0,-pi/4) q[0];
cx q[0],q[1];
measure q[0] -> meas[0];
measure q[1] -> meas[1];
"""
    operations, qubits = parse_qasm(qasm)

    assert qubits == 2
    assert len(operations) == 4
    assert to_clifft_stim(qasm).splitlines() == [
        "U3(0.5,0.0,-0.25) 0",
        "CX 0 1",
        "M 0",
        "M 1",
    ]


def test_qasm_expression_evaluator_is_restricted() -> None:
    assert _safe_eval("3*pi/4") == pytest.approx(3 * math.pi / 4)
    with pytest.raises(ValueError, match="unsupported"):
        _safe_eval("__import__('os').system('true')")


def test_qasm_adapter_rejects_unknown_statements() -> None:
    with pytest.raises(ValueError, match="unsupported QASM statement"):
        parse_qasm("OPENQASM 2.0;\nqreg q[1];\nh q[0];\n")


def test_schedule_alternates_tool_order_per_circuit() -> None:
    cases = schedule_cases([6], [42, 43], ["clifft", "qiskit", "qrack"])

    assert cases == [
        (6, 42, "clifft"),
        (6, 42, "qiskit"),
        (6, 42, "qrack"),
        (6, 43, "qrack"),
        (6, 43, "qiskit"),
        (6, 43, "clifft"),
    ]


def test_physical_cpu_selection_uses_one_logical_cpu_per_core() -> None:
    topology = [
        (0, 0, 0),
        (1, 0, 1),
        (2, 0, 0),
        (3, 0, 1),
    ]

    assert select_physical_cpus(2, topology) == [0, 1]
    with pytest.raises(ValueError, match="only 2"):
        select_physical_cpus(3, topology)
