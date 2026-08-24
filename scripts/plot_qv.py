#!/usr/bin/env python3
"""Plot the QV current-tool comparison and Clifft strong scaling."""

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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXECUTION = ROOT / "results/qv-multicore-v1/qv-multicore-v1-2026082"

TOOL_STYLES = {
    "clifft-0.9.0-current": {
        "color": "#0072B2",
        "label": "Clifft 0.9.0",
        "linestyle": "-",
        "marker": "o",
    },
    "qiskit-aer-current": {
        "color": "#E69F00",
        "label": "Qiskit Aer",
        "linestyle": "--",
        "marker": "s",
    },
    "qulacs-current": {
        "color": "#009E73",
        "label": "Qulacs",
        "linestyle": "-.",
        "marker": "^",
    },
    "qsim-current": {
        "color": "#D55E00",
        "label": "qsim",
        "linestyle": ":",
        "marker": "D",
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_EXECUTION / "cases.csv",
        help="Finalized QV cases.csv",
    )
    parser.add_argument(
        "--output-base",
        type=Path,
        default=ROOT / "figures/qv-multicore-v1-2026082",
        help="Output path without an extension; writes PDF and PNG",
    )
    return parser


def _read_successes(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    successes = [row for row in rows if row["status"] == "success"]
    if not successes:
        raise ValueError(f"no successful QV cases in {path}")
    return successes


def _range_stats(values: list[float]) -> tuple[float, float, float]:
    return statistics.median(values), min(values), max(values)


def _plot_current_tools(axis: Axes, rows: list[dict[str, str]]) -> None:
    grouped: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        if row["phase"] != "current-tools":
            continue
        grouped[(row["run_id"], int(row["qubits"]))].append(float(row["execution_seconds"]))

    for run_id, style in TOOL_STYLES.items():
        points = []
        for candidate_run, qubits in sorted(grouped):
            if candidate_run == run_id:
                median, minimum, maximum = _range_stats(grouped[(candidate_run, qubits)])
                points.append((qubits, median, minimum, maximum))
        if not points:
            raise ValueError(f"missing current-tool results for {run_id}")
        x = [point[0] for point in points]
        y = [point[1] for point in points]
        lower = [point[1] - point[2] for point in points]
        upper = [point[3] - point[1] for point in points]
        axis.errorbar(
            x,
            y,
            yerr=[lower, upper],
            color=str(style["color"]),
            label=str(style["label"]),
            linestyle=str(style["linestyle"]),
            marker=str(style["marker"]),
            linewidth=1.4,
            markersize=4,
            capsize=2,
            elinewidth=0.7,
        )

    axis.set_yscale("log")
    axis.set_xticks(range(6, 29, 2))
    axis.set_xlabel("Number of qubits")
    axis.set_ylabel("Execution time (s)")
    axis.set_title("(a) Current tools at 16 physical cores", loc="left")
    axis.legend(loc="upper left", ncols=2, fontsize=8)


def _plot_strong_scaling(axis: Axes, rows: list[dict[str, str]]) -> None:
    elapsed: dict[tuple[int, int, int], float] = {}
    for row in rows:
        if row["phase"] != "clifft-scaling":
            continue
        key = (int(row["qubits"]), int(row["seed"]), int(row["threads_requested"]))
        elapsed[key] = float(row["execution_seconds"])

    speedups: dict[tuple[int, int], list[float]] = defaultdict(list)
    for (qubits, seed, threads), duration in elapsed.items():
        baseline = elapsed.get((qubits, seed, 1))
        if baseline is None:
            raise ValueError(f"missing one-thread baseline for QV{qubits} seed {seed}")
        speedups[(qubits, threads)].append(baseline / duration)

    widths = sorted({qubits for qubits, _threads in speedups})
    colors = plt.colormaps["viridis"].resampled(len(widths))
    for color_index, qubits in enumerate(widths):
        points = []
        for candidate_qubits, threads in sorted(speedups):
            if candidate_qubits == qubits:
                median, minimum, maximum = _range_stats(speedups[(qubits, threads)])
                points.append((threads, median, minimum, maximum))
        x = [point[0] for point in points]
        y = [point[1] for point in points]
        lower = [point[1] - point[2] for point in points]
        upper = [point[3] - point[1] for point in points]
        axis.errorbar(
            x,
            y,
            yerr=[lower, upper],
            color=colors(color_index),
            label=f"QV{qubits}",
            marker="o",
            linewidth=1.3,
            markersize=3.5,
            capsize=2,
            elinewidth=0.7,
        )

    threads = [1, 2, 4, 8, 16]
    axis.plot(threads, threads, color="0.35", linestyle="--", linewidth=1, label="Ideal")
    axis.set_xscale("log", base=2)
    axis.set_xticks(threads, labels=[str(value) for value in threads])
    axis.set_xlim(0.85, 18.5)
    axis.set_ylim(0.5, 16.7)
    axis.set_xlabel("Physical cores")
    axis.set_ylabel("Speedup over 1 core")
    axis.set_title("(b) Clifft 0.9.0 strong scaling", loc="left")
    axis.legend(loc="upper left", ncols=2, fontsize=8)


def plot(input_path: Path, output_base: Path) -> tuple[Path, Path]:
    rows = _read_successes(input_path)
    plt.rcParams.update(
        {
            "axes.grid": True,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "font.size": 9,
            "grid.alpha": 0.25,
            "legend.framealpha": 0.9,
            "pdf.fonttype": 42,
        }
    )
    figure, axes = plt.subplots(1, 2, figsize=(9.2, 3.8), constrained_layout=True)
    _plot_current_tools(axes[0], rows)
    _plot_strong_scaling(axes[1], rows)
    figure.suptitle("Single-shot Quantum Volume performance on AWS c8i.8xlarge", fontsize=11)
    figure.text(
        0.5,
        -0.01,
        "Exploratory curated execution; median of 3 seeds; whiskers show the seed range.",
        ha="center",
        fontsize=8,
    )

    output_base.parent.mkdir(parents=True, exist_ok=True)
    pdf_path = output_base.with_suffix(".pdf")
    png_path = output_base.with_suffix(".png")
    figure.savefig(
        pdf_path,
        bbox_inches="tight",
        metadata={
            "Title": "QV multicore performance",
            "Author": "Unitary Foundation",
            "CreationDate": None,
            "ModDate": None,
        },
    )
    figure.savefig(png_path, bbox_inches="tight", dpi=200)
    plt.close(figure)
    return pdf_path, png_path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pdf_path, png_path = plot(args.input.resolve(), args.output_base.resolve())
    print(pdf_path)
    print(png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
