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
        "manifests/run-runner-aa.v1.json",
    ],
)
def test_checked_in_manifests_validate(relative: str) -> None:
    validate_path(ROOT / relative)


@pytest.mark.parametrize(
    "relative",
    [
        "manifests/run-smoke.v1.json",
        "manifests/run-phase1.v1.json",
        "manifests/run-runner-aa.v1.json",
    ],
)
def test_checked_in_suites_resolve_and_verify_artifacts(relative: str) -> None:
    suite = load_suite(ROOT / relative)
    assert suite.cases
    assert all(case.workload.artifact_path.is_file() for case in suite.cases)


def test_runner_study_installs_an_exactly_pinned_simulator_environment() -> None:
    requirements_path = ROOT / "requirements/runner-study.txt"
    pins = {}
    for line in requirements_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        distribution, separator, version = line.partition("==")
        assert separator == "==", f"runner-study requirement is not exactly pinned: {line}"
        assert distribution and version
        assert distribution not in pins
        pins[distribution] = version

    software = validate_path(ROOT / "manifests/software.v1.json")
    clifft = next(item for item in software["implementations"] if item["name"] == "clifft")
    assert pins[clifft["distribution"]] == clifft["version"]
    assert set(clifft["dependency_distributions"]) <= pins.keys()

    workflow = (ROOT / ".github/workflows/ubicloud-study.yml").read_text()
    assert "python -m pip install -e . -r requirements/runner-study.txt" in workflow
    assert "runs-on: ubicloud-standard-4-ubuntu-2404" in workflow
    assert "max-parallel: 1" in workflow
    assert '\n  schedule:' in workflow
    assert 'SCHEDULED_DISPATCH_TARGET: "6"' in workflow
    assert "needs: collection-gate" in workflow


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
