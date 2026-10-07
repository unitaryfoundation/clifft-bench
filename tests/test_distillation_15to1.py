from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.manifest import load_suite

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_ID = "distillation-15to1-rm15-p1e-3"
ARTIFACT = ROOT / "workloads/circuits/distillation_15to1_rm15_p1e-3.stim"
NOISE = "Z_ERROR(0.001) " + " ".join(map(str, range(15)))


def _prepare(adapter, tmp_path, text):
    module = pytest.importorskip(adapter)
    path = tmp_path / "distillation.stim"
    path.write_text(text)
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    case = next(c for c in suite.cases if c.workload.id == WORKLOAD_ID)
    execution = {"batch_enabled": True, "batch_size": 1024,
                 "sample_chunk_shots": 2048 if adapter == "symft" else 0}
    if adapter == "clifft" and hasattr(module, "ActiveWidthSchedulePass"):
        execution["clifft_scheduler"] = json.loads(
            (ROOT / "campaigns/release-v1/clifft-scheduled-execution.json").read_text()
        )["clifft_scheduler"]
    prepared = load_adapter(adapter).prepare(
        artifact_path=path, workload=case.workload.definition, execution=execution,
    )
    for key, expected in case.workload.definition["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == expected
    prepared.begin_sample(42)
    return prepared


def _accepted_weight_enumerator():
    # The four X checks form the [15,11,3] Hamming parity-check matrix:
    # column q is binary(q+1). A Z pattern passes iff XOR(q+1) is zero;
    # inverse-T verification reports its odd/even weight as logical X parity.
    counts = Counter()
    for mask in range(1 << 15):
        syndrome = 0
        for q in range(15):
            if mask >> q & 1:
                syndrome ^= q + 1
        if syndrome == 0:
            counts[mask.bit_count()] += 1
    return counts


def test_circuit_checks_match_hamming_matrix_and_cubic_error_floor():
    text = ARTIFACT.read_text()
    assert text.isascii()
    assert text.count(NOISE) == 1
    checks = [line for line in text.splitlines() if line.startswith("MPP X")]
    assert checks[:4] == [
        "MPP " + "*".join(f"X{q}" for q in range(15) if (q + 1) >> bit & 1)
        for bit in range(4)
    ]
    assert checks[4] == "MPP " + "*".join(f"X{q}" for q in range(15))
    counts = _accepted_weight_enumerator()
    assert sum(counts.values()) == 2**11
    assert counts[0] == 1
    assert counts[1] == counts[2] == 0
    assert counts[3] == 35


@pytest.mark.parametrize("adapter", ["clifft", "symft", "xtim"])
@pytest.mark.parametrize("faults,accepted,logical", [
    ([], True, 0), ([0], False, 0), ([0, 1], False, 0),
    ([0, 1, 2], True, 1), ([0, 1, 3, 6], True, 0),
])
def test_exact_fault_patterns_verify_postselection_and_logical_readout(
    adapter, faults, accepted, logical, tmp_path,
):
    replacement = "Z " + " ".join(map(str, faults)) if faults else ""
    p = _prepare(adapter, tmp_path, ARTIFACT.read_text().replace(NOISE, replacement))
    result = p.sample(32, 42)
    assert result.accepted_shots == (32 if accepted else 0)
    assert result.discarded_shots == (0 if accepted else 32)
    assert result.logical_errors == logical * 32


@pytest.mark.parametrize("adapter", ["clifft", "symft", "xtim"])
def test_noisy_distribution_matches_exhaustive_phase_fault_enumeration(adapter, tmp_path):
    probability = 0.05  # Enough accepted logical faults for a useful statistical check.
    text = ARTIFACT.read_text().replace("Z_ERROR(0.001)", f"Z_ERROR({probability})")
    p = _prepare(adapter, tmp_path, text)
    weights = _accepted_weight_enumerator()
    probabilities = {
        w: n * probability**w * (1 - probability)**(15 - w)
        for w, n in weights.items()
    }
    expected_accept = sum(probabilities.values())
    expected_joint_error = sum(value for w, value in probabilities.items() if w % 2)
    assert expected_accept == pytest.approx((1 + 15 * (1 - 2 * probability)**8) / 16)
    shots = 200000
    counts = p.sample(shots, 173)
    for observed, expected in [
        (counts.accepted_shots / shots, expected_accept),
        (counts.logical_errors / shots, expected_joint_error),
    ]:
        tolerance = 6 * math.sqrt(expected * (1 - expected) / shots) + 1 / shots
        assert observed == pytest.approx(expected, abs=tolerance)
