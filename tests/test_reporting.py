from __future__ import annotations

import csv
import json
import math
import statistics
import struct
from pathlib import Path

from reporting.qec import WORKLOAD_ORDER, build_report, web_output_paths

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "reporting/sources.json"


def _source_document() -> dict:
    return json.loads(SOURCES.read_text())


def _comparison_rows(execution: Path) -> list[dict[str, str]]:
    with (execution / "comparisons.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_reporting_chains_calibrated_release_onto_scalar_history() -> None:
    report = build_report(SOURCES)

    assert report.history.versions[:10] == (
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
    )
    assert report.history.source_executions[8:10] == (
        "clifft-history-v1-20260902",
        "release-v1-20260903-133252",
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


def test_reporting_exposes_release_comparison_and_qv_source() -> None:
    report = build_report(SOURCES)
    source_document = _source_document()

    assert report.qv_execution == Path(source_document["qv_execution"]).name
    assert len(report.release_points) == len(WORKLOAD_ORDER)
    release_points = {point.workload_id: point for point in report.release_points}
    assert release_points["coherent-surface-d3-r1-p1e-3-rz2e-2"].current_packed
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

    near_tie = points["distillation-color-code-85q-p5e-2"]
    assert round(near_tie.clifft_over_alternative, 2) == 1.05
    outlier = points["coherent-surface-d5-r5-p1e-3-rz2e-2"]
    assert round(outlier.clifft_rate) == 5819
    assert round(outlier.alternative_rate) == 66
    assert round(outlier.clifft_over_alternative, 1) == 87.7


def test_web_output_paths_cover_all_qec_assets(tmp_path: Path) -> None:
    assert {path.name for path in web_output_paths(tmp_path)} == {
        "clifft-throughput-light.png",
        "clifft-throughput-dark.png",
        "clifft-symft-throughput-light.png",
        "clifft-symft-throughput-dark.png",
        "clifft-vs-symft-light.png",
        "clifft-vs-symft-dark.png",
        "performance-over-time-light.png",
        "performance-over-time-dark.png",
        "v010-vs-v009-light.png",
        "v010-vs-v009-dark.png",
    }


def test_checked_in_web_assets_cover_reporting_outputs() -> None:
    output_dir = ROOT / "reporting/figures/web"
    expected = {path.name for path in web_output_paths(output_dir)} | {
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
