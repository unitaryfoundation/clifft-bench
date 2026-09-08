from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from tsim_gpu import run, worker
from tsim_gpu.common import WORKLOADS, require_gpu, workloads, write_json

SEMANTICS = {"postselect_all_detectors": True, "observable_index": 0}


def test_counts_exclude_placeholders_and_component_detector_failures():
    def sample(shots, **kwargs):
        assert shots == 4
        assert kwargs["postselection_mask"].tolist() == [True, True]
        assert not kwargs["use_detector_reference_sample"]
        assert not kwargs["use_observable_reference_sample"]
        return np.array([[1, 0], [0, 1], [0, 0], [0, 0]], dtype=bool), np.array(
            [[0], [1], [1], [0]], dtype=bool
        )

    counts = worker.sample_counts(SimpleNamespace(sample=sample), 4, 2, SEMANTICS, 2)
    assert counts == {
        "attempted_shots": 4,
        "accepted_shots": 2,
        "discarded_shots": 2,
        "logical_errors": 1,
    }


@pytest.mark.parametrize("postselect", [True, False])
def test_real_tsim_raw_nonzero_reference(postselect):
    import tsim

    circuit = tsim.Circuit("X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    sampler = circuit.compile_detector_sampler(strategy="cat5", seed=4)
    counts = worker.sample_counts(
        sampler, 17, 8, {**SEMANTICS, "postselect_all_detectors": postselect}, 1
    )
    assert counts["discarded_shots"] == (17 if postselect else 0)
    assert counts["logical_errors"] == (0 if postselect else 17)


def test_real_tsim_near_clifford_analytic_distribution_with_postselection():
    import tsim

    circuit = tsim.Circuit(
        "X_ERROR(0.25) 0\nH 1\nT 1\nH 1\nM 0 1\nDETECTOR rec[-2]\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    )
    sampler = circuit.compile_detector_sampler(strategy="cutting", seed=42)
    counts = worker.sample_counts(sampler, 8192, 1024, SEMANTICS, 1)
    assert counts["discarded_shots"] / 8192 == pytest.approx(0.25, abs=0.025)
    assert counts["logical_errors"] / counts["accepted_shots"] == pytest.approx(
        np.sin(np.pi / 8) ** 2, abs=0.025
    )


def test_gpu_guard_rejects_cpu_and_multiple_devices():
    with pytest.raises(RuntimeError, match="CPU fallback"):
        require_gpu(SimpleNamespace(devices=lambda: [SimpleNamespace(platform="cpu")]))
    with pytest.raises(RuntimeError, match="one GPU"):
        require_gpu(SimpleNamespace(devices=lambda: [SimpleNamespace(platform="gpu")] * 2))


def test_corpus_identity_and_historic_failures_are_retained():
    corpus = workloads()
    assert set(corpus) == set(WORKLOADS)
    assert "msc-d5-inject-cultivate-p1e-3" in corpus
    assert "coherent-surface-d5-r5-p1e-3-rz2e-2" in corpus
    assert all(
        w["semantics"]["reference_convention"] == "raw-record-parity" for w in corpus.values()
    )


def test_strategy_selection_uses_probes_not_final_samples():
    candidates = [
        {"status": "success", "selection_probe_rate": 2, "summary": {"rate": 1000}},
        {"status": "success", "selection_probe_rate": 3, "summary": {"rate": 1}},
        {"status": "timeout", "selection_probe_rate": 100},
    ]
    assert run.choose_strategy(candidates) is candidates[1]
    assert run.choose_strategy([candidates[2]]) is None


def test_hard_timeout_preserves_phase_and_completed_probe(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    script = tmp_path / "hang.py"
    script.write_text(
        "import json, time\nfrom pathlib import Path\n"
        f"Path({str(checkpoint)!r}).write_text(json.dumps({{'status': 'running', "
        "'phase': 'compile', 'phase_started': time.monotonic(), 'tuning': [42]}))\n"
        "time.sleep(20)\n"
    )
    options = {**run.DEFAULTS, "compile_timeout_seconds": 0.1}
    result = run.supervise(
        [sys.executable, str(script)],
        tmp_path,
        {"options": options, "remaining_prepare_seconds": 1, "workload": {}, "strategy": "cutting"},
        os.environ.copy(),
    )
    assert result["status"] == "timeout"
    assert result["error"]["phase"] == "compile"
    assert result["tuning"] == [42]
    assert result["wall_seconds"] < 5
    assert result["exit_code"] < 0


def test_resume_reuses_failures_and_retry_appends_without_overwrite(tmp_path, monkeypatch):
    options = deepcopy(run.DEFAULTS)
    calls = []

    def preflight(*args, **kwargs):
        write_json(tmp_path / "preflight.json", {"gpu": "test"})

    def supervise(command, directory, request, environment):
        calls.append(request["strategy"])
        return {
            "status": "timeout",
            "strategy": request["strategy"],
            "preparation_seconds": 1,
            "workload": request["workload"],
        }

    monkeypatch.setattr(run.subprocess, "run", preflight)
    monkeypatch.setattr(run, "supervise", supervise)

    def collect(retry=False):
        run.collect(
            tmp_path,
            options,
            [WORKLOADS[0]],
            allow_dirty=True,
            retry_failures=retry,
            host_label="test",
        )

    collect()
    before = {p: p.read_bytes() for p in tmp_path.glob("raw/**/result.json")}
    assert len(calls) == 3
    collect()
    assert len(calls) == 3
    collect(retry=True)
    assert len(calls) == 6
    assert len(list(tmp_path.glob("raw/**/result.json"))) == 6
    assert all(p.read_bytes() == value for p, value in before.items())
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["workloads"][0]["status"] == "no-success"
    options["shots_per_call"] += 1
    with pytest.raises(ValueError, match="fingerprint changed"):
        collect()


@pytest.mark.parametrize("recovery", [False, True])
def test_real_worker_compiles_once_and_separates_final_samples(tmp_path, monkeypatch, recovery):
    import jax
    import tsim

    # Exercise the worker with CPU JAX in tests only. Production run has no such flag.
    monkeypatch.setattr(worker, "require_gpu", lambda _: jax.devices())
    original = tsim.Circuit.compile_detector_sampler
    calls = []

    def compile_once(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(tsim.Circuit, "compile_detector_sampler", compile_once)
    circuit = tmp_path / "circuit.stim"
    circuit.write_text("H 0\nT 0\nH 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    result = worker.run(
        {
            "workload": {
                "artifact_path": str(circuit),
                "expected_metadata": {
                    "num_qubits": 1,
                    "num_measurements": 1,
                    "num_detectors": 0,
                    "num_observables": 1,
                },
                "semantics": {**SEMANTICS, "postselect_all_detectors": False},
            },
            "options": {
                **run.DEFAULTS,
                "shots_per_call": 32,
                "batch_sizes": [8, 32],
                "probe_seconds": 0.001,
                "probe_repetitions": 2,
                "sample_seconds": 0.001,
                "repetitions": 2,
            },
            "strategy": "cat5",
            "seed": 7,
            **({"recovery_batch_sizes": [8]} if recovery else {}),
        },
        tmp_path / "checkpoint.json",
    )
    assert result["status"] == "success", result.get("error")
    assert calls == [1]
    assert len(result["samples"]) == 2
    assert len(result["tuning"]) == (1 if recovery else 2)
    if recovery:
        assert result["batch_candidates"] == [8]
    assert all(c["warmup_seconds"] > 0 for c in result["tuning"])
    assert result["runtime_metadata"]["compiled_phase_dtypes"]


def test_worker_environment_restores_jax_defaults(monkeypatch):
    from tsim_gpu.common import JAX_DEFAULT_ENVIRONMENT, worker_environment

    for key in JAX_DEFAULT_ENVIRONMENT:
        monkeypatch.setenv(key, "overridden")
    env = worker_environment()
    assert all(key not in env for key in JAX_DEFAULT_ENVIRONMENT)
    assert env["JAX_PLATFORMS"] == "cuda"


def test_tuning_timeout_recovers_completed_batches_and_charges_both_attempts(tmp_path, monkeypatch):
    calls = []
    workload = workloads()[WORKLOADS[1]]
    request = {
        "workload": workload,
        "strategy": "cutting",
        "options": run.DEFAULTS,
        "remaining_prepare_seconds": 60,
        "seed": 42,
    }

    def supervise(command, directory, request, environment):
        calls.append(request)
        if len(calls) == 1:
            return {
                "status": "timeout",
                "strategy": "cutting",
                "workload": workload,
                "error": {"phase": "tune"},
                "preparation_seconds": 25,
                "tuning": [
                    {"batch_size": 1024, "status": "success"},
                    {"batch_size": 8192, "status": "success"},
                    {"batch_size": 65536, "status": "running"},
                ],
            }
        assert request["remaining_prepare_seconds"] == 35
        assert request["recovery_batch_sizes"] == [1024, 8192]
        return {
            "status": "success",
            "strategy": "cutting",
            "workload": workload,
            "preparation_seconds": 15,
        }

    monkeypatch.setattr(run, "supervise", supervise)
    result = run.collect_strategy(tmp_path, request, {}, "fingerprint", retry_failures=False)
    assert result["status"] == "success"
    assert result["strategy_preparation_seconds"] == 40
    assert result["recovery_of"] == "attempt-001"
    assert json.loads((tmp_path / "attempt-001/result.json").read_text())["status"] == "timeout"
    assert (
        run.collect_strategy(tmp_path, request, {}, "fingerprint", retry_failures=False) == result
    )
    assert len(calls) == 2  # Resuming does not repeat either compilation.


@pytest.mark.parametrize(
    "phase,budget,completed,recovery",
    [
        ("compile", 60, True, False),
        ("tune", 25, True, False),
        ("tune", 60, False, False),
        ("tune", 60, True, True),
    ],
)
def test_timeout_recovery_is_bounded(tmp_path, monkeypatch, phase, budget, completed, recovery):
    calls = []

    def supervise(command, directory, request, environment):
        calls.append(request)
        result = {
            "status": "timeout",
            "strategy": "cutting",
            "workload": request["workload"],
            "error": {"phase": phase},
            "preparation_seconds": 25,
            "tuning": [{"batch_size": 1024, "status": "success"}] if completed else [],
        }
        if recovery:
            result["recovery_of"] = "attempt-000"
        return result

    monkeypatch.setattr(run, "supervise", supervise)
    run.collect_strategy(
        tmp_path,
        {
            "workload": workloads()[WORKLOADS[1]],
            "strategy": "cutting",
            "options": run.DEFAULTS,
            "remaining_prepare_seconds": budget,
            "seed": 42,
        },
        {},
        "test",
        retry_failures=False,
    )
    assert len(calls) == 1
