import copy

import pytest
from toml_helpers import write_toml

from clifft_bench.cli import main
from clifft_bench.config import read_toml
from clifft_bench.schema import (
    repository_root,
    validate_document,
    validate_path,
)


def test_example_result_validates() -> None:
    path = repository_root() / "examples/result.v1.json"
    document = validate_path(path)
    assert document["schema_version"] == "clifft-bench/result/v1"


def test_result_accepts_complete_cloud_identity() -> None:
    document = validate_path(repository_root() / "examples/result.v1.json")
    document["runner"]["cloud"] = {
        "provider": "aws",
        "instance_id": "i-0123456789abcdef0",
        "instance_type": "m7a.xlarge",
        "image_id": "ami-0123456789abcdef0",
        "region": "us-east-1",
        "availability_zone": "us-east-1a",
        "lifecycle": "on-demand",
        "boot_id": "12345678-1234-1234-1234-123456789abc",
    }
    validate_document(document, source="cloud result")


def test_incomplete_success_result_is_rejected() -> None:
    document = validate_path(repository_root() / "examples/result.v1.json")
    broken = copy.deepcopy(document)
    broken["cases"][0]["samples"] = []
    with pytest.raises(ValueError, match="non-empty"):
        validate_document(broken, source="broken result")


def test_seed_zero_is_rejected(tmp_path) -> None:
    document = read_toml(repository_root() / "campaigns/smoke.toml")
    document["seed"] = 0
    path = tmp_path / "zero.toml"
    write_toml(path, document)
    assert main(["validate", str(path)]) == 2


def test_unsupported_result_status_is_rejected() -> None:
    document = validate_path(repository_root() / "examples/result.v1.json")
    document["cases"][0]["status"] = "unsupported"
    with pytest.raises(ValueError, match="unsupported"):
        validate_document(document, source="unsupported result")


def test_validate_deep_checks_run_manifest_regardless_of_filename(tmp_path) -> None:
    root = repository_root()
    document = read_toml(root / "campaigns/smoke.toml")
    document["workloads_file"] = str(root / "workloads.toml")
    document["software_file"] = str(root / "software.toml")
    document["cases"][0]["workload_id"] = "missing-workload"
    path = tmp_path / "arbitrary-name.toml"
    write_toml(path, document)
    assert main(["validate", str(path)]) == 2
