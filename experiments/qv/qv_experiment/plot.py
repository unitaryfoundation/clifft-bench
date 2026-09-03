"""Plot median single-shot QV execution time from one stored execution."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STYLES = {
    "clifft": ("o", "-"),
    "qiskit": ("s", "--"),
    "qulacs": ("^", "-."),
    "qsim": ("D", ":"),
}
DISPLAY_NAMES = {
    "qiskit": "Qiskit Aer",
    "qulacs": "Qulacs",
    "qsim": "qsim",
}


@dataclass(frozen=True)
class WebTheme:
    name: str
    foreground: str
    muted: str
    grid: str
    blue: str
    orange: str
    green: str
    red: str


WEB_THEMES = (
    WebTheme(
        "light",
        "#172033",
        "#64748B",
        "#CBD5E1",
        "#3C64B4",
        "#C26713",
        "#147D64",
        "#B8465F",
    ),
    WebTheme(
        "dark",
        "#E6EDF7",
        "#AAB6C8",
        "#526077",
        "#83A7F2",
        "#F2A65A",
        "#57C7A5",
        "#F08AA0",
    ),
)


def web_output_paths(output_dir: Path) -> tuple[Path, ...]:
    return tuple(output_dir / f"quantum-volume-{theme.name}.png" for theme in WEB_THEMES)


def load_samples(execution_dir: Path) -> dict[tuple[str, int], list[float]]:
    samples: dict[tuple[str, int], list[float]] = defaultdict(list)
    with (execution_dir / "cases.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["status"] != "success":
                continue
            value = float(row["execution_seconds"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"successful case {row['case_id']!r} has invalid execution time "
                    f"{value!r}"
                )
            samples[(row["simulator"], int(row["qubits"]))].append(value)
    return samples


def clifft_display_name(execution_dir: Path, *, compact: bool = False) -> str:
    metadata = json.loads((execution_dir / "metadata.json").read_text())
    release_version = metadata["clifft_source"]["release_version"]
    if compact:
        release_version = release_version.removesuffix(".0")
    return f"Clifft {release_version}"


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


def _clean_web_axis(axis: Any, theme: WebTheme) -> None:
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.tick_params(axis="both", length=0)
    axis.grid(axis="x", color=theme.grid, linewidth=0.8, alpha=0.48)
    axis.set_axisbelow(True)


def render_web(
    execution_dir: Path,
    samples: dict[tuple[str, int], list[float]],
    output_dir: Path,
    plt: Any,
) -> list[Path]:
    simulators = {simulator for simulator, _width in samples}
    if simulators != {"clifft", "qiskit", "qsim", "qulacs"}:
        raise ValueError("web figure requires Clifft, Qiskit, qsim, and Qulacs samples")
    output_dir.mkdir(parents=True, exist_ok=True)
    for theme in WEB_THEMES:
        _configure_web(theme, plt)
        styles = {
            "clifft": (clifft_display_name(execution_dir, compact=True), theme.blue, "o"),
            "qiskit": ("Qiskit Aer", theme.orange, "s"),
            "qsim": ("qsim", theme.green, "D"),
            "qulacs": ("Qulacs", theme.red, "^"),
        }
        figure, axis = plt.subplots(figsize=(9.6, 4.7))
        for simulator, (label, color, marker) in styles.items():
            widths = sorted(width for candidate, width in samples if candidate == simulator)
            medians = [
                statistics.median(samples[(simulator, width)]) for width in widths
            ]
            axis.plot(
                widths,
                medians,
                label=label,
                color=color,
                marker=marker,
                markersize=5.5,
                linewidth=2.3,
            )
        axis.set_yscale("log")
        axis.set_xticks(range(6, 29, 2))
        axis.set_xlabel("Quantum Volume circuit width and depth")
        axis.set_ylabel("Execution time (seconds; lower is better)")
        axis.legend(
            loc="upper left",
            frameon=False,
            ncols=4,
            labelcolor=theme.foreground,
            handletextpad=0.5,
            columnspacing=1.2,
            bbox_to_anchor=(0, 1.03),
        )
        _clean_web_axis(axis, theme)
        figure.subplots_adjust(left=0.1, right=0.98, top=0.88, bottom=0.17)
        figure.savefig(
            output_dir / f"quantum-volume-{theme.name}.png",
            dpi=200,
            transparent=True,
        )
        plt.close(figure)
    return list(web_output_paths(output_dir))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--web-output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.output and args.web_output_dir:
        parser.error("--output and --web-output-dir are mutually exclusive")

    try:
        samples = load_samples(args.execution_dir)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if not samples:
        raise SystemExit("execution contains no successful cases")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if args.web_output_dir:
        for output in render_web(args.execution_dir, samples, args.web_output_dir, plt):
            print(f"Plot written to {output}")
        return 0

    figure, axis = plt.subplots(figsize=(7.0, 4.4))
    simulators = sorted({simulator for simulator, _width in samples})
    for simulator in simulators:
        widths = sorted(width for candidate, width in samples if candidate == simulator)
        medians = [
            statistics.median(samples[(simulator, width)]) for width in widths
        ]
        marker, line_style = STYLES.get(simulator, ("x", "-"))
        label = (
            clifft_display_name(args.execution_dir)
            if simulator == "clifft"
            else DISPLAY_NAMES.get(simulator, simulator)
        )
        axis.plot(
            widths,
            medians,
            label=label,
            marker=marker,
            linestyle=line_style,
            linewidth=1.2,
        )
    axis.set_yscale("log")
    axis.set_xlabel("Number of qubits")
    axis.set_ylabel("Execution time (s)")
    axis.grid(True, which="both", alpha=0.25)
    axis.legend()
    figure.tight_layout()
    output = args.output or args.execution_dir / "qv-scaling.png"
    figure.savefig(output, dpi=180)
    print(f"Plot written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
