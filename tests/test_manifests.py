import copy
import json
import re
from pathlib import Path

import pytest

from clifft_bench.manifest import load_campaign, load_suite
from clifft_bench.schema import SchemaValidationError, repository_root, validate_path

ROOT = repository_root()


@pytest.mark.parametrize(
    "relative",
    [
        "manifests/workloads.v1.json",
        "manifests/software.v1.json",
        "manifests/run-smoke.v1.json",
        "campaigns/clifft-history-v1/campaign.v1.json",
        "campaigns/current-tools-v1/campaign.v1.json",
        "campaigns/qv-multicore-v1/qv-campaign.v1.json",
    ],
)
def test_checked_in_manifests_validate(relative: str) -> None:
    validate_path(ROOT / relative)


@pytest.mark.parametrize(
    "relative",
    [
        "manifests/run-smoke.v1.json",
    ],
)
def test_checked_in_suites_resolve_and_verify_artifacts(relative: str) -> None:
    suite = load_suite(ROOT / relative)
    assert suite.cases
    assert all(case.workload.artifact_path.is_file() for case in suite.cases)


def test_smoke_suite_uses_symft_automatic_batching() -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    symft_cases = [
        case
        for case in suite.cases
        if case.implementation.definition["adapter"] == "symft"
        and case.definition["execution"]["batch_enabled"]
    ]
    assert symft_cases
    assert {
        case.definition["execution"]["batch_size"] for case in symft_cases
    } == {"auto"}


def test_automatic_batch_size_requires_batching_enabled(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    run = copy.deepcopy(suite.run)
    auto_case = next(
        case for case in run["cases"] if case["execution"]["batch_size"] == "auto"
    )
    auto_case["execution"]["batch_enabled"] = False
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="with batching disabled"):
        load_suite(run_path)


@pytest.mark.parametrize("campaign_id", ["clifft-history-v1", "current-tools-v1"])
def test_checked_in_campaigns_resolve_all_runs(campaign_id: str) -> None:
    campaign = load_campaign(ROOT / "campaigns" / campaign_id / "campaign.v1.json")
    assert campaign.suites
    assert all(environment["import_modules"] for environment in campaign.document["environments"])
    assert {suite.run["run_id"] for suite in campaign.suites} == {
        run["id"] for run in campaign.document["runs"]
    }


def test_qec_campaigns_target_clifft_0_9_0() -> None:
    history = load_campaign(ROOT / "campaigns/clifft-history-v1/campaign.v1.json")
    current = load_campaign(ROOT / "campaigns/current-tools-v1/campaign.v1.json")
    smoke = load_suite(ROOT / "manifests/run-smoke.v1.json")

    assert "clifft-0.9.0" in {run["id"] for run in history.document["runs"]}
    assert {run["id"] for run in current.document["runs"] if run["id"].startswith("clifft-")} == {
        "clifft-0.8.0",
        "clifft-0.9.0",
    }
    assert {
        case.implementation.id
        for case in smoke.cases
        if case.implementation.definition["adapter"] == "clifft"
    } == {"clifft-0.9.0"}


def test_current_cpu_campaign_contains_only_clifft_and_symft() -> None:
    campaign = load_campaign(ROOT / "campaigns/current-tools-v1/campaign.v1.json")

    assert {
        case.implementation.definition["adapter"]
        for suite in campaign.suites
        for case in suite.cases
    } == {"clifft", "symft"}
    assert {environment["id"] for environment in campaign.document["environments"]} == {
        "clifft-0.8.0",
        "clifft-0.9.0",
        "symft-0.1.0-9ec5790",
    }


def test_campaign_environment_locks_pin_every_requirement() -> None:
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


def test_campaign_matrix_expands_to_unique_cases() -> None:
    run_path = ROOT / "campaigns/current-tools-v1/symft-single.run.json"
    suite = load_suite(run_path)
    assert len(suite.cases) == 8
    assert len({case.id for case in suite.cases}) == 8
    assert {case.implementation.id for case in suite.cases} == {
        "symft-0.1.0-9ec5790"
    }


def test_artifact_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    workloads = dict(suite.workloads_document)
    workloads["workloads"] = [dict(item) for item in workloads["workloads"]]
    workloads["workloads"][0]["artifact"] = {
        "path": str(tmp_path / "changed.stim"),
        "sha256": workloads["workloads"][0]["artifact"]["sha256"],
    }
    (tmp_path / "changed.stim").write_text("M 0\n")
    path = tmp_path / "workloads.json"
    path.write_text(json.dumps(workloads))
    run = dict(suite.run)
    run["workloads_manifest"] = "workloads.json"
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))
    with pytest.raises(SchemaValidationError, match="SHA-256 mismatch"):
        load_suite(run_path)


def test_incompatible_case_is_rejected_before_execution(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    workloads = copy.deepcopy(suite.workloads_document)
    for workload in workloads["workloads"]:
        workload["artifact"]["path"] = str(
            suite.workloads_path.parent / workload["artifact"]["path"]
        )
    target = workloads["workloads"][0]
    target["compatible_adapters"] = ["clifft"]
    workloads_path = tmp_path / "workloads.json"
    workloads_path.write_text(json.dumps(workloads))

    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = "workloads.json"
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "suite.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="forces incomparable adapter"):
        load_suite(run_path)


def test_out_of_range_observable_is_rejected_before_execution(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    workloads = copy.deepcopy(suite.workloads_document)
    for workload in workloads["workloads"]:
        workload["artifact"]["path"] = str(
            suite.workloads_path.parent / workload["artifact"]["path"]
        )
    workloads["workloads"][0]["semantics"]["observable_index"] = 1
    workloads_path = tmp_path / "workloads.json"
    workloads_path.write_text(json.dumps(workloads))

    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = "workloads.json"
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "suite.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="observable index 1 is outside"):
        load_suite(run_path)
