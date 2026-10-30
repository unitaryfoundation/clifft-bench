from __future__ import annotations

import copy
import math
import re
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.manifest import load_suite
from clifft_bench.selection import detector_mask, has_postselection

ROOT = Path(__file__).resolve().parents[1]
PAIRS = (
    "msc-d3-inject-cultivate-p1e-3",
    "surface-code-d7-r7-p1e-3",
    "coherent-surface-d3-r3-p1e-3-rz2e-2",
)


def test_selection_pairs_reuse_exact_circuits_and_public_call_sizes():
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    workloads = {w["id"]: w for w in suite.workloads_document["workloads"]}
    pairs = [w for w in workloads.values() if "postselection_pair" in w["parameters"]]
    assert len(pairs) == len(PAIRS)
    assert {w["parameters"]["postselection_pair"] for w in pairs} == set(PAIRS)
    for unselected in pairs:
        selected_id = unselected["parameters"]["postselection_pair"]
        selected = workloads[selected_id]
        assert selected["artifact"] == unselected["artifact"]
        assert selected["expected_metadata"] == unselected["expected_metadata"]
        assert selected["compatible_adapters"] == unselected["compatible_adapters"]
        assert has_postselection(selected["semantics"])
        assert not has_postselection(unselected["semantics"])
        for case in suite.cases:
            if case.workload.id != unselected["id"]:
                continue
            peer = next(c for c in suite.cases if c.workload.id == selected_id
                        and c.definition["variant_id"] ==
                        case.definition["variant_id"].replace("-coverage", "-current"))
            assert case.definition["shots_per_call"] == peer.definition["shots_per_call"]
            assert case.definition["execution"] == peer.definition["execution"]


def test_full_cultivation_mask_follows_pinned_coordinate_rule():
    import stim

    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    case = next(c for c in suite.cases
                if c.workload.id == "msc-full-d5-cultivation-selected-p1e-3")
    # Clifford substitution is for annotation indexing ONLY, not a physics oracle.
    layout = stim.Circuit(re.sub(r"^(T_DAG|T)\b", "I",
                                 case.workload.artifact_path.read_text(), flags=re.MULTILINE))
    expected = [d for d, coords in layout.get_detector_coordinates().items()
                if len(coords) <= 4 or coords[4] == -9]
    semantics = case.workload.definition["semantics"]
    assert semantics["postselection_detector_indices"] == expected
    assert len(expected) == 107 < layout.num_detectors == 2744
    assert not semantics["postselect_all_detectors"] and has_postselection(semantics)
    assert sum(detector_mask(semantics, layout.num_detectors)) == 107
    assert not re.search(r"^EXP_VAL\b", case.workload.artifact_path.read_text(), re.MULTILINE)


def test_coverage_smoke_exercises_every_declared_release_combination():
    release = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    smoke = load_suite(ROOT / "manifests/run-coverage-smoke.v1.json")
    expected = {c.id: c for c in release.cases
                if c.definition["variant_id"].endswith("-coverage")}
    assert len(expected) == len(smoke.cases) == 23
    assert set(expected) == {c.id for c in smoke.cases}
    for case in smoke.cases:
        assert case.workload == expected[case.id].workload
        assert case.implementation == expected[case.id].implementation


@pytest.mark.parametrize("indices", [None, [], [1, 1], [1, 0], [-1], [2], [True], [0.5]])
def test_detector_subsets_reject_ambiguous_or_out_of_range_indices(indices):
    with pytest.raises(ValueError, match="indices"):
        detector_mask({"postselect_all_detectors": False,
                       "postselection_detector_indices": indices}, 2)


