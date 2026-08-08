import copy
import json
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
        "manifests/run-phase1.v1.json",
    ],
)
def test_checked_in_manifests_validate(relative: str) -> None:
    validate_path(ROOT / relative)


@pytest.mark.parametrize(
    "relative",
    ["manifests/run-smoke.v1.json", "manifests/run-phase1.v1.json"],
)
def test_checked_in_suites_resolve_and_verify_artifacts(relative: str) -> None:
    suite = load_suite(ROOT / relative)
    assert suite.cases
    assert all(case.workload.artifact_path.is_file() for case in suite.cases)


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
