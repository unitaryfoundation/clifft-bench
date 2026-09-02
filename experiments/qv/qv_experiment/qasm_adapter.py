"""Adapters for the cx/u3 QASM emitted by the paper QV generator."""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import NamedTuple


class GateOp(NamedTuple):
    name: str
    params: list[float]
    qubits: list[int]


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_RE_U3 = re.compile(r"u3\(([^)]+)\)\s+(\w+)\[(\d+)\]\s*;")
_RE_CX = re.compile(r"cx\s+(\w+)\[(\d+)\]\s*,\s*(\w+)\[(\d+)\]\s*;")
_RE_MEASURE = re.compile(
    r"measure\s+(\w+)\[(\d+)\]\s*->\s*(\w+)\[(\d+)\]\s*;"
)
_RE_QREG = re.compile(r"qreg\s+(\w+)\[(\d+)\]\s*;")


def _safe_eval(expression: str) -> float:
    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id == "pi":
            return math.pi
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return float(_BINARY[type(node.op)](evaluate(node.left), evaluate(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return float(_UNARY[type(node.op)](evaluate(node.operand)))
        raise ValueError(f"unsupported QASM expression: {expression!r}")

    try:
        return evaluate(ast.parse(expression.strip(), mode="eval"))
    except SyntaxError as error:
        raise ValueError(f"invalid QASM expression: {expression!r}") from error


def parse_qasm(qasm: str) -> tuple[list[GateOp], int]:
    operations: list[GateOp] = []
    num_qubits = 0
    for raw_line in qasm.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("OPENQASM", "include", "creg")):
            continue
        if match := _RE_QREG.fullmatch(line):
            num_qubits = max(num_qubits, int(match.group(2)))
        elif line.startswith("barrier"):
            continue
        elif match := _RE_U3.fullmatch(line):
            params = [_safe_eval(item) for item in match.group(1).split(",")]
            if len(params) != 3:
                raise ValueError(f"u3 requires three parameters: {line!r}")
            operations.append(GateOp("u3", params, [int(match.group(3))]))
        elif match := _RE_CX.fullmatch(line):
            operations.append(
                GateOp("cx", [], [int(match.group(2)), int(match.group(4))])
            )
        elif match := _RE_MEASURE.fullmatch(line):
            operations.append(
                GateOp("measure", [], [int(match.group(2)), int(match.group(4))])
            )
        else:
            raise ValueError(f"unsupported QASM statement: {line!r}")
    if num_qubits < 1:
        raise ValueError("QASM contains no quantum register")
    return operations, num_qubits


def to_clifft_stim(qasm: str) -> str:
    lines: list[str] = []
    for operation in parse_qasm(qasm)[0]:
        if operation.name == "u3":
            theta, phi, lam = (value / math.pi for value in operation.params)
            lines.append(f"U3({theta},{phi},{lam}) {operation.qubits[0]}")
        elif operation.name == "cx":
            lines.append(f"CX {operation.qubits[0]} {operation.qubits[1]}")
        elif operation.name == "measure":
            lines.append(f"M {operation.qubits[0]}")
    return "\n".join(lines) + "\n"


def to_qulacs_circuit(qasm: str):  # type: ignore[no-untyped-def]
    import qulacs
    from qulacs import gate

    operations, num_qubits = parse_qasm(qasm)
    circuit = qulacs.QuantumCircuit(num_qubits)
    register_index = 0
    for operation in operations:
        if operation.name == "u3":
            circuit.add_gate(gate.U3(operation.qubits[0], *operation.params))
        elif operation.name == "cx":
            circuit.add_gate(gate.CNOT(operation.qubits[0], operation.qubits[1]))
        elif operation.name == "measure":
            circuit.add_gate(gate.Measurement(operation.qubits[0], register_index))
            register_index += 1
    return circuit, num_qubits


def to_cirq_circuit(qasm: str):  # type: ignore[no-untyped-def]
    from cirq.contrib.qasm_import import circuit_from_qasm

    source = "\n".join(
        line for line in qasm.splitlines() if not line.strip().startswith("barrier")
    )
    return circuit_from_qasm(source)
