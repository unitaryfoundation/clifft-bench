"""Render release and cross-tool comparisons from one recorded execution."""

from __future__ import annotations

import math
import textwrap
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
    lines += [
        "", "## Figures", "",
        "Rates count attempted shots and exclude compilation; lazy planning during sampling",
        "is included. Workload call sizes appear on each row. Colors and marker shapes",
        "identify implementations consistently across figures. Unsupported pairings have",
        "no marker; unmeasured and failed cases are labeled. Exact identities and outcomes",
        "are recorded above and in cases.csv.", "",
        "Release changes within ±50% use a linear percentage axis; larger changes use",
        "a separate logarithmic ratio panel. The shaded ±1.1% band is the contract's",
        "inconclusive region, not a confidence interval. Unpaired workloads have no ratio.",
        "",
    ]
    for kind in FIGURES:
        panels = (
            [("Release changes", [])] if kind == "release-change"
            else _throughput_panels(report, kind)
        )
        name = "Clifft" if kind == "clifft-throughput" else "CPU simulators"
        for index, (heading, _) in enumerate(panels):
            title = heading if kind == "release-change" else f"{name} — {heading}"
            stem = _figure_stem(kind, index)
            lines += [
                f"### {title}", "", f"[Dark version]({stem}-dark.png)", "",
                f"![{title}]({stem}-light.png)", "",
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
        return workload["id"].split("-")[0].upper() + " · direct X"
    if family == "reed-muller":
        return "Reed–Muller"
    if family == "msc-performance-proxy":
        return f"Cultivation d{parameters['distance']} proxy (unverified)"
    if family == "magic-state-cultivation-full":
        count = len(workload["semantics"]["postselection_detector_indices"])
        return f"Full cultivation d{parameters['distance']} · {count} detectors selected"
    return workload["id"]


def _representative(entry: dict) -> dict:
    return next(case for case in entry["cases"].values() if case is not None)


def _throughput_panels(report: dict, kind: str) -> list[tuple[str, list[dict]]]:
    entries = (
        [{"variant": c["variant_id"], "cases": {c["variant_id"]: c}} for c in report["current"]]
        if kind == "clifft-throughput" else report["tools"]
    )
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        case = _representative(entry)
        workload = case["workload"]
        family = workload["family"]
        if family == "quantum-volume":
            title = "Dense Quantum Volume · repeated sampling"
        elif family in {"msc-performance-proxy", "magic-state-cultivation-full"}:
            title = "Cultivation stress workloads"
        elif workload["parameters"].get("postselection_pair"):
            title = "Selection controls · postselection off"
        elif entry["variant"] == "clifft-bulk":
            title = "Bulk calls"
        elif family in {"prepared-basis-arithmetic", "code-switching", "reed-muller"}:
            title = "Arithmetic and code switching · postselection off"
        elif family in {
            "magic-state-cultivation", "magic-state-distillation",
            "surface-code-memory", "coherent-noise-surface-code",
        }:
            semantics = workload["semantics"]
            selection = (
                "all-detector postselection" if semantics["postselect_all_detectors"]
                else "selected detectors" if semantics.get("postselection_detector_indices")
                else "postselection off"
            )
            title = f"QEC · {selection}"
        else:
            title = entry["variant"].replace("-", " ").capitalize()
        # Never merge different baseline configurations, even with the same family.
        groups.setdefault((entry["variant"], title), []).append(entry)
    panels = []
    for (_, title), group in groups.items():
        def rate(entry):
            case = entry["cases"].get(entry["variant"])
            return (
                report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
                if case is not None and case["status"] == "success" else -1
            )
        ordered = sorted(group, key=rate, reverse=True)
        for start in range(0, len(ordered), 7):
            heading = title if start == 0 else title + " (continued)"
            panels.append((heading, ordered[start:start + 7]))
    return panels or [("No matching comparisons selected", [])]


def _figure_stem(kind: str, index: int) -> str:
    return kind if index == 0 else f"{kind}-{index + 1}"


def _number_label(value: float) -> str:
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= divisor:
            return f"{value / divisor:.3g}{suffix}"
    return f"{value:.3g}"


def _style_axis(axis, labels: list[str], grid: str) -> None:
    axis.set_yticks(range(len(labels)), labels, fontsize=13)
    axis.set_ylim(len(labels) - 0.5, -0.6)
    axis.grid(axis="x", color=grid, linewidth=1)
    axis.set_axisbelow(True)
    axis.tick_params(axis="y", length=0, pad=12)
    for spine in axis.spines.values():
        spine.set_visible(False)


def _render_throughput(report, title, entries, colors, grid):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    # Match color and shape across panels, regardless of campaign iteration order.
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
    # Unsupported pairings are in the outcome table, not repeated on every row.
    visible = [
        [(v, case) for v, case in entry["cases"].items()
         if _outcome(case, _representative(entry), report["variants"].get(v)) != "unsupported"]
        for entry in entries
    ]
    names = [_label(_representative(entry)) for entry in entries]
    names = [
        textwrap.fill(_representative(entry)["workload"]["id"] if names.count(name) > 1
                      else name, 28)
        for name, entry in zip(names, entries, strict=True)
    ]
    label_height = max((name.count("\n") + 2 for name in names), default=2) * 0.23 + 0.16
    row_height = max(label_height, max((len(cases) for cases in visible), default=1) * 0.27 + 0.22)
    figure, axis = plt.subplots(figsize=(12, 3.2 + max(len(entries), 1) * row_height))
    figure.subplots_adjust(left=0.31, right=0.95, top=1 - 1.65 / figure.get_figheight(),
                           bottom=0.95 / figure.get_figheight())
    rates = [
        report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
        for cases in visible for _, case in cases
        if case is not None and case["status"] == "success"
    ]
    low = 10 ** math.floor(math.log10(min(rates))) / 1.3 if rates else 0.1
    high = max(rates, default=1)
    axis.set_xscale("log")
    # Reserve physical room for a rate label even across a very broad log range.
    axis.set_xlim(low, low * (high / low) ** 1.13 * 1.5)
    axis.xaxis.set_major_locator(LogLocator(base=10, numticks=7))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _number_label(value)))
    axis.xaxis.set_minor_formatter(NullFormatter())
    labels = []
    used = set()
    for index, (entry, cases) in enumerate(zip(entries, visible, strict=True)):
        representative = _representative(entry)
        calls = representative["execution"]["shots_per_call"]
        unit = "shot" if calls == 1 else "shots"
        labels.append(f"{names[index]}\n{calls:,} {unit}/call")
        cases.sort(key=lambda item: variants.index(item[0]))
        for offset, (variant, case) in enumerate(cases):
            color, marker = styles[variant]
            y = index + (offset - (len(cases) - 1) / 2) * (0.27 / row_height)
            state = _outcome(case, representative, report["variants"].get(variant))
            used.add(variant)
            if state == "measured":
                rate = report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
                axis.hlines(y, low, rate, color=color, linewidth=2.5)
                axis.plot(rate, y, marker, color=color, markersize=8)
                axis.annotate(f"{_number_label(rate)}/s", (rate, y), xytext=(8, 0),
                              textcoords="offset points", va="center", color=color,
                              fontsize=12, fontweight="bold")
            else:
                simulator = report["variants"].get(variant)
                name = simulator["name"] if simulator else variant
                axis.text(0.02, y, f"{name}: {state}", transform=axis.get_yaxis_transform(),
                          va="center", fontsize=11, color=color)
        if index < len(entries) - 1:
            axis.axhline(index + 0.5, color=grid, linewidth=0.7)
    _style_axis(axis, labels, grid)
    axis.set_xlabel("Attempted shots per second · log scale · higher is faster", labelpad=14)
    handles = []
    for variant in variants:
        if variant not in used:
            continue
        simulator = report["variants"].get(variant)
        name = f"{simulator['name']} {simulator['display_version']}" if simulator else variant
        color, marker = styles[variant]
        handles.append(Line2D([], [], color=color, marker=marker, linewidth=2.5, label=name))
    if handles:
        axis.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1.02),
                    borderaxespad=0, ncol=min(3, len(handles)), frameon=False,
                    fontsize=11, labelcolor="linecolor", handlelength=1.3, columnspacing=1.3)
    figure.suptitle(title, x=0.04, ha="left", fontsize=17, fontweight="bold",
                    y=1 - 0.12 / figure.get_figheight())
    figure.text(0.04, 0.18 / figure.get_figheight(),
                "Compilation excluded. Missing markers: see outcomes in review.md.",
                fontsize=10)
    return figure


