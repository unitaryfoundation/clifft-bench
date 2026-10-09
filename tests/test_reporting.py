from __future__ import annotations

import csv
import json
import math
import statistics
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from clifft_bench.manifest import load_suite
from clifft_bench.results import COMPARISON_FIELDS
from clifft_bench.results import _comparison_rows as collect_comparisons
from reporting.qec import WORKLOAD_ORDER, _load_release, build_report, web_output_paths

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "reporting/sources.json"


def _source_document() -> dict:
    return json.loads(SOURCES.read_text())


def _comparison_rows(execution: Path) -> list[dict[str, str]]:
    with (execution / "comparisons.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def copied_sources(tmp_path: Path) -> Path:
    document = _source_document()
    history = ROOT / document["history_execution"]
    history_copy = tmp_path / history.name
    history_copy.mkdir()
    (history_copy / "cases.csv").write_bytes((history / "cases.csv").read_bytes())
    document["history_execution"] = str(history_copy)
    releases = []
    for relative in document["release_executions"]:
        source = ROOT / relative
        destination = tmp_path / source.name
        destination.mkdir()
        for name in ("comparisons.csv", "index.json"):
            (destination / name).write_bytes((source / name).read_bytes())
        releases.append(str(destination))
    document["release_executions"] = releases
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(document))
    return path


def test_reporting_selects_the_release_core_from_archived_results(copied_sources: Path) -> None:
    expected = build_report(SOURCES)
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    core = {
        case.workload.id for case in suite.cases
        if case.workload.definition["family"] != "prepared-basis-arithmetic"
    }
    assert len(core) == 6
    assert set(WORKLOAD_ORDER) == core
    assert set(expected.history.speedups) == core
    assert expected.history.medians == tuple(
        statistics.median(values) for values in zip(*expected.history.speedups.values())
    )

    document = json.loads(copied_sources.read_text())
    paths = [Path(document["history_execution"]) / "cases.csv"] + [
        Path(execution) / "comparisons.csv" for execution in document["release_executions"]
    ]
    archived_extras = set()
    for path in paths:
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        archived_extras.update({row["workload_id"] for row in rows} - core)
        _write_rows(path, [row for row in rows if row["workload_id"] in core])
    assert archived_extras == {
        "coherent-surface-d3-r1-p1e-3-rz2e-2",
        "coherent-surface-d5-r1-p1e-3-rz2e-2",
    }

    # A future six-workload execution and the archived eight-workload evidence
    # must produce the same view when the shared measurements are identical.
    assert build_report(copied_sources) == expected


@pytest.mark.parametrize("section", ["history", "current-vs-previous", "alternatives-vs-current"])
def test_reporting_rejects_missing_core_measurements(copied_sources: Path, section: str) -> None:
    document = json.loads(copied_sources.read_text())
    if section == "history":
        path = Path(document["history_execution"]) / "cases.csv"
    else:
        path = Path(document["release_executions"][-1]) / "comparisons.csv"
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    _write_rows(path, [
        row for row in rows
        if not (
            row["workload_id"] == "coherent-surface-d3-r3-p1e-3-rz2e-2"
            and (
                row["simulator_display_version"] == "0.5.0"
                if section == "history" else row["comparison_id"] == section
            )
        )
    ])

    with pytest.raises(ValueError, match="does not cover the reporting core"):
        build_report(copied_sources)


def test_reporting_chains_calibrated_release_onto_scalar_history() -> None:
    report = build_report(SOURCES)

    assert report.history.versions == (
        "0.1.0",
        "0.2.0",
        "0.3.0",
        "0.4.1",
        "0.5.0",
        "0.6.0",
        "0.7.0",
        "0.8.0",
        "0.9.0",
        "0.10.0",
        "0.11.0",
    )
    assert report.history.source_executions[8:] == (
        "clifft-history-v1-20260902",
        "release-v1-20260903-133252",
        "release-v1-20260930-180134",
    )
    assert set(report.history.speedups) == set(WORKLOAD_ORDER)
    assert all(
        len(values) == len(report.history.versions)
        for values in report.history.speedups.values()
    )

    first_release = ROOT / _source_document()["release_executions"][0]
    current_row = next(
        row
        for row in _comparison_rows(first_release)
        if row["comparison_id"] == "current-vs-previous"
        and row["workload_id"] == "msc-d3-inject-cultivate-p1e-3"
    )
    cultivation_d3 = report.history.speedups["msc-d3-inject-cultivate-p1e-3"]
    assert math.isclose(
        cultivation_d3[9] / cultivation_d3[8],
        float(current_row["ratio_candidate_over_baseline"]),
    )


def test_reporting_uses_latest_calibrated_cross_tool_comparison() -> None:
    report = build_report(SOURCES)

    latest_release = ROOT / _source_document()["release_executions"][-1]
    assert report.release_executions[-1] == latest_release.name
    assert len(report.relative_points) == len(WORKLOAD_ORDER)
    assert len(report.throughput_points) == len(WORKLOAD_ORDER)

    alternative_row = next(
        row
        for row in _comparison_rows(latest_release)
        if row["comparison_id"] == "alternatives-vs-current"
        and row["workload_id"] == "msc-d3-inject-cultivate-p1e-3"
    )
    points = {point.workload_id: point for point in report.relative_points}
    cultivation_d3 = points["msc-d3-inject-cultivate-p1e-3"]
    assert math.isclose(
        cultivation_d3.clifft_over_alternative,
        1 / float(alternative_row["ratio_candidate_over_baseline"]),
    )
    throughput = {point.workload_id: point for point in report.throughput_points}
    assert math.isclose(
        throughput["msc-d3-inject-cultivate-p1e-3"].attempted_shots_per_second,
        float(alternative_row["baseline_rate"]),
    )

    slow_coherent = points["coherent-surface-d5-r5-p1e-3-rz2e-2"]
    assert slow_coherent.clifft_over_alternative > 80


