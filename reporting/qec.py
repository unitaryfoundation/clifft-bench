#!/usr/bin/env python3
"""Generate release-history and current-tool QEC throughput figures."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "reporting/sources.json"
DEFAULT_OUTPUT_DIR = ROOT / "reporting/figures"
DEFAULT_WEB_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "web"
CURRENT_COMPARISON = "current-vs-previous"
ALTERNATIVES_COMPARISON = "alternatives-vs-current"

WORKLOAD_LABELS = {
    "coherent-surface-d3-r3-p1e-3-rz2e-2": "Coherent d3, r3",
    "coherent-surface-d5-r5-p1e-3-rz2e-2": "Coherent d5, r5",
    "distillation-color-code-85q-p5e-2": "85q distillation",
    "msc-d3-inject-cultivate-p1e-3": "Cultivation d3",
    "msc-d5-inject-cultivate-p1e-3": "Cultivation d5",
    "surface-code-d7-r7-p1e-3": "Surface code d7, r7",
}
WORKLOAD_ORDER = tuple(WORKLOAD_LABELS)
DISTILLATION_WORKLOAD = "distillation-15to1-rm15-p1e-3"
ADDER_WORKLOAD = "draper-adder-m16-basis"
CURRENT_WORKLOAD_ORDER = (*WORKLOAD_ORDER, DISTILLATION_WORKLOAD)
WORKLOAD_LABELS[DISTILLATION_WORKLOAD] = "15-to-1 distillation"
TOOL_COMPARISONS = {
    "SymFT": (ALTERNATIVES_COMPARISON, CURRENT_WORKLOAD_ORDER),
    "xtim": (
        "xtim-vs-current",
        (
            "msc-d3-inject-cultivate-p1e-3",
            "msc-d5-inject-cultivate-p1e-3",
            "surface-code-d7-r7-p1e-3",
            DISTILLATION_WORKLOAD,
        ),
    ),
    "Stim": ("stim-anchor-vs-current", ("surface-code-d7-r7-p1e-3",)),
}


@dataclass(frozen=True)
class ReleaseData:
    execution_id: str
    previous_version: str
    current_version: str
    current_ratios: dict[str, float]
    current_rates: dict[str, float]
    current_packed: dict[str, bool]
    alternative_name: str
    alternative_version: str
    alternative_ratios: dict[str, float]
    alternative_rates: dict[str, float]
    shots_per_call: dict[str, int]


@dataclass(frozen=True)
class HistorySeries:
    versions: tuple[str, ...]
    speedups: dict[str, tuple[float, ...]]
    medians: tuple[float, ...]
    source_executions: tuple[str, ...]


@dataclass(frozen=True)
class RelativePoint:
    workload_id: str
    clifft_over_alternative: float


@dataclass(frozen=True)
class ThroughputPoint:
    workload_id: str
    attempted_shots_per_second: float


@dataclass(frozen=True)
class ToolThroughputPoint:
    workload_id: str
    rates: dict[str, float]
    failures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AdderComparison:
    previous_rate: float
    current_rate: float
    current_over_previous: float
    previous_compile_seconds: float
    current_compile_seconds: float
    previous_peak_active_width: int
    current_peak_active_width: int


@dataclass(frozen=True)
class ReleaseComparisonPoint:
    workload_id: str
    current_over_previous: float
    current_packed: bool


@dataclass(frozen=True)
class Report:
    history: HistorySeries
    relative_points: tuple[RelativePoint, ...]
    throughput_points: tuple[ThroughputPoint, ...]
    tool_throughput_points: tuple[ToolThroughputPoint, ...]
    tool_versions: dict[str, str]
    adder: AdderComparison
    release_points: tuple[ReleaseComparisonPoint, ...]
    clifft_version: str
    previous_clifft_version: str
    alternative_name: str
    alternative_version: str
    history_execution: str
    release_executions: tuple[str, ...]
    qv_execution: str


@dataclass(frozen=True)
class SourceSelection:
    history: Path
    releases: tuple[Path, ...]
    qv: Path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"no rows in {path}")
    return rows


def _one(rows: list[dict[str, str]], field: str, description: str) -> str:
    values = {row[field] for row in rows}
    if len(values) != 1:
        raise ValueError(f"expected one {description}, found {sorted(values)}")
    return next(iter(values))


def _median_rates(
    rows: list[dict[str, str]], key_fields: tuple[str, ...], value_field: str
) -> dict[tuple[str, ...], float]:
    grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for row in rows:
        value = float(row[value_field])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{value_field} must be finite and positive")
        grouped[tuple(row[field] for field in key_fields)].append(value)
    return {key: statistics.median(values) for key, values in grouped.items()}


def _single_ints(
    rows: list[dict[str, str]], field: str, description: str
) -> dict[str, int]:
    grouped: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        grouped[row["workload_id"]].add(int(row[field]))
    if any(len(values) != 1 for values in grouped.values()):
        raise ValueError(f"{description} varies within a workload")
    return {workload: next(iter(values)) for workload, values in grouped.items()}


def _select_comparison(
    rows: list[dict[str, str]], comparison_id: str, execution_id: str,
    workloads: tuple[str, ...] = WORKLOAD_ORDER,
) -> list[dict[str, str]]:
    selected = [
        row for row in rows
        if row["comparison_id"] == comparison_id and row["workload_id"] in workloads
    ]
    if {row["workload_id"] for row in selected} != set(workloads):
        raise ValueError(f"{execution_id} {comparison_id} does not cover the reporting core")
    if any(
        row["baseline_shots_per_call"] != row["candidate_shots_per_call"]
        for row in selected
    ):
        raise ValueError(f"{execution_id} {comparison_id} has unequal shots_per_call")
    return selected


def _load_release(path: Path, workloads: tuple[str, ...] = WORKLOAD_ORDER) -> ReleaseData:
    index = json.loads((path / "index.json").read_text())
    execution_id = str(index["execution_id"])
    if execution_id != path.name or index["campaign_id"] != "release-v1":
        raise ValueError(f"release identity mismatch in {path}")
    rows = _read_csv(path / "comparisons.csv")
    current = _select_comparison(rows, CURRENT_COMPARISON, execution_id, workloads)
    alternatives = _select_comparison(rows, ALTERNATIVES_COMPARISON, execution_id, workloads)

    if _one(current, "baseline_simulator_name", "previous simulator") != "Clifft":
        raise ValueError(f"{execution_id} previous simulator is not Clifft")
    if _one(current, "candidate_simulator_name", "current simulator") != "Clifft":
        raise ValueError(f"{execution_id} current simulator is not Clifft")
    previous_version = _one(
        current, "baseline_simulator_display_version", "previous Clifft version"
    )
    current_version = _one(
        current, "candidate_simulator_display_version", "current Clifft version"
    )
    if _one(
        alternatives, "baseline_simulator_display_version", "alternative baseline"
    ) != current_version:
        raise ValueError(f"{execution_id} cross-tool baseline is not current Clifft")

    current_cases = {
        (row["placement"], row["replica"], row["workload_id"]): (
            row["candidate_case_id"],
            row["candidate_rate"],
            row["candidate_batch_size_effective"],
        )
        for row in current
    }
    alternative_cases = {
        (row["placement"], row["replica"], row["workload_id"]): (
            row["baseline_case_id"],
            row["baseline_rate"],
            row["baseline_batch_size_effective"],
        )
        for row in alternatives
    }
    if current_cases != alternative_cases:
        raise ValueError(f"{execution_id} cross-tool rows do not reuse current Clifft")

    current_ratios = _median_rates(
        current, ("workload_id",), "ratio_candidate_over_baseline"
    )
    alternative_ratios = _median_rates(
        alternatives, ("workload_id",), "ratio_candidate_over_baseline"
    )
    current_rates = _median_rates(alternatives, ("workload_id",), "baseline_rate")
    alternative_rates = _median_rates(alternatives, ("workload_id",), "candidate_rate")
    current_packed = {
        row["workload_id"]: int(row["candidate_batch_size_effective"]) > 1
        for row in current
    }
    return ReleaseData(
        execution_id=execution_id,
        previous_version=previous_version,
        current_version=current_version,
        current_ratios={key[0]: value for key, value in current_ratios.items()},
        current_rates={key[0]: value for key, value in current_rates.items()},
        current_packed=current_packed,
        alternative_name=_one(
            alternatives, "candidate_simulator_name", "alternative simulator"
        ),
        alternative_version=_one(
            alternatives,
            "candidate_simulator_display_version",
            "alternative simulator version",
        ),
        alternative_ratios={key[0]: value for key, value in alternative_ratios.items()},
        alternative_rates={key[0]: value for key, value in alternative_rates.items()},
        shots_per_call=_single_ints(current, "baseline_shots_per_call", "shots_per_call"),
    )


def _case_map(
    rows: list[dict[str, str]], side: str | None,
) -> dict[tuple[str, ...], tuple[str, ...]]:
    fields = (
        "result_id", "case_id", "simulator_name", "simulator_version",
        "simulator_display_version", "rate", "batch_size_effective", "shots_per_call",
    )
    if side is not None:
        fields = tuple(f"{side}_{name}" for name in fields)
    else:
        fields = tuple("median_attempted_shots_per_second" if name == "rate" else name
                       for name in fields)
    cases = {}
    for row in rows:
        key = tuple(row[name] for name in ("placement", "replica", "workload_id"))
        if key in cases:
            raise ValueError(f"duplicate case for {key}")
        cases[key] = tuple(row[name] for name in fields)
    return cases


def _load_current_tsim(
    path: Path, rows: list[dict[str, str]], current_cases: dict[tuple[str, ...], tuple[str, ...]],
) -> tuple[dict[str, float], dict[str, str], str | None]:
    # cases.csv retains failures even when no successful comparison can be made.
    cases = [row for row in _read_csv(path / "cases.csv")
             if row["variant_id"] == "tsim-current"
             and row["workload_id"] in CURRENT_WORKLOAD_ORDER]
    comparisons = [row for row in rows if row["comparison_id"] == "tsim-vs-current"
                   and row["workload_id"] in CURRENT_WORKLOAD_ORDER]
    if not cases and not comparisons:
        return {}, {}, None  # Archived campaigns predate this variant.
    if set(_case_map(cases, None)) != set(current_cases):
        raise ValueError(f"{path.name} Tsim cases do not cover the reporting core")
    if _one(cases, "simulator_name", "Tsim simulator") != "Tsim":
        raise ValueError(f"{path.name} tsim-current does not measure Tsim")
    version = _one(cases, "simulator_display_version", "Tsim version")
    successful = []
    failed: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for case in cases:
        key = tuple(case[name] for name in ("placement", "replica", "workload_id"))
        if case["shots_per_call"] != current_cases[key][-1]:
            raise ValueError(f"{path.name} Tsim has unequal shots_per_call")
        workload = case["workload_id"]
        totals[workload] += 1
        if case["status"] == "success":
            successful.append(case)
        elif (case["status"] in {"error", "interrupted"}
              and case["error_phase"] and case["error_type"]):
            failed[workload] += 1
        else:
            raise ValueError(f"{path.name} Tsim has no terminal outcome for {key}")
    if any(row["baseline_shots_per_call"] != row["candidate_shots_per_call"]
           for row in comparisons):
        raise ValueError(f"{path.name} tsim-vs-current has unequal shots_per_call")
    candidates = _case_map(comparisons, "candidate")
    if candidates != _case_map(successful, None):
        raise ValueError(f"{path.name} tsim-vs-current does not match successful Tsim cases")
    expected = {key: current_cases[key] for key in candidates}
    if _case_map(comparisons, "baseline") != expected:
        raise ValueError(f"{path.name} tsim-vs-current does not reuse current Clifft")
    measured = _median_rates(comparisons, ("workload_id",), "candidate_rate")
    # Do not silently report only the replicas that happened to finish.
    rates = {key[0]: rate for key, rate in measured.items() if key[0] not in failed}
    failures = {workload: f"{count}/{totals[workload]} failed"
                for workload, count in failed.items()}
    return rates, failures, version


def _load_current_tools(
    path: Path, release: ReleaseData,
) -> tuple[tuple[ToolThroughputPoint, ...], dict[str, str]]:
    rows = _read_csv(path / "comparisons.csv")
    current = _select_comparison(
        rows, CURRENT_COMPARISON, path.name, CURRENT_WORKLOAD_ORDER
    )
    current_cases = _case_map(current, "candidate")
    versions = {"Clifft": release.current_version}
    rates = {workload: {"Clifft": release.current_rates[workload]}
             for workload in CURRENT_WORKLOAD_ORDER}
    for tool, (comparison, workloads) in TOOL_COMPARISONS.items():
        selected = _select_comparison(rows, comparison, path.name, workloads)
        if _one(selected, "candidate_simulator_name", "alternative simulator") != tool:
            raise ValueError(f"{path.name} {comparison} does not measure {tool}")
        expected = {key: value for key, value in current_cases.items() if key[2] in workloads}
        if _case_map(selected, "baseline") != expected:
            raise ValueError(f"{path.name} {comparison} does not reuse current Clifft")
        versions[tool] = _one(
            selected, "candidate_simulator_display_version", f"{tool} version"
        )
        tool_rates = _median_rates(selected, ("workload_id",), "candidate_rate")
        for (workload,), rate in tool_rates.items():
            rates[workload][tool] = rate
    tsim_rates, tsim_failures, tsim_version = _load_current_tsim(path, rows, current_cases)
    if tsim_version is not None:
        versions["Tsim"] = tsim_version
    for workload, rate in tsim_rates.items():
        rates[workload]["Tsim"] = rate
    return (
        tuple(ToolThroughputPoint(
            workload, rates[workload],
            {"Tsim": tsim_failures[workload]} if workload in tsim_failures else {},
        ) for workload in CURRENT_WORKLOAD_ORDER),
        versions,
    )


def _load_adder(path: Path, release: ReleaseData) -> AdderComparison:
    comparisons = _select_comparison(
        _read_csv(path / "comparisons.csv"), CURRENT_COMPARISON, path.name, (ADDER_WORKLOAD,)
    )
    cases = [row for row in _read_csv(path / "cases.csv") if row["workload_id"] == ADDER_WORKLOAD]
    measurements = []
    for side, variant, version in (
        ("baseline", "clifft-previous", release.previous_version),
        ("candidate", "clifft-current", release.current_version),
    ):
        selected = [row for row in cases if row["variant_id"] == variant]
        if not selected or any(row["status"] != "success" for row in selected):
            raise ValueError(f"{path.name} is missing successful adder cases for {variant}")
        if _one(selected, "simulator_display_version", "adder Clifft version") != version:
            raise ValueError(f"{path.name} adder version does not match current release pair")
        # Join compile/width evidence to the exact throughput cases being plotted.
        adapted = [{
            **{key: row[key] for key in ("placement", "replica", "workload_id")},
            **{f"{side}_{key}": row[key] for key in (
                "result_id", "case_id", "simulator_name", "simulator_version",
                "simulator_display_version", "batch_size_effective", "shots_per_call",
            )},
            f"{side}_rate": row["median_attempted_shots_per_second"],
        } for row in selected]
        if _case_map(adapted, side) != _case_map(comparisons, side):
            raise ValueError(f"{path.name} adder cases do not match comparison")
        compile_times = [float(row["compile_seconds"]) for row in selected]
        if any(not math.isfinite(value) or value < 0 for value in compile_times):
            raise ValueError("adder compile_seconds must be finite and nonnegative")
        widths = _single_ints(selected, "peak_active_width", "adder peak_active_width")
        width = widths[ADDER_WORKLOAD]
        if width < 0 or _one(selected, "simulator_name", "adder simulator") != "Clifft":
            raise ValueError("invalid adder Clifft evidence")
        rate = _median_rates(comparisons, ("workload_id",), f"{side}_rate")[(ADDER_WORKLOAD,)]
        measurements.append((rate, statistics.median(compile_times), width))
    previous, current = measurements
    ratio = _median_rates(
        comparisons, ("workload_id",), "ratio_candidate_over_baseline"
    )[(ADDER_WORKLOAD,)]
    return AdderComparison(
        previous[0], current[0], ratio, previous[1], current[1], previous[2], current[2]
    )


def _load_sources(path: Path) -> SourceSelection:
    document = json.loads(path.read_text())
    history = ROOT / document["history_execution"]
    releases = tuple(ROOT / value for value in document["release_executions"])
    qv = ROOT / document["qv_execution"]
    if not releases:
        raise ValueError("reporting requires at least one recurring release execution")
    qv_metadata = json.loads((qv / "metadata.json").read_text())
    if qv_metadata["execution_id"] != qv.name or qv_metadata["status"] != "complete":
        raise ValueError(f"QV identity or status mismatch in {qv}")
    qv_rows = _read_csv(qv / "cases.csv")
    if any(row["status"] != "success" for row in qv_rows):
        raise ValueError(f"QV execution {qv.name} contains failed cases")
    qv_simulators = {row["simulator"] for row in qv_rows}
    if qv_simulators != {"clifft", "qiskit", "qsim", "qulacs"}:
        raise ValueError(f"QV execution {qv.name} does not cover all four tools")
    return SourceSelection(history, releases, qv)


def build_report(sources_path: Path = DEFAULT_SOURCES) -> Report:
    selected = _load_sources(sources_path)
    history_path = selected.history
    history_rows = _read_csv(history_path / "cases.csv")
    if any(row["status"] != "success" for row in history_rows):
        raise ValueError(f"history execution {history_path.name} contains failed cases")
    history_versions = tuple(
        dict.fromkeys(row["simulator_display_version"] for row in history_rows)
    )
    # Every plotted version must use the same core so differing source coverage
    # cannot create an apparent performance change.
    history_rows = [row for row in history_rows if row["workload_id"] in WORKLOAD_ORDER]
    history_rates = _median_rates(
        history_rows,
        ("workload_id", "simulator_display_version"),
        "median_attempted_shots_per_second",
    )
    if set(history_rates) != {
        (workload, version) for workload in WORKLOAD_ORDER for version in history_versions
    }:
        raise ValueError(f"history execution {history_path.name} does not cover the reporting core")
    history_shots = _single_ints(history_rows, "shots_per_call", "historical shots_per_call")
    releases = tuple(_load_release(path) for path in selected.releases)
    if any(release.shots_per_call != history_shots for release in releases):
        raise ValueError("release shots_per_call do not match historical workloads")

    anchor = releases[0].previous_version
    if anchor not in history_versions:
        raise ValueError(f"first release baseline {anchor} is absent from history")
    versions = list(history_versions[: history_versions.index(anchor) + 1])
    sources = [history_path.name] * len(versions)
    baseline_version = versions[0]
    speedups = {
        workload: [
            history_rates[(workload, version)] / history_rates[(workload, baseline_version)]
            for version in versions
        ]
        for workload in WORKLOAD_ORDER
    }

    for release in releases:
        if release.previous_version != versions[-1]:
            raise ValueError(
                f"release chain expected {versions[-1]}, found {release.previous_version}"
            )
        versions.append(release.current_version)
        sources.append(release.execution_id)
        for workload in WORKLOAD_ORDER:
            speedups[workload].append(
                speedups[workload][-1] * release.current_ratios[workload]
            )

    medians = tuple(
        statistics.median(speedups[workload][index] for workload in WORKLOAD_ORDER)
        for index in range(len(versions))
    )
    # New workloads belong in current comparisons, never retroactively in the
    # fixed six-workload history or its median.
    latest = _load_release(selected.releases[-1], CURRENT_WORKLOAD_ORDER)
    points = tuple(
        RelativePoint(
            workload,
            1 / latest.alternative_ratios[workload],
        )
        for workload in CURRENT_WORKLOAD_ORDER
    )
    throughput_points = tuple(
        ThroughputPoint(workload, latest.current_rates[workload])
        for workload in CURRENT_WORKLOAD_ORDER
    )
    tool_throughput_points, tool_versions = _load_current_tools(selected.releases[-1], latest)
    adder = _load_adder(selected.releases[-1], latest)
    release_points = tuple(
        ReleaseComparisonPoint(
            workload,
            latest.current_ratios[workload],
            latest.current_packed[workload],
        )
        for workload in CURRENT_WORKLOAD_ORDER
    )
    return Report(
        HistorySeries(
            tuple(versions),
            {workload: tuple(values) for workload, values in speedups.items()},
            medians,
            tuple(sources),
        ),
        points,
        throughput_points,
        tool_throughput_points,
        tool_versions,
        adder,
        release_points,
        latest.current_version,
        latest.previous_version,
        latest.alternative_name,
        latest.alternative_version,
        history_path.name,
        tuple(release.execution_id for release in releases),
        selected.qv.name,
    )


def _plot_history(axis: Any, report: Report) -> None:
    positions = list(range(len(report.history.versions)))
    for index, workload in enumerate(WORKLOAD_ORDER):
        axis.plot(
            positions,
            report.history.speedups[workload],
            color="0.55",
            linewidth=1.15,
            alpha=0.38,
            label="Individual workloads" if index == 0 else None,
        )
    axis.plot(
        positions,
        report.history.medians,
        color="#0072B2",
        linewidth=3,
        marker="o",
        markersize=5.5,
        label="Median",
        zorder=3,
    )
    axis.axhline(1, color="0.35", linestyle="--", linewidth=1.1)
    axis.set_yscale("log")
    axis.set_xticks(positions, labels=report.history.versions)
    axis.set_xlim(-0.4, len(positions) - 0.35)
    axis.set_xlabel("Release")
    axis.set_ylabel("Speedup versus v0.1.0")
    axis.set_title("Clifft Throughput over Time")
    axis.annotate(
        f"{report.history.medians[-1]:.0f}× median speedup",
        (positions[-1], report.history.medians[-1]),
        xytext=(-8, 10),
        textcoords="offset points",
        ha="right",
        color="#0072B2",
        fontsize=11,
        fontweight="bold",
    )
    axis.legend(loc="upper left", fontsize=10.5)
    axis.grid(which="major", alpha=0.16)


def _ratio_tick(value: float, _position: float) -> str:
    return f"{value:g}×"


def _plot_relative(axis: Any, report: Report) -> None:
    points = sorted(report.relative_points, key=lambda point: point.clifft_over_alternative)
    ratios = [point.clifft_over_alternative for point in points]
    positions = list(range(len(points)))
    upper = max(2.5, max(ratios) * 1.45)
    axis.axvspan(0.7, 1, color="#E69F00", alpha=0.06, linewidth=0)
    axis.axvspan(1, upper, color="#56B4E9", alpha=0.055, linewidth=0)
    axis.axvline(1, color="0.35", linestyle="--", linewidth=1.4)
    for position, ratio in zip(positions, ratios, strict=True):
        axis.plot([min(ratio, 1), max(ratio, 1)], [position, position], color="0.76")
    axis.scatter(ratios, positions, color="#0072B2", edgecolor="white", s=88, zorder=3)

    for position, point in zip(positions, points, strict=True):
        ratio = point.clifft_over_alternative
        place_left = ratio > upper / 2
        axis.annotate(
            f"{ratio:.2f}×",
            (ratio, position),
            xytext=(-8 if place_left else 8, 0),
            textcoords="offset points",
            ha="right" if place_left else "left",
            va="center",
            fontsize=10.5,
            fontweight="bold",
        )

    from matplotlib.ticker import FixedLocator, FuncFormatter

    axis.set_xscale("log")
    axis.set_xlim(0.7, upper)
    ticks = [value for value in (1, 2, 5, 10, 20, 50, 100) if value <= upper]
    axis.xaxis.set_major_locator(FixedLocator(ticks))
    axis.xaxis.set_major_formatter(FuncFormatter(_ratio_tick))
    axis.set_yticks(positions, labels=[WORKLOAD_LABELS[point.workload_id] for point in points])
    axis.set_xlabel("Ratio (Clifft / SymFT)")
    axis.set_title("Throughput Comparison", pad=25)
    axis.text(
        0.5,
        1.01,
        f"Clifft {report.clifft_version} / "
        f"{report.alternative_name} {report.alternative_version}",
        transform=axis.transAxes,
        ha="center",
        color="0.3",
    )
    axis.grid(axis="x", which="major", alpha=0.16)


def _throughput_tick(value: float, _position: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:g}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1_000:
        return f"{value / 1_000:g}k"
    return f"{value:g}"


def _throughput_label(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M shots/s"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k shots/s"
    return f"{value:.1f} shots/s"


def _plot_throughput(axis: Any, report: Report) -> None:
    points = sorted(
        report.throughput_points,
        key=lambda point: point.attempted_shots_per_second,
    )
    rates = [point.attempted_shots_per_second for point in points]
    positions = list(range(len(points)))
    axis.scatter(rates, positions, color="#0072B2", edgecolor="white", s=88, zorder=3)
    for position, rate in zip(positions, rates, strict=True):
        axis.annotate(
            _throughput_label(rate),
            (rate, position),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=10.5,
            fontweight="bold",
        )

    from matplotlib.ticker import FixedLocator, FuncFormatter

    lower_power = math.floor(math.log10(min(rates)))
    upper_power = math.ceil(math.log10(max(rates)))
    ticks = [10**power for power in range(lower_power, upper_power + 1)]
    axis.set_xscale("log")
    axis.set_xlim(10 ** (lower_power - 0.15), 10 ** (upper_power + 0.3))
    axis.xaxis.set_major_locator(FixedLocator(ticks))
    axis.xaxis.set_major_formatter(FuncFormatter(_throughput_tick))
    axis.set_yticks(positions, labels=[WORKLOAD_LABELS[point.workload_id] for point in points])
    axis.set_xlabel("Attempted shots per second")
    axis.set_title("Clifft Throughput", pad=25)
    axis.text(
        0.5,
        1.01,
        f"Clifft {report.clifft_version}, current release configuration",
        transform=axis.transAxes,
        ha="center",
        color="0.3",
    )
    axis.grid(axis="x", which="major", alpha=0.16)


def render(report: Report, output_dir: Path, write_pdf: bool = False) -> list[Path]:
    try:
        import matplotlib
    except ModuleNotFoundError as error:
        raise RuntimeError("install the report extra to render figures") from error
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "axes.titlesize": 16,
            "axes.labelsize": 12.5,
            "font.size": 11,
            "grid.linewidth": 0.8,
            "pdf.fonttype": 42,
            "xtick.labelsize": 10.5,
            "ytick.labelsize": 10.5,
        }
    )
    figures = []
    history, axis = plt.subplots(figsize=(8.3, 5), constrained_layout=True)
    _plot_history(axis, report)
    figures.append((history, output_dir / "clifft-performance-over-time"))
    relative, axis = plt.subplots(figsize=(8.3, 5.6), constrained_layout=True)
    _plot_relative(axis, report)
    figures.append((relative, output_dir / "clifft-vs-symft"))
    throughput, axis = plt.subplots(figsize=(8.3, 5.4), constrained_layout=True)
    _plot_throughput(axis, report)
    figures.append((throughput, output_dir / "clifft-throughput"))

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for figure, base in figures:
        png = base.with_suffix(".png")
        figure.savefig(png, bbox_inches="tight", dpi=200)
        outputs.append(png)
        if write_pdf:
            pdf = base.with_suffix(".pdf")
            figure.savefig(pdf, bbox_inches="tight")
            outputs.append(pdf)
        plt.close(figure)
    return outputs


@dataclass(frozen=True)
class WebTheme:
    name: str
    foreground: str
    muted: str
    grid: str
    blue: str
    orange: str
    green: str
    purple: str
    red: str


WEB_THEMES = (
    WebTheme("light", "#172033", "#64748B", "#CBD5E1", "#3C64B4", "#C26713",
             "#167568", "#8750A6", "#B33D50"),
    WebTheme("dark", "#E6EDF7", "#AAB6C8", "#526077", "#83A7F2", "#F2A65A",
             "#66CCB6", "#C8A0E5", "#F28C9B"),
)
WEB_QEC_FIGURES = (
    "clifft-throughput",
    "simulator-throughput",
    "clifft-vs-symft",
    "performance-over-time",
    "v012-vs-v011",
    "draper-adder",
)


def web_output_paths(output_dir: Path) -> tuple[Path, ...]:
    return tuple(
        output_dir / f"{figure}-{theme.name}.png"
        for figure in WEB_QEC_FIGURES
        for theme in WEB_THEMES
    )


def _configure_web(theme: WebTheme, plt: Any) -> None:
    plt.rcParams.update(
        {
            "axes.edgecolor": theme.muted,
            "axes.labelcolor": theme.foreground,
            "axes.labelsize": 12,
            "axes.titlecolor": theme.foreground,
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "savefig.facecolor": "none",
            "text.color": theme.foreground,
            "xtick.color": theme.muted,
            "xtick.labelsize": 10.5,
            "ytick.color": theme.foreground,
            "ytick.labelsize": 11.5,
        }
    )


def _clean_web_axis(axis: Any, theme: WebTheme, *, grid_axis: str = "x") -> None:
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.tick_params(axis="both", length=0)
    axis.grid(axis=grid_axis, color=theme.grid, linewidth=0.8, alpha=0.48)
    axis.set_axisbelow(True)


def _web_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and parts[2] == "0":
        return ".".join(parts[:2])
    return version


def _web_ratio_label(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}x"
    if value >= 10:
        return f"{value:.1f}x"
    return f"{value:.2f}x"


def _plot_web_ratios(
    plt: Any,
    theme: WebTheme,
    *,
    points: list[tuple[str, float, bool]],
    output: Path,
    xlabel: str,
    ticks: tuple[float, ...],
) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

    points.sort(key=lambda point: point[1])
    figure, axis = plt.subplots(figsize=(9.6, 4.5))
    positions = list(range(len(points)))
    maximum = max(point[1] for point in points)
    upper = maximum * 1.55

    axis.axvline(1, color=theme.muted, linewidth=1.2, linestyle=(0, (3, 3)))
    for position, (_workload, ratio, packed) in zip(positions, points, strict=True):
        axis.plot(
            [1, ratio],
            [position, position],
            color=theme.blue,
            linewidth=4.5,
            alpha=0.32,
            solid_capstyle="round",
        )
        axis.scatter(
            ratio,
            position,
            s=88,
            facecolor=theme.blue if packed else "none",
            edgecolor=theme.blue,
            linewidth=2,
            zorder=3,
        )
        axis.annotate(
            _web_ratio_label(ratio),
            (ratio, position),
            xytext=(9, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=theme.foreground,
        )

    axis.set_xscale("log")
    axis.set_xlim(0.88, upper)
    axis.xaxis.set_major_locator(FixedLocator([tick for tick in ticks if tick <= upper]))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:g}x"))
    axis.xaxis.set_minor_formatter(NullFormatter())
    axis.set_yticks(
        positions,
        labels=[WORKLOAD_LABELS[workload] for workload, _ratio, _packed in points],
    )
    axis.set_xlabel(xlabel, labelpad=12)
    _clean_web_axis(axis, theme)
    figure.subplots_adjust(left=0.24, right=0.98, top=0.96, bottom=0.17)
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def _plot_web_tool_comparison(
    plt: Any, theme: WebTheme, report: Report, output: Path
) -> None:
    current = _web_version(report.clifft_version)
    alternative = _web_version(report.alternative_version)
    _plot_web_ratios(
        plt,
        theme,
        points=[
            (point.workload_id, point.clifft_over_alternative, True)
            for point in report.relative_points
        ],
        output=output,
        xlabel=(
            f"Clifft v{current} throughput relative to "
            f"{report.alternative_name} v{alternative}"
        ),
        ticks=(1, 2, 5, 10, 20, 50, 100),
    )


def _plot_web_release_comparison(
    plt: Any, theme: WebTheme, report: Report, output: Path
) -> None:
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    points = sorted(report.release_points, key=lambda point: point.current_over_previous)
    changes = [100 * (point.current_over_previous - 1) for point in points]
    extent = max(2.5, max(abs(value) for value in changes) * 1.5)
    figure, axis = plt.subplots(figsize=(9.6, 4.5))
    axis.axvline(0, color=theme.muted, linewidth=1.2, linestyle=(0, (3, 3)))
    for position, (point, change) in enumerate(zip(points, changes, strict=True)):
        axis.plot([0, change], [position, position], color=theme.blue, linewidth=4.5, alpha=0.32)
        axis.scatter(
            change, position, s=88, edgecolor=theme.blue,
            facecolor=theme.blue if point.current_packed else "none", linewidth=2, zorder=3,
        )
        axis.annotate(
            f"{change:+.2f}%", (change, position),
            xytext=(9 if change >= 0 else -9, 0), textcoords="offset points",
            ha="left" if change >= 0 else "right", va="center", fontweight="bold",
        )
    axis.set_xlim(-extent, extent)
    axis.set_ylim(-0.5, len(points) - 0.5)
    axis.xaxis.set_major_locator(MaxNLocator(nbins=5))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:+g}%"))
    axis.set_yticks(range(len(points)), labels=[WORKLOAD_LABELS[p.workload_id] for p in points])
    axis.set_xlabel(
        f"Clifft v{_web_version(report.clifft_version)} throughput change "
        f"from v{_web_version(report.previous_clifft_version)}", labelpad=12,
    )
    _clean_web_axis(axis, theme)
    figure.text(
        0.24, 0.035,
        "Filled: packed batch   ·   Open: scalar   ·   Prepared-basis adder shown separately",
        fontsize=8.5, color=theme.muted,
    )
    figure.subplots_adjust(left=0.24, right=0.98, top=0.96, bottom=0.22)
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def _web_throughput_label(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M/s"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k/s"
    return f"{value:.1f}/s"


def _plot_web_throughput(
    plt: Any, theme: WebTheme, report: Report, output: Path
) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter

    points = sorted(
        report.throughput_points,
        key=lambda point: point.attempted_shots_per_second,
    )
    rates = [point.attempted_shots_per_second for point in points]
    lower_power = math.floor(math.log10(min(rates)))
    upper_power = math.ceil(math.log10(max(rates)))
    lower = 10 ** (lower_power - 0.15)
    upper = 10 ** (upper_power + 0.38)
    positions = list(range(len(points)))
    figure, axis = plt.subplots(figsize=(9.6, 4.5))
    for position, rate in zip(positions, rates, strict=True):
        axis.plot(
            [lower, rate],
            [position, position],
            color=theme.blue,
            linewidth=4.5,
            alpha=0.32,
            solid_capstyle="round",
        )
        axis.scatter(rate, position, color=theme.blue, s=88, zorder=3)
        axis.annotate(
            _web_throughput_label(rate),
            (rate, position),
            xytext=(9, 0),
            textcoords="offset points",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=theme.foreground,
        )
    axis.set_xscale("log")
    axis.set_xlim(lower, upper)
    axis.xaxis.set_major_locator(
        FixedLocator([10**power for power in range(lower_power, upper_power + 1)])
    )
    axis.xaxis.set_major_formatter(FuncFormatter(_throughput_tick))
    axis.set_yticks(positions, labels=[WORKLOAD_LABELS[point.workload_id] for point in points])
    axis.set_xlabel(
        f"Clifft v{_web_version(report.clifft_version)} attempted shots per second",
        labelpad=12,
    )
    _clean_web_axis(axis, theme)
    figure.subplots_adjust(left=0.24, right=0.98, top=0.96, bottom=0.17)
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def _plot_web_history(plt: Any, theme: WebTheme, report: Report, output: Path) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter

    positions = list(range(len(report.history.versions)))
    medians = report.history.medians
    symbolic_index = report.history.versions.index("0.8.0")
    packing_index = report.history.versions.index("0.10.0")
    scheduling_index = report.history.versions.index("0.11.0")
    figure, axis = plt.subplots(figsize=(9.6, 3.8))
    axis.axhline(1, color=theme.muted, linewidth=1.2, linestyle=(0, (3, 3)))
    axis.plot(
        positions,
        medians,
        color=theme.blue,
        linewidth=3.5,
        marker="o",
        markersize=6.5,
        solid_capstyle="round",
        zorder=3,
    )
    axis.fill_between(
        positions, 1, medians, color=theme.blue, alpha=0.09, linewidth=0, zorder=1
    )
    axis.annotate(
        f"{medians[-1]:.0f}x median speedup",
        (positions[-1], medians[-1]),
        xytext=(0, 18),
        textcoords="offset points",
        ha="right",
        color=theme.blue,
        fontsize=11.5,
        fontweight="bold",
        zorder=4,
    )
    axis.annotate(
        "symbolic plans",
        (positions[symbolic_index], medians[symbolic_index]),
        xytext=(0, 28),
        textcoords="offset points",
        ha="center",
        color=theme.muted,
        fontsize=10,
        arrowprops={"arrowstyle": "-", "color": theme.grid, "linewidth": 1},
    )
    axis.annotate(
        "packing + compiler",
        (positions[packing_index], medians[packing_index]),
        xytext=(-72, 20),
        textcoords="offset points",
        ha="center",
        color=theme.muted,
        fontsize=10,
        arrowprops={"arrowstyle": "-", "color": theme.grid, "linewidth": 1},
    )
    axis.annotate(
        "scheduling\n+ sampling",
        (positions[scheduling_index], medians[scheduling_index]),
        xytext=(8, -65),
        textcoords="offset points",
        ha="right",
        color=theme.muted,
        fontsize=10,
        arrowprops={"arrowstyle": "-", "color": theme.grid, "linewidth": 1},
    )
    axis.set_yscale("log", base=2)
    upper = max(10.5, max(medians) * 1.6)
    axis.set_ylim(0.72, upper)
    axis.yaxis.set_major_locator(
        FixedLocator([2**power for power in range(math.floor(math.log2(upper)) + 1)])
    )
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _position: f"{value:g}x"))
    axis.set_xticks(
        positions,
        labels=[f"v{version}" for version in report.history.versions],
    )
    axis.set_ylabel("Median speedup vs v0.1")
    _clean_web_axis(axis, theme, grid_axis="y")
    figure.subplots_adjust(left=0.11, right=0.98, top=0.92, bottom=0.18)
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def _plot_web_combined_throughput(
    plt: Any, theme: WebTheme, report: Report, output: Path
) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FixedLocator, FuncFormatter

    points = sorted(report.tool_throughput_points, key=lambda point: point.rates["Clifft"])
    rates = [rate for point in points for rate in point.rates.values()]
    lower_power = math.floor(math.log10(min(rates)))
    upper_power = math.ceil(math.log10(max(rates)))
    lower = 10 ** (lower_power - 0.1)
    # Reserve room after the fastest marker for its absolute-rate label.
    upper = max(rates) * 9
    positions = list(range(len(points)))
    figure, axis = plt.subplots(figsize=(9.6, 7))
    styles = (
        ("Clifft", 0.30, theme.blue, "o"),
        ("SymFT", 0.10, theme.orange, "s"),
        ("xtim", -0.10, theme.green, "D"),
        ("Stim", -0.30, theme.purple, "^"),
    )
    has_tsim = "Tsim" in report.tool_versions
    if has_tsim:
        styles = (
            ("Clifft", 0.34, theme.blue, "o"),
            ("SymFT", 0.17, theme.orange, "s"),
            ("xtim", 0, theme.green, "D"),
            ("Stim", -0.17, theme.purple, "^"),
            ("Tsim", -0.34, theme.red, "v"),
        )

    for position, point in zip(positions, points, strict=True):
        for tool, offset, color, marker in styles:
            if tool in point.failures:
                axis.text(
                    0.01, position + offset, point.failures[tool],
                    transform=axis.get_yaxis_transform(), va="center", color=color, fontsize=8,
                )
                continue
            if tool not in point.rates:
                continue
            rate = point.rates[tool]
            y = position + offset
            axis.plot(
                [lower, rate],
                [y, y],
                color=color,
                linewidth=2.2,
                alpha=0.32,
                solid_capstyle="round",
            )
            axis.scatter(rate, y, color=color, marker=marker, s=52, zorder=3)
            axis.annotate(
                _web_throughput_label(rate),
                (rate, y),
                xytext=(8, 0),
                textcoords="offset points",
                va="center",
                color=color,
                fontsize=9,
                fontweight="bold",
            )
        if position > 0:
            axis.axhline(position - 0.5, color=theme.grid, linewidth=0.6, alpha=0.35)

    axis.set_xscale("log")
    axis.set_xlim(lower, upper)
    axis.set_ylim(-0.5, len(points) - 0.5)
    axis.xaxis.set_major_locator(
        FixedLocator([10**power for power in range(lower_power, upper_power + 1)])
    )
    axis.xaxis.set_major_formatter(FuncFormatter(_throughput_tick))
    axis.set_yticks(positions, labels=[WORKLOAD_LABELS[point.workload_id] for point in points])
    axis.set_xlabel("Attempted shots per second", labelpad=12)
    axis.legend(
        handles=[
            Line2D(
                [], [], color=color, marker=marker, linewidth=2.2, markersize=5.5,
                label=f"{tool} v{_web_version(report.tool_versions[tool])}",
            ) for tool, _offset, color, marker in styles
        ],
        loc="lower left",
        bbox_to_anchor=(0, 1.035),
        frameon=False,
        ncols=3 if has_tsim else 4,
        fontsize=9,
        handletextpad=0.6,
        columnspacing=1.4,
        borderaxespad=0,
    )
    _clean_web_axis(axis, theme)
    figure.text(
        0.24, 0.95 if has_tsim else 0.935,
        "One pinned CPU  ·  Calibrated batching  ·  Median of five ≥30 s samples",
        fontsize=8.5, color=theme.muted,
    )
    figure.text(
        0.24, 0.035,
        "xtim: each sample starts with an empty cache; lazy plan construction is timed.\n"
        "Absent markers: no measurement in this campaign. Rates count attempted shots."
        + ("\nTsim: CPU active_statevector. Failed cases labelled; details in cases.csv."
           if has_tsim else ""),
        fontsize=8.5, color=theme.muted, linespacing=1.6,
    )
    figure.subplots_adjust(
        left=0.24, right=0.98, top=0.82 if has_tsim else 0.87,
        bottom=0.21 if has_tsim else 0.17,
    )
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def _plot_web_adder(plt: Any, theme: WebTheme, report: Report, output: Path) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter

    adder = report.adder
    figure, axis = plt.subplots(figsize=(9.6, 4.5))
    figure.text(0.06, 0.93, "16-bit Draper adder", fontsize=17, fontweight="bold")
    figure.text(
        0.06, 0.865, "32 qubits  ·  Prepared basis input  ·  No noise or postselection",
        fontsize=10, color=theme.muted,
    )
    figure.text(
        0.96, 0.92, f"{adder.current_over_previous:,.0f}×", fontsize=23,
        color=theme.blue, ha="right", fontweight="bold",
    )
    figure.text(0.96, 0.865, "throughput gain", fontsize=10, ha="right", color=theme.muted)
    rates = (adder.previous_rate, adder.current_rate)
    lower_power = math.floor(math.log10(min(rates)))
    upper_power = math.ceil(math.log10(max(rates)))
    lower = 10 ** (lower_power - 0.1)
    for position, (rate, color) in enumerate(zip(rates, (theme.orange, theme.blue), strict=True)):
        axis.plot([lower, rate], [position, position], color=color, linewidth=4.5, alpha=0.32)
        axis.scatter(rate, position, color=color, s=88, zorder=3)
        axis.annotate(
            _web_throughput_label(rate), (rate, position), xytext=(9, 0),
            textcoords="offset points", va="center", fontweight="bold", color=color,
        )
    axis.set_xscale("log")
    axis.set_xlim(lower, max(rates) * 15)
    axis.set_ylim(-0.6, 1.6)
    axis.xaxis.set_major_locator(FixedLocator(
        [10**power for power in range(lower_power, upper_power + 1, 2)]
    ))
    axis.xaxis.set_major_formatter(FuncFormatter(_throughput_tick))
    axis.set_yticks((0, 1), labels=(
        f"Clifft v{_web_version(report.previous_clifft_version)}",
        f"Clifft v{_web_version(report.clifft_version)}",
    ))
    axis.set_xlabel("Attempted shots per second", labelpad=10)
    _clean_web_axis(axis, theme)
    figure.subplots_adjust(left=0.21, right=0.98, top=0.77, bottom=0.39)
    for x, label, value in (
        (0.21, "Compile time", f"{adder.previous_compile_seconds * 1000:.2f} ms → "
         f"{adder.current_compile_seconds * 1000:.2f} ms"),
        (0.65, "Peak active width", f"{adder.previous_peak_active_width} → "
         f"{adder.current_peak_active_width} qubits"),
    ):
        figure.text(x, 0.19, label, fontsize=10, color=theme.muted)
        figure.text(x, 0.12, value, fontsize=13, fontweight="bold")
    figure.text(
        0.06, 0.03,
        "Fixed basis inputs: 37449 + 18724. Compilation is measured separately from sampling.",
        fontsize=8.5, color=theme.muted,
    )
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def render_web(report: Report, output_dir: Path) -> list[Path]:
    try:
        import matplotlib
    except ModuleNotFoundError as error:
        raise RuntimeError("install the report extra to render figures") from error
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    for theme in WEB_THEMES:
        _configure_web(theme, plt)
        suffix = theme.name
        _plot_web_throughput(
            plt, theme, report, output_dir / f"clifft-throughput-{suffix}.png"
        )
        _plot_web_tool_comparison(
            plt, theme, report, output_dir / f"clifft-vs-symft-{suffix}.png"
        )
        _plot_web_history(
            plt, theme, report, output_dir / f"performance-over-time-{suffix}.png"
        )
        _plot_web_release_comparison(
            plt, theme, report, output_dir / f"v012-vs-v011-{suffix}.png"
        )
        _plot_web_combined_throughput(
            plt, theme, report, output_dir / f"simulator-throughput-{suffix}.png"
        )
        _plot_web_adder(
            plt, theme, report, output_dir / f"draper-adder-{suffix}.png"
        )
    return list(web_output_paths(output_dir))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--style", choices=("paper", "web"), default="paper")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.style == "web" and args.pdf:
        parser.error("--pdf is available only with --style paper")
    report = build_report(args.sources.resolve())
    print(f"history execution: {report.history_execution}")
    print(f"release chain: {', '.join(report.release_executions)}")
    print(f"QV execution: {report.qv_execution}")
    print(f"releases: {' -> '.join(report.history.versions)}")
    if not args.check:
        default_output = DEFAULT_WEB_OUTPUT_DIR if args.style == "web" else DEFAULT_OUTPUT_DIR
        output_dir = (args.output_dir or default_output).resolve()
        outputs = (
            render_web(report, output_dir)
            if args.style == "web"
            else render(report, output_dir, args.pdf)
        )
        for path in outputs:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
