from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters import _shared
from clifft_bench.adapters import xtim as adapter
from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.manifest import load_suite

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = {"batch_enabled": True, "batch_size": 32, "sample_chunk_shots": 0}
SEMANTICS = {
    "observable_index": 0, "postselect_all_detectors": True,
    "reference_convention": "raw-record-parity",
}


def prepare(tmp_path, text, *, observable=0, postselect=True, chunk=32, initialize=True):
    pytest.importorskip("xtim")
    artifact = tmp_path / "circuit.stim"
    artifact.write_text(text)
    prepared = adapter.XtimAdapter().prepare(
        artifact_path=artifact,
        workload={"semantics": {
            **SEMANTICS, "observable_index": observable, "postselect_all_detectors": postselect,
        }},
        execution={**EXECUTION, "batch_size": chunk},
    )

    if initialize:
        prepared.begin_sample(7)
    return prepared


def test_packed_padding_nonzero_reference_and_selected_observable(tmp_path):
    text = "X 0\nM 0\n" + "DETECTOR rec[-1]\n" * 9 + "OBSERVABLE_INCLUDE(9) rec[-1]\n"
    p = prepare(tmp_path, text, observable=9, postselect=False, chunk=7)
    assert p.sample(19, 42).logical_errors == 19
    p = prepare(tmp_path, text, observable=9, chunk=7)
    assert p.sample(19, 42).discarded_shots == 19


def test_noise_counts_and_fresh_per_call_streams(tmp_path):
    p = prepare(tmp_path,
        "X_ERROR(0.2) 0\nM 0\nDETECTOR rec[-1]\n"
        "X_ERROR(0.3) 1\nM 1\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        chunk=1024,
    )
    a = p.sample(100000, 42)
    assert abs(a.discarded_shots / 100000 - 0.2) < 0.01
    assert abs(a.logical_errors / a.accepted_shots - 0.3) < 0.01
    assert p.sample(100000, 42) == a
    assert p.sample(100000, 43) != a


@pytest.mark.parametrize("channel", ["DETECTOR rec[-1]", "OBSERVABLE_INCLUDE(0) rec[-1]"])
def test_refuses_nondeterministic_channels(tmp_path, channel):
    with pytest.raises((ValueError, RuntimeError), match="deterministic|anti|refused"):
        prepare(tmp_path, f"H 0\nM 0\n{channel}\nM 1\nOBSERVABLE_INCLUDE(1) rec[-1]\n",
                observable=1)


def test_refuses_expectation_payload(tmp_path):
    with pytest.raises(ValueError, match="PAULI_EXPECTATION"):
        prepare(tmp_path, "M 0\nOBSERVABLE_INCLUDE(0) rec[-1]\nPAULI_EXPECTATION(0) Z0\n")


def test_cache_persists_within_repetition_and_compiles_before_timing(tmp_path, monkeypatch):
    compiled = []
    compile_sampler = adapter._compile

    def track_compile(text):
        sampler = compile_sampler(text)
        compiled.append(sampler)
        return sampler

    monkeypatch.setattr(adapter, "_compile", track_compile)
    p = prepare(tmp_path,
                "X_ERROR(0.3) 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
                postselect=False, chunk=65536, initialize=False)
    assert len(compiled) == 1  # prepare validates once; construction does not compile.
    assert p._sampler is None
    with pytest.raises(RuntimeError, match="initialized before timing"):
        p.sample(1, 7)
    p.begin_sample(7)
    assert len(compiled) == 2
    first = p.sample(1000001, 7)
    assert p.sample(1000001, 7) == first
    assert len(compiled) == 2
    assert p._sampler is compiled[1]
    p.begin_sample(9)
    assert len(compiled) == 3
    assert p._sampler is compiled[2]
    assert p.sample(1000001, 7) == first
    with pytest.raises(ValueError, match="shots per call"):
        p.sample(0, 7)


@pytest.mark.parametrize("mismatch", ["wheel", "commit", "repository", None])
def test_verifies_source_identity(monkeypatch, mismatch):
    commit = "c" * 40
    direct = {"url": "https://github.com/ikim-quantum/xtim.git",
              "vcs_info": {"vcs": "git", "commit_id": commit}}
    if mismatch == "commit":
        direct["vcs_info"]["commit_id"] = "d" * 40
    if mismatch == "repository":
        direct["url"] = "https://example.com/other"
    monkeypatch.setattr(_shared, "distribution", lambda _: SimpleNamespace(
        read_text=lambda _: None if mismatch == "wheel" else json.dumps(direct)))
    args = {"expected_commit": commit, "source_url": "https://github.com/ikim-quantum/xtim"}
    if mismatch:
        with pytest.raises(RuntimeError, match="source identity mismatch"):
            adapter.XtimAdapter().verify_installation(**args)
    else:
        result = adapter.XtimAdapter().verify_installation(**args)
        assert result["installed_source_commit"] == commit


@pytest.mark.parametrize("workload_id", [
    "msc-d3-inject-cultivate-p1e-3", "msc-d5-inject-cultivate-p1e-3",
    "surface-code-d7-r7-p1e-3",
])
def test_release_workload_metadata_and_acceptance_agree_with_clifft(workload_id):
    pytest.importorskip("xtim")
    pytest.importorskip("clifft")
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    case = next(c for c in suite.cases if c.workload.id == workload_id)
    args = {
        "artifact_path": case.workload.artifact_path,
        "workload": case.workload.definition,
        "execution": {**EXECUTION, "batch_size": 256},
    }
    p = adapter.XtimAdapter().prepare(**args)
    for key, value in case.workload.definition["expected_metadata"].items():
        assert p.runtime_metadata[key] == value
    p.begin_sample(103)
    a = p.sample(20000, 103)
    b = ClifftAdapter().prepare(**args).sample(20000, 104)
    assert abs(a.accepted_shots - b.accepted_shots) / 20000 < 0.025
    assert abs(a.logical_errors - b.logical_errors) / 20000 < 0.025