def test_release_loader_accepts_current_campaign_with_stim_anchor(tmp_path: Path) -> None:
    source = ROOT / _source_document()["release_executions"][-1]
    expected = _load_release(source)
    with (source / "cases.csv").open(newline="") as stream:
        cases = [row for row in csv.DictReader(stream) if row["variant_id"] != "stim-current"]
    surface = "surface-code-d7-r7-p1e-3"
    template = next(
        row for row in cases
        if row["variant_id"] == "symft-current" and row["workload_id"] == surface
    )
    cases.append({
        **template,
        "case_id": f"{surface}--stim-current",
        "variant_id": "stim-current",
        "implementation_id": "stim-1.16.0",
        "simulator_name": "Stim",
        "simulator_version": "1.16.0",
        "simulator_display_version": "1.16.0",
        "median_attempted_shots_per_second": "1000000",
    })
    # Use the real producer and active manifest so putting Stim back into the
    # SymFT comparison makes this fail, whether or not the source already has Stim.
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    rows = collect_comparisons(suite, source.name, cases)
    execution = tmp_path / source.name
    execution.mkdir()
    (execution / "index.json").write_bytes((source / "index.json").read_bytes())
    with (execution / "comparisons.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COMPARISON_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    assert _load_release(execution) == expected
    anchors = [row for row in rows if row["candidate_simulator_name"] == "Stim"]
    assert len(anchors) == 1
    assert anchors[0]["comparison_id"] == "stim-anchor-vs-current"
    assert anchors[0]["workload_id"] == surface
    assert anchors[0]["baseline_variant_id"] == "clifft-current"


def test_reporting_exposes_release_comparison_and_qv_source() -> None:
    report = build_report(SOURCES)
    source_document = _source_document()

    assert report.qv_execution == Path(source_document["qv_execution"]).name
    assert len(report.release_points) == len(WORKLOAD_ORDER)
    release_points = {point.workload_id: point for point in report.release_points}
    assert release_points["distillation-color-code-85q-p5e-2"].current_packed
    assert not release_points["coherent-surface-d5-r5-p1e-3-rz2e-2"].current_packed


def test_combined_throughput_uses_both_calibrated_absolute_rates() -> None:
    report = build_report(SOURCES)
    latest_release = ROOT / _source_document()["release_executions"][-1]
    rows = [
        row for row in _comparison_rows(latest_release)
        if row["comparison_id"] == "alternatives-vs-current"
    ]
    points = {point.workload_id: point for point in report.tool_throughput_points}
    assert set(points) == set(WORKLOAD_ORDER)
    for workload, point in points.items():
        selected = [row for row in rows if row["workload_id"] == workload]
        assert point.clifft_rate == statistics.median(
            float(row["baseline_rate"]) for row in selected
        )
        assert point.alternative_rate == statistics.median(
            float(row["candidate_rate"]) for row in selected
        )
        assert point.clifft_over_alternative == 1 / statistics.median(
            float(row["ratio_candidate_over_baseline"]) for row in selected
        )

    near_tie = points["msc-d3-inject-cultivate-p1e-3"]
    assert round(near_tie.clifft_over_alternative, 2) == 1.06
    outlier = points["coherent-surface-d5-r5-p1e-3-rz2e-2"]
    assert round(outlier.clifft_rate) == 12893
    assert round(outlier.alternative_rate) == 64
    assert round(outlier.clifft_over_alternative, 1) == 200.9


def test_web_output_paths_cover_all_qec_assets(tmp_path: Path) -> None:
    assert {path.name for path in web_output_paths(tmp_path, build_report(SOURCES))} == {
        "clifft-throughput-light.png",
        "clifft-throughput-dark.png",
        "clifft-symft-throughput-light.png",
        "clifft-symft-throughput-dark.png",
        "clifft-vs-symft-light.png",
        "clifft-vs-symft-dark.png",
        "performance-over-time-light.png",
        "performance-over-time-dark.png",
        "v011-vs-v010-light.png",
        "v011-vs-v010-dark.png",
    }


def test_web_release_filenames_follow_selected_evidence(tmp_path: Path) -> None:
    reviewed = build_report(SOURCES)
    next_release = replace(reviewed, clifft_version="0.12.0", previous_clifft_version="0.11.0")
    current_paths = {path.name for path in web_output_paths(tmp_path, reviewed)}
    next_paths = {path.name for path in web_output_paths(tmp_path, next_release)}
    assert next_paths - current_paths == {
        "v012-vs-v011-light.png", "v012-vs-v011-dark.png",
    }
    assert current_paths - next_paths == {
        "v011-vs-v010-light.png", "v011-vs-v010-dark.png",
    }


def test_checked_in_web_assets_cover_reporting_outputs() -> None:
    output_dir = ROOT / "reporting/figures/web"
    expected = {path.name for path in web_output_paths(output_dir, build_report(SOURCES))} | {
        "quantum-volume-light.png",
        "quantum-volume-dark.png",
    }
    assert {path.name for path in output_dir.glob("*.png")} == expected

    for path in output_dir.glob("*.png"):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert width == 1920
        if path.name.startswith("performance-over-time"):
            assert height == 760
        elif path.name.startswith("quantum-volume"):
            assert height == 940
        elif path.name.startswith("clifft-symft-throughput"):
            assert height == 1080
        else:
            assert height == 900
        assert data[25] == 6  # RGBA
