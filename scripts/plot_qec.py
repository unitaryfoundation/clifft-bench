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
from matplotlib.ticker import FuncFormatter  # noqa: E402

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
    "coherent-surface-d3-r1-p1e-3-rz2e-2": "coherent d3, r1",
    "coherent-surface-d3-r3-p1e-3-rz2e-2": "coherent d3, r3",
    "coherent-surface-d5-r1-p1e-3-rz2e-2": "coherent d5, r1",
    "coherent-surface-d5-r5-p1e-3-rz2e-2": "coherent d5, r5",
    "distillation-color-code-85q-p5e-2": "85q distillation",
    "msc-d3-inject-cultivate-p1e-3": "cultivation d3",
    "msc-d5-inject-cultivate-p1e-3": "cultivation d5",
    "surface-code-d7-r7-p1e-3": "surface code d7, r7",
}

ANNOTATIONS = {
    "coherent-surface-d5-r5-p1e-3-rz2e-2": (8, -2),
    "msc-d5-inject-cultivate-p1e-3": (8, 8),
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


def _rate_tick(value: float, _position: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1_000:
        return f"{value / 1_000:g}k"
    return f"{value:g}"


def _plot_current_tools(axis: Axes, rows: list[dict[str, str]]) -> None:
    rates = _median_rates(rows)
    points: list[tuple[str, float, float, str]] = []
    for workload in WORKLOAD_ORDER:
        clifft_rate = rates[(workload, "clifft-0.9.0")]
        symft_rate, symft_run = max(
            (rate, run)
            for (candidate_workload, run), rate in rates.items()
            if candidate_workload == workload and run.startswith("symft-")
        )
        points.append((workload, symft_rate, clifft_rate, symft_run))

    all_rates = [value for _, x, y, _ in points for value in (x, y)]
    lower = min(all_rates) / 1.8
    upper = max(all_rates) * 1.8
    axis.plot([lower, upper], [lower, upper], color="0.35", linestyle="--", label="equal")

    for run_id, style in SYMFT_STYLES.items():
        selected = [point for point in points if point[3] == run_id]
        axis.scatter(
            [point[1] for point in selected],
            [point[2] for point in selected],
            color=str(style["color"]),
            edgecolor="white",
            linewidth=0.5,
            label=f"SymFT {style['label']}",
            marker=str(style["marker"]),
            s=48,
            zorder=3,
        )

    for workload, symft_rate, clifft_rate, _run_id in points:
        if workload not in ANNOTATIONS:
            continue
        axis.annotate(
            WORKLOAD_LABELS[workload],
            (symft_rate, clifft_rate),
            textcoords="offset points",
            xytext=ANNOTATIONS[workload],
            fontsize=8,
        )

    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlim(lower, upper)
    axis.set_ylim(lower, upper)
    axis.set_box_aspect(1)
    axis.xaxis.set_major_formatter(FuncFormatter(_rate_tick))
    axis.yaxis.set_major_formatter(FuncFormatter(_rate_tick))
    axis.set_xlabel("Fastest measured SymFT attempted shots/s")
    axis.set_ylabel("Clifft 0.9 attempted shots/s")
    axis.set_title("Current single-core QEC throughput")
    axis.text(
        0.04,
        0.96,
        "Above line: Clifft faster",
        transform=axis.transAxes,
        va="top",
        fontsize=8,
    )
    axis.text(
        0.96,
        0.31,
        "Below line: SymFT faster",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
    )
    axis.legend(loc="lower right", title="Fastest SymFT mode", fontsize=8, title_fontsize=8)


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
            color="0.55",
            linewidth=1.0,
            marker="o",
            markersize=2.5,
            alpha=0.65,
            label="Individual workload" if index == 0 else None,
        )

    medians = [
        statistics.median(values[index] for values in speedups.values())
        for index in range(len(HISTORY_RUNS))
    ]
    axis.plot(
        releases,
        medians,
        color="#0072B2",
        linewidth=2.0,
        marker="o",
        markersize=4,
        label="Median",
        zorder=3,
    )

    axis.axhline(1, color="0.35", linestyle="--", linewidth=1)
    axis.set_yscale("log")
    axis.set_xlabel("Clifft release")
    axis.set_ylabel("Speedup over Clifft 0.1.0")
    axis.set_title("Clifft throughput across releases")
    axis.legend(loc="upper left", fontsize=8)


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
            "axes.grid": True,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "font.size": 9,
            "grid.alpha": 0.25,
            "legend.framealpha": 0.9,
            "pdf.fonttype": 42,
        }
    )

    current_figure, current_axis = plt.subplots(figsize=(6.0, 5.4), constrained_layout=True)
    _plot_current_tools(current_axis, _read_successes(current_path))
    current_figure.text(
        0.5,
        -0.01,
        "Medians of 3 placements on one pinned AWS m7a.xlarge core; marker shows the "
        "fastest applicable SymFT mode.",
        ha="center",
        fontsize=8,
    )

    history_figure, history_axis = plt.subplots(figsize=(6.4, 4.4), constrained_layout=True)
    _plot_history(history_axis, _read_successes(history_path))
    history_figure.text(
        0.5,
        -0.01,
        "One placement on the single-core AWS m7a.xlarge hardware epoch.",
        ha="center",
        fontsize=8,
    )

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
