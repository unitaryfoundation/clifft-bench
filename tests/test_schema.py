import copy
import json

import pytest

from clifft_bench.cli import main
from clifft_bench.schema import (
    SchemaValidationError,
    repository_root,
    validate_document,
    validate_path,
)


def test_example_result_validates() -> None:
    path = repository_root() / "examples/result.v1.json"
    document = validate_path(path)
    assert document["schema_version"] == "clifft-bench/result/v1"


def test_incomplete_success_result_is_rejected() -> None:
    document = validate_path(repository_root() / "examples/result.v1.json")
    broken = copy.deepcopy(document)
    broken["cases"][0]["samples"] = []
    with pytest.raises(SchemaValidationError, match="non-empty"):
        validate_document(broken, source="broken result")


def test_seed_zero_is_rejected() -> None:
    document = validate_path(repository_root() / "manifests/run-smoke.v1.json")
    document["seed"] = 0
    with pytest.raises(SchemaValidationError, match="minimum of 1"):
        validate_document(document, source="zero seed")


def test_unsupported_result_status_is_rejected() -> None:
    document = validate_path(repository_root() / "examples/result.v1.json")
    document["cases"][0]["status"] = "unsupported"
    with pytest.raises(SchemaValidationError, match="unsupported"):
        validate_document(document, source="unsupported result")


def test_validate_deep_checks_run_manifest_regardless_of_filename(tmp_path) -> None:
    root = repository_root()
    document = validate_path(root / "manifests/run-smoke.v1.json")
    document["workloads_manifest"] = str(root / "manifests/workloads.v1.json")
    document["software_manifest"] = str(root / "manifests/software.v1.json")
    document["cases"][0]["workload_id"] = "missing-workload"
    path = tmp_path / "arbitrary-name.json"
    path.write_text(json.dumps(document))
    assert main(["validate", str(path)]) == 2
