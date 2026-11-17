from __future__ import annotations

from pathlib import Path

import pytest
import stim

from clifft_bench.adapters.stim import StimAdapter
from clifft_bench.config import read_toml

ROOT = Path(__file__).resolve().parents[1]


def prepare(tmp_path, text, *, observable=0, postselect=True, chunk=32, seed=42):
    path = tmp_path / "circuit.stim"
    path.write_text(text)
    return StimAdapter().prepare(
        artifact_path=path,
        workload={
            "semantics": {
                "observable_index": observable,
                "postselect_all_detectors": postselect,
            }
        },
        execution={"batch_size": chunk, "options": {}},
        seed=seed,
    )


def test_surface_artifact_matches_paper_generator_and_declared_metadata():
    circuit = stim.Circuit((ROOT / "workloads/circuits/pure_surface_d7_r7_p1e-3.stim").read_text())
    generated = stim.Circuit.generated(
        "surface_code:rotated_memory_z",
        distance=7,
        rounds=7,
        after_clifford_depolarization=0.001,
        after_reset_flip_probability=0.001,
        before_measure_flip_probability=0.001,
        before_round_data_depolarization=0.001,
    )
    assert circuit == generated
    workloads = read_toml(ROOT / "workloads.toml")["workloads"]
    compatible = [w for w in workloads if "stim" in w["compatible_adapters"]]
    assert [w["id"] for w in compatible] == [
        "surface-code-d7-r7-p1e-3", "surface-code-d7-r7-p1e-3-unselected",
    ]
    assert all(getattr(circuit, k) == v for w in compatible
               for k, v in w["expected_metadata"].items())


def test_packed_padding_nonzero_reference_and_observable_beyond_first_byte(tmp_path):
    text = "X 0\nM 0\n" + "DETECTOR rec[-1]\n" * 9 + "OBSERVABLE_INCLUDE(9) rec[-1]\n"
    p = prepare(tmp_path, text, observable=9, postselect=False, chunk=7)
    counts = p.sample(19, 42)
    assert counts.accepted_shots == counts.logical_errors == 19
    p = prepare(tmp_path, text, observable=9, chunk=7)
    assert p.sample(19, 42).discarded_shots == 19


def test_noise_and_logical_counts_match_analytic_distribution(tmp_path):
    p = prepare(
        tmp_path,
        "X_ERROR(0.2) 0\nM 0\nDETECTOR rec[-1]\n"
        "X_ERROR(0.3) 1\nM 1\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        chunk=1024,
    )
    counts = p.sample(100000, 42)
    assert abs(counts.discarded_shots / 100000 - 0.2) < 0.01
    assert abs(counts.logical_errors / counts.accepted_shots - 0.3) < 0.01


def test_stream_is_reproducible_without_recompiling_per_call(tmp_path):
    text = "H 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    p = prepare(tmp_path, text, postselect=False, chunk="auto", seed=10)
    native = p._sampler
    first = [p.sample(1000, i) for i in (10, 11)]
    assert p._sampler is native
    replay = prepare(tmp_path, text, postselect=False, chunk="auto", seed=10)
    assert [replay.sample(1000, i) for i in (10, 11)] == first


def test_surface_aggregate_distribution_agrees_with_clifft_and_current_symft():
    import math

    from clifft_bench.adapters import load_adapter

    pytest.importorskip("clifft")
    pytest.importorskip("symft")
    workloads = read_toml(ROOT / "workloads.toml")["workloads"]
    workload = next(w for w in workloads if w["id"] == "surface-code-d7-r7-p1e-3")
    artifact = (ROOT / workload["artifact"]["path"]).resolve()
    shots = 65536
    observed = []
    for adapter in ("stim", "clifft", "symft"):
        prepared = load_adapter(adapter).prepare(
            artifact_path=artifact,
            workload=workload,
            execution={
                "batch_size": 1024,
                "options": {"sample_chunk_shots": 2048} if adapter == "symft" else {},
            },
        )
        observed.append(prepared.sample(shots, 42))
    reference = observed[0]
    for actual in observed[1:]:
        # Compare unconditional event probabilities. Accepted logical errors can
        # be rare; the additive term keeps the zero-count case well defined.
        for key in ("discarded_shots", "logical_errors"):
            p, q = getattr(reference, key) / shots, getattr(actual, key) / shots
            pooled = (p + q) / 2
            assert abs(p - q) < 6 * math.sqrt(2 * pooled * (1 - pooled) / shots) + 6 / shots
