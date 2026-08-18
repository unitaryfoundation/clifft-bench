from __future__ import annotations

import pytest

from clifft_bench.system import (
    CLOUD_METADATA_ENVIRONMENT,
    RUN_PROVENANCE_ENVIRONMENT,
    collect_cloud_metadata,
    collect_workflow_metadata,
)


def test_cloud_metadata_is_absent_without_an_external_launcher(monkeypatch) -> None:
    for variable in CLOUD_METADATA_ENVIRONMENT.values():
        monkeypatch.delenv(variable, raising=False)
    assert collect_cloud_metadata() is None


def test_cloud_metadata_requires_and_preserves_a_complete_identity(monkeypatch) -> None:
    expected = {}
    for key, variable in CLOUD_METADATA_ENVIRONMENT.items():
        value = f"example-{key}"
        monkeypatch.setenv(variable, value)
        expected[key] = value

    assert collect_cloud_metadata() == expected

    monkeypatch.delenv(CLOUD_METADATA_ENVIRONMENT["image_id"])
    with pytest.raises(ValueError, match="CLIFFT_BENCH_CLOUD_IMAGE_ID"):
        collect_cloud_metadata()


def test_explicit_run_provenance_overrides_ci_detection(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    expected = {}
    for key, variable in RUN_PROVENANCE_ENVIRONMENT.items():
        value = f"manual-{key}"
        monkeypatch.setenv(variable, value)
        expected[key] = value

    assert collect_workflow_metadata() == expected

    monkeypatch.delenv(RUN_PROVENANCE_ENVIRONMENT["run_id"])
    with pytest.raises(ValueError, match="CLIFFT_BENCH_RUN_ID"):
        collect_workflow_metadata()
