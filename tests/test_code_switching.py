from __future__ import annotations

import cmath
import itertools
import math
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.config import load_suite

ROOT = Path(__file__).resolve().parents[1]
NEW_WORKLOADS = {"bt27-direct-x", "bt81-direct-x", "reed-muller-measured"}


def bt_observable_probability(circuit: str) -> Fraction:
    """Exact raw observable-0 marginal for the frozen BT circuit grammar.

    Initial RXs introduce independent binary variables. CNOTs map each qubit
    to a linear form; T/T_DAG add +/- that form to a phase P modulo eight.
    Early Z measurements are deferred: their untouched qubits retain all
    branch records, and record-controlled Xs become coherent CNOTs. Terminal
    measurements on other qubits are traced out (no selection or feedback).

    If L*d = e_q has no solution, X_q maps the support to an orthogonal space.
    Otherwise <X_q> is the uniform average of exp(i*pi*(P(x+d)-P(x))/4).
    Only T terms whose linear form flips contribute. Expanding their XORs
    modulo eight leaves a quadratic polynomial. For these circuits its
    coefficients are 0 or 4, so the average is an exact signed integer sum.
    """
    instructions = [line.split("#", 1)[0].split() for line in circuit.splitlines()]
    instructions = [tokens for tokens in instructions if tokens]
    gates = {"RX", "CX", "T", "T_DAG"}
    last_gate = max((i for i, t in enumerate(instructions) if t[0] in gates), default=-1)
    forms, records, observables, terms = {}, [], {}, []
    early_measured, terminal_measured = set(), set()
    variables, preparing = 0, True

    def qubit(token):
        assert re.fullmatch(r"[0-9]+", token), "expected a nonnegative qubit index"
        q = int(token)
        forms.setdefault(q, 0)
        return q

    def record(token):
        assert re.fullmatch(r"rec\[-[1-9][0-9]*\]", token), "expected a record lookback"
        offset = int(token[4:-1])
        assert -len(records) <= offset < 0, "record lookback is out of range"
        return records[offset]

    for position, (op, *args) in enumerate(instructions):
        assert args, "instruction needs targets"
        if op == "RX":
            assert preparing, "RX is only supported in initial preparation"
            for token in args:
                q = qubit(token)
                assert not forms[q], "repeated preparation"
                forms[q] = 1 << variables
                variables += 1
            continue
        preparing = False
        if op == "CX":
            assert len(args) == 2, "reference expects one CNOT pair per instruction"
            control, target = args
            q = qubit(target)
            assert q not in early_measured, "cannot reuse an early measured qubit"
            if control.startswith("rec["):
                basis, source, form = record(control)
                assert basis == "M" and source in early_measured, "feedback needs an early Z record"
                assert forms[source] == form, "deferred record qubit changed"
            else:
                source = qubit(control)
                assert source != q, "CNOT targets must differ"
                assert source not in early_measured, "cannot reuse an early measured qubit"
                form = forms[source]
            forms[q] ^= form
        elif op in {"T", "T_DAG"}:
            for token in args:
                q = qubit(token)
                assert q not in early_measured, "cannot reuse an early measured qubit"
                terms.append((forms[q], 1 if op == "T" else -1))
        elif op in {"M", "MX"}:
            for token in args:
                q = qubit(token)
                assert q not in early_measured | terminal_measured, "repeated measurement"
                records.append((op, q, forms[q]))
                if position < last_gate:
                    assert op == "M" and not terms, "only pre-phase Z measurements may be deferred"
                    early_measured.add(q)
                else:
                    terminal_measured.add(q)
        elif op == "DETECTOR":
            # Annotations do not select branches; validate their record references.
            for token in args:
                record(token)
        elif match := re.fullmatch(r"OBSERVABLE_INCLUDE\(([0-9]+)\)", op):
            index = int(match[1])
            assert index not in observables and len(args) == 1, "expected one record per observable"
            observables[index] = record(args[0])
        else:
            raise AssertionError(f"unsupported reference instruction: {op}")

    assert 0 in observables, "missing observable 0"
    basis, target, _ = observables[0]
    assert basis == "MX" and target in terminal_measured, "observable 0 must be a terminal X record"

    # Solve L*d=e_target over GF(2), retaining every branch-record qubit in L.
    equations, outside_support = {}, False
    for q, form in forms.items():
        rhs = int(q == target)
        while form:
            pivot = form.bit_length() - 1
            if pivot not in equations:
                equations[pivot] = (form, rhs)
                break
            row, bit = equations[pivot]
            form, rhs = form ^ row, rhs ^ bit
        else:
            outside_support |= bool(rhs)
    assert len(equations) == variables, "initial variables must remain independent"
    if outside_support:
        return Fraction(1, 2)
    delta = 0
    for pivot, (form, rhs) in sorted(equations.items()):
        if ((form & delta).bit_count() & 1) ^ rhs:
            delta |= 1 << pivot

    # For a flipped form l, its contribution is sign*(1-2*l(x)) mod 8.
    # XOR(x_i) = sum(x_i)-2*sum(x_i*x_j)+4*sum(x_i*x_j*x_k)... .
    phase = Counter()
    for form, sign in terms:
        if not (form & delta).bit_count() & 1:
            continue
        bits = [1 << i for i in range(variables) if form >> i & 1]
        phase[0] += sign
        for bit in bits:
            phase[bit] -= 2 * sign
        for a, b in itertools.combinations(bits, 2):
            phase[a | b] += 4 * sign
    phase = {monomial: coefficient % 8 for monomial, coefficient in phase.items()
             if coefficient % 8}
    assert set(phase.values()) <= {4}, "reference requires a real sign-valued phase difference"
    active = sorted({i for monomial in phase for i in range(variables) if monomial >> i & 1})
    assert len(active) <= 16, "exact reference enumeration exceeds its bound"
    monomials = [sum(1 << j for j, i in enumerate(active) if monomial >> i & 1)
                 for monomial in phase]
    signed_sum = sum(
        (-1) ** (sum(word & monomial == monomial for monomial in monomials) % 2)
        for word in range(1 << len(active))
    )
    expectation = Fraction(signed_sum, 1 << len(active))
    return (1 - expectation) / 2


