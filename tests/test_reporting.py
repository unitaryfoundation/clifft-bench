import copy

import pytest

from clifft_bench.cli import main
from clifft_bench.report import build_report, report_execution, review_summary
from clifft_bench.results import validate_result, write_json


def test_release_reporting_needs_only_one_execution(raw_result, tmp_path, monkeypatch):
    from clifft_bench import config

    def no_checkout(*args, **kwargs):
        raise AssertionError("report must not read current configuration")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_suite", no_checkout)
    monkeypatch.setattr(config, "repository_root", no_checkout)
    write_json(tmp_path / "raw.json", raw_result)
    before = set(tmp_path.iterdir())
    summary = report_execution(tmp_path, check=True)
    assert "1.25×" in summary and "1.1.0rc1" in summary
    assert set(tmp_path.iterdir()) == before
    assert main(["report", str(tmp_path), "--check"]) == 0


def test_current_coverage_without_previous_baseline_has_no_release_ratio(raw_result):
    case = copy.deepcopy(raw_result["cases"][1])
    expected = copy.deepcopy(raw_result["campaign"]["cases"][1])
    case.update(case_id="new-current")
    case["workload"]["id"] = "new-workload"
    expected.update(id="new-current", workload_id="new-workload")
    raw_result["cases"].append(case)
    raw_result["campaign"]["cases"].append(expected)
    validate_result(raw_result)
    report = build_report(raw_result)
    assert len(report["current"]) == 2
    assert report["release"][-1]["ratio"] is None
    assert "baseline: not measured" in review_summary(report)


def test_tool_failures_and_incompatible_absences_remain_visible(raw_result):
    case = copy.deepcopy(raw_result["cases"][1])
    expected = copy.deepcopy(raw_result["campaign"]["cases"][1])
    case.update(
        case_id="peer",
        variant_id="peer",
        status="error",
        error={"phase": "sampling", "type": "WorkerTimeout", "message": "timed out"},
    )
    case["simulator"].update(implementation_id="peer", adapter="symft", name="SymFT")
    expected.update(id="peer", variant_id="peer", implementation_id="peer")
    raw_result["cases"].append(case)
    raw_result["campaign"]["cases"].append(expected)
    raw_result["campaign"]["comparisons"].append(
        {"id": "peers", "baseline_variant": "current", "candidate_variants": ["peer"]}
    )
    extra = copy.deepcopy(raw_result["cases"][1])
    extra.update(case_id="clifft-only")
    extra["workload"].update(id="clifft-only", compatible_adapters=["clifft"])
    definition = copy.deepcopy(raw_result["campaign"]["cases"][1])
    definition.update(id="clifft-only", workload_id="clifft-only")
    raw_result["cases"].append(extra)
    raw_result["campaign"]["cases"].append(definition)
    validate_result(raw_result)
    report = build_report(raw_result)
    summary = review_summary(report)
    assert "candidate: error" in summary and "candidate: unsupported" in summary
    assert all(p["ratio"] is None for p in report["pairs"] if p["comparison_id"] == "peers")
    assert report["rows"]["peer"]["median_attempted_shots_per_second"] == ""


def test_bulk_and_short_calls_are_separate_report_groups(raw_result):
    for index in range(2):
        case = copy.deepcopy(raw_result["cases"][index])
        expected = copy.deepcopy(raw_result["campaign"]["cases"][index])
        variant = case["variant_id"] + "-bulk"
        case.update(case_id=variant, variant_id=variant)
        expected.update(id=variant, variant_id=variant, shots_per_call=16)
        case["execution"]["shots_per_call"] = 16
        for sample in case["samples"]:
            for key in ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors"):
                sample[key] *= 2
            sample["duration_seconds"] *= 2
        raw_result["cases"].append(case)
        raw_result["campaign"]["cases"].append(expected)
    raw_result["campaign"]["comparisons"].append(
        {"id": "bulk", "baseline_variant": "previous-bulk", "candidate_variants": ["current-bulk"]}
    )
    validate_result(raw_result)
    report = build_report(raw_result)
    assert len(report["release"]) == len(report["tools"]) == 1
    assert report["tools"][0]["cases"]["current-bulk"]["execution"]["shots_per_call"] == 16


@pytest.mark.parametrize("all_failed", [False, True])
def test_render_all_themes_and_regenerate_stale_csv(raw_result, tmp_path, all_failed):
    pytest.importorskip("matplotlib")
    if all_failed:
        for case in raw_result["cases"]:
            case.update(status="error", error={"phase": "sampling", "type": "WorkerTimeout",
                                              "message": "timed out"})
    write_json(tmp_path / "raw.json", raw_result)
    (tmp_path / "cases.csv").write_text("stale table")
    report_execution(tmp_path)
    assert ("125.0" in (tmp_path / "cases.csv").read_text()) is not all_failed
    for stem in ("clifft-throughput", "simulator-throughput", "release-change"):
        for theme in ("light", "dark"):
            path = tmp_path / "report" / f"{stem}-{theme}.png"
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    summary = (tmp_path / "report/review.md").read_text()
    assert ("1.25×" in summary) is not all_failed
    if all_failed:
        assert "candidate: error" in summary
