from __future__ import annotations

import cmath
import itertools
import math
from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.adapters.base import Counts
from clifft_bench.config import load_suite

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
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    return next(c for c in suite.cases if c.workload.id == workload_id
                and c.definition["variant_id"] == "clifft-current-off")


def _check_readout(sample, shots, probability):
    if isinstance(sample, Counts):
        assert sample.attempted_shots == sample.accepted_shots == shots
        assert sample.discarded_shots == 0
        ones = sample.logical_errors
    else:
        detectors, observables = sample
        assert len(detectors) == len(observables) == shots
        ones = int((observables[:, 0] & 1).sum())
    tolerance = 6 * math.sqrt(shots * probability * (1 - probability)) + 1
    assert abs(ones - shots * probability) <= tolerance


def test_new_circuits_are_part_of_the_release_campaign():
    release = load_suite(ROOT / "campaigns/release-v1.toml")
    cases = [case for case in release.cases if case.workload.id in NEW_WORKLOADS]
    assert {c.workload.id for c in cases} == NEW_WORKLOADS
    assert all(c.definition["execution"]["postselect"] == "off" for c in cases)
    for case in cases:
        assert not case.workload.definition["semantics"]["postselect_all_detectors"]
        assert case.definition["shots_per_call"] == case.workload.definition["shots_per_call"]
        assert case.definition["execution"]["batch_size"] == "auto"
    smoke_cases = [case for path in ("smoke.toml", "tsim-smoke.toml")
                   for case in load_suite(ROOT / "campaigns" / path).cases]
    assert NEW_WORKLOADS <= {case.workload.id for case in smoke_cases}


def test_logical_reference_probabilities():
    assert bt_observable_probability() == 3 / 8
    assert reed_muller_probability() == pytest.approx((1 - 2**-0.5) / 2)


@pytest.mark.parametrize("adapter", ["clifft", "symft", "tsim"])
@pytest.mark.parametrize("batch_size", [1, 32])
def test_measured_reed_muller_sampling(adapter, batch_size):
    module = pytest.importorskip(adapter)
    case = _case("reed-muller-measured")
    sampler = load_adapter(adapter, module.version() if adapter == "clifft" else None)
    mode = "off" if "off" in sampler.supported_postselect else "on"
    execution = {"postselect": mode, "batch_size": batch_size,
                 "options": {"sample_chunk_shots": 2048} if adapter == "symft" else {}}
    prepared = sampler.prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution=execution, seed=87,
    )
    for key, value in case.workload.definition["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == value
    _check_readout(prepared.sample(16384, 88), 16384, reed_muller_probability())


@pytest.mark.parametrize("workload_id,width", [("bt27-direct-x", 9), ("bt81-direct-x", 6)])
@pytest.mark.parametrize("batch_size", [1, 32])
def test_current_clifft_code_switching_sampling(workload_id, width, batch_size):
    clifft = pytest.importorskip("clifft")
    sampler = load_adapter("clifft", clifft.version())
    if "off" not in sampler.supported_postselect:
        pytest.skip("requires Clifft packed sampling")
    case = _case(workload_id)
    execution = dict(case.definition["execution"], batch_size=batch_size)
    prepared = sampler.prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution=execution, seed=117,
    )
    for key, value in case.workload.definition["expected_metadata"].items():
        assert prepared.runtime_metadata[key] == value
    assert prepared.runtime_metadata["peak_active_width"] == width
    _check_readout(prepared.sample(4096, 118), 4096, bt_observable_probability())
