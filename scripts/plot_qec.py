#!/usr/bin/env python3
"""Plot the current QEC tool comparison and Clifft release history."""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CURRENT_EXECUTION = ROOT / "results/current-tools-v1/current-tools-v1-20260824-r1"
HISTORY_EXECUTION = ROOT / "results/clifft-history-v1/clifft-history-v1-20260825-r1"

WORKLOAD_ORDER = [
    "coherent-surface-d3-r1-p1e-3-rz2e-2",
    "coherent-surface-d3-r3-p1e-3-rz2e-2",
    "coherent-surface-d5-r1-p1e-3-rz2e-2",
    "coherent-surface-d5-r5-p1e-3-rz2e-2",
    "distillation-color-code-85q-p5e-2",
    "msc-d3-inject-cultivate-p1e-3",
    "msc-d5-inject-cultivate-p1e-3",
    "surface-code-d7-r7-p1e-3",
]

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

SYMFT_STYLES = {
    "symft-single": {"color": "#0072B2", "label": "single", "marker": "o"},
    "symft-batch-32": {"color": "#E69F00", "label": "batch 32", "marker": "s"},
    "symft-batch-2048": {"color": "#009E73", "label": "batch 2048", "marker": "^"},
}

HISTORY_RUNS = [
    "clifft-0.1.0",
    "clifft-0.2.0",
    "clifft-0.3.0",
    "clifft-0.4.1",
    "clifft-0.5.0",
    "clifft-0.6.0",
    "clifft-0.7.0",
    "clifft-0.8.0",
    "clifft-0.9.0",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current-input",
        type=Path,
        default=CURRENT_EXECUTION / "cases.csv",
        help="Finalized current-tools cases.csv",
    )
    parser.add_argument(
        "--history-input",
        type=Path,
        default=HISTORY_EXECUTION / "cases.csv",
        help="Finalized release-history cases.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "figures",
        help="Directory for generated figures",
    )
    parser.add_argument("--pdf", action="store_true", help="Also write publication PDFs")
    return parser


