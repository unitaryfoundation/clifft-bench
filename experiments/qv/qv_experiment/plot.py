"""Plot median single-shot QV execution time from one stored execution."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

STYLES = {
    "clifft": ("o", "-"),
    "qiskit": ("s", "--"),
    "qulacs": ("^", "-."),
    "qsim": ("D", ":"),
    "qrack": ("P", (0, (3, 1, 1, 1))),
}
DISPLAY_NAMES = {
    "qiskit": "Qiskit Aer",
    "qulacs": "Qulacs",
    "qsim": "qsim",
    "qrack": "Qrack",
}


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


def clifft_display_name(execution_dir: Path) -> str:
    metadata = json.loads((execution_dir / "metadata.json").read_text())
    release_version = metadata["clifft_source"]["release_version"]
    return f"Clifft {release_version}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        samples = load_samples(args.execution_dir)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if not samples:
        raise SystemExit("execution contains no successful cases")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
