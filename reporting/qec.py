#!/usr/bin/env python3
"""Generate release-history and current-tool QEC throughput figures."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "reporting/sources.json"
DEFAULT_OUTPUT_DIR = ROOT / "reporting/figures"
CURRENT_COMPARISON = "current-vs-previous"
ALTERNATIVES_COMPARISON = "alternatives-vs-current"

WORKLOAD_LABELS = {
    "coherent-surface-d3-r1-p1e-3-rz2e-2": "Coherent d3, r1",
    "coherent-surface-d3-r3-p1e-3-rz2e-2": "Coherent d3, r3",
    "coherent-surface-d5-r1-p1e-3-rz2e-2": "Coherent d5, r1",
    "coherent-surface-d5-r5-p1e-3-rz2e-2": "Coherent d5, r5",
    "distillation-color-code-85q-p5e-2": "85q distillation",
    "msc-d3-inject-cultivate-p1e-3": "Cultivation d3",
    "msc-d5-inject-cultivate-p1e-3": "Cultivation d5",
    "surface-code-d7-r7-p1e-3": "Surface code d7, r7",
}
WORKLOAD_ORDER = tuple(WORKLOAD_LABELS)


@dataclass(frozen=True)
class ReleaseData:
    execution_id: str
    previous_version: str
    current_version: str
    current_ratios: dict[str, float]
    current_rates: dict[str, float]
    alternative_name: str
    alternative_version: str
    alternative_ratios: dict[str, float]
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
class Report:
    history: HistorySeries
    relative_points: tuple[RelativePoint, ...]
    throughput_points: tuple[ThroughputPoint, ...]
    clifft_version: str
    alternative_name: str
    alternative_version: str
    history_execution: str
    release_executions: tuple[str, ...]


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
        grouped[tuple(row[field] for field in key_fields)].append(float(row[value_field]))
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
    rows: list[dict[str, str]], comparison_id: str, execution_id: str
) -> list[dict[str, str]]:
    selected = [row for row in rows if row["comparison_id"] == comparison_id]
    if {row["workload_id"] for row in selected} != set(WORKLOAD_ORDER):
        raise ValueError(f"{execution_id} {comparison_id} does not cover the reporting core")
    if any(
        row["baseline_shots_per_call"] != row["candidate_shots_per_call"]
        for row in selected
    ):
        raise ValueError(f"{execution_id} {comparison_id} has unequal shots_per_call")
    return selected


def _load_release(path: Path) -> ReleaseData:
    index = json.loads((path / "index.json").read_text())
    execution_id = str(index["execution_id"])
    if execution_id != path.name or index["campaign_id"] != "release-v1":
        raise ValueError(f"release identity mismatch in {path}")
    rows = _read_csv(path / "comparisons.csv")
    current = _select_comparison(rows, CURRENT_COMPARISON, execution_id)
    alternatives = _select_comparison(rows, ALTERNATIVES_COMPARISON, execution_id)

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
    return ReleaseData(
        execution_id=execution_id,
        previous_version=previous_version,
        current_version=current_version,
        current_ratios={key[0]: value for key, value in current_ratios.items()},
        current_rates={key[0]: value for key, value in current_rates.items()},
        alternative_name=_one(
            alternatives, "candidate_simulator_name", "alternative simulator"
        ),
        alternative_version=_one(
            alternatives,
            "candidate_simulator_display_version",
            "alternative simulator version",
        ),
        alternative_ratios={key[0]: value for key, value in alternative_ratios.items()},
        shots_per_call=_single_ints(current, "baseline_shots_per_call", "shots_per_call"),
    )


def _load_sources(path: Path) -> tuple[Path, tuple[Path, ...]]:
    document = json.loads(path.read_text())
    history = ROOT / document["history_execution"]
    releases = tuple(ROOT / value for value in document["release_executions"])
    if not releases:
        raise ValueError("reporting requires at least one recurring release execution")
    return history, releases


def build_report(sources_path: Path = DEFAULT_SOURCES) -> Report:
    history_path, release_paths = _load_sources(sources_path)
    history_rows = _read_csv(history_path / "cases.csv")
    if any(row["status"] != "success" for row in history_rows):
        raise ValueError(f"history execution {history_path.name} contains failed cases")
    history_versions = tuple(
        dict.fromkeys(row["simulator_display_version"] for row in history_rows)
    )
    history_rates = _median_rates(
        history_rows,
        ("workload_id", "simulator_display_version"),
        "median_attempted_shots_per_second",
    )
    history_shots = _single_ints(history_rows, "shots_per_call", "historical shots_per_call")
    releases = tuple(_load_release(path) for path in release_paths)
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
    latest = releases[-1]
    points = tuple(
        RelativePoint(
            workload,
            1 / latest.alternative_ratios[workload],
        )
        for workload in WORKLOAD_ORDER
    )
    throughput_points = tuple(
        ThroughputPoint(workload, latest.current_rates[workload])
        for workload in WORKLOAD_ORDER
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
        latest.current_version,
        latest.alternative_name,
        latest.alternative_version,
        history_path.name,
        tuple(release.execution_id for release in releases),
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(args.sources.resolve())
    print(f"history execution: {report.history_execution}")
    print(f"release chain: {', '.join(report.release_executions)}")
    print(f"releases: {' -> '.join(report.history.versions)}")
    if not args.check:
        for path in render(report, args.output_dir.resolve(), args.pdf):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
