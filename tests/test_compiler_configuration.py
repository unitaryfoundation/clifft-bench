from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from toml_helpers import write_toml

from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.config import load_suite, read_toml
from clifft_bench.schema import repository_root

ROOT = repository_root()


def _release_copy(tmp_path: Path) -> tuple[Path, dict]:
    document = read_toml(ROOT / "campaigns/release-v1.toml")
    document["workloads_file"] = str(ROOT / "workloads.toml")
    document["software_file"] = str(ROOT / "software.toml")
    return tmp_path / "release.toml", document


def _scheduler_options() -> dict:
    return (
        load_suite(ROOT / "campaigns/release-v1.toml")
        .cases[0]
        .definition["execution"]["options"]["scheduler"]
    )


@pytest.mark.parametrize("options", [
    {"beam_width": 0},
    {"search_budget": float("nan")},
    {"search_budget": float("inf")},
    {"typo": True},
])
def test_invalid_scheduler_options_are_rejected_before_launch(tmp_path, options):
    path, document = _release_copy(tmp_path)
    document["variants"][1]["execution"]["options"]["scheduler"] = {
        **_scheduler_options(),
        **options,
    }
    write_toml(path, document)
    with pytest.raises(ValueError):
        load_suite(path)


def test_scheduler_options_cannot_be_silently_ignored_by_another_adapter(tmp_path):
    path, document = _release_copy(tmp_path)
    document["variants"][2]["execution"]["options"]["scheduler"] = _scheduler_options()
    write_toml(path, document)
    with pytest.raises(ValueError, match="unknown SymFT options"):
        load_suite(path)


def test_requested_scheduler_is_rejected_on_older_clifft(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "clifft", SimpleNamespace())
    with pytest.raises(ValueError, match="does not support active-width scheduling"):
        ClifftAdapter().prepare(
            artifact_path=tmp_path / "unused.stim",
            workload={"semantics": {"reference_convention": "raw-record-parity"}},
            execution={"options": {"scheduler": _scheduler_options()}},
        )


def test_scheduler_requires_explicit_options(tmp_path):
    path, document = _release_copy(tmp_path)
    options = _scheduler_options()
    options.pop("search_budget")
    document["variants"][1]["execution"]["options"]["scheduler"] = options
    write_toml(path, document)
    with pytest.raises(ValueError, match="requires explicit"):
        load_suite(path)


@pytest.mark.parametrize("adapter, options", [
    ("symft", {"sample_chunk_shots": True}),
    ("symft", {"sample_chunk_shots": -1}),
    ("tsim", {"trajectory_sharing": 1}),
    ("stim", {"trajectory_sharing": True}),
    ("xtim", {"sample_chunk_shots": 0}),
    ("clifft", {"scheduler": {}}),
])
def test_adapter_owns_option_validation(adapter, options):
    from clifft_bench.adapters import load_adapter

    with pytest.raises(ValueError):
        load_adapter(adapter).validate_options(options)