def _dense_observable_probability(circuit: str, qubits: int) -> float:
    """Tiny independent oracle: explicit complex amplitudes and measurement branches."""
    assert qubits <= 5
    states = [([1 + 0j] + [0j] * ((1 << qubits) - 1), [])]
    observable = None

    def hadamard(state, q):
        for word in range(len(state)):
            if not word >> q & 1:
                other = word | (1 << q)
                a, b = state[word], state[other]
                state[word], state[other] = (a + b) / math.sqrt(2), (a - b) / math.sqrt(2)

    for line in circuit.splitlines():
        tokens = line.split("#", 1)[0].split()
        if not tokens:
            continue
        op, *args = tokens
        if op in {"M", "MX"}:
            for q in map(int, args):
                branches = []
                for state, records in states:
                    if op == "MX":
                        hadamard(state, q)
                    for outcome in (0, 1):
                        projected = [a if word >> q & 1 == outcome else 0j
                                     for word, a in enumerate(state)]
                        if sum(abs(a)**2 for a in projected) > 1e-24:
                            if op == "MX":
                                hadamard(projected, q)
                            branches.append((projected, records + [outcome]))
                states = branches
        elif op == "OBSERVABLE_INCLUDE(0)":
            observable = len(states[0][1]) + int(args[0][4:-1])
        elif op == "DETECTOR" or op.startswith("OBSERVABLE_INCLUDE("):
            continue
        else:
            for state, records in states:
                if op == "RX":
                    for q in map(int, args):
                        hadamard(state, q)
                elif op == "CX":
                    control, target = args
                    q = int(target)
                    for word in range(len(state)):
                        enabled = (records[int(control[4:-1])] if control.startswith("rec[")
                                   else word >> int(control) & 1)
                        if enabled and not word >> q & 1:
                            other = word | (1 << q)
                            state[word], state[other] = state[other], state[word]
                elif op in {"T", "T_DAG"}:
                    phase = cmath.exp((1j if op == "T" else -1j) * math.pi / 4)
                    for q in map(int, args):
                        for word in range(len(state)):
                            if word >> q & 1:
                                state[word] *= phase
                else:
                    raise AssertionError(op)
    assert sum(abs(a)**2 for state, _ in states for a in state) == pytest.approx(1)
    assert observable is not None
    return sum(abs(a)**2 for state, records in states if records[observable] for a in state)


