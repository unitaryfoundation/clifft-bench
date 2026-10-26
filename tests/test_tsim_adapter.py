from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters import _shared
from clifft_bench.adapters.base import validate_counts
from clifft_bench.adapters.tsim import TsimAdapter
from clifft_bench.manifest import load_suite

ROOT = Path(__file__).resolve().parents[1]
SEMANTICS = {
    "observable_index": 0, "postselect_all_detectors": True,
    "reference_convention": "raw-record-parity",
}


@pytest.fixture(autouse=True)
def single_thread(monkeypatch):
    monkeypatch.setenv("NUMBA_NUM_THREADS", "1")


def prepare(tmp_path, text, *, observable=0, postselect=True, batch=256, sharing=True, seed=42):
    pytest.importorskip("tsim")
    artifact = tmp_path / "circuit.stim"
    artifact.write_text(text)
    return TsimAdapter().prepare(
        artifact_path=artifact,
        workload={"semantics": {
            **SEMANTICS, "observable_index": observable, "postselect_all_detectors": postselect,
        }},
        execution={"batch_size": batch, "sample_chunk_shots": 0,
                   "tsim_trajectory_sharing": sharing},
        seed=seed,
    )


def test_tsim_raw_nonzero_parity_and_observable_index_beyond_first_byte(tmp_path):
    text = "X 0\nM 0\n" + "DETECTOR rec[-1]\n" * 9 + "OBSERVABLE_INCLUDE(9) rec[-1]\n"
    p = prepare(tmp_path, text, observable=9, postselect=False, batch=7)
    c = p.sample(19, 7)
    assert c.accepted_shots == c.logical_errors == 19
    p = prepare(tmp_path, text, observable=9, batch=7)
    c = p.sample(19, 7)
    assert c.discarded_shots == 19
    assert c.accepted_shots == c.logical_errors == 0


@pytest.mark.parametrize("sharing", [False, True])
@pytest.mark.parametrize("postselect", [False, True])
def test_tsim_joint_quantum_noise_and_postselection_law(tmp_path, sharing, postselect):
    # P(m0=1)=sin(pi/8)^2; m1=m0 exactly. An independent classical detector
    # fires with p=.2. These test both component pruning and direct rejection.
    p = prepare(tmp_path,
        "RX 0\nT 0\nH 0\nCX 0 1\nM 0 1\nDETECTOR rec[-2]\n"
        "OBSERVABLE_INCLUDE(0) rec[-1]\n"
        "X_ERROR(0.2) 2\nM 2\nDETECTOR rec[-1]\n",
        sharing=sharing, postselect=postselect, batch=257,
    )
    shots = 30001  # Exercise a partial final batch.
    counts = p.sample(shots, 99)
    q = math.sin(math.pi / 8)**2
    assert validate_counts(counts, postselect=postselect) == []
    if postselect:
        assert counts.accepted_shots / shots == pytest.approx(.8 * (1-q), abs=.015)
        assert counts.logical_errors == 0  # Same quantum bit as the rejected detector.
    else:
        assert counts.accepted_shots == shots
        assert counts.logical_errors / shots == pytest.approx(q, abs=.015)
    assert p.runtime_metadata["trajectory_sharing"] is sharing
    assert p.runtime_metadata["precision"] == "complex-fp64"
    assert p.runtime_metadata["batch_size_applicable"] is True


def test_tsim_continuous_seeded_stream_does_not_recompile_or_replay(tmp_path, monkeypatch):
    text = "RX 0\nT 0\nH 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    a = prepare(tmp_path, text, postselect=False, seed=123)
    b = prepare(tmp_path, text, postselect=False, seed=123)
    import tsim

    def unexpected_compile(*args, **kwargs):
        raise AssertionError("sampler recompiled after preparation")

    monkeypatch.setattr(tsim.Circuit, "compile_detector_sampler", unexpected_compile)
    a.begin_sample(100)
    first = a.sample(30001, 100)
    a.begin_sample(200)
    second = a.sample(30001, 200)
    # Audit identifiers do not reset the native stream. A fresh prepared
    # sampler with the same initial seed reproduces the entire sequence.
    assert first == b.sample(30001, 999)
    assert second == b.sample(30001, 999)
    assert first != second
    assert a.runtime_metadata["initial_seed"] == 123


def test_tsim_requires_explicit_and_effective_single_thread(tmp_path, monkeypatch):
    monkeypatch.delenv("NUMBA_NUM_THREADS")
    with pytest.raises(ValueError, match="NUMBA_NUM_THREADS"):
        TsimAdapter().prepare(artifact_path=tmp_path, workload={}, execution={})
    monkeypatch.setenv("NUMBA_NUM_THREADS", "1")
    numba = pytest.importorskip("numba")
    pytest.importorskip("tsim")
    monkeypatch.setattr(numba, "get_num_threads", lambda: 2)
    with pytest.raises(RuntimeError, match="effective Numba thread"):
        TsimAdapter().prepare(artifact_path=tmp_path, workload={}, execution={})


@pytest.mark.parametrize("mismatch", ["wheel", "commit", "repository", None])
def test_tsim_verifies_exact_source_identity(monkeypatch, mismatch):
    commit = "c" * 40
    direct = {"url": "https://github.com/QuEraComputing/tsim.git",
              "vcs_info": {"vcs": "git", "commit_id": commit}}
    if mismatch == "commit":
        direct["vcs_info"]["commit_id"] = "d" * 40
    if mismatch == "repository":
        direct["url"] = "https://example.com/other"
    monkeypatch.setattr(_shared, "distribution", lambda _: SimpleNamespace(
        read_text=lambda _: None if mismatch == "wheel" else json.dumps(direct)))
    args = {"expected_commit": commit, "source_url": "https://github.com/QuEraComputing/tsim"}
    if mismatch:
        with pytest.raises(RuntimeError, match="source identity mismatch"):
            TsimAdapter().verify_installation(**args)
    else:
        assert TsimAdapter().verify_installation(**args)["installed_source_commit"] == commit


@pytest.mark.parametrize("workload_id", [
    "msc-d3-inject-cultivate-p1e-3", "msc-d5-inject-cultivate-p1e-3",
    "surface-code-d7-r7-p1e-3", "distillation-color-code-85q-p5e-2",
    "coherent-surface-d3-r3-p1e-3-rz2e-2", "coherent-surface-d5-r5-p1e-3-rz2e-2",
    "draper-adder-m16-basis", "distillation-15to1-rm15-p1e-3",
])
def test_tsim_release_circuit_metadata_and_counts(workload_id):
    pytest.importorskip("tsim")
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    case = next(c for c in suite.cases if c.workload.id == workload_id)
    p = TsimAdapter().prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution={"batch_size": 16, "sample_chunk_shots": 0}, seed=42,
    )
    assert all(p.runtime_metadata[k] == v
               for k, v in case.workload.definition["expected_metadata"].items())
    counts = p.sample(17, 42)
    assert not validate_counts(
        counts, postselect=case.workload.definition["semantics"]["postselect_all_detectors"]
    )
    if workload_id in {"surface-code-d7-r7-p1e-3", "draper-adder-m16-basis"}:
        assert p.runtime_metadata["batch_size_applicable"] is False
    if workload_id == "draper-adder-m16-basis":
        assert counts.accepted_shots == 17 and counts.logical_errors == 0
