import copy
import json
import re
from pathlib import Path

import pytest

from clifft_bench.calibration import BATCH_CALIBRATION_CANDIDATES
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
        "campaigns/clifft-history-v1/run.v1.json",
    ],
)
def test_checked_in_manifests_validate(relative: str) -> None:
    validate_path(ROOT / relative)


def test_release_manifest_expands_named_variants() -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")

    assert suite.run["collection"]["placements"] == 1
    assert len(suite.cases) == 40
    assert len({case.id for case in suite.cases}) == 40
    assert {case.definition["variant_id"] for case in suite.cases} == {
        "clifft-previous",
        "clifft-current",
        "clifft-current-calibrated",
        "symft-calibrated",
        "symft-single",
    }
    assert {case.implementation.definition["adapter"] for case in suite.cases} == {
        "clifft",
        "symft",
    }
    versions_by_variant = {
        variant_id: {
            case.implementation.definition["version"]
            for case in suite.cases
            if case.definition["variant_id"] == variant_id
        }
        for variant_id in (
            "clifft-previous",
            "clifft-current",
            "clifft-current-calibrated",
        )
    }
    assert versions_by_variant == {
        "clifft-previous": {"0.9.0"},
        "clifft-current": {"0.10.0rc1"},
        "clifft-current-calibrated": {"0.10.0rc1"},
    }
    candidate = next(
        case.implementation.definition
        for case in suite.cases
        if case.definition["variant_id"] == "clifft-current"
    )
    assert candidate["version"] == "0.10.0rc1"
    assert candidate["display_version"] == "0.10.0"
    assert candidate["source_tag"] == "v0.10.0rc1"

    comparisons = {item["id"]: item for item in suite.run["comparisons"]}
    assert comparisons == {
        "current-vs-previous": {
            "id": "current-vs-previous",
            "baseline_variant": "clifft-previous",
            "candidate_variants": ["clifft-current"],
        },
        "alternatives-vs-current": {
            "id": "alternatives-vs-current",
            "baseline_variant": "clifft-current-calibrated",
            "candidate_variants": ["symft-calibrated"],
        },
        "scalar-alternatives-vs-current": {
            "id": "scalar-alternatives-vs-current",
            "baseline_variant": "clifft-current",
            "candidate_variants": ["symft-single"],
        },
    }

    scalar_cases = [
        case
        for case in suite.cases
        if case.definition["variant_id"]
        in {"clifft-previous", "clifft-current", "symft-single"}
    ]
    assert scalar_cases
    assert all(
        case.definition["execution"]["batch_enabled"] is False
        and case.definition["execution"]["batch_size"] == 1
        for case in scalar_cases
    )

    calibrated_by_variant = {
        variant_id: [
            case
            for case in suite.cases
            if case.definition["variant_id"] == variant_id
        ]
        for variant_id in ("clifft-current-calibrated", "symft-calibrated")
    }
    assert all(len(cases) == 8 for cases in calibrated_by_variant.values())
    for cases in calibrated_by_variant.values():
        assert all(
            case.definition["execution"]["batch_enabled"] is True
            and case.definition["execution"]["batch_size"] == "calibrate"
            and case.definition["shots_per_call"] >= max(BATCH_CALIBRATION_CANDIDATES)
            for case in cases
        )
    signatures = {
        variant_id: {
            (case.workload.id, case.definition["shots_per_call"])
            for case in cases
        }
        for variant_id, cases in calibrated_by_variant.items()
    }
    assert signatures["clifft-current-calibrated"] == signatures["symft-calibrated"]


def test_history_manifest_runs_each_release_with_the_same_measurement_inputs() -> None:
    suite = load_suite(ROOT / "campaigns/clifft-history-v1/run.v1.json")

    assert suite.run["collection"]["placements"] == 1
    versions = {
        "0.1.0",
        "0.2.0",
        "0.3.0",
        "0.4.1",
        "0.5.0",
        "0.6.0",
        "0.7.0",
        "0.8.0",
        "0.9.0",
        "0.10.0rc1",
    }
    assert len(suite.cases) == 80
    assert {case.implementation.definition["version"] for case in suite.cases} == versions
    case_signatures = {
        case.definition["variant_id"]: {
            (
                member.workload.id,
                member.definition["shots_per_call"],
                tuple(sorted(member.definition["execution"].items())),
            )
            for member in suite.cases
            if member.definition["variant_id"] == case.definition["variant_id"]
        }
        for case in suite.cases
    }
    assert len({frozenset(items) for items in case_signatures.values()}) == 1


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
    implementations["clifft-0.9.0"]["python_executable_env"] = implementations[
        "clifft-0.10.0rc1"
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