def _ccz(a, b, c):
    # Seven-T identity, used only to construct tiny statevector cross-checks.
    return (
        f"T {a} {b} {c}\nCX {a} {b}\nT_DAG {b}\nCX {a} {b}\n"
        f"CX {a} {c}\nT_DAG {c}\nCX {b} {c}\nT {c}\n"
        f"CX {a} {c}\nT_DAG {c}\nCX {b} {c}\n"
    )


@pytest.mark.parametrize("preparation", [
    "RX 0 1 2\n",
    "RX 0 1 2 3\nM 3\nCX rec[-1] 1\n",
    "RX 0 1 2\nCX 1 3\nM 3\nCX rec[-1] 1\n",
    "RX 0 1 2\nCX 1 3\nM 3\nCX rec[-1] 2\n",
    "RX 0 1 2 4\nCX 1 3\nM 3 4\nCX rec[-2] 1\nCX rec[-1] 2\n",
])
@pytest.mark.parametrize("readout", [
    "MX 0 1 2\nOBSERVABLE_INCLUDE(0) rec[-3]\n",
    "MX 0 1 2\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
    "CX 0 2\nMX 2\nM 1\nMX 0\nDETECTOR rec[-2]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
    "T 0 0 0 0\nMX 0 1 2\nOBSERVABLE_INCLUDE(0) rec[-3]\n",
])
def test_bt_reference_matches_explicit_measurement_branches(preparation, readout):
    circuit = preparation + _ccz(0, 1, 2) + readout
    assert float(bt_observable_probability(circuit)) == pytest.approx(
        _dense_observable_probability(circuit, 5), abs=1e-12
    )


@pytest.mark.parametrize("circuit, probability", [
    ("MX 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", Fraction(1, 2)),
    ("RX 0\nMX 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", Fraction(0)),
    ("RX 0\nT 0 0 0 0\nMX 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", Fraction(1)),
    ("RX 0\nCX 0 1\nM 1\nMX 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", Fraction(1, 2)),
    ("RX 0\nM 0\nMX 1\nOBSERVABLE_INCLUDE(0) rec[-1]\n", Fraction(1, 2)),
])
def test_bt_reference_readout_and_support(circuit, probability):
    assert bt_observable_probability(circuit) == probability
    assert float(probability) == pytest.approx(_dense_observable_probability(circuit, 2))


@pytest.mark.parametrize("circuit, message", [
    ("H 0\n", "unsupported reference instruction"),
    ("RX 0\nRX 0\n", "repeated preparation"),
    ("CX 0 1\nRX 0\n", "only supported in initial preparation"),
    ("RX 0\nM 0\nCX 0 1\n", "cannot reuse an early measured qubit"),
    ("RX 0\nM 0\nT 0\n", "cannot reuse an early measured qubit"),
    ("RX 0\nMX 0\nCX rec[-1] 1\n", "only pre-phase Z measurements"),
    ("RX 0\nT 0\nM 0\nCX rec[-1] 1\n", "only pre-phase Z measurements"),
    ("RX 0\nCX rec[-1] 1\n", "record lookback is out of range"),
    ("RX 0\nCX -1 0\n", "nonnegative qubit index"),
    ("RX 0\nMX 0\nDETECTOR rec[-2]\n", "record lookback is out of range"),
    ("RX 0\nMX 0 0\n", "repeated measurement"),
    ("RX 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", "terminal X record"),
    ("RX 0\nT 0\nMX 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", "real sign-valued"),
    ("RX 0\nMX 0\nOBSERVABLE_INCLUDE(0) rec[-1] rec[-1]\n", "one record per observable"),
])
def test_bt_reference_rejects_unsupported_circuits(circuit, message):
    with pytest.raises(AssertionError, match=message):
        bt_observable_probability(circuit)


@pytest.mark.parametrize("workload_id", ["bt27-direct-x", "bt81-direct-x"])
def test_bt_full_circuit_reference(workload_id):
    case = _case(workload_id)
    assert case.workload.definition["semantics"] == {
        "observable_index": 0, "postselect_all_detectors": False,
    }
    assert bt_observable_probability(case.workload.artifact_path.read_text()) == Fraction(3, 8)


