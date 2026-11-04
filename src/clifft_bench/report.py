"""Render release and cross-tool comparisons from one recorded execution."""

from __future__ import annotations

import math
import textwrap
from pathlib import Path
from typing import Any

from clifft_bench.results import case_rows, comparison_pairs, export_csv, read_result

FIGURES = ("simulator-throughput", "release-change")
WORKLOAD_GROUPS = (
    ("Magic-state preparation", (
        "magic-state-cultivation", "magic-state-cultivation-full",
        "magic-state-distillation", "reed-muller",
    )),
    ("Surface-code memory & coherent noise", (
        "surface-code-memory", "coherent-noise-surface-code",
    )),
    ("Arithmetic, code switching & dense QV", (
        "prepared-basis-arithmetic", "code-switching", "quantum-volume",
    )),
)
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
    lines += [
        "", "## Figures", "",
        "Rates count attempted shots and exclude compilation; lazy planning during sampling",
        "is included. The main figures share circuit selection and thematic ordering.",
        "Selection-off controls, the unverified cultivation proxy, and bulk-call variants",
        "remain in the detailed results above. Colors and marker shapes identify simulators.",
        "Unsupported pairings have",
        "no marker; unmeasured and failed cases are labeled. Exact identities and outcomes",
        "are recorded above. Call sizes are in cases.csv; postselection settings are in raw.json.",
        "",
        "Release changes within ±50% use a linear percentage axis; larger changes use",
        "a separate logarithmic ratio panel. The shaded ±1.1% band is the contract's",
        "inconclusive region, not a confidence interval. Unpaired workloads have no ratio.",
        "",
    ]
    for kind, title in zip(FIGURES, ("CPU simulator throughput", "Clifft release changes")):
        lines += [
            f"### {title}", "", f"[Dark version]({kind}-dark.png)", "",
            f"![{title}]({kind}-light.png)", "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _label(case: dict) -> str:
    """Use recorded workload fields; reporting never needs the current manifest."""
    workload = case["workload"]
    family, parameters = workload["family"], workload["parameters"]
    if family == "magic-state-cultivation":
        return f"Cultivation d{parameters['distance']}"
    if family in {"surface-code-memory", "coherent-noise-surface-code"}:
        name = "Surface code" if family == "surface-code-memory" else "Coherent"
        return f"{name} d{parameters['distance']}, r{parameters['rounds']}"
    if family == "magic-state-distillation":
        if parameters["code"].startswith("[[15,1,3]]"):
            return "15-to-1 distillation"
        return f"{workload['expected_metadata']['num_qubits']}q distillation"
    if family == "prepared-basis-arithmetic":
        return f"Draper adder · {parameters['bits_per_operand']}-bit"
    if family == "quantum-volume":
        return f"Quantum Volume {parameters['num_qubits']}"
    if family == "code-switching":
        return workload["id"].split("-")[0].upper()
    if family == "reed-muller":
        return "Reed–Muller"
    if family == "msc-performance-proxy":
        return f"Cultivation d{parameters['distance']} proxy (unverified)"
    if family == "magic-state-cultivation-full":
        return f"End-to-end cultivation d{parameters['distance']}"
    return workload["id"]


def _representative(entry: dict) -> dict:
    return next(case for case in entry["cases"].values() if case is not None)


def _main_workload(case: dict, variant: str) -> bool:
    """Keep diagnostic configurations in the tables, independent of their rates."""
    workload = case["workload"]
    return (
        variant != "clifft-bulk"
        and not workload["parameters"].get("postselection_pair")
        and workload["family"] != "msc-performance-proxy"
    )


def _workload_group(case: dict) -> tuple[int, int, str]:
    family = case["workload"]["family"]
    for index, (heading, families) in enumerate(WORKLOAD_GROUPS):
        if family in families:
            return index, families.index(family), heading
    return len(WORKLOAD_GROUPS), 0, "Other workloads"


def _workload_order(case: dict) -> tuple:
    """Shared by both figures; new measured pairs need no release-specific list."""
    workload = case["workload"]
    parameters = workload["parameters"]
    size = parameters.get("distance", parameters.get("bits_per_operand", parameters.get(
        "num_qubits", workload["expected_metadata"].get("num_qubits", 0)
    )))
    return (*_workload_group(case)[:2], workload["family"], size,
            parameters.get("rounds", 0), workload["id"], case["variant_id"])


def _throughput_entries(report: dict) -> list[dict]:
    entries = list(report["tools"])
    covered = {c["case_id"] for e in entries for c in e["cases"].values() if c is not None}
    entries.extend(
        {"variant": c["variant_id"], "cases": {c["variant_id"]: c}}
        for c in report["current"] if c["case_id"] not in covered
    )
    return sorted(
        (e for e in entries if _main_workload(_representative(e), e["variant"])),
        key=lambda e: _workload_order(_representative(e)),
    )


def _release_pairs(report: dict) -> list[dict]:
    return sorted(
        (p for p in report["release"] if p["ratio"] is not None
         and _main_workload(p["candidate"], p["candidate_variant"])),
        key=lambda p: _workload_order(p["candidate"]),
    )


def _layout(cases: list[dict], heights: list[float]) -> tuple[list, list, float]:
    headers, positions = [], []
    y, last = 0.0, None
    for case, height in zip(cases, heights, strict=True):
        group, _, heading = _workload_group(case)
        if group != last:
            if last is not None:
                y += 0.20
            headers.append((heading, y + 0.10))
            y += 0.55
            last = group
        positions.append(y + height / 2)
        y += height
    return headers, positions, max(0.65, y + 0.12)


def _number_label(value: float) -> str:
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= divisor:
            return f"{value / divisor:.3g}{suffix}"
    return f"{value:.3g}"


def _style_axis(axis, extent: float, grid: str) -> None:
    axis.set_ylim(extent, -0.12)
    axis.set_yticks([])
    axis.grid(axis="x", color=grid, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(axis="x", labelsize=11)
    for spine in axis.spines.values():
        spine.set_visible(False)


def _header_bands(axis, headers, grid, *, left=-0.46, right=1, labels=True):
    for heading, y in headers:
        axis.axhspan(y - 0.15, y + 0.20, xmin=left, xmax=right,
                     color=grid, alpha=0.45, clip_on=False, zorder=0)
        if labels:
            axis.text(left + 0.015, y + 0.025, heading,
                      transform=axis.get_yaxis_transform(), va="center",
                      fontsize=13, fontweight="bold")


def _render_throughput(report, colors, grid):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator

    entries = _throughput_entries(report)
    adapters = ("clifft", "symft", "xtim", "stim", "tsim")
    markers = ("o", "s", "D", "^", "P")
    variants = list(dict.fromkeys(v for entry in entries for v in entry["cases"]))
    variants.sort(key=lambda v: (
        adapters.index(report["variants"][v]["adapter"])
        if v in report["variants"] and report["variants"][v]["adapter"] in adapters
        else len(adapters), v,
    ))
    styles = {}
    for index, variant in enumerate(variants):
        simulator = report["variants"].get(variant)
        adapter = simulator["adapter"] if simulator else ""
        position = adapters.index(adapter) if adapter in adapters else index
        styles[variant] = colors[position % len(colors)], markers[position % len(markers)]
    visible = [
        sorted(((v, case) for v, case in entry["cases"].items()
                if _outcome(case, _representative(entry), report["variants"].get(v))
                != "unsupported"), key=lambda item: variants.index(item[0]))
        for entry in entries
    ]
    cases = [_representative(entry) for entry in entries]
    names = [textwrap.fill(_label(case), 29) for case in cases]
    heights = [max(0.58, len(values) * 0.23 + 0.19, (name.count("\n") + 1) * 0.24 + 0.19)
               for values, name in zip(visible, names, strict=True)]
    headers, positions, extent = _layout(cases, heights)
    height = extent + 1.55
    figure = plt.figure(figsize=(12, height))
    axis = figure.add_axes((0.32, 0.62 / height, 0.64, extent / height))
    rates = [
        report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
        for values in visible for _, case in values
        if case is not None and case["status"] == "success"
    ]
    low = 10 ** math.floor(math.log10(min(rates))) / 1.3 if rates else 0.1
    high = low * (max(rates, default=1) / low) ** 1.13
    axis.set_xscale("log")
    axis.set_xlim(low, high)
    axis.xaxis.set_major_locator(LogLocator(base=10, numticks=7))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _number_label(value)))
    axis.xaxis.set_minor_locator(NullLocator())
    _style_axis(axis, extent, grid)
    _header_bands(axis, headers, grid)
    used = set()
    for entry, values, name, y, row_height in zip(
        entries, visible, names, positions, heights, strict=True,
    ):
        axis.text(-0.025, y, name, transform=axis.get_yaxis_transform(),
                  ha="right", va="center", fontsize=13)
        for offset, (variant, case) in enumerate(values):
            color, marker = styles[variant]
            py = y + (offset - (len(values) - 1) / 2) * 0.23
            state = _outcome(case, _representative(entry), report["variants"].get(variant))
            used.add(variant)
            if state == "measured":
                rate = report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
                axis.hlines(py, low, rate, color=color, linewidth=2.4)
                axis.plot(rate, py, marker, color=color, markersize=7)
                axis.annotate(f"{_number_label(rate)}/s", (rate, py), xytext=(7, 0),
                              textcoords="offset points", va="center", color=color,
                              fontsize=11.5, fontweight="bold")
            else:
                simulator = report["variants"].get(variant)
                label = simulator["name"] if simulator else variant
                axis.text(0.02, py, f"{label}: {state}", transform=axis.get_yaxis_transform(),
                          va="center", fontsize=11, color=color)
        axis.axhline(y + row_height / 2, color=grid, linewidth=0.5, alpha=0.65)
    if not entries:
        axis.text(0.5, 0.5, "No main workloads selected", ha="center", transform=axis.transAxes)
    axis.set_xlabel("Attempted shots per second · log scale · higher is faster", labelpad=12)
    handles, names_seen = [], set()
    for variant in variants:
        if variant not in used:
            continue
        simulator = report["variants"].get(variant)
        name = simulator["name"] if simulator else variant
        if name in names_seen:
            continue
        names_seen.add(name)
        color, marker = styles[variant]
        handles.append(Line2D([], [], color=color, marker=marker, linewidth=2.4, label=name))
    if handles:
        figure.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.32, 1 - 0.34 / height),
                      ncol=5, frameon=False, fontsize=11, handlelength=1.3, columnspacing=1.2)
    figure.suptitle("CPU simulator throughput", x=0.035, ha="left", fontsize=19,
                    fontweight="bold", y=1 - 0.04 / height)
    return figure