def _render_release(report, colors, foreground, grid):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    measured = [p for p in report["release"] if p["ratio"] is not None]
    small = sorted((p for p in measured if 0.5 <= p["ratio"] <= 1.5),
                   key=lambda p: p["ratio"], reverse=True)
    large = sorted((p for p in measured if p not in small),
                   key=lambda p: p["ratio"], reverse=True)
    panels = [(False, small), (True, large)]
    panels = [(log, pairs) for log, pairs in panels if pairs] or [(False, [])]
    notes = []
    for pair in report["release"]:
        if pair["ratio"] is not None:
            continue
        case = pair["candidate"] or pair["baseline"]
        states = [
            f"{name} {_outcome(pair[side], pair[other], report['variants'].get(pair[key]))}"
            for name, side, other, key in (
                ("previous", "baseline", "candidate", "baseline_variant"),
                ("current", "candidate", "baseline", "candidate_variant"),
            )
            if _outcome(pair[side], pair[other], report["variants"].get(pair[key])) != "measured"
        ]
        notes.append(f"{_label(case)}: {', '.join(states)}")
    note = textwrap.fill("No release ratio — " + "; ".join(notes), 110) if notes else ""
    footer = 0.55 + (note.count("\n") + 1) * 0.20 if note else 0.55
    heights = [max(len(pairs), 1) * 0.55 + 1.15 for _, pairs in panels]
    height = sum(heights) + 0.8 + footer
    figure = plt.figure(figsize=(12, height))
    top = height - 0.8
    for (log, pairs), panel_height in zip(panels, heights, strict=True):
        plot_height = panel_height - 1.15
        axis = figure.add_axes((0.31, (top - plot_height - 0.35) / height,
                                0.63, plot_height / height))
        top -= panel_height
        labels = [textwrap.fill(_label(p["candidate"] or p["baseline"]), 29) for p in pairs]
        _style_axis(axis, labels, grid)
        axis.set_title("Larger changes · separate log scale" if log else "Percentage changes",
                       loc="left", fontsize=13, pad=12)
        anchor = 1 if log else 0
        axis.axvline(anchor, color=foreground, linewidth=1, linestyle="--")
        if log:
            ratios = [1, *(p["ratio"] for p in pairs)]
            low, high = min(ratios), max(ratios)
            padding = max(math.log(high / low) * 0.22, math.log(1.5))
            axis.set_xscale("log")
            axis.set_xlim(low / math.exp(padding), high * math.exp(padding))
            axis.xaxis.set_major_locator(LogLocator(base=10, numticks=6))
            axis.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}×"))
            axis.xaxis.set_minor_formatter(NullFormatter())
            axis.set_xlabel("Current / previous throughput · 1× = unchanged", labelpad=10)
        else:
            span = max([3, *(abs((p["ratio"] - 1) * 100) * 1.4 for p in pairs)])
            axis.set_xlim(-span, span)
            axis.axvspan(-1.1, 1.1, color=grid, alpha=0.5, zorder=0)
            axis.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:+g}%"))
            axis.set_xlabel("Throughput change · 0% = unchanged", labelpad=10)
        for index, pair in enumerate(pairs):
            value = pair["ratio"] if log else (pair["ratio"] - 1) * 100
            label = f"{value:.3g}×" if log else f"{value:+.2f}%"
            color = colors[0] if value >= anchor else colors[-1]
            axis.hlines(index, anchor, value, color=color, linewidth=3)
            axis.plot(value, index, "o", color=color, markersize=9)
            axis.annotate(label, (value, index),
                          xytext=(9 if value >= anchor else -9, 0),
                          ha="left" if value >= anchor else "right",
                          textcoords="offset points", va="center",
                          fontsize=13, fontweight="bold", color=color)
        if not pairs:
            axis.text(0.5, 0.5, "No measured release pairs", ha="center",
                      transform=axis.transAxes)
    versions = ""
    if report["release"]:
        pair = report["release"][0]
        current, previous = (
            report["variants"].get(pair[key], {}).get("display_version", "unmeasured")
            for key in ("candidate_variant", "baseline_variant")
        )
        versions = f" · {current} vs {previous}"
    figure.suptitle("Clifft release changes" + versions, x=0.04, ha="left",
                    fontsize=17, fontweight="bold", y=1 - 0.12 / height)
    caption = "Shaded ±1.1%: contract's inconclusive band, not a confidence interval."
    if not any(not log for log, _ in panels):
        caption = "Ratios compare paired sample medians; higher is faster."
    figure.text(0.04, (footer - 0.22) / height, caption, fontsize=10)
    if note:
        figure.text(0.04, 0.12 / height, note, fontsize=10, va="bottom")
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
                panels = (
                    [("", [])] if kind == "release-change"
                    else _throughput_panels(report, kind)
                )
                for index, (heading, entries) in enumerate(panels):
                    if kind == "release-change":
                        figure = _render_release(report, colors, foreground, grid)
                    else:
                        name = (
                            "Clifft throughput" if kind == "clifft-throughput" else "CPU simulators"
                        )
                        figure = _render_throughput(
                            report, f"{name}\n{heading}", entries, colors, grid,
                        )
                    if qualifier:
                        figure.text(0.5, 1.01, qualifier.rstrip(" ·"), ha="center",
                                    transform=figure.transFigure, fontsize=14, color=colors[-1])
                    path = output_dir / f"{_figure_stem(kind, index)}-{theme}.png"
                    figure.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.15)
                    plt.close(figure)
                    outputs.append(path)
            # Remove obsolete numbered pages when regenerating a smaller selection.
            for kind in FIGURES:
                for path in output_dir.glob(f"{kind}-*-{theme}.png"):
                    page = path.name.removeprefix(f"{kind}-").removesuffix(f"-{theme}.png")
                    if page.isdecimal() and path not in outputs:
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
