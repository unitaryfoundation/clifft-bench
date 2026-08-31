from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from clifft_bench.manifest import load_suite
from clifft_bench.runner import SEED_REPETITION_STRIDE, _effective_batch_size, run_suite
from clifft_bench.schema import validate_document


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _write_fixture_suite(
    tmp_path: Path,
    *,
    parameters: dict[str, Any] | None = None,
    request_timeout_seconds: float = 5,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    artifact = tmp_path / "tiny.stim"
    artifact.write_text("M 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    source_commit = "0" * 40
    workloads = {
        "schema_version": "clifft-bench/workloads/v1",
        "suite_version": "0.1.0",
        "workloads": [
            {
                "id": "tiny-fixture",
                "family": "test",
                "description": "Deterministic harness fixture.",
                "artifact": {"path": "tiny.stim", "sha256": digest},
                "dialect": "stim",
                "angle_convention": "none",
                "parameters": parameters or {},
                "semantics": {
                    "logical_work": "One deterministic fixture update.",
                    "output_contract": "aggregate detector-discard and logical-observable counts",
                    "observable_index": 0,
                    "postselect_all_detectors": False,
                    "reference_convention": "raw-record-parity",
                    "throughput_numerator": "attempted shots",
                    "batch_semantics": "Fixture batch semantics.",
                },
                "expected_metadata": {
                    "num_qubits": 1,
                    "num_measurements": 1,
                    "num_detectors": 0,
                    "num_observables": 1,
                },
                "compatible_adapters": ["fixture"],
                "provenance": {
                    "source_url": "https://example.com/fixture",
                    "source_commit": source_commit,
                    "source_path": "tiny.stim",
                    "license": "CC0-1.0",
                },
            }
        ],
    }
    software = {
        "schema_version": "clifft-bench/software/v1",
        "suite_version": "0.1.0",
        "implementations": [
            {
                "id": identifier,
                "name": "fixture",
                "adapter": "fixture",
                "distribution": "clifft-bench",
                "version": "1.0.0",
                "commit_sha": source_commit,
                "commit_datetime": "2026-01-01T00:00:00Z",
                "release_datetime": None,
                "source_url": "https://example.com/fixture",
                "dependency_distributions": ["clifft-bench"],
                "build": {"precision": "integer", "compiler_flags": [], "features": []},
            }
            for identifier in ["fixture-a", "fixture-b"]
        ],
    }
    cases = [
        {
            "id": identifier,
            "pair_id": "fixture-pair",
            "workload_id": "tiny-fixture",
            "implementation_id": identifier,
            "shots_per_call": 8,
            "execution": {
                "mode": "throughput",
                "batch_enabled": False,
                "batch_size": 1,
                "sample_chunk_shots": 0,
            },
        }
        for identifier in ["fixture-a", "fixture-b"]
    ]
    run = {
        "schema_version": "clifft-bench/run/v1",
        "suite_version": "0.1.0",
        "profile_id": "fixture",
        "run_id": "fixture",
        "classification": "smoke",
        "seed": 10,
        "workloads_manifest": "workloads.json",
        "software_manifest": "software.json",
        "resources": {"logical_cpu": None, "concurrent_cases": 1, "threads_per_case": 1},
        "measurement": {
            "setup_timeout_seconds": 5,
            "request_timeout_seconds": request_timeout_seconds,
            "warmup_shots": 1,
            "correctness_shots": 8,
            "min_sample_seconds": 0.0001,
            "repetitions": 2,
        },
        "cases": cases,
    }
    _write(tmp_path / "workloads.json", workloads)
    _write(tmp_path / "software.json", software)
    run_path = tmp_path / "run.json"
    _write(run_path, run)
    return run_path, run, software


def test_effective_batch_size_is_capped_by_shots_per_call() -> None:
    metadata = {"effective_batch_size": 2048}
    assert _effective_batch_size(metadata, 8) == 8
    assert _effective_batch_size(metadata, 4096) == 2048


def test_isolated_workers_emit_valid_interleaved_raw_results(tmp_path: Path) -> None:
    run_path, run, software = _write_fixture_suite(
        tmp_path, parameters={"write_native_stdout": True}
    )
    output = tmp_path / "result.json"
    result = run_suite(load_suite(run_path), output_path=output, memory_limit_gib=1)
    validate_document(result)
    assert [case["status"] for case in result["cases"]] == ["success", "success"]
    sequences = [
        [sample["sequence_index"] for sample in case["samples"]] for case in result["cases"]
    ]
    assert sequences == [[0, 3], [1, 2]]
    assert all(case["correctness"]["status"] == "passed" for case in result["cases"])
    for case in result["cases"]:
        assert case["execution"]["memory_limit_bytes"] == 1 << 30
        assert case["setup"]["runtime_metadata"]["address_space_limit_bytes"] in {
            None,
            1 << 30,
        }
        samples = sorted(case["samples"], key=lambda sample: sample["repetition"])
        assert samples[1]["seed_first"] - samples[0]["seed_first"] == (
            SEED_REPETITION_STRIDE
        )

    bad_software = json.loads(json.dumps(software))
    for implementation in bad_software["implementations"]:
        implementation["version"] = "2.0.0"
    bad_run = dict(run)
    bad_run["software_manifest"] = "software-bad.json"
    _write(tmp_path / "software-bad.json", bad_software)
    _write(tmp_path / "run-bad.json", bad_run)
    failed = run_suite(load_suite(tmp_path / "run-bad.json"), output_path=tmp_path / "failed.json")
    validate_document(failed)
    assert [case["status"] for case in failed["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "setup" for case in failed["cases"])


def test_worker_timeout_is_recorded_and_run_completes(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(
        tmp_path,
        parameters={"sleep_on_seed": 10010, "sleep_seconds": 2},
        request_timeout_seconds=0.05,
    )
    result = run_suite(load_suite(run_path), output_path=tmp_path / "timeout.json")
    validate_document(result)
    assert [case["status"] for case in result["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "sampling" for case in result["cases"])
    assert all(case["error"]["type"] == "WorkerTimeout" for case in result["cases"])


def test_warmup_failure_records_warmup_phase(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(
        tmp_path,
        parameters={"sleep_on_seed": 9, "sleep_seconds": 2},
        request_timeout_seconds=0.05,
    )
    result = run_suite(load_suite(run_path), output_path=tmp_path / "warmup-timeout.json")
    validate_document(result)
    assert [case["status"] for case in result["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "warmup" for case in result["cases"])


def test_sample_interval_must_fit_inside_request_timeout(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(tmp_path, request_timeout_seconds=5)
    with pytest.raises(ValueError, match="must be greater than min_sample_seconds"):
        run_suite(
            load_suite(run_path),
            output_path=tmp_path / "invalid-timeout.json",
            min_sample_seconds=5,
        )


def test_repetition_seed_ranges_must_fit_unsigned_32_bit_space(tmp_path: Path) -> None:
    run_path, run, _ = _write_fixture_suite(tmp_path)
    run["measurement"]["repetitions"] = 50
    _write(run_path, run)

    with pytest.raises(ValueError, match="unsigned 32-bit benchmark seed space"):
        run_suite(load_suite(run_path), output_path=tmp_path / "invalid-seeds.json")
