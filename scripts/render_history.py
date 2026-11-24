"""Render the v0.1-to-current historical QEC speedup figure from committed results.

Chains paired 1-core survivor-count ``current / previous`` ratios onto the common
``v0.1`` baseline across the fixed six-workload QEC core. Run with:

    uv run --extra report python scripts/render_history.py
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "reporting/figures/web"

CORE_WORKLOADS = (
    "coherent-surface-d3-r3-p1e-3-rz2e-2",
    "coherent-surface-d5-r5-p1e-3-rz2e-2",
    "distillation-color-code-85q-p5e-2",
    "msc-d3-inject-cultivate-p1e-3",
    "msc-d5-inject-cultivate-p1e-3",
    "surface-code-d7-r7-p1e-3",
)

HISTORY_EXECUTION = "results/clifft-history-v1/clifft-history-v1-20260902"
RELEASE_EXECUTIONS = (
    "results/release-v1/release-v1-20260903-133252",
    "results/release-v1/release-v1-20260930-180134",
    "results/release-v1/release-v1-20261009-192718",
    "results/release-clifft/0.13.0rc2-20261104-140631",
    "results/release-clifft/0.14.0rc3-20261124-0148",
)

THEMES = {
    "light": {
        "foreground": "#172033",
        "muted": "#52627a",
        "grid": "#dbe2eb",
        "accent": "#3366b3",
    },
    "dark": {
        "foreground": "#edf2fa",
        "muted": "#9fb0c8",
        "grid": "#35445c",
        "accent": "#8fb4ff",
    },
}


@dataclass(frozen=True)
class HistorySeries:
    """Chained per-workload and median speedups relative to the baseline release."""

    versions: tuple[str, ...]
    speedups: dict[str, tuple[float, ...]]
    medians: tuple[float, ...]
    sources: tuple[str, ...]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _normalize_version(raw: str) -> str:
    """Normalize a release-candidate version label (e.g. '0.14.0rc3') to '0.14.0'."""
    match = re.match(r"^(\d+\.\d+\.\d+)", raw.strip())
    if not match:
        raise ValueError(f"Unrecognized version label: {raw!r}")
    return match.group(1)


def _short_version(version: str) -> str:
    return version[:-2] if version.endswith(".0") else version


def _load_release_ratios(execution_dir: Path) -> tuple[str, str, dict[str, float]]:
    """Load paired 1-core current/previous ratios for CORE_WORKLOADS."""
    comparisons_path = execution_dir / "comparisons.csv"
    if comparisons_path.exists():
        rows = [
            row
            for row in _read_csv(comparisons_path)
            if row["comparison_id"] == "current-vs-previous"
            and row["workload_id"] in CORE_WORKLOADS
        ]
        prev_versions = {_normalize_version(r["baseline_simulator_display_version"]) for r in rows}
        curr_versions = {_normalize_version(r["candidate_simulator_display_version"]) for r in rows}
        if len(prev_versions) != 1 or len(curr_versions) != 1:
            raise ValueError(f"Ambiguous release versions in {execution_dir}")
        ratios: dict[str, float] = {}
        for workload in CORE_WORKLOADS:
            values = [
                float(r["ratio_candidate_over_baseline"])
                for r in rows
                if r["workload_id"] == workload
            ]
            if not values:
                raise ValueError(f"Missing {workload} in {comparisons_path}")
            ratios[workload] = statistics.median(values)
        return next(iter(prev_versions)), next(iter(curr_versions)), ratios

    cases_path = execution_dir / "cases.csv"
    rows = [
        row
        for row in _read_csv(cases_path)
        if row["workload_id"] in CORE_WORKLOADS
        and row["variant_id"] in ("clifft-previous", "clifft-current")
    ]
    prev_rows = [r for r in rows if r["variant_id"] == "clifft-previous"]
    curr_rows = [r for r in rows if r["variant_id"] == "clifft-current"]
    for side, side_rows in (("previous", prev_rows), ("current", curr_rows)):
        if any(r["status"] != "success" for r in side_rows):
            raise ValueError(f"Non-success {side} case in {cases_path}")
    prev_versions = {_normalize_version(r["simulator_display_version"]) for r in prev_rows}
    curr_versions = {_normalize_version(r["simulator_display_version"]) for r in curr_rows}
    for row in (*prev_rows, *curr_rows):
        if row.get("postselect") not in (None, "", "on"):
            raise ValueError(f"Expected survivor-count postselect=on in {cases_path}: {row}")
        if row.get("threads_requested") not in (None, "", "1"):
            raise ValueError(f"Expected 1-core baseline in {cases_path}: {row}")
    if len(prev_versions) != 1 or len(curr_versions) != 1:
        raise ValueError(f"Ambiguous release versions in {cases_path}")
    prev_by_w = {
        r["workload_id"]: float(r["median_attempted_shots_per_second"]) for r in prev_rows
    }
    curr_by_w = {
        r["workload_id"]: float(r["median_attempted_shots_per_second"]) for r in curr_rows
    }
    missing = set(CORE_WORKLOADS) - (set(prev_by_w) & set(curr_by_w))
    if missing:
        raise ValueError(f"Missing core workloads in {cases_path}: {sorted(missing)}")
    ratios = {w: curr_by_w[w] / prev_by_w[w] for w in CORE_WORKLOADS}
    return next(iter(prev_versions)), next(iter(curr_versions)), ratios


def build_history_series(root: Path = ROOT) -> HistorySeries:
    """Build the chained v0.1-to-current speedup series over CORE_WORKLOADS."""
    history_dir = root / HISTORY_EXECUTION
    history_rows = _read_csv(history_dir / "cases.csv")
    if any(r["status"] != "success" for r in history_rows):
        raise ValueError(f"Failed cases in {history_dir}")
    all_history_versions = list(
        dict.fromkeys(_normalize_version(r["simulator_display_version"]) for r in history_rows)
    )

    releases = [
        (rel, *_load_release_ratios(root / rel))
        for rel in RELEASE_EXECUTIONS
    ]
    anchor = releases[0][1]
    if anchor not in all_history_versions:
        raise ValueError(f"First release baseline {anchor} absent from {HISTORY_EXECUTION}")

    versions = list(all_history_versions[: all_history_versions.index(anchor) + 1])
    sources = [HISTORY_EXECUTION] * len(versions)

    history_rates: dict[tuple[str, str], float] = {}
    for workload in CORE_WORKLOADS:
        for version in versions:
            vals = [
                float(r["median_attempted_shots_per_second"])
                for r in history_rows
                if r["workload_id"] == workload
                and _normalize_version(r["simulator_display_version"]) == version
            ]
            if not vals:
                raise ValueError(f"Missing ({workload}, {version}) in {history_dir}")
            history_rates[(workload, version)] = statistics.median(vals)

    baseline = versions[0]
    speedups: dict[str, list[float]] = {
        workload: [
            history_rates[(workload, version)] / history_rates[(workload, baseline)]
            for version in versions
        ]
        for workload in CORE_WORKLOADS
    }

    for rel_path, prev_version, curr_version, ratios in releases:
        if prev_version != versions[-1]:
            raise ValueError(
                f"Release chain expected previous={versions[-1]}, got {prev_version} in {rel_path}"
            )
        versions.append(curr_version)
        sources.append(rel_path)
        for workload in CORE_WORKLOADS:
            speedups[workload].append(speedups[workload][-1] * ratios[workload])

    medians = tuple(
        statistics.median(speedups[workload][i] for workload in CORE_WORKLOADS)
        for i in range(len(versions))
    )
    return HistorySeries(
        versions=tuple(versions),
        speedups={w: tuple(vals) for w, vals in speedups.items()},
        medians=medians,
        sources=tuple(sources),
    )


def _plot_theme(plt: Any, theme: dict[str, str], series: HistorySeries, output: Path) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter

    positions = list(range(len(series.versions)))
    medians = series.medians
    idx = {version: i for i, version in enumerate(series.versions)}

    figure, axis = plt.subplots(figsize=(9.6, 4.1))
    axis.axhline(1, color=theme["muted"], linewidth=1.2, linestyle=(0, (3, 3)), zorder=2)
    axis.plot(
        positions,
        medians,
        color=theme["accent"],
        linewidth=3.2,
        marker="o",
        markersize=6.0,
        solid_capstyle="round",
        zorder=3,
    )
    axis.fill_between(
        positions, 1, medians, color=theme["accent"], alpha=0.09, linewidth=0, zorder=1
    )

    axis.annotate(
        f"{medians[-1]:.0f}\u00d7 median chained speedup",
        (positions[-1], medians[-1]),
        xytext=(0, 28),
        textcoords="offset points",
        ha="right",
        color=theme["accent"],
        fontsize=11.5,
        fontweight="bold",
        zorder=4,
    )

    milestones = (
        ("0.8.0", "symbolic plans", (0, 36), "center"),
        ("0.10.0", "packing + compiler", (-74, 30), "center"),
        ("0.11.0", "scheduling\n+ sampling", (8, -56), "center"),
        ("0.14.0", "reconstruction\n+ sharing", (-56, -8), "right"),
    )
    for version, label, offset, align in milestones:
        if version not in idx:
            continue
        i = idx[version]
        axis.annotate(
            label,
            (positions[i], medians[i]),
            xytext=offset,
            textcoords="offset points",
            ha=align,
            va="center",
            color=theme["muted"],
            fontsize=9.5,
            arrowprops={"arrowstyle": "-", "color": theme["grid"], "linewidth": 1.1},
            zorder=4,
        )

    axis.set_yscale("log", base=2)
    upper = max(380.0, max(medians) * 2.8)
    axis.set_ylim(0.7, upper)
    max_exp = int(math.floor(math.log2(upper)))
    ticks = [2**p for p in range(0, min(max_exp, 8) + 1, 2)]
    axis.yaxis.set_major_locator(FixedLocator(ticks))
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:g}\u00d7"))
    axis.yaxis.set_minor_locator(FixedLocator([]))

    axis.set_xticks(positions, labels=[f"v{_short_version(v)}" for v in series.versions])
    axis.set_xlim(-0.4, positions[-1] + 0.4)
    axis.set_ylabel("Median speedup vs v0.1", color=theme["foreground"], fontsize=10, labelpad=8)

    axis.set_facecolor("none")
    axis.grid(axis="y", color=theme["grid"], linewidth=0.8, zorder=0)
    axis.set_axisbelow(True)
    for spine in ("top", "right", "left", "bottom"):
        axis.spines[spine].set_visible(False)
    axis.tick_params(axis="both", length=0, colors=theme["muted"], labelsize=9.5, pad=6)

    figure.text(
        0.11,
        0.03,
        "Chained 1-core paired survivor-count ratios across the 6-workload QEC core "
        "(v0.1\u2013v0.14; host and call-size protocol updated at v0.14)",
        fontsize=8.5,
        color=theme["muted"],
    )
    figure.subplots_adjust(left=0.11, right=0.98, top=0.92, bottom=0.20)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=200, transparent=True)
    plt.close(figure)


def render_figures(
    series: HistorySeries, output_dir: Path = DEFAULT_OUTPUT_DIR
) -> tuple[Path, ...]:
    """Render light and dark performance-over-time PNGs."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outputs = []
    for name, theme in THEMES.items():
        path = output_dir / f"performance-over-time-{name}.png"
        _plot_theme(plt, theme, series, path)
        outputs.append(path)
    return tuple(outputs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--summary-only", action="store_true", help="print chained medians only")
    args = parser.parse_args()

    series = build_history_series()
    for version, median in zip(series.versions, series.medians, strict=True):
        print(f"v{version:7s}  {median:7.2f}x")
    if not args.summary_only:
        for path in render_figures(series, args.output_dir):
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
