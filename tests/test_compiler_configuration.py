from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.manifest import load_suite
from clifft_bench.schema import repository_root, write_json

ROOT = repository_root()


def _release_copy(tmp_path: Path) -> tuple[Path, dict]:
    document = json.loads((ROOT / "campaigns/release-v1/run.v1.json").read_text())
    document["workloads_manifest"] = str(ROOT / "manifests/workloads.v1.json")
    document["software_manifest"] = str(ROOT / "manifests/software.v1.json")
    return tmp_path / "release.json", document


def _scheduler_options() -> dict:
    return json.loads(
        (ROOT / "campaigns/release-v1/clifft-scheduled-execution.json").read_text()
    )["clifft_scheduler"]


@pytest.mark.parametrize("options", [
    {"beam_width": 0},
    {"search_budget": float("nan")},
    {"search_budget": float("inf")},
    {"typo": True},
])
def test_invalid_scheduler_options_are_rejected_before_launch(tmp_path, options):
    path, document = _release_copy(tmp_path)
    document["variants"][1]["execution"]["clifft_scheduler"] = {**_scheduler_options(), **options}
    write_json(path, document)
    with pytest.raises(ValueError):
        load_suite(path)


def test_scheduler_options_cannot_be_silently_ignored_by_another_adapter(tmp_path):
    path, document = _release_copy(tmp_path)
    document["variants"][2]["execution"]["clifft_scheduler"] = _scheduler_options()
    write_json(path, document)
    with pytest.raises(ValueError, match="Clifft compiler options"):
        load_suite(path)


def test_requested_scheduler_is_rejected_on_older_clifft(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "clifft", SimpleNamespace())
    with pytest.raises(ValueError, match="does not support active-width scheduling"):
        ClifftAdapter().prepare(
            artifact_path=tmp_path / "unused.stim",
            workload={"semantics": {"reference_convention": "raw-record-parity"}},
            execution={"clifft_scheduler": _scheduler_options()},
        )


def test_scheduler_requires_explicit_options(tmp_path):
    path, document = _release_copy(tmp_path)
    options = _scheduler_options()
    options.pop("search_budget")
    document["variants"][1]["execution"]["clifft_scheduler"] = options
    write_json(path, document)
    with pytest.raises(ValueError, match="required property"):
        load_suite(path)
