from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters import _shared
from clifft_bench.adapters import xtim as adapter
from clifft_bench.adapters.base import Counts, UnsupportedConfiguration
from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.config import load_suite

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = {"postselect": "on", "batch_size": 32, "options": {}}
SEMANTICS = {
    "observable_index": 0,
    "postselect_all_detectors": True,
}


def prepare(tmp_path, text, *, observable=0, chunk=32):
    pytest.importorskip("xtim")
    artifact = tmp_path / "circuit.stim"
    artifact.write_text(text)
    prepared = adapter.XtimAdapter().prepare(
        artifact_path=artifact,
        workload={
            "semantics": {
                **SEMANTICS,
                "observable_index": observable,
            }
        },
        execution={**EXECUTION, "batch_size": chunk},
    )

    return prepared


@pytest.mark.parametrize("measurement", ["X 0\nM 0", "M !0"])
def test_nonzero_raw_reference_is_unsupported(tmp_path, measurement):
    with pytest.raises(UnsupportedConfiguration, match="zero raw reference"):
        prepare(tmp_path, measurement + "\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")


def test_pinned_xtim_packed_endpoint_is_unsupported():
    with pytest.raises(UnsupportedConfiguration, match="postselect=off"):
        adapter.XtimAdapter().validate_execution({**EXECUTION, "postselect": "off"}, {})


@pytest.mark.parametrize("probability", [0, 0.25, 1])
@pytest.mark.parametrize("chunk", [1, 7, 64])
def test_postselection_matches_unmasked_raw_parity_filter(
    tmp_path, probability, chunk
):
    np = pytest.importorskip("numpy")
    text = (
        f"X_ERROR({probability}) 0\nM 0\n"
        + "DETECTOR rec[-1]\n" * 9
        + "X_ERROR(1) 1\nM 1\nOBSERVABLE_INCLUDE(9) rec[-1]\n"
    )
    p = prepare(tmp_path, text, observable=9, chunk=chunk)
    assert p.runtime_metadata["postselection_backend"] == "native-twirl-sampler"
    # Independent unmasked sample and unpacked raw-parity reduction. Includes
    # empty/all accepted batches, a partial tail, padding and a nonzero O9 offset.
    dets, obs = adapter._compile(text).sample(67, seed=74)
    raw_dets = np.unpackbits(dets, axis=1, bitorder="little")[:, :9]
    raw_obs = np.unpackbits(obs, axis=1, bitorder="little")[:, 9]
    keep = ~raw_dets.any(axis=1)
    passed = int(keep.sum())
    expected = Counts(67, passed, 67 - passed, int(raw_obs[keep].sum()))
    assert p.sample(67, 74) == expected
    assert p.sample(67, 74) == expected


def test_native_postselection_counts_attempts_and_early_rejects(tmp_path):
    p = prepare(
        tmp_path,
        "X_ERROR(0.5) 0\nM 0\nDETECTOR rec[-1]\n"
        "X_ERROR(1) 1\nM 1\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        chunk=32,
    )
    native_sample = p._sampler.sample
    calls = []

    def sample(shots, seed, *, postselect):
        assert postselect.dtype == bool
        assert postselect.tolist() == [True]
        result = native_sample(shots, seed=seed, postselect=postselect)
        calls.append((shots, seed, result[2]))
        return result

    p._sampler = SimpleNamespace(sample=sample)
    counts = p.sample(103, 42)
    assert [(n, seed) for n, seed, _ in calls] == [
        (32, 42), (32, None), (32, None), (7, None)
    ]
    assert counts.attempted_shots == sum(c["attempts"] for _, _, c in calls) == 103
    assert counts.accepted_shots == sum(c["passed"] for _, _, c in calls)
    assert counts.discarded_shots == sum(c["discarded"] for _, _, c in calls)
    assert sum(c["early"] for _, _, c in calls) > 0
    assert counts.logical_errors == counts.accepted_shots