def _render_release(report, colors, foreground, grid):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator

    pairs = _release_pairs(report)
    cases = [p["candidate"] for p in pairs]
    names = [textwrap.fill(_label(case), 29) for case in cases]
    heights = [max(0.53, (name.count("\n") + 1) * 0.24 + 0.19) for name in names]
    headers, positions, extent = _layout(cases, heights)
    height = extent + 1.65
    figure = plt.figure(figsize=(12, height))
    percent = figure.add_axes((0.32, 0.74 / height, 0.295, extent / height))
    ratio_axis = figure.add_axes((0.69, 0.74 / height, 0.27, extent / height), sharey=percent)
    small = [p["ratio"] for p in pairs if 0.5 <= p["ratio"] <= 1.5]
    large = [p["ratio"] for p in pairs if p["ratio"] < 0.5 or p["ratio"] > 1.5]
    span = max([3, *(abs((ratio - 1) * 100) * 1.65 for ratio in small)])
    percent.set_xlim(-span, span)
    percent.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}%"))
    percent.axvspan(-1.1, 1.1, color=grid, alpha=0.45, zorder=0)
    percent.axvline(0, color=foreground, linestyle="--", linewidth=0.8)
    percent.set_title("Percentage change", fontsize=12, pad=13)
    percent.set_xlabel("0% = unchanged", labelpad=10, fontsize=11)
    ratio_axis.set_xscale("log")
    low, high = min([1, *large]), max([1, *large])
    padding = max(math.log(high / low) * 0.32, math.log(1.5))
    ratio_axis.set_xlim(low / math.exp(padding), high * math.exp(padding))
    ratio_axis.xaxis.set_major_locator(LogLocator(base=10, numticks=5))
    ratio_axis.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}×"))
    ratio_axis.xaxis.set_minor_locator(NullLocator())
    ratio_axis.axvline(1, color=foreground, linestyle="--", linewidth=0.8)
    ratio_axis.set_title("Larger changes", fontsize=12, pad=13)
    ratio_axis.set_xlabel("Current / previous · log scale", labelpad=10, fontsize=11)
    for axis in (percent, ratio_axis):
        _style_axis(axis, extent, grid)
    _header_bands(percent, headers, grid, left=-1, right=(0.96 - 0.32) / 0.295)
    _header_bands(ratio_axis, headers, grid, left=0, labels=False)
    for pair, name, y in zip(pairs, names, positions, strict=True):
        percent.text(-0.055, y, name, transform=percent.get_yaxis_transform(),
                     ha="right", va="center", fontsize=13)
        ratio = pair["ratio"]
        is_small = 0.5 <= ratio <= 1.5
        axis = percent if is_small else ratio_axis
        value, baseline = ((ratio - 1) * 100, 0) if is_small else (ratio, 1)
        color = colors[0] if ratio >= 1 else colors[-1]
        if is_small and abs(value) <= 1.1:
            color = foreground
        axis.hlines(y, baseline, value, color=color, linewidth=2.7)
        axis.plot(value, y, "o", color=color, markersize=8)
        axis.annotate(f"{value:+.2f}%" if is_small else f"{ratio:.3g}×", (value, y),
                      xytext=(8 if ratio >= 1 else -8, 0), textcoords="offset points",
                      ha="left" if ratio >= 1 else "right", va="center",
                      fontsize=12, fontweight="bold", color=color)
    if not pairs:
        percent.text(0.5, 0.5, "No measured release pairs", ha="center",
                     transform=percent.transAxes)
    versions = ""
    if report["release"]:
        pair = report["release"][0]
        current, previous = (
            report["variants"].get(pair[key], {}).get("display_version", "unmeasured")
            for key in ("candidate_variant", "baseline_variant")
        )
        versions = f" · {current} vs {previous}"
    figure.suptitle("Clifft release changes" + versions, x=0.035, ha="left",
                    fontsize=19, fontweight="bold", y=1 - 0.04 / height)
    return figure


