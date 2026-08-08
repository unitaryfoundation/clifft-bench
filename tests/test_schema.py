import copy

import pytest

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