def test_native_postselection_with_no_detectors_keeps_every_shot(tmp_path):
    p = prepare(tmp_path, "X_ERROR(1) 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n", chunk=7)
    assert p.runtime_metadata["postselection_backend"] == "native-twirl-sampler"
    assert p.sample(19, 42) == Counts(19, 19, 0, 19)


@pytest.mark.parametrize("counter", ["attempts", "passed", "discarded", "early"])
def test_native_postselection_rejects_inconsistent_counters(tmp_path, counter):
    p = prepare(tmp_path, "M 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    native_sample = p._sampler.sample

    def sample(*args, **kwargs):
        dets, obs, counts = native_sample(*args, **kwargs)
        counts[counter] += 1
        return dets, obs, counts

    p._sampler = SimpleNamespace(sample=sample)
    with pytest.raises(RuntimeError, match="inconsistent postselection counts"):
        p.sample(19, 42)


def test_noise_counts_and_fresh_per_call_streams(tmp_path):
    p = prepare(
        tmp_path,
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
        prepare(
            tmp_path, f"H 0\nM 0\n{channel}\nM 1\nOBSERVABLE_INCLUDE(1) rec[-1]\n", observable=1
        )


def test_refuses_expectation_payload(tmp_path):
    with pytest.raises(ValueError, match="PAULI_EXPECTATION"):
        prepare(tmp_path, "M 0\nOBSERVABLE_INCLUDE(0) rec[-1]\nPAULI_EXPECTATION(0) Z0\n")


def test_sampler_compiles_once_and_reuses_its_cache(tmp_path, monkeypatch):
    compiled = []
    compile_sampler = adapter._compile

    def track_compile(text):
        sampler = compile_sampler(text)
        compiled.append(sampler)
        return sampler

    monkeypatch.setattr(adapter, "_compile", track_compile)
    p = prepare(
        tmp_path,
        "X_ERROR(0.3) 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        chunk=65536,
    )
    assert len(compiled) == 1
    assert p._sampler is compiled[0]
    first = p.sample(1000001, 7)
    assert p.sample(1000001, 7) == first
    p.sample(1000001, 9)
    assert len(compiled) == 1
    assert p._sampler is compiled[0]
    with pytest.raises(ValueError, match="shots per call"):
        p.sample(0, 7)


@pytest.mark.parametrize("mismatch", ["wheel", "commit", "repository", None])
def test_verifies_source_identity(monkeypatch, mismatch):
    commit = "c" * 40
    direct = {
        "url": "https://github.com/ikim-quantum/xtim.git",
        "vcs_info": {"vcs": "git", "commit_id": commit},
    }
    if mismatch == "commit":
        direct["vcs_info"]["commit_id"] = "d" * 40
    if mismatch == "repository":
        direct["url"] = "https://example.com/other"
    monkeypatch.setattr(
        _shared,
        "distribution",
        lambda _: SimpleNamespace(
            read_text=lambda _: None if mismatch == "wheel" else json.dumps(direct)
        ),
    )
    args = {"expected_commit": commit, "source_url": "https://github.com/ikim-quantum/xtim"}
    if mismatch:
        with pytest.raises(RuntimeError, match="source identity mismatch"):
            adapter.XtimAdapter().verify_installation(**args)
    else:
        result = adapter.XtimAdapter().verify_installation(**args)
        assert result["installed_source_commit"] == commit


@pytest.mark.parametrize("seed", [1, 42, 74])
def test_continuation_keeps_independent_coins_across_chunks(tmp_path, seed):
    # The certain q1 fault makes O a fair coin. Select an independent zero detector
    # so all coins survive; replaying a per-shot counter would produce 0 or 4096 ones.
    text = (
        "H 0\nX_ERROR(1) 1\nT 0\nCX 1 0\nT_DAG 0\nCX 1 0\n"
        "M 2\nDETECTOR rec[-1]\nH 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    )
    results = []
    for chunk in [1, 32, 4096]:
        p = prepare(tmp_path, text, chunk=chunk)
        counts = p.sample(4096, seed)
        assert counts.attempted_shots == counts.accepted_shots == 4096
        assert counts.discarded_shots == 0
        assert abs(counts.logical_errors - 2048) <= 6 * math.sqrt(1024)
        results.append(counts)
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("seed", [1, 42, 74])
def test_noisy_joint_law_is_preserved_across_chunks(tmp_path, seed):
    text = (
        "H 0\nDEPOLARIZE1(0.05) 0 1\nT 0\nCX 1 0\nT_DAG 0\nCX 1 0\n"
        "M 1\nDETECTOR rec[-1]\nH 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    )
    shots = 131072
    results = []
    for chunk in [1, 32, 2048, shots]:
        p = prepare(tmp_path, text, chunk=chunk)
        counts = p.sample(shots, seed)
        for observed, probability in [
            (counts.accepted_shots, 29 / 30),
            (counts.logical_errors, 29 / 900),
        ]:
            assert abs(observed - shots * probability) <= (
                6 * math.sqrt(shots * probability * (1 - probability)) + 1
            )
        assert counts.accepted_shots + counts.discarded_shots == shots
        results.append(counts)
    assert all(counts == results[0] for counts in results)


@pytest.mark.parametrize(
    "workload_id",
    [
        "msc-d3-inject-cultivate-p1e-3",
        "msc-d5-inject-cultivate-p1e-3",
        "surface-code-d7-r7-p1e-3",
        "distillation-15to1-rm15-p1e-3",
    ],
)
def test_release_native_postselection_matches_unmasked_stream(workload_id):
    pytest.importorskip("xtim")
    np = pytest.importorskip("numpy")
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    case = next(c for c in suite.cases if c.workload.id == workload_id)
    p = adapter.XtimAdapter().prepare(
        artifact_path=case.workload.artifact_path,
        workload=case.workload.definition,
        execution={**EXECUTION, "postselect": "on", "batch_size": 256},
    )
    assert p.runtime_metadata["postselection_backend"] == "native-twirl-sampler"
    assert p.runtime_metadata["record_materialization"] == (
        "bit-packed-accepted-detectors-and-observables"
    )
    assert p.runtime_metadata["raw_reference_detector_ones"] == 0
    assert p.runtime_metadata["raw_reference_observable_ones"] == 0
    shots, seed = 2049, 103
    dets, obs = adapter._compile(case.workload.artifact_path.read_text()).sample(shots, seed)
    keep = ~np.any(dets, axis=1)
    observable = case.workload.definition["semantics"]["observable_index"]
    logical = (obs[:, observable // 8] >> (observable % 8)) & 1
    passed = int(keep.sum())
    assert p.sample(shots, seed) == Counts(
        shots, passed, shots - passed, int(logical[keep].sum())
    )


@pytest.mark.parametrize(
    "workload_id",
    [
        "msc-d3-inject-cultivate-p1e-3",
        "msc-d5-inject-cultivate-p1e-3",
        "surface-code-d7-r7-p1e-3",
    ],
)
def test_release_workload_metadata_and_acceptance_agree_with_clifft(workload_id):
    pytest.importorskip("xtim")
    pytest.importorskip("clifft")
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    case = next(c for c in suite.cases if c.workload.id == workload_id)
    args = {
        "artifact_path": case.workload.artifact_path,
        "workload": case.workload.definition,
        "execution": {**EXECUTION, "postselect": "on", "batch_size": 256},
    }
    p = adapter.XtimAdapter().prepare(**args)
    for key, value in case.workload.definition["expected_metadata"].items():
        assert p.runtime_metadata[key] == value
    a = p.sample(20000, 103)
    b = ClifftAdapter().prepare(**args).sample(20000, 104)
    assert abs(a.accepted_shots - b.accepted_shots) / 20000 < 0.025
    assert abs(a.logical_errors - b.logical_errors) / 20000 < 0.025
