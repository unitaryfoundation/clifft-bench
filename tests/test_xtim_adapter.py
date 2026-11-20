from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters import _shared
from clifft_bench.adapters import xtim as adapter
from clifft_bench.adapters.base import Counts, UnsupportedConfiguration
from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.config import load_suite, read_toml
from clifft_bench.selection import detector_mask

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


@pytest.mark.parametrize("measurement", ["X 0\nM 0", "M !0", "MPP !Z0"])
def test_nonzero_raw_reference_is_unsupported(tmp_path, measurement):
    with pytest.raises(UnsupportedConfiguration, match="zero raw reference"):
        prepare(tmp_path, measurement + "\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")


@pytest.mark.parametrize("threads", [1, 8])
def test_subset_counts_and_unselected_packed_reference(tmp_path, threads):
    pytest.importorskip("xtim")
    np = pytest.importorskip("numpy")
    artifact = tmp_path / "subset.stim"
    artifact.write_text("M !0\nX_ERROR(1) 1\nM 1\nDETECTOR rec[-1] rec[-1]\n"
                        "DETECTOR rec[-2]\nOBSERVABLE_INCLUDE(9) rec[-1]\n")
    workload = {"semantics": {"postselect_all_detectors": False,
                              "postselection_detector_indices": [0], "observable_index": 9}}
    options = {"threads": threads, "batch_size": "auto", "postselect": "on"}
    def prepared():
        return adapter.XtimAdapter().prepare(
            artifact_path=artifact, workload=workload, execution=options,
        )

    p = prepared()
    assert p.sample(257, 42) == Counts(257, 257, 0, 257)
    assert 1 <= p.first_call_metadata()["threads"] <= threads
    options["postselect"] = "off"
    with pytest.raises(UnsupportedConfiguration, match="zero raw reference"):
        prepared()
    artifact.write_text(artifact.read_text().replace("M !0", "M 0"))
    p = prepared()
    dets, obs = p.sample(257, 42)
    assert dets.shape == (257, 1) and dets.dtype == np.uint8 and not np.any(dets)
    assert obs.shape == (257, 2) and obs.dtype == np.uint8
    assert np.all(obs[:, 0] == 0) and np.all(obs[:, 1] == 2)
    assert 1 <= p.first_call_metadata()["threads"] <= threads


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

    def sample(shots, seed, *, postselect, threads):
        assert postselect.dtype == bool
        assert postselect.tolist() == [True]
        result = native_sample(shots, seed=seed, postselect=postselect, threads=threads)
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
            tmp_path, f"H 0\nM 0\n{channel}\nM 1\nOBSERVABLE_INCLUDE(1) rec[-1]\n",
            observable=1 if channel.startswith("DETECTOR") else 0
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
    # The noisy observable is an independent fair coin, with a zero reference.
    text = "X_ERROR(0.5) 0\nM 0 1\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-2]\n"
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
    text = ("X_ERROR(0.1) 0\nCX 0 1\nX_ERROR(0.25) 1\nM 0 1\n"
            "DETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-2]\n")
    shots = 131072
    results = []
    for chunk in [1, 32, 2048, shots]:
        p = prepare(tmp_path, text, chunk=chunk)
        counts = p.sample(shots, seed)
        for observed, probability in [
            (counts.accepted_shots, 0.9 * 0.75 + 0.1 * 0.25),
            (counts.logical_errors, 0.1 * 0.25),
        ]:
            assert abs(observed - shots * probability) <= (
                6 * math.sqrt(shots * probability * (1 - probability)) + 1
            )
        assert counts.accepted_shots + counts.discarded_shots == shots
        results.append(counts)
    assert all(counts == results[0] for counts in results)


@pytest.mark.parametrize("workload_id", [
    "msc-full-d5-cultivation-selected-p1e-3",
    "fold-transversal-d3-p1e-3", "fold-transversal-d5-p1e-3",
    "cain-15to1-d5-p1e-3", "cain-15to1-d9-p1e-3",
])
def test_extended_circuits_require_verified_raw_reference(workload_id):
    pytest.importorskip("xtim")
    workload = next(w for w in read_toml(ROOT / "workloads.toml")["workloads"]
                    if w["id"] == workload_id)
    for mode in workload["postselect_modes"]:
        with pytest.raises(adapter.UnsupportedReferenceCircuit, match="raw reference parity"):
            adapter.XtimAdapter().prepare(
                artifact_path=ROOT / workload["artifact"]["path"], workload=workload,
                execution={"postselect": mode, "batch_size": "auto"},
            )


QUALIFIED_WORKLOADS = [
    "msc-d3-inject-cultivate-p1e-3", "msc-d5-inject-cultivate-p1e-3",
    "distillation-15to1-rm15-p1e-3", "surface-code-d7-r7-p1e-3",
]
DECLARED = [w for w in read_toml(ROOT / "workloads.toml")["workloads"]
            if "raw_zero_reference" in w["semantics"]]