def render(report: dict, output_dir: Path) -> list[Path]:
    try:
        import matplotlib
    except ImportError as error:
        raise ValueError(
            "rendering requires the report extra: uv run --extra report clifft-bench report"
        ) from error

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    run = report["document"]["run"]
    qualifier = (
        "SMOKE / VALIDATION ONLY · "
        if run["classification"] != "official" or run["selection"] else ""
    )
    for theme, (background, foreground, grid, colors) in THEMES.items():
        with plt.rc_context({
            "font.family": "DejaVu Sans", "font.size": 12,
            "text.color": foreground, "axes.labelcolor": foreground,
            "xtick.color": foreground, "ytick.color": foreground,
            "figure.facecolor": background, "axes.facecolor": background,
            "savefig.facecolor": background,
        }):
            for kind in FIGURES:
                figure = (
                    _render_release(report, colors, foreground, grid)
                    if kind == "release-change" else _render_throughput(report, colors, grid)
                )
                if qualifier:
                    figure.text(0.5, 1.01, qualifier.rstrip(" ·"), ha="center",
                                transform=figure.transFigure, fontsize=14, color=colors[-1])
                path = output_dir / f"{kind}-{theme}.png"
                figure.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.16)
                plt.close(figure)
                outputs.append(path)
            # Remove superseded generated figures, preserving unrelated images.
            (output_dir / f"clifft-throughput-{theme}.png").unlink(missing_ok=True)
            for kind in ("clifft-throughput", *FIGURES):
                for path in output_dir.glob(f"{kind}-*-{theme}.png"):
                    page = path.name.removeprefix(f"{kind}-").removesuffix(f"-{theme}.png")
                    if page.isdecimal():
                        path.unlink()
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
