from __future__ import annotations

import hashlib
import json
from pathlib import Path

from clifft_bench.manifest import load_suite
from clifft_bench.runner import run_suite
from clifft_bench.schema import validate_document


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def test_isolated_workers_emit_valid_interleaved_raw_results(tmp_path: Path) -> None:
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
                "parameters": {},
                "semantics": {
                    "logical_work": "One deterministic fixture update.",
                    "output_contract": "aggregate detector-discard and logical-observable counts",
                    "postselect_all_detectors": False,
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
                "source_url": "https://example.com/fixture",
                "dependency_distributions": ["clifft-bench"],
                "build": {"precision": "integer", "compiler_flags": [], "features": []},
            }
            for identifier in ["fixture-a", "fixture-b"]
        ],
    }
    cases = []
    for identifier in ["fixture-a", "fixture-b"]:
        cases.append(
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
        )
    run = {
        "schema_version": "clifft-bench/run/v1",
        "suite_version": "0.1.0",
        "profile_id": "fixture",
        "classification": "smoke",
        "seed": 10,
        "workloads_manifest": "workloads.json",
        "software_manifest": "software.json",
        "resources": {"logical_cpu": None, "concurrent_cases": 1, "threads_per_case": 1},
        "measurement": {
            "setup_timeout_seconds": 5,
            "warmup_shots": 1,
            "correctness_shots": 8,
            "min_sample_seconds": 0.0001,
            "repetitions": 2,
        },
        "cases": cases,
    }
    _write(tmp_path / "workloads.json", workloads)
    _write(tmp_path / "software.json", software)
    _write(tmp_path / "run.json", run)

    suite = load_suite(tmp_path / "run.json")
    output = tmp_path / "result.json"
    result = run_suite(suite, output_path=output)
    validate_document(result)
    assert [case["status"] for case in result["cases"]] == ["success", "success"]
    sequences = [
        [sample["sequence_index"] for sample in case["samples"]] for case in result["cases"]
    ]
    assert sequences == [
        [0, 3],
        [1, 2],
    ]
    assert all(case["correctness"]["status"] == "passed" for case in result["cases"])

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
