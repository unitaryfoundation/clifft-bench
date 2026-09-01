import copy
import json
import re
from pathlib import Path

import pytest

from clifft_bench.manifest import load_suite
from clifft_bench.schema import SchemaValidationError, repository_root, validate_path

ROOT = repository_root()


@pytest.mark.parametrize(
    "relative",
    [
        "manifests/workloads.v1.json",
        "manifests/software.v1.json",
        "manifests/run-smoke.v1.json",
        "campaigns/release-v1/run.v1.json",
    ],
)
def test_checked_in_manifests_validate(relative: str) -> None:
    validate_path(ROOT / relative)


def test_release_manifest_expands_named_variants() -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")

    assert len(suite.cases) == 32
    assert len({case.id for case in suite.cases}) == 32
    assert {case.definition["variant_id"] for case in suite.cases} == {
        "clifft-previous",
        "clifft-current",
        "symft-single",
        "symft-batch-32",
        "symft-batch-2048",
    }
    assert {case.implementation.definition["adapter"] for case in suite.cases} == {
        "clifft",
        "symft",
    }


def test_smoke_suite_exercises_symft_batch_calibration() -> None:
    smoke = load_suite(ROOT / "manifests/run-smoke.v1.json")
    calibrated = [
        case
        for case in smoke.cases
        if case.implementation.definition["adapter"] == "symft"
        and case.definition["execution"]["mode"] == "throughput"
    ]

    assert calibrated
    assert all(
        case.definition["execution"]["batch_size"] == "calibrate"
        for case in calibrated
    )


def test_batch_calibration_requires_batch_enabled_throughput_case(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    run = copy.deepcopy(suite.run)
    target = next(
        case
        for case in run["cases"]
        if case["execution"]["batch_size"] == "calibrate"
    )
    target["execution"]["batch_enabled"] = False
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="batching disabled"):
        load_suite(run_path)


def test_official_implementations_require_unique_python_variables(
    tmp_path: Path,
) -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    software = copy.deepcopy(suite.software_document)
    implementations = {item["id"]: item for item in software["implementations"]}
    implementations["clifft-0.8.0"]["python_executable_env"] = implementations[
        "clifft-0.9.0"
    ]["python_executable_env"]
    for implementation in implementations.values():
        environment = implementation.get("environment")
        if environment is not None:
            environment["requirements"] = str(
                (suite.software_path.parent / environment["requirements"]).resolve()
            )
    software_path = tmp_path / "software.json"
    software_path.write_text(json.dumps(software))

    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="share python executable variable"):
        load_suite(run_path)


def test_environment_locks_pin_every_requirement() -> None:
    for requirements_path in sorted((ROOT / "environments").glob("*.txt")):
        requirements = [
            line.strip()
            for line in requirements_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert requirements
        for requirement in requirements:
            assert "==" in requirement or " @ git+https://" in requirement
            if " @ git+https://" in requirement:
                assert re.search(r"@[0-9a-f]{40}(?:#|$)", requirement)


def _suite_with_local_workloads(tmp_path: Path):
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    workloads = copy.deepcopy(suite.workloads_document)
    for workload in workloads["workloads"]:
        workload["artifact"]["path"] = str(
            suite.workloads_path.parent / workload["artifact"]["path"]
        )
    workloads_path = tmp_path / "workloads.json"
    workloads_path.write_text(json.dumps(workloads))
    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = "workloads.json"
    run["software_manifest"] = str(suite.software_path)
    return suite, workloads, workloads_path, run


def test_artifact_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    suite, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["artifact"] = {
        "path": str(tmp_path / "changed.stim"),
        "sha256": workloads["workloads"][0]["artifact"]["sha256"],
    }
    (tmp_path / "changed.stim").write_text("M 0\n")
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="SHA-256 mismatch"):
        load_suite(run_path)


def test_incompatible_case_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["compatible_adapters"] = ["clifft"]
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="forces incomparable adapter"):
        load_suite(run_path)


def test_out_of_range_observable_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["semantics"]["observable_index"] = 1
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="observable index 1 is outside"):
        load_suite(run_path)
