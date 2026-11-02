"""Render release and cross-tool comparisons from one recorded execution."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from clifft_bench.results import case_rows, comparison_pairs, export_csv, read_result

FIGURES = ("clifft-throughput", "simulator-throughput", "release-change")
THEMES = {
    "light": (
        "#ffffff",
        "#172033",
        "#dbe2eb",
        ("#3366b3", "#bb5712", "#12846e", "#8852a1", "#ba3d55"),
    ),
    "dark": (
        "#111827",
        "#edf2fa",
        "#35445c",
        ("#8fb4ff", "#ffb26c", "#70d9bf", "#cca3f0", "#ff91a7"),
    ),
}


def build_report(document: dict) -> dict[str, Any]:
    rows = {row["case_id"]: row for row in case_rows(document)}
    pairs = comparison_pairs(document)
    release = [p for p in pairs if p["comparison_id"] == "current-vs-previous"]
    previous = {p["baseline_variant"] for p in release}
    current = [
        c
        for c in document["cases"]
        if c["simulator"]["adapter"] == "clifft" and c["variant_id"] not in previous
    ]
    for pair in release:
        for case in (pair["baseline"], pair["candidate"]):
            if case is not None and case["simulator"]["adapter"] != "clifft":
                raise ValueError("current-vs-previous must compare Clifft implementations")
    groups: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        if pair["comparison_id"] == "current-vs-previous":
            continue
        key = pair["baseline_variant"], pair["workload_id"]
        group = groups.setdefault(key, {"workload_id": key[1], "variant": key[0], "cases": {}})
        group["cases"][pair["baseline_variant"]] = pair["baseline"]
        group["cases"][pair["candidate_variant"]] = pair["candidate"]
    variants = {}
    for case in document["cases"]:
        variants.setdefault(case["variant_id"], case["simulator"])
    return {
        "document": document,
        "rows": rows,
        "pairs": pairs,
        "current": current,
        "release": release,
        "tools": list(groups.values()),
        "variants": variants,
    }


def _outcome(case: dict | None, other: dict | None, simulator: dict | None) -> str:
    if case is not None:
        return "measured" if case["status"] == "success" else case["status"]
    if other is not None and simulator is not None:
        if simulator["adapter"] not in other["workload"]["compatible_adapters"]:
            return "unsupported"
    return "not measured"


def review_summary(report: dict) -> str:
    document = report["document"]
    run, runner = document["run"], document["runner"]
    lines = [
        f"# {run['profile_id']} — {run['id']}",
        "",
        f"Classification: **{run['classification']}**. Collected {run['finished_at']}.",
        f"Source: `{runner['suite_source']['commit']}`; dirty: {runner['suite_source']['dirty']}.",
        f"Host: {runner['cpu_model']} ({runner['os']}, {runner['machine']}).",
        "",
    ]
    if run["classification"] != "official" or run["selection"]:
        lines += [
            "**Smoke/partial execution: timings are validation evidence, not publication data.**",
            f"Selection: `{run['selection']}`. Overrides: `{run['overrides']}`.",
            "",
        ]
    lines += [
        "## Measured implementations",
        "",
        "| Implementation | Exact version | Commit |",
        "| --- | --- | --- |",
    ]
    identities = {c["simulator"]["implementation_id"]: c["simulator"] for c in document["cases"]}
    for identifier, simulator in identities.items():
        lines.append(f"| {identifier} | {simulator['version']} | `{simulator['commit_sha']}` |")
    lines += ["", "## Outcomes", "", "| Case | Outcome | Detail |", "| --- | --- | --- |"]
    for case in document["cases"]:
        error = case["error"]
        detail = (
            f"{error['phase']}: {error['type']}: {error['message']}"
            if error
            else (
                f"{len(case['samples'])} samples; batch {case['execution']['batch_size_effective']}"
            )
        )
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {case['case_id']} | {case['status']} | {detail} |")
    lines += [
        "",
        "## Paired comparisons",
        "",
        "Ratios are candidate / baseline sample medians. Failed cases have no rate or ratio.",
        "Bulk and short calls remain separate. Missing baselines are not regressions.",
        "",
        "| Comparison | Workload | Candidate | Candidate / baseline | Outcome |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for pair in report["pairs"]:
        ratio = f"{pair['ratio']:.6g}×" if pair["ratio"] is not None else "—"
        states = [
            f"{side}: {_outcome(pair[side], pair[other], report['variants'].get(pair[key]))}"
            for side, other, key in (
                ("baseline", "candidate", "baseline_variant"),
                ("candidate", "baseline", "candidate_variant"),
            )
        ]
        lines.append(
            f"| {pair['comparison_id']} | {pair['workload_id']} | "
            f"{pair['candidate_variant']} | {ratio} | {'; '.join(states)} |"
        )
    if not report["pairs"]:
        lines.append("\nNo paired comparisons were selected.")
    return "\n".join(lines) + "\n"


def _label(case: dict, variant: str | None = None) -> str:
    return (
        f"{case['workload']['id']}\n"
        f"{variant or case['variant_id']} · {case['execution']['shots_per_call']:,} shots/call"
    )


def render(report: dict, output_dir: Path) -> list[Path]:
    try:
        import matplotlib
    except ImportError as error:
        raise ValueError(
            "rendering requires the report extra: uv run --extra report clifft-bench report"
        ) from error

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, NullFormatter

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    rows = report["rows"]
    previous = {p["baseline_variant"] for p in report["release"]}
    implementations = list(
        dict.fromkeys(
            s["implementation_id"] for v, s in report["variants"].items() if v not in previous
        )
    )
    run = report["document"]["run"]
    qualifier = "SMOKE / VALIDATION ONLY · " if run["classification"] != "official" else ""
    subtitle = f"{qualifier}{run['profile_id']} · attempted shots · one worker at a time"
    for theme, (background, foreground, grid, colors) in THEMES.items():
        with plt.rc_context(
            {
                "font.family": "DejaVu Sans",
                "font.size": 10,
                "text.color": foreground,
                "axes.labelcolor": foreground,
                "xtick.color": foreground,
                "ytick.color": foreground,
                "figure.facecolor": background,
                "axes.facecolor": background,
                "savefig.facecolor": background,
            }
        ):
            for kind in FIGURES:
                entries = report[
                    {
                        "clifft-throughput": "current",
                        "simulator-throughput": "tools",
                        "release-change": "release",
                    }[kind]
                ]
                figure, axis = plt.subplots(figsize=(15, max(4.5, 2 + len(entries) * 0.7)))
                title = {
                    "clifft-throughput": "Clifft throughput",
                    "simulator-throughput": "Simulator throughput",
                    "release-change": "Clifft change from the previous release",
                }[kind]
                if kind == "release-change" and entries:
                    pair = entries[0]
                    versions = [
                        report["variants"].get(pair[key], {}).get("display_version", "unmeasured")
                        for key in ("candidate_variant", "baseline_variant")
                    ]
                    title += f"\n{versions[0]} / {versions[1]}"
                axis.set_title(title + "\n" + subtitle, loc="left", fontsize=15, pad=22)
                labels = []
                legend = set()
                for index, entry in enumerate(entries):
                    if kind == "release-change":
                        case = entry["candidate"] or entry["baseline"]
                        labels.append(_label(case, entry["candidate_variant"]))
                        ratio = entry["ratio"]
                        if ratio is not None:
                            axis.plot(ratio, index, "o", color=colors[0], markersize=7)
                            axis.annotate(
                                f"{ratio:.3g}× ({(ratio - 1) * 100:+.2f}%)",
                                (ratio, index),
                                xytext=(10, 0),
                                textcoords="offset points",
                                va="center",
                                fontsize=9,
                            )
                        else:
                            states = [
                                _outcome(
                                    entry[side], entry[other], report["variants"].get(entry[key])
                                )
                                for side, other, key in (
                                    ("baseline", "candidate", "baseline_variant"),
                                    ("candidate", "baseline", "candidate_variant"),
                                )
                            ]
                            reason = (
                                "failed"
                                if any(s in {"error", "interrupted"} for s in states)
                                else "no measured pair"
                            )
                            axis.text(
                                0.01,
                                index,
                                reason,
                                transform=axis.get_yaxis_transform(),
                                va="center",
                                color=colors[-1],
                            )
                    else:
                        cases = (
                            {entry["variant_id"]: entry}
                            if kind == "clifft-throughput"
                            else entry["cases"]
                        )
                        representative = next(c for c in cases.values() if c is not None)
                        labels.append(
                            _label(
                                representative,
                                None if kind == "clifft-throughput" else entry["variant"],
                            )
                        )
                        for offset, (variant, case) in enumerate(cases.items()):
                            simulator = report["variants"].get(variant)
                            name = (
                                f"{simulator['name']}\n{simulator['display_version']} "
                                f"({simulator['commit_sha'][:7]})"
                                if simulator
                                else variant
                            )
                            color = (
                                colors[
                                    implementations.index(simulator["implementation_id"])
                                    % len(colors)
                                ]
                                if simulator and simulator["implementation_id"] in implementations
                                else colors[0]
                            )
                            y = index + (offset - (len(cases) - 1) / 2) * 0.20
                            if case is not None and case["status"] == "success":
                                rate = rows[case["case_id"]]["median_attempted_shots_per_second"]
                                axis.plot(
                                    rate,
                                    y,
                                    "o",
                                    color=color,
                                    markersize=6,
                                    label=name if name not in legend else None,
                                )
                                legend.add(name)
                            else:
                                state = _outcome(case, representative, simulator)
                                status_label = {
                                    "unsupported": "n/a",
                                    "not measured": "unmeasured",
                                    "error": "failed",
                                    "interrupted": "interrupted",
                                }[state]
                                short_name = simulator["name"] if simulator else variant
                                axis.text(
                                    1.02,
                                    y,
                                    f"{short_name}: {status_label}",
                                    transform=axis.get_yaxis_transform(),
                                    fontsize=8,
                                    color=color,
                                    va="center",
                                )
                axis.set_yticks(range(len(labels)), labels, fontsize=9)
                axis.set_ylim(len(labels) - 0.5, -0.7)
                if entries:
                    axis.set_xscale("log")
                    axis.margins(x=0.25)
                else:
                    axis.text(
                        0.5,
                        0.5,
                        "No matching comparisons selected",
                        ha="center",
                        transform=axis.transAxes,
                    )
                if kind == "release-change":
                    axis.axvline(1, color=foreground, linewidth=0.8, alpha=0.6)
                    formatter = FuncFormatter(lambda x, pos: f"{x:g}×")
                    axis.xaxis.set_major_formatter(formatter)
                    axis.xaxis.set_minor_formatter(NullFormatter())
                    # Include parity even when every measured ratio is on the same side of 1×.
                    ratios = [1.0, *(p["ratio"] for p in entries if p["ratio"] is not None)]
                    low, high = math.log(min(ratios)), math.log(max(ratios))
                    padding = max((high - low) * 0.25, math.log(1.01))
                    axis.set_xlim(math.exp(low - padding), math.exp(high + padding))
                    axis.set_xlabel("Current / previous throughput · above 1× is faster")
                else:
                    axis.set_xlabel(
                        "Attempted shots per second · logarithmic scale\n"
                        "n/a = unsupported · unmeasured = not selected"
                    )
                axis.grid(axis="x", color=grid, alpha=0.7)
                axis.set_axisbelow(True)
                axis.tick_params(axis="y", length=0)
                for spine in axis.spines.values():
                    spine.set_visible(False)
                figure.subplots_adjust(left=0.39, right=0.88, top=0.84, bottom=0.19)
                if kind != "release-change" and legend:
                    # Anchor below the rendered label in physical space, independent of row count.
                    figure.canvas.draw()
                    label = axis.xaxis.label.get_window_extent(figure.canvas.get_renderer())
                    anchor = figure.transFigure.inverted().transform(
                        (axis.bbox.x0 + axis.bbox.width / 2, label.y0 - figure.dpi * 0.12)
                    )
                    axis.legend(
                        loc="upper center", bbox_to_anchor=anchor,
                        bbox_transform=figure.transFigure, borderaxespad=0,
                        ncol=min(3, len(legend)), frameon=False, labelcolor=foreground,
                    )
                path = output_dir / f"{kind}-{theme}.png"
                figure.savefig(path, dpi=160, bbox_inches="tight")
                plt.close(figure)
                outputs.append(path)
    return outputs


def report_execution(
    execution_dir: Path, *, output_dir: Path | None = None, check: bool = False
) -> str:
    document = read_result(execution_dir / "raw.json")
    report = build_report(document)
    summary = review_summary(report)
    if not check:
        # Regenerate the sole derived table from authoritative evidence; never audit
        # or trust a potentially stale second copy of the same facts.
        export_csv(document, execution_dir / "cases.csv")
        destination = output_dir or execution_dir / "report"
        render(report, destination)
        (destination / "review.md").write_text(summary)
    return summary
