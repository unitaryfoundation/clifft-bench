from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.cli import main
from clifft_bench.compiler import scheduler_configuration
from clifft_bench.manifest import load_suite
from clifft_bench.results import _case_rows, _validate_compiler_record
from clifft_bench.runner import _simulator_record, _workload_record
from clifft_bench.schema import repository_root, validate_path, write_json
from clifft_bench.tuning import tuning_summary

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


def _tuning_result(tmp_path):
    path = tmp_path / "tuning.json"
    assert main([
        "tuning-manifest", "--run-manifest", str(ROOT / "campaigns/release-v1/run.v1.json"),
        "--variant", "clifft-current", "--output", str(path),
    ]) == 0
    manifest = validate_path(path)
    assert len(manifest["cases"]) == 18
    assert manifest["classification"] == "smoke"
    manifest["cases"] = manifest["cases"][:3]
    for definition in manifest["cases"]:
        definition["shots_per_call"] = 64
    write_json(path, manifest)
    suite = load_suite(path)
    result = copy.deepcopy(validate_path(ROOT / "examples/result.v1.json"))
    result["run"]["profile_id"] = "compiler-tuning"
    template = result["cases"][0]
    result["cases"] = []
    for i, definition in enumerate(suite.cases):
        case = copy.deepcopy(template)
        case.update(case_id=definition.id, variant_id=definition.definition["variant_id"])
        case["simulator"] = _simulator_record(definition)
        case["workload"] = _workload_record(definition)
        case["execution"]["clifft_scheduler"] = scheduler_configuration(
            definition.definition["execution"]
        )
        case["execution"].update(batch_size=1, batch_enabled=False, shots_per_call=64)
        metadata = case["setup"]["runtime_metadata"]
        metadata.update(
            clifft_scheduler=case["execution"]["clifft_scheduler"],
            precision="complex-fp64", peak_active_width=3 - i,
            scheduler_statistics={"applied": True},
            compile_seconds=0.1 + i, batch_calibration={
                "candidates": [1, 32], "selected_batch_size": 1, "duration_seconds": 6,
            },
        )
        case["summary"]["median_attempted_shots_per_second"] = [100, 200, 150][i]
        result["cases"].append(case)
    raw = tmp_path / "raw.json"
    write_json(raw, result)
    return suite, result, raw


def test_tuning_selects_throughput_instead_of_width_and_keeps_batch_calibration(tmp_path):
    suite, result, raw = _tuning_result(tmp_path)
    summary = tuning_summary(suite, result, raw)
    assert summary["status"] == "provisional"
    assert summary["trials"][0]["selected_case_id"].endswith("scheduler-default")
    execution = summary["workloads"][0]["execution"]
    assert execution["clifft_scheduler"]["search_budget"] == 16
    assert execution["batch_size"] == "calibrate"
    assert len(summary["trials"][0]["candidates"]) == 3
    assert main([
        "tuning-summary", "--run-manifest", str(suite.run_path),
        "--output", str(tmp_path / "recommendations.json"), str(raw),
    ]) == 0


def test_tuning_prefers_off_when_scheduling_does_not_change_the_plan(tmp_path):
    suite, result, raw = _tuning_result(tmp_path)
    for case in result["cases"]:
        case["setup"]["runtime_metadata"]["scheduler_statistics"]["applied"] = False
    summary = tuning_summary(suite, result, raw)
    assert summary["workloads"][0]["execution"]["clifft_scheduler"] == {"enabled": False}


@pytest.mark.parametrize("mutation, message", [
    ("missing", "every declared case"),
    ("failed", "resolve failures"),
    ("compiler", "compiler metadata"),
    ("version", "software identity"),
    ("shots", "workload does not match"),
    ("precision", "different measurement conditions"),
])
def test_tuning_rejects_incomplete_or_incomparable_evidence(tmp_path, mutation, message):
    suite, result, raw = _tuning_result(tmp_path)
    case = result["cases"][1]
    if mutation == "missing":
        result["cases"].pop()
    elif mutation == "failed":
        case["status"] = "error"
        case["error"] = {"phase": "setup", "type": "TimeoutError", "message": "timeout",
                         "traceback": ""}
    elif mutation == "compiler":
        case["setup"]["runtime_metadata"]["clifft_scheduler"] = {"enabled": False}
    elif mutation == "version":
        case["simulator"]["version"] = "wrong"
    elif mutation == "shots":
        case["execution"]["shots_per_call"] = 1
    elif mutation == "precision":
        case["setup"]["runtime_metadata"]["precision"] = "complex-fp32"
    with pytest.raises(ValueError, match=message):
        tuning_summary(suite, result, raw)


def test_finalization_checks_compiler_identity_and_tables_expose_costs(tmp_path):
    suite, result, _ = _tuning_result(tmp_path)
    expected, observed = suite.cases[1], result["cases"][1]
    _validate_compiler_record(expected, observed)
    result["run"]["workflow"]["run_attempt"] = "1.1"
    result["runner"]["cloud"] = {"instance_type": "test", "image_id": "test", "boot_id": "test"}
    suite.run["hardware_epoch"] = "test"
    rows = _case_rows(suite, "test", [result])
    assert json.loads(rows[1]["clifft_scheduler"])["enabled"] is True
    assert rows[1]["compile_seconds"] == 1.1
    assert rows[1]["batch_calibration_seconds"] == 6
    observed["execution"]["clifft_scheduler"] = {"enabled": False}
    with pytest.raises(ValueError, match="configuration does not match manifest"):
        _validate_compiler_record(expected, observed)
