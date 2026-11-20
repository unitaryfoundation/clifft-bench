from __future__ import annotations

import copy
import math
import re
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.adapters.base import Counts
from clifft_bench.config import load_suite
from clifft_bench.selection import detector_mask, has_postselection

ROOT = Path(__file__).resolve().parents[1]


def test_modes_reuse_canonical_workload_and_call_size():
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    cases = {(c.definition["variant_id"], c.workload.id): c for c in suite.cases}
    matched = 0
    for (variant, workload), off in cases.items():
        on = cases.get((variant.removesuffix("-off"), workload))
        if off.definition["execution"]["postselect"] != "off" or on is off or on is None:
            continue
        matched += 1
        assert on.workload is off.workload
        assert on.definition["shots_per_call"] == off.definition["shots_per_call"]
    assert matched


def test_full_cultivation_mask_follows_pinned_coordinate_rule():
    import stim

    suite = load_suite(ROOT / "campaigns/release-v1.toml")
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


def test_coverage_smoke_exercises_release_endpoints_on_wide_and_subset_circuits():
    release = load_suite(ROOT / "campaigns/release-v1.toml")
    smoke = load_suite(ROOT / "campaigns/coverage-smoke.toml")
    workloads = {c.workload.id for c in smoke.cases}
    expected = {c.id: c for c in release.cases if c.workload.id in workloads
                and c.definition["execution"]["threads"] == 1
                and not c.definition["variant_id"].startswith("clifft-previous")}
    assert expected
    assert set(expected) == {c.id for c in smoke.cases}
    for case in smoke.cases:
        assert case.workload == expected[case.id].workload
        assert case.implementation == expected[case.id].implementation
        assert case.definition["execution"] == expected[case.id].definition["execution"]


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
    semantics = {"observable_index": 0,
                 "postselect_all_detectors": False, "postselection_detector_indices": [selected]}
    prepared = load_adapter(adapter).prepare(
        artifact_path=circuit, workload={"semantics": semantics},
        execution={"postselect": "on", "batch_size": batch,
                   "options": {}}, seed=918,
    )
    counts = prepared.sample(16384, 919)
    probability = 0.75 if selected == 0 else 0
    assert counts.attempted_shots == counts.accepted_shots + counts.discarded_shots == 16384
    assert counts.logical_errors == counts.accepted_shots
    assert abs(counts.accepted_shots - 16384 * probability) <= (
        6 * math.sqrt(16384 * probability * (1-probability)) + 1
    )


def qv_observable_probability(path: Path, qubits: int, observable: int) -> float:
    """Independent dense statevector for the immutable U3/CX QV grammar."""
    import numpy as np

    state = np.zeros(1 << qubits, dtype=np.complex128)
    state[0] = 1
    indices = np.arange(len(state))
    measurements, observables = [], []
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
            observables.append(line)
    assert len(measurements) == qubits
    assert observables == [
        f"OBSERVABLE_INCLUDE({k}) rec[-{qubits - k}]" for k in range(qubits)
    ]
    assert float(np.vdot(state, state).real) == pytest.approx(1)
    return float(np.sum(np.abs(state[(indices >> measurements[observable]) & 1 == 1])**2))


def _observable_ones(result, shots, observable):
    if isinstance(result, Counts):
        assert result.attempted_shots == result.accepted_shots == shots
        assert result.discarded_shots == 0
        return result.logical_errors
    detectors, observables = result
    assert detectors.shape == (shots, 0)
    return int(((observables[:, observable // 8] >> (observable % 8)) & 1).sum())


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
@pytest.mark.parametrize("batch", [1, 32])
@pytest.mark.parametrize("observable", [0, 9])
def test_repeated_qv10_matches_independent_statevector(adapter, batch, observable):
    module = pytest.importorskip(adapter)
    sampler = load_adapter(adapter, module.version() if adapter == "clifft" else None)
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    case = next(c for c in suite.cases
                if c.workload.id == "quantum-volume-q10-seed42-all-observables"
                and c.definition["variant_id"] == f"{adapter}-current-off")
    probability = qv_observable_probability(case.workload.artifact_path, 10, observable)
    execution = copy.deepcopy(case.definition["execution"])
    execution["batch_size"] = batch
    if "off" not in sampler.supported_postselect:
        execution["postselect"] = "on"
    workload = copy.deepcopy(case.workload.definition)
    workload["semantics"]["observable_index"] = observable
    prepared = sampler.prepare(
        artifact_path=case.workload.artifact_path, workload=workload,
        execution=execution, seed=1742,
    )
    for seed in (1743, 1744):
        ones = _observable_ones(prepared.sample(4096, seed), 4096, observable)
        assert abs(ones - 4096 * probability) <= (
            6 * math.sqrt(4096 * probability * (1-probability)) + 1
        )


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
@pytest.mark.parametrize("batch", [1, 32])
@pytest.mark.parametrize("observable", [1, 16, 17, 32])
def test_adder_readout_matches_basis_sum(adapter, batch, observable):
    module = pytest.importorskip(adapter)
    sampler = load_adapter(adapter, module.version() if adapter == "clifft" else None)
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    case = next(c for c in suite.cases
                if c.workload.id == "draper-adder-m16-basis-all-observables"
                and c.definition["variant_id"] == f"{adapter}-current-off")
    workload = copy.deepcopy(case.workload.definition)
    workload["semantics"]["observable_index"] = observable
    params = workload["parameters"]
    bits = params["bits_per_operand"]
    output_a = params["input_a"]
    output_b = (output_a + params["input_b"]) % (1 << bits)
    register, bit = divmod(observable - 1, bits)
    expected = ((output_a, output_b)[register] >> bit) & 1
    execution = copy.deepcopy(case.definition["execution"])
    execution["batch_size"] = batch
    if "off" not in sampler.supported_postselect:
        execution["postselect"] = "on"
    prepared = sampler.prepare(
        artifact_path=case.workload.artifact_path, workload=workload,
        execution=execution, seed=1742,
    )
    assert prepared.runtime_metadata["num_observables"] == 2 * bits + 1
    assert _observable_ones(prepared.sample(65, 1743), 65, observable) == expected * 65