def reed_muller_probability() -> float:
    # Project |+>^15 onto the ten Z checks. The feedback sends every syndrome
    # branch to this same uniform code state. Transversal T assigns exp(i*pi*w/4).
    checks = [
        (1, 2, 3, 4),
        (0, 2, 3, 5),
        (0, 1, 3, 6),
        (1, 2, 7, 8),
        (0, 2, 7, 9),
        (0, 1, 7, 10),
        (0, 1, 2, 3, 7, 11),
        (0, 3, 7, 12),
        (1, 3, 7, 13),
        (2, 3, 7, 14),
    ]
    masks = [sum(1 << q for q in check) for check in checks]
    words = [w for w in range(1 << 15) if all((w & m).bit_count() % 2 == 0 for m in masks)]
    assert len(words) == 32
    expectation_x = sum(
        cmath.exp(1j * math.pi / 4 * (15 - 2 * w.bit_count())) for w in words
    ) / len(words)
    assert abs(expectation_x.imag) < 1e-12
    return (1 - expectation_x.real) / 2


def _case(workload_id):
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    return next(c for c in suite.cases if c.workload.id == workload_id
                and c.definition["variant_id"] == "clifft-current")


def _check_counts(counts, probability):
    assert counts.attempted_shots == counts.accepted_shots
    assert counts.discarded_shots == 0
    shots = counts.attempted_shots
    tolerance = 6 * math.sqrt(shots * probability * (1 - probability)) + 1
    assert abs(counts.logical_errors - shots * probability) <= tolerance


def test_new_circuits_are_part_of_the_release_campaign():
    release = load_suite(ROOT / "campaigns/release-v1.toml")
    cases = [case for case in release.cases if case.workload.id in NEW_WORKLOADS]
    assert {(c.workload.id, c.definition["variant_id"]) for c in cases} == {
        ("bt27-direct-x", "clifft-current"),
        ("bt81-direct-x", "clifft-current"),
        ("reed-muller-measured", "clifft-current"),
        ("reed-muller-measured", "clifft-previous"),
        ("reed-muller-measured", "symft-current"),
        ("reed-muller-measured", "tsim-current"),
    }
    for case in cases:
        assert not case.workload.definition["semantics"]["postselect_all_detectors"]
        assert case.definition["shots_per_call"] == (
            100000 if case.workload.id == "reed-muller-measured" else 2048
        )
        assert case.definition["execution"]["batch_size"] == "calibrate"
    smoke_cases = [case for path in ("smoke.toml", "tsim-smoke.toml")
                   for case in load_suite(ROOT / "campaigns" / path).cases]
    assert NEW_WORKLOADS <= {case.workload.id for case in smoke_cases}


def test_reed_muller_reference_probability():
    assert reed_muller_probability() == pytest.approx((1 - 2**-0.5) / 2)


@pytest.mark.parametrize("adapter", ["clifft", "symft", "tsim"])
@pytest.mark.parametrize("batch_size", [1, 32])
def test_measured_reed_muller_sampling(adapter, batch_size):
    pytest.importorskip(adapter)
    case = _case("reed-muller-measured")
    execution = {"batch_size": batch_size,
                 "options": {"sample_chunk_shots": 2048} if adapter == "symft" else {}}
    if adapter == "clifft":
        execution["options"]["scheduler"] = case.definition["execution"]["options"]["scheduler"]
    prepared = load_adapter(adapter).prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution=execution, seed=87,
    )
    for key, value in case.workload.definition["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == value
    prepared.begin_sample(88)
    _check_counts(prepared.sample(16384, 88), reed_muller_probability())


@pytest.mark.parametrize("workload_id,width", [("bt27-direct-x", 9), ("bt81-direct-x", 6)])
@pytest.mark.parametrize("batch_size", [1, 32])
def test_current_clifft_code_switching_sampling(workload_id, width, batch_size):
    clifft = pytest.importorskip("clifft")
    if not hasattr(clifft.HirModule, "postselection_mask"):
        pytest.skip("requires the post-0.12 code-switching compiler")
    case = _case(workload_id)
    execution = dict(case.definition["execution"], batch_size=batch_size)
    prepared = load_adapter("clifft").prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution=execution, seed=117,
    )
    for key, value in case.workload.definition["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == value
    assert prepared.runtime_metadata["peak_active_width"] == width
    prepared.begin_sample(118)
    _check_counts(
        # A nonmultiple of the packed capacity also exercises the final partial batch.
        prepared.sample(4097, 118),
        bt_observable_probability(case.workload.artifact_path.read_text()),
    )