def _read_successes(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    successes = [row for row in rows if row["status"] == "success"]
    if not successes:
        raise ValueError(f"no successful cases in {path}")
    return successes


def _median_rates(rows: list[dict[str, str]]) -> dict[tuple[str, str], float]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        key = (row["workload_id"], row["campaign_run_id"])
        grouped[key].append(float(row["median_attempted_shots_per_second"]))
    return {key: statistics.median(values) for key, values in grouped.items()}


def _ratio_tick(value: float, _position: float) -> str:
    return f"{value:g}x"


def _plot_current_tools(axis: Axes, rows: list[dict[str, str]]) -> None:
    rates = _median_rates(rows)
    points: list[tuple[str, float, str]] = []
    for workload in WORKLOAD_ORDER:
        clifft_rate = rates[(workload, "clifft-0.9.0")]
        symft_rate, symft_run = max(
            (rate, run)
            for (candidate_workload, run), rate in rates.items()
            if candidate_workload == workload and run.startswith("symft-")
        )
        points.append((workload, clifft_rate / symft_rate, symft_run))
    points.sort(key=lambda point: point[1])

    axis.axvline(1, color="0.35", linestyle="--", linewidth=1.4, zorder=1)
    for y_position, (_workload, ratio, _symft_run) in enumerate(points):
        axis.plot(
            [min(ratio, 1), max(ratio, 1)],
            [y_position, y_position],
            color="0.78",
            linewidth=1.4,
            zorder=1,
        )

    for run_id, style in SYMFT_STYLES.items():
        selected = [(index, point) for index, point in enumerate(points) if point[2] == run_id]
        axis.scatter(
            [point[1] for _index, point in selected],
            [index for index, _point in selected],
            color=str(style["color"]),
            edgecolor="white",
            linewidth=0.7,
            label=str(style["label"]),
            marker=str(style["marker"]),
            s=82,
            zorder=3,
        )

    for y_position, (_workload, ratio, _symft_run) in enumerate(points):
        left_of_point = ratio > 1
        axis.annotate(
            f"{ratio:.2f}x",
            (ratio, y_position),
            textcoords="offset points",
            xytext=(-8 if left_of_point else 8, 0),
            ha="right" if left_of_point else "left",
            va="center",
            fontsize=11,
            fontweight="bold",
        )

    axis.set_xscale("log")
    axis.set_xlim(0.075, 2.0)
    axis.set_ylim(-0.65, len(points) - 0.35)
    axis.set_yticks(
        range(len(points)),
        labels=[WORKLOAD_LABELS[workload] for workload, _ratio, _run in points],
    )
    ratio_ticks = [0.1, 0.2, 0.5, 1.0, 2.0]
    axis.xaxis.set_major_locator(FixedLocator(ratio_ticks))
    axis.xaxis.set_major_formatter(FuncFormatter(_ratio_tick))
    axis.set_xlabel("Throughput ratio (Clifft / fastest SymFT)")
    axis.set_title("QEC Workload Throughput Comparison")
    axis.text(
        0.02,
        0.97,
        "SymFT faster",
        transform=axis.transAxes,
        va="top",
        fontsize=10.5,
    )
    axis.text(
        0.98,
        0.97,
        "Clifft faster",
        transform=axis.transAxes,
        ha="right",
        va="top",
        fontsize=10.5,
    )
    axis.legend(
        loc="lower right",
        title="Fastest SymFT mode",
        fontsize=10.5,
        title_fontsize=10.5,
    )
    axis.grid(axis="x", which="major")


def _plot_history(axis: Axes, rows: list[dict[str, str]]) -> None:
    rates = _median_rates(rows)
    releases = [run.removeprefix("clifft-") for run in HISTORY_RUNS]
    speedups: dict[str, list[float]] = {}
    for workload in WORKLOAD_ORDER:
        baseline = rates[(workload, "clifft-0.1.0")]
        speedups[workload] = [rates[(workload, run)] / baseline for run in HISTORY_RUNS]

    for index, values in enumerate(speedups.values()):
        axis.plot(
            releases,
            values,
            color="0.52",
            linewidth=1.1,
            alpha=0.32,
            label="Workloads" if index == 0 else None,
        )

    medians = [
        statistics.median(values[index] for values in speedups.values())
        for index in range(len(HISTORY_RUNS))
    ]
    axis.plot(
        releases,
        medians,
        color="#0072B2",
        linewidth=3.0,
        marker="o",
        markersize=5.5,
        label="Median",
        zorder=3,
    )

    axis.axhline(1, color="0.35", linestyle="--", linewidth=1)
    axis.set_yscale("log")
    axis.annotate(
        f"{medians[-1]:.2f}x median",
        (releases[-1], medians[-1]),
        textcoords="offset points",
        xytext=(-8, 10),
        ha="right",
        color="#0072B2",
        fontsize=11,
        fontweight="bold",
    )
    axis.set_xlabel("Release")
    axis.set_ylabel("Speedup vs 0.1")
    axis.set_title("Clifft Throughput Across Releases")
    axis.legend(loc="upper left", fontsize=11)
    axis.grid(which="major")


def _save_figure(
    figure: Figure,
    output_base: Path,
    *,
    title: str,
    write_pdf: bool,
) -> list[Path]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    png_path = output_base.with_suffix(".png")
    outputs = [png_path]
    figure.savefig(png_path, bbox_inches="tight", dpi=200)
    if write_pdf:
        pdf_path = output_base.with_suffix(".pdf")
        figure.savefig(
            pdf_path,
            bbox_inches="tight",
            metadata={
                "Title": title,
                "Author": "Unitary Foundation",
                "CreationDate": None,
                "ModDate": None,
            },
        )
        outputs.append(pdf_path)
    plt.close(figure)
    return outputs


def plot(
    current_path: Path,
    history_path: Path,
    output_dir: Path,
    *,
    write_pdf: bool = False,
) -> list[Path]:
    plt.rcParams.update(
        {
            "axes.grid": False,
            "axes.titlesize": 16,
            "axes.labelsize": 13,
            "font.size": 11,
            "grid.alpha": 0.15,
            "grid.linewidth": 0.8,
            "legend.framealpha": 0.9,
            "pdf.fonttype": 42,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
        }
    )

    current_figure, current_axis = plt.subplots(figsize=(7.2, 5.2), constrained_layout=True)
    _plot_current_tools(current_axis, _read_successes(current_path))

    history_figure, history_axis = plt.subplots(figsize=(7.2, 4.8), constrained_layout=True)
    _plot_history(history_axis, _read_successes(history_path))

    outputs = _save_figure(
        current_figure,
        output_dir / "current-tools-v1-20260824-r1",
        title="Current single-core QEC throughput",
        write_pdf=write_pdf,
    )
    outputs.extend(
        _save_figure(
            history_figure,
            output_dir / "clifft-history-v1-20260825-r1",
            title="Clifft throughput across releases",
            write_pdf=write_pdf,
        )
    )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = plot(
        args.current_input.resolve(),
        args.history_input.resolve(),
        args.output_dir.resolve(),
        write_pdf=args.pdf,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
