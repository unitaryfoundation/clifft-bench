from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.compiler import scheduler_configuration
from clifft_bench.manifest import load_suite
from clifft_bench.schema import repository_root, write_json

ROOT = repository_root()


def _release_copy(tmp_path: Path) -> tuple[Path, dict]:
    document = json.loads((ROOT / "campaigns/release-v1/run.v1.json").read_text())
    document["workloads_manifest"] = str(ROOT / "manifests/workloads.v1.json")
    document["software_manifest"] = str(ROOT / "manifests/software.v1.json")
    return tmp_path / "release.json", document


def test_workload_override_replaces_scheduler_and_preserves_other_execution_options(tmp_path):
    path, document = _release_copy(tmp_path)
    variant = document["variants"][1]
    variant["execution"]["clifft_scheduler"] = {"enabled": True, "beam_width": 32}
    variant["workloads"][0]["execution"] = {"clifft_scheduler": {"enabled": False}}
    write_json(path, document)
    cases = [c for c in load_suite(path).cases
             if c.definition["variant_id"] == "clifft-current"]
    assert scheduler_configuration(cases[0].definition["execution"]) == {"enabled": False}
    assert scheduler_configuration(cases[1].definition["execution"])["beam_width"] == 32
    assert all(c.definition["execution"]["batch_size"] == "calibrate" for c in cases)
    cases[1].definition["execution"]["clifft_scheduler"]["beam_width"] = 1
    assert cases[2].definition["execution"]["clifft_scheduler"]["beam_width"] == 32


@pytest.mark.parametrize("options", [
    {"enabled": True, "beam_width": 0},
    {"enabled": True, "search_budget": -1},
    {"enabled": True, "search_budget": float("nan")},
    {"enabled": True, "search_budget": float("inf")},
    {"enabled": True, "typo": True},
    {"enabled": False, "beam_width": 8},
])
def test_invalid_scheduler_options_are_rejected_before_launch(tmp_path, options):
    path, document = _release_copy(tmp_path)
    document["variants"][1]["workloads"][0]["execution"] = {"clifft_scheduler": options}
    write_json(path, document)
    with pytest.raises(ValueError):
        load_suite(path)


def test_scheduler_options_cannot_be_silently_ignored_by_another_adapter(tmp_path):
    path, document = _release_copy(tmp_path)
    document["variants"][2]["execution"]["clifft_scheduler"] = {"enabled": True}
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
            execution={"clifft_scheduler": {"enabled": True}},
        )


def test_release_scheduler_configuration_preserves_workload_inputs(tmp_path):
    path, document = _release_copy(tmp_path)
    original = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    execution = json.loads(
        (ROOT / "campaigns/release-v1/clifft-scheduled-execution.json").read_text()
    )
    document["variants"][1]["execution"] = execution
    write_json(path, document)
    scheduled = load_suite(path)
    assert [(c.id, c.workload.id, c.definition["shots_per_call"]) for c in scheduled.cases] == [
        (c.id, c.workload.id, c.definition["shots_per_call"]) for c in original.cases
    ]
    for before, after in zip(original.cases, scheduled.cases):
        if after.definition["variant_id"] == "clifft-current":
            assert after.definition["execution"]["batch_size"] == "calibrate"
            assert after.definition["execution"]["clifft_scheduler"] == scheduler_configuration(
                {"clifft_scheduler": {"enabled": True}}
            )
        else:
            assert after.definition == before.definition
