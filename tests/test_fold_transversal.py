from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest
import stim

from clifft_bench.adapters import load_adapter
from clifft_bench.adapters.base import validate_counts
from clifft_bench.config import load_suite
from workloads.generate_fold_transversal import circuit_text, controlled_sx

ROOT = Path(__file__).resolve().parents[1]


def _unitary(lines, qubits):
    matrix = np.eye(1 << qubits, dtype=complex)
    states = np.arange(len(matrix))
    gates = {
        "H": np.array([[1, 1], [1, -1]]) / math.sqrt(2),
        "S": np.diag([1, 1j]),
        "T": np.diag([1, np.exp(1j * np.pi / 4)]),
        "T_DAG": np.diag([1, np.exp(-1j * np.pi / 4)]),
    }
    for line in lines:
        name, *targets = line.split()
        if name == "MPP":
            break
        targets = list(map(int, targets))
        if name == "CX":
            control, target = targets
            matrix = matrix[states ^ (((states >> control) & 1) << target)]
        else:
            for target in targets:
                for zero in states[(states >> target) & 1 == 0]:
                    pair = [zero, zero | (1 << target)]
                    matrix[pair] = gates[name] @ matrix[pair]
    return matrix


@pytest.mark.parametrize("dagger", [False, True])
def test_controlled_sx_matches_source_unitary_including_controlled_phase(dagger):
    expected = np.eye(4, dtype=complex)
    # Source local order is little endian; the first qubit controls S X or S† X.
    expected[np.ix_([1, 3], [1, 3])] = [[0, 1], [-1j if dagger else 1j, 0]]
    actual = _unitary(controlled_sx(0, 1, dagger=dagger), 2)
    np.testing.assert_allclose(actual, expected, atol=1e-15)


def test_injection_preserves_the_source_unitary_on_faulty_inputs():
    model = {"events": [{"name": "INJECT_HXY", "support": [0]}],
             "channels": [], "logical": 0}
    w = np.exp(1j * np.pi / 4)
    source = np.array([[1, w**3], [w, 1]]) / math.sqrt(2)
    # Include the generator's ideal T† readout before its X measurement.
    np.testing.assert_allclose(
        _unitary(circuit_text(model, 0).splitlines(), 1),
        np.diag([1, w.conjugate()]) @ source, atol=1e-15,
    )


@pytest.mark.parametrize("probability", [0, 0.001, 0.25])
def test_three_qubit_noise_preserves_the_full_joint_pauli_distribution(probability):
    model = {"events": [{"name": "NOISE", "location": 0}], "logical": 0,
             "channels": [{"kind": "depol3", "support": [0, 1, 2]}]}
    noise = stim.Circuit("\n".join(line for line in circuit_text(model, probability).splitlines()
                                   if line.startswith("CORRELATED_ERROR")))
    distribution = np.zeros(64)
    distribution[0] = 1
    for instruction in noise:
        pauli = 0
        for target in instruction.targets_copy():
            axis = 1 if target.is_x_target else 2 if target.is_z_target else 3
            pauli ^= axis << (2 * target.value)
        p, = instruction.gate_args_copy()
        distribution = (1 - p) * distribution + p * distribution[np.arange(64) ^ pauli]
    expected = np.full(64, probability / 63)
    expected[0] = 1 - probability
    np.testing.assert_allclose(distribution, expected, rtol=1e-12, atol=2e-15)


def _workload(distance):
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    return next(w for w in suite.workloads_document["workloads"]
                if w["id"] == f"fold-transversal-d{distance}-p1e-3")


@pytest.mark.parametrize("distance", [3, 5])
def test_fold_metadata_and_logical_readout_match_the_manifest(distance):
    workload = _workload(distance)
    text = (ROOT / workload["artifact"]["path"]).read_text()
    # Annotation indexing only; the substitutions are not a physics oracle.
    layout = stim.Circuit(re.sub(r"(?m)^(T_DAG|T|CCZ)\b", "I", text))
    assert {k: getattr(layout, k) for k in workload["expected_metadata"]} == (
        workload["expected_metadata"]
    )
    assert str(layout[-1]) == "OBSERVABLE_INCLUDE(0) rec[-1]"
    assert layout[-2].name == "MPP"
    assert workload["semantics"]["postselect_all_detectors"]


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
@pytest.mark.parametrize("distance", [3, 5])
def test_fold_noisy_artifact_with_auto_batch(adapter, distance):
    pytest.importorskip(adapter)
    workload = _workload(distance)
    prepared = load_adapter(adapter).prepare(
        artifact_path=ROOT / workload["artifact"]["path"], workload=workload,
        execution={"postselect": "on", "batch_size": "auto"}, seed=91,
    )
    for key, value in workload["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == value
    counts = prepared.sample(16, 91)
    assert counts.attempted_shots == 16
    assert not validate_counts(counts)


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
@pytest.mark.parametrize("distance", [3, 5])
@pytest.mark.parametrize("logical_flip", [False, True])
def test_fold_ideal_acceptance_and_logical_error(adapter, distance, logical_flip, tmp_path):
    pytest.importorskip(adapter)
    workload = _workload(distance)
    text = (ROOT / workload["artifact"]["path"]).read_text()
    text = re.sub(r"(?m)^(?:[XYZ]_ERROR|DEPOLARIZE[12]|CORRELATED_ERROR)\([^\n]*\n", "", text)
    if logical_flip:
        # The last T† is the noiseless logical readout. Z on its parity target
        # applies logical Z after all detectors and reverses the H_XY outcome.
        prefix, readout = text.rsplit("T_DAG ", 1)
        target = readout.splitlines()[0]
        text = prefix + f"Z {target}\nT_DAG " + readout
    artifact = tmp_path / "fold.stim"
    artifact.write_text(text)
    prepared = load_adapter(adapter).prepare(
        artifact_path=artifact, workload=workload,
        execution={"postselect": "on", "batch_size": 1}, seed=91,
    )
    counts = prepared.sample(1, 91)
    assert counts.accepted_shots == counts.attempted_shots == 1
    assert counts.discarded_shots == 0
    assert counts.logical_errors == int(logical_flip)
