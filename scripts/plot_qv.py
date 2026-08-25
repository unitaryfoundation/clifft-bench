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
from matplotlib.figure import Figure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXECUTION = ROOT / "results/qv-multicore-v1/qv-multicore-v1-2026082"

TOOL_STYLES = {
    "clifft-0.9.0-current": {
        "color": "#0072B2",
        "label": "Clifft 0.9",
        "label_offset": -8,
        "linestyle": "-",
        "marker": "o",
        "primary": True,
    },
    "qiskit-aer-current": {
        "color": "#E69F00",
        "label": "Qiskit Aer",
        "label_offset": 8,
        "linestyle": "--",
        "marker": "s",
        "primary": False,
    },
    "qulacs-current": {
        "color": "#009E73",
        "label": "Qulacs",
        "label_offset": 0,
        "linestyle": "-.",
        "marker": "^",
        "primary": False,
    },
    "qsim-current": {
        "color": "#D55E00",
        "label": "qsim",
        "label_offset": 0,
        "linestyle": ":",
        "marker": "D",
        "primary": False,
    },
}

SCALING_LABEL_OFFSETS = {
    18: 0,
    20: 0,
    22: -22,
    24: 2,
    26: 0,
    28: 22,
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
        help="Output path without an extension; writes PNG by default",
    )
    parser.add_argument("--pdf", action="store_true", help="Also write a publication PDF")
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
                median = statistics.median(grouped[(candidate_run, qubits)])
                points.append((qubits, median))
        if not points:
            raise ValueError(f"missing current-tool results for {run_id}")
        x = [point[0] for point in points]
        y = [point[1] for point in points]
        axis.plot(
            x,
            y,
            color=str(style["color"]),
            linestyle=str(style["linestyle"]),
            marker=str(style["marker"]),
            linewidth=3.0 if style["primary"] else 1.8,
            markersize=6 if style["primary"] else 5,
            alpha=1.0 if style["primary"] else 0.72,
            zorder=3 if style["primary"] else 2,
        )
        axis.annotate(
            str(style["label"]),
            (x[-1], y[-1]),
            textcoords="offset points",
            xytext=(8, int(style["label_offset"])),
            va="center",
            color=str(style["color"]),
            fontsize=11,
            fontweight="bold" if style["primary"] else "normal",
        )

    axis.set_yscale("log")
    axis.set_xticks(range(6, 29, 2))
    axis.set_xlim(5.0, 31.0)
    axis.set_xlabel("Qubits")
    axis.set_ylabel("Execution time (s)")
    axis.set_title("Quantum Volume Execution Time by Simulator")
    axis.grid(which="major")


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
        color = "#0072B2" if qubits == 24 else colors(color_index)
        axis.errorbar(
            x,
            y,
            yerr=[lower, upper],
            color=color,
            marker="o",
            linewidth=3.0 if qubits == 24 else 1.6,
            markersize=6 if qubits == 24 else 4.5,
            capsize=2,
            elinewidth=0.7,
            alpha=1.0 if qubits == 24 else 0.72,
            zorder=3 if qubits == 24 else 2,
        )
        offset = SCALING_LABEL_OFFSETS[qubits]
        axis.annotate(
            f"QV{qubits}",
            (x[-1], y[-1]),
            textcoords="offset points",
            xytext=(10, offset),
            va="center",
            color=color,
            fontsize=11,
            fontweight="bold" if qubits == 24 else "normal",
            arrowprops=(
                {
                    "arrowstyle": "-",
                    "color": color,
                    "linewidth": 0.8,
                    "alpha": 0.7,
                }
                if offset
                else None
            ),
        )

    threads = [1, 2, 4, 8, 16]
    axis.plot(threads, threads, color="0.35", linestyle="--", linewidth=1.2)
    axis.annotate(
        "Ideal",
        (8, 8),
        textcoords="offset points",
        xytext=(6, 6),
        color="0.35",
        fontsize=11,
    )
    axis.set_xscale("log", base=2)
    axis.set_xticks(threads, labels=[str(value) for value in threads])
    axis.set_xlim(0.85, 21.5)
    axis.set_ylim(0.5, 16.7)
    axis.set_xlabel("Cores")
    axis.xaxis.set_label_coords(0.5, -0.09)
    axis.set_ylabel("Speedup vs 1 core")
    axis.set_title("Clifft Quantum Volume Strong Scaling")
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


def plot(input_path: Path, output_base: Path, *, write_pdf: bool = False) -> list[Path]:
    rows = _read_successes(input_path)
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
    current_figure, current_axis = plt.subplots(figsize=(7.2, 4.8), constrained_layout=True)
    _plot_current_tools(current_axis, rows)

    scaling_figure, scaling_axis = plt.subplots(figsize=(7.2, 4.8), constrained_layout=True)
    _plot_strong_scaling(scaling_axis, rows)

    outputs = _save_figure(
        current_figure,
        output_base.parent / f"{output_base.name}-current-tools",
        title="Current-tool Quantum Volume execution time",
        write_pdf=write_pdf,
    )
    outputs.extend(
        _save_figure(
            scaling_figure,
            output_base.parent / f"{output_base.name}-clifft-scaling",
            title="Clifft Quantum Volume strong scaling",
            write_pdf=write_pdf,
        )
    )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = plot(args.input.resolve(), args.output_base.resolve(), write_pdf=args.pdf)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
