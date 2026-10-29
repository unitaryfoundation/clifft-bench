from __future__ import annotations

import cmath
import itertools
import math
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.manifest import load_suite

ROOT = Path(__file__).resolve().parents[1]
NEW_WORKLOADS = {"bt27-direct-x", "bt81-direct-x", "reed-muller-measured"}


def bt_observable_probability() -> float:
    # Merlin's pinned generator gives the six permutations of (0, 1, 2)
    # for both BT27 and BT81: logical CCZ on (a, 3+b, 6+c), starting in |+>^9.
    triples = list(itertools.permutations(range(3)))

    def sign(word):
        parity = (
            sum(
                ((word >> a) & 1) * ((word >> (3 + b)) & 1) * ((word >> (6 + c)) & 1)
                for a, b, c in triples
            )
            % 2
        )
        return (-1) ** parity

    expectation_x0 = sum(sign(word) * sign(word ^ 1) for word in range(512)) / 512
    return (1 - expectation_x0) / 2


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
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    return next(c for c in suite.cases if c.workload.id == workload_id
                and c.definition["variant_id"] == "clifft-current")


def _check_counts(counts, probability):
    assert counts.attempted_shots == counts.accepted_shots
    assert counts.discarded_shots == 0
    shots = counts.attempted_shots
    tolerance = 6 * math.sqrt(shots * probability * (1 - probability)) + 1
    assert abs(counts.logical_errors - shots * probability) <= tolerance


def test_new_circuits_are_part_of_the_release_campaign():
    release = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    cases = [case for case in release.cases if case.workload.id in NEW_WORKLOADS]
    assert {(c.workload.id, c.definition["variant_id"]) for c in cases} == {
        ("bt27-direct-x", "clifft-current"),
        ("bt81-direct-x", "clifft-current"),
        ("reed-muller-measured", "clifft-current"),
        ("reed-muller-measured", "clifft-previous"),
        ("reed-muller-measured", "symft-current"),
        ("reed-muller-measured", "tsim-current"),
        ("bt27-direct-x", "clifft-tuned"),
        ("bt81-direct-x", "clifft-tuned"),
        ("reed-muller-measured", "clifft-tuned"),
        ("reed-muller-measured", "symft-tuned"),
        ("reed-muller-measured", "tsim-tuned"),
    }
    for case in cases:
        assert not case.workload.definition["semantics"]["postselect_all_detectors"]
        assert case.definition["shots_per_call"] == (
            100000 if case.workload.id == "reed-muller-measured" else 2048
        )
        assert case.definition["execution"]["batch_size"] == "calibrate"
    smoke_cases = [case for path in ("run-smoke.v1.json", "run-tsim-smoke.v1.json")
                   for case in load_suite(ROOT / "manifests" / path).cases]
    assert NEW_WORKLOADS <= {case.workload.id for case in smoke_cases}


def test_logical_reference_probabilities():
    assert bt_observable_probability() == 3 / 8
    assert reed_muller_probability() == pytest.approx((1 - 2**-0.5) / 2)


@pytest.mark.parametrize("adapter", ["clifft", "symft", "tsim"])
@pytest.mark.parametrize("batch_size", [1, 32])
def test_measured_reed_muller_sampling(adapter, batch_size):
    pytest.importorskip(adapter)
    case = _case("reed-muller-measured")
    execution = {"batch_enabled": True, "batch_size": batch_size,
                 "sample_chunk_shots": 2048 if adapter == "symft" else 0}
    if adapter == "clifft":
        execution["clifft_scheduler"] = case.definition["execution"]["clifft_scheduler"]
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
    _check_counts(prepared.sample(4096, 118), bt_observable_probability())
