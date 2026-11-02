from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from toml_helpers import write_toml

from clifft_bench.adapters.clifft import ClifftAdapter
from clifft_bench.config import load_suite, read_toml, repository_root

ROOT = repository_root()


def _release_copy(tmp_path: Path) -> tuple[Path, dict]:
    document = load_suite(ROOT / "campaigns/release-v1.toml").run
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
            workload={"semantics": {}},
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


@pytest.mark.parametrize("damage, message", [
    ("unknown", "unknown options preset"),
    ("adapter", "requires adapter"),
    ("both", "declare options or options_preset"),
    ("invalid", "beam_width"),
])
def test_option_presets_are_validated_before_launch(tmp_path, damage, message):
    campaign = read_toml(ROOT / "campaigns/smoke.toml")
    software = read_toml(ROOT / "software.toml")
    case = next(c for c in campaign["cases"] if "options_preset" in c["execution"])
    if damage == "unknown":
        case["execution"]["options_preset"] = "missing-preset"
    elif damage == "adapter":
        case["implementation_id"] = next(i["id"] for i in software["implementations"]
                                         if i["adapter"] == "symft")
    elif damage == "both":
        case["execution"]["options"] = {}
    else:
        software["option_presets"][case["execution"]["options_preset"]]["options"][
            "scheduler"
        ]["beam_width"] = 0
    campaign["workloads_file"] = str(ROOT / "workloads.toml")
    campaign["software_file"] = "software.toml"
    write_toml(tmp_path / "software.toml", software)
    write_toml(tmp_path / "campaign.toml", campaign)
    with pytest.raises(ValueError, match=message):
        load_suite(tmp_path / "campaign.toml")


def test_shared_preset_changes_reach_every_selected_case(tmp_path):
    campaign = read_toml(ROOT / "campaigns/smoke.toml")
    software = read_toml(ROOT / "software.toml")
    selected = [c for c in campaign["cases"] if "options_preset" in c["execution"]]
    preset = selected[0]["execution"]["options_preset"]
    software["option_presets"][preset]["options"]["scheduler"]["beam_width"] += 1
    expected = software["option_presets"][preset]["options"]
    ids = {c["id"] for c in selected if c["execution"]["options_preset"] == preset}
    campaign["workloads_file"] = str(ROOT / "workloads.toml")
    campaign["software_file"] = "software.toml"
    write_toml(tmp_path / "software.toml", software)
    write_toml(tmp_path / "campaign.toml", campaign)
    suite = load_suite(tmp_path / "campaign.toml")
    executions = [c.definition["execution"] for c in suite.cases if c.id in ids]
    assert executions
    assert all(e["options"] == expected for e in executions)
    executions[0]["options"]["scheduler"]["beam_width"] += 1
    assert executions[1]["options"] == expected