def test_detector_subsets_cannot_also_select_all():
    with pytest.raises(ValueError, match="also select all"):
        detector_mask({"postselect_all_detectors": True,
                       "postselection_detector_indices": [0]}, 2)


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
@pytest.mark.parametrize("batch", [1, 32])
@pytest.mark.parametrize("selected", [0, 1])
def test_partial_selection_counts_only_selected_raw_detector(adapter, batch, selected, tmp_path):
    pytest.importorskip(adapter)
    # D0 is Bernoulli(1/4); D1 is always raw 1. O0 = NOT D0.
    # Selecting D0 keeps 3/4, all with O0=1. Selecting D1 rejects everything.
    circuit = tmp_path / "partial.stim"
    circuit.write_text("X_ERROR(0.25) 0\nM 0\nDETECTOR rec[-1]\n"
                       "X 1\nM 1\nDETECTOR rec[-1]\n"
                       "OBSERVABLE_INCLUDE(0) rec[-2] rec[-1]\n")
    semantics = {"reference_convention": "raw-record-parity", "observable_index": 0,
                 "postselect_all_detectors": False, "postselection_detector_indices": [selected]}
    prepared = load_adapter(adapter).prepare(
        artifact_path=circuit, workload={"semantics": semantics},
        execution={"batch_enabled": batch > 1, "batch_size": batch,
                   "sample_chunk_shots": 0}, seed=918,
    )
    prepared.begin_sample(919)
    counts = prepared.sample(16384, 919)
    probability = 0.75 if selected == 0 else 0
    assert counts.attempted_shots == counts.accepted_shots + counts.discarded_shots == 16384
    assert counts.logical_errors == counts.accepted_shots
    assert abs(counts.accepted_shots - 16384 * probability) <= (
        6 * math.sqrt(16384 * probability * (1-probability)) + 1
    )


def qv_observable_probability(path: Path, qubits: int) -> float:
    """Independent dense statevector for the immutable U3/CX QV grammar."""
    import numpy as np

    state = np.zeros(1 << qubits, dtype=np.complex128)
    state[0] = 1
    indices = np.arange(len(state))
    measurements = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("U3("):
            assert not measurements
            angles, target = line[3:].split(")")
            theta, phi, lam = [float(a) * np.pi for a in angles.split(",")]
            c, s = np.cos(theta / 2), np.sin(theta / 2)
            block = state.reshape(-1, 2, 1 << int(target))
            a, b = block[:, 0, :].copy(), block[:, 1, :].copy()
            block[:, 0, :] = c*a - np.exp(1j*lam)*s*b
            block[:, 1, :] = np.exp(1j*phi)*s*a + np.exp(1j*(phi+lam))*c*b
        elif line.startswith("CX "):
            assert not measurements
            control, target = map(int, line.split()[1:])
            a = indices[((indices >> control) & 1 == 1) & ((indices >> target) & 1 == 0)]
            b = a | (1 << target)
            state[a], state[b] = state[b].copy(), state[a].copy()
        elif line.startswith("M "):
            measurements.extend(map(int, line.split()[1:]))
        else:
            assert line == "OBSERVABLE_INCLUDE(0) rec[-1]"
    assert len(measurements) == qubits
    assert float(np.vdot(state, state).real) == pytest.approx(1)
    return float(np.sum(np.abs(state[(indices >> measurements[-1]) & 1 == 1])**2))


@pytest.mark.parametrize("adapter", ["clifft", "symft", "tsim"])
@pytest.mark.parametrize("batch", [1, 32])
def test_repeated_qv10_matches_independent_statevector(adapter, batch):
    pytest.importorskip(adapter)
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    case = next(c for c in suite.cases if c.workload.id == "quantum-volume-q10-seed42")
    probability = qv_observable_probability(case.workload.artifact_path, 10)
    execution = copy.deepcopy(case.definition["execution"])
    execution.update(batch_size=batch, batch_enabled=batch > 1,
                     sample_chunk_shots=2048 if adapter == "symft" else 0)
    if adapter != "clifft":
        execution.pop("clifft_scheduler", None)
    prepared = load_adapter(adapter).prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution=execution, seed=1742,
    )
    for seed in (1743, 1744):
        prepared.begin_sample(seed)
        counts = prepared.sample(4096, seed)
        assert counts.accepted_shots == counts.attempted_shots == 4096
        assert counts.discarded_shots == 0
        assert abs(counts.logical_errors - 4096 * probability) <= (
            6 * math.sqrt(4096 * probability * (1-probability)) + 1
        )