@pytest.mark.parametrize("workload", DECLARED, ids=lambda w: w["id"])
def test_declared_endpoint_has_deterministic_zero_raw_reference(workload):
    xtim = pytest.importorskip("xtim")
    clifft = pytest.importorskip("clifft")
    np = pytest.importorskip("numpy")
    artifact = (ROOT / workload["artifact"]["path"]).read_bytes()
    assert hashlib.sha256(artifact).hexdigest() == workload["artifact"]["sha256"]
    text = artifact.decode()
    ideal = xtim.Circuit(text).without_noise().text
    program = clifft.compile(ideal, postselection_mask=[], normalize_syndromes=False)
    assert clifft.parse(text).num_qubits == workload["expected_metadata"]["num_qubits"]
    raw = clifft.sample(program, 1024, seed=731, threads=1)
    assert raw.detectors.shape == (1024, workload["expected_metadata"]["num_detectors"])
    assert raw.observables.shape == (1024, workload["expected_metadata"]["num_observables"])
    semantics = workload["semantics"]
    for mode in semantics["raw_zero_reference"]:
        # Preparing also checks xtim's determinism classification for these channels.
        adapter.XtimAdapter().prepare(
            artifact_path=ROOT / workload["artifact"]["path"], workload=workload,
            execution={**EXECUTION, "postselect": mode, "batch_size": "auto"},
        )
        det, obs = raw.detectors, raw.observables
        if mode == "on":
            selected = detector_mask(semantics, det.shape[1])
            det = det[:, np.flatnonzero(selected)]
            obs = obs[:, semantics["observable_index"]]
        assert not np.any(det)
        assert not np.any(obs)


def test_extended_reference_requires_matching_bytes_and_endpoint(tmp_path):
    pytest.importorskip("xtim")
    text = "T 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    path = tmp_path / "extended.stim"
    path.write_text(text)
    workload = {"artifact": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "semantics": {**SEMANTICS, "raw_zero_reference": {"on": "clifft"}},
                "expected_metadata": {"num_qubits": 1}}
    def run(mode):
        return adapter.XtimAdapter().prepare(
            artifact_path=path, workload=workload,
            execution={**EXECUTION, "postselect": mode, "batch_size": "auto"},
        )
    run("on")
    with pytest.raises(adapter.UnsupportedReferenceCircuit):
        run("off")
    workload["semantics"]["raw_zero_reference"]["on"] = "stim"
    with pytest.raises(ValueError, match="Stim could not parse"):
        run("on")
    path.write_text(text.replace("M 0", "M !0"))
    with pytest.raises(ValueError, match="SHA-256"):
        run("on")


def test_surface_packed_distribution_and_thread_reproducibility():
    pytest.importorskip("xtim")
    np = pytest.importorskip("numpy")
    stim = pytest.importorskip("stim")
    workload = next(w for w in DECLARED if w["id"] == "surface-code-d7-r7-p1e-3")
    path = ROOT / workload["artifact"]["path"]
    circuit = stim.Circuit(path.read_text())
    shots = 65536
    def sample(threads):
        p = adapter.XtimAdapter().prepare(
            artifact_path=path, workload=workload,
            execution={"threads": threads, "batch_size": "auto", "postselect": "off"},
        )
        return p.sample(shots, seed=101)
    single, multi = sample(1), sample(8)
    reference = circuit.compile_detector_sampler(seed=202).sample(
        shots, separate_observables=True, bit_packed=True,
    )
    for one, eight, oracle, cols in zip(single, multi, reference,
                                        (circuit.num_detectors, circuit.num_observables),
                                        strict=True):
        np.testing.assert_array_equal(one, eight)
        rates = [np.unpackbits(x, axis=1, bitorder="little", count=cols).mean(axis=0)
                 for x in (one, oracle)]
        pooled = (rates[0] + rates[1]) / 2
        tolerance = 6 * np.sqrt(2 * pooled * (1 - pooled) / shots) + 2 / shots
        assert np.all(np.abs(rates[0] - rates[1]) <= tolerance)


@pytest.mark.parametrize("workload_id", QUALIFIED_WORKLOADS)
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
    shots, seed = 2049, 103
    packed = adapter.XtimAdapter().prepare(
        artifact_path=case.workload.artifact_path, workload=case.workload.definition,
        execution={**EXECUTION, "postselect": "off", "batch_size": "auto"},
    )
    dets, obs = packed.sample(shots, seed)
    keep = ~np.any(dets, axis=1)
    observable = case.workload.definition["semantics"]["observable_index"]
    logical = (obs[:, observable // 8] >> (observable % 8)) & 1
    passed = int(keep.sum())
    assert p.sample(shots, seed) == Counts(
        shots, passed, shots - passed, int(logical[keep].sum())
    )


@pytest.mark.parametrize("workload_id", QUALIFIED_WORKLOADS)
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
    for left, right in [(a.accepted_shots, b.accepted_shots), (a.logical_errors, b.logical_errors)]:
        pooled = (left + right) / (2 * a.attempted_shots)
        assert abs(left - right) <= 6 * math.sqrt(2 * a.attempted_shots * pooled * (1 - pooled)) + 2
