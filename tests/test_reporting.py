from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from reporting.qec import WORKLOAD_ORDER, build_report

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
