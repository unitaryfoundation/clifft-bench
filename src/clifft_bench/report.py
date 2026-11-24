"""Render release comparisons and explicitly sourced simulator throughput."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from clifft_bench.results import (
    _compatible,
    case_rows,
    comparison_pairs,
    export_csv,
    read_result,
    write_json,
)

THROUGHPUT_MODES = (
    (
        "off",
        "simulator-throughput-packed",
        "CPU simulator throughput — packed samples",
        "Postselection off · packed detector and observable samples",
    ),
    (
        "on",
        "simulator-throughput-survivors",
        "CPU simulator throughput — survivor counts",
        "Postselection on · survivor counts",
    ),
)
THROUGHPUT_STEMS = tuple(stem for _, stem, _, _ in THROUGHPUT_MODES)
FIGURES = (*THROUGHPUT_STEMS, "release-change")
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


def _measurement_rows(document: dict) -> dict[str, dict]:
    """Match core counts within one execution, using the full measurement inputs."""
    rows = {row["case_id"]: row for row in case_rows(document)}
    groups = {}
    for case in document["cases"]:
        execution, workload = case["execution"], case["workload"]
        key = (
            case["simulator"]["implementation_id"], case["simulator"]["commit_sha"],
            workload["id"], workload["artifact"]["sha256"],
            json.dumps(workload["semantics"], sort_keys=True),
            json.dumps(execution["options"], sort_keys=True),
            *(execution[k] for k in ("postselect", "shots_per_call", "batch_size",
                                     "memory_limit_bytes", "min_sample_seconds",
                                     "request_timeout_seconds", "repetitions")),
        )
        row = rows[case["case_id"]]
        row["eight_core_speedup"] = None
        groups.setdefault(key, {}).setdefault(execution["threads_requested"], []).append(row)
    for budgets in groups.values():
        single, multi = budgets.get(1, []), budgets.get(8, [])
        if (len(single) == len(multi) == 1
                and single[0]["status"] == multi[0]["status"] == "success"):
            rate = "median_attempted_shots_per_second"
            multi[0]["eight_core_speedup"] = multi[0][rate] / single[0][rate]
    return rows


def build_report(document: dict, *, peer_document: dict | None = None) -> dict[str, Any]:
    rows = _measurement_rows(document)
    pairs = comparison_pairs(document)
    variants = {case["variant_id"]: case["simulator"] for case in document["cases"]}
    release_ids = set()
    previous = set()
    for comparison in document["campaign"].get("comparisons", []):
        baseline = variants.get(comparison["baseline_variant"])
        candidates = [variants.get(v) for v in comparison["candidate_variants"]]
        if baseline and baseline["adapter"] == "clifft" and all(
            candidate and candidate["adapter"] == "clifft"
            and candidate["implementation_id"] != baseline["implementation_id"]
            for candidate in candidates
        ):
            release_ids.add(comparison["id"])
            previous.add(baseline["implementation_id"])
    release = [p for p in pairs if p["comparison_id"] in release_ids]
    current = [
        c for c in document["cases"]
        if c["simulator"]["adapter"] == "clifft"
        and c["simulator"]["implementation_id"] not in previous
    ]
    groups: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        if pair["comparison_id"] in release_ids:
            continue
        key = pair["baseline_variant"], pair["workload_id"]
        group = groups.setdefault(key, {"workload_id": key[1], "variant": key[0], "cases": {}})
        group["cases"][pair["baseline_variant"]] = pair["baseline"]
        group["cases"][pair["candidate_variant"]] = pair["candidate"]
    report = {
        "document": document,
        "sources": {"primary": {"document": document, "cases": document["cases"]}},
        "rows": rows,
        "pairs": pairs,
        "current": current,
        "release": release,
        "tools": list(groups.values()),
        "variants": variants,
    }
    if peer_document is not None:
        _add_peer_results(report, peer_document)
    return report


def _add_peer_results(report: dict, peers: dict) -> None:
    """Join throughput views only; each raw execution and its paired ratios stay intact."""
    document = report["document"]
    if any(c["simulator"]["adapter"] != "clifft" for c in document["cases"]):
        raise ValueError("--peer-results requires a Clifft-only primary execution")
    if document["run"]["id"] == peers["run"]["id"]:
        raise ValueError("peer results must come from a separate execution")
    for source in (document, peers):
        if source["run"]["selection"] is not None:
            raise ValueError("peer reuse requires complete declared executions")
    if document["run"]["classification"] == "official" and not document["campaign"].get(
        "hardware_epoch"
    ):
        raise ValueError("peer reuse requires an explicit official hardware epoch")
    for key in ("classification", "hardware_epoch", "reference_host", "measurement",
                "resources", "seed"):
        if document["campaign"].get(key) != peers["campaign"].get(key):
            raise ValueError(f"incompatible peer collection {key}")
    for key in ("cpu_model", "os", "os_release", "machine", "python"):
        if document["runner"].get(key) != peers["runner"].get(key):
            raise ValueError(f"incompatible peer host {key}")

    peer_cases = [c for c in peers["cases"] if c["simulator"]["adapter"] != "clifft"]
    if not peer_cases:
        raise ValueError("peer execution has no peer measurements")
    if ({c["case_id"] for c in peer_cases} & report["rows"].keys()
            or {c["variant_id"] for c in peer_cases} & report["variants"].keys()
            or {c["simulator"]["implementation_id"] for c in peer_cases}
            & {c["simulator"]["implementation_id"] for c in document["cases"]}):
        raise ValueError("ambiguous peer case, variant, or implementation identity")
    source = build_report(peers)
    current = {(c["variant_id"], c["workload"]["id"]): c for c in report["current"]}
    groups, variants = {}, {}
    for entry in source["tools"]:
        baseline = entry["variant"]
        if source["variants"][baseline]["adapter"] != "clifft":
            raise ValueError("peer comparisons must use a current Clifft baseline")
        candidates = set(entry["cases"]) - {baseline}
        if any(source["variants"][v]["adapter"] == "clifft" for v in candidates):
            raise ValueError("peer comparisons must contain only non-Clifft candidates")
        key = baseline, entry["workload_id"]
        replacement = current.get(key)
        if replacement is None:
            raise ValueError(f"missing current Clifft case for peer comparison: {key}")
        for case in entry["cases"].values():
            if case is not None:
                _compatible(replacement, case)
        groups[key] = {**entry, "cases": {**entry["cases"], baseline: replacement}}
        variants.setdefault(baseline, set()).update(candidates)
    used = {c["case_id"] for e in groups.values() for v, c in e["cases"].items()
            if v != e["variant"] and c is not None}
    if used != {c["case_id"] for c in peer_cases}:
        raise ValueError("peer measurements require declared Clifft comparisons")
    # Newly measured Clifft workloads retain explicit absent/unsupported peer outcomes.
    for (variant, workload), case in current.items():
        if variant in variants and (variant, workload) not in groups:
            groups[variant, workload] = {
                "variant": variant, "workload_id": workload,
                "cases": {variant: case, **dict.fromkeys(sorted(variants[variant]))},
            }
    report["tools"] = list(groups.values())
    report["sources"]["peers"] = {"document": peers, "cases": peer_cases}
    report["rows"].update({key: row for key, row in source["rows"].items() if key in used})
    report["variants"].update({c["variant_id"]: c["simulator"] for c in peer_cases})


def _outcome(case: dict | None, other: dict | None, simulator: dict | None) -> str:
    if case is not None:
        return "measured" if case["status"] == "success" else case["status"]
    if other is not None and simulator is not None:
        if simulator["adapter"] not in other["workload"]["compatible_adapters"]:
            return "unsupported"
    return "not measured"


def _release_change_band(document: dict) -> float | None:
    """Paired single-core release runs on the commissioned host epoch."""
    if (document["run"]["classification"] == "official"
            and document["campaign"].get("hardware_epoch") == "aws-m7a-xlarge-ubuntu2404-v1"):
        return 1.1
    return None


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
    peer_source = report["sources"].get("peers")
    if peer_source is not None:
        peer_document = peer_source["document"]
        peer_run = peer_document["run"]
        lines += [
            "## Collection sources", "",
            "Clifft and paired release changes use the primary execution above.",
            f"Peer throughput is reused from run `{peer_run['id']}`, collected "
            f"{peer_run['finished_at']}.",
            f"Peer source: `{peer_document['runner']['suite_source']['commit']}`.",
            "These are separate collections on matching reference hardware and protocols;",
            "cross-collection throughput is not a fresh paired comparison.",
            "The peer versions below retain their source measurements; peers were not rerun.",
            "",
        ]
    cases = [case for source in report["sources"].values() for case in source["cases"]]
    lines += [
        "## Measured implementations",
        "",
        "| Implementation | Exact version | Commit |",
        "| --- | --- | --- |",
    ]
    identities = {c["simulator"]["implementation_id"]: c["simulator"] for c in cases}
    for identifier, simulator in identities.items():
        lines.append(f"| {identifier} | {simulator['version']} | `{simulator['commit_sha']}` |")
    lines += ["", "## Outcomes", "", "| Case | Outcome | Detail |", "| --- | --- | --- |"]
    for case in cases:
        error = case["error"]
        detail = (
            f"{error['phase']}: {error['type']}: {error['message']}"
            if error
            else (
                f"{len(case['samples'])} samples; "
                f"batch {case['execution']['batch_size_effective'] or 'unknown'}; "
                f"{case['execution']['threads_requested']} physical cores"
            )
        )
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {case['case_id']} | {case['status']} | {detail} |")
    lines += ["", "## Measurements", "",
              "| Case | Postselection | Shots per call | Compile (s) | First call (s) | "
              "Peak active width | Attempted shots/s (median) | Cores | 8-core / 1-core |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for case in cases:
        row = report["rows"][case["case_id"]]
        values = [f"{row[key]:.6g}" if row[key] not in ("", None) else "—" for key in (
            "compile_seconds", "first_call_warmup_seconds", "peak_active_width",
            "median_attempted_shots_per_second",
        )]
        ratio = row["eight_core_speedup"]
        speedup = f"{ratio:.4g}×" if ratio is not None else "—"
        lines.append(f"| {case['case_id']} | {row['postselect']} | "
                     f"{row['shots_per_call']} | {' | '.join(values)} | "
                     f"{row['threads_requested']} | {speedup} |")
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
    if peer_source is not None:
        lines += ["", "The paired table uses only the primary execution. Peer cases in the",
                  "outcomes and throughput figures belong to the peer source above.",
                  "Each source raw/CSV retains its own call sizes, selection, and samples."]
    lines += [
        "", "## Figures", "",
        "[Throughput data for the docs widget](simulator-throughput.json)", "",
        "Rates count attempted shots after the first full call; compilation and that call",
        "are reported separately. Later lazy planning remains timed. The main figures",
        "share circuit selection and thematic ordering.",
        "Each case uses a fresh worker. Tsim retains its upstream Numba disk cache.",
        "Peer-comparison throughput figures are split by output mode with 1-core and",
        "8-core budgets side by side; each thematic band has its own logarithmic scale.",
        "When peer comparisons are present, peerless circuits are omitted from the",
        "static peer-comparison PNGs while remaining in the tables above, `cases.csv`,",
        "`raw.json`, and `simulator-throughput.json`.",
        "The unverified cultivation proxy and bulk-call variants",
        "remain in the detailed results above. Colors and marker shapes identify simulators.",
        "Unsupported pairings have",
        "no marker; unmeasured and failed cases are labeled. Exact identities and outcomes",
        "are recorded above. Each source cases.csv/raw.json retains call sizes and postselection.",
        "",
        "Release changes use a single logarithmic ratio axis when any change exceeds ±50%,",
        "and a linear percentage axis when all changes remain within ±50%, with core",
        "budgets side by side. Unpaired workloads have no ratio.",
    ]
    band = _release_change_band(document)
    if band is not None:
        lines.append(f"The shaded ±{band}% band marks the inconclusive range for this host epoch.")
    lines.append("")
    figure_items = [
        *((stem, title) for _, stem, title, _ in _throughput_figure_specs(report)),
        ("release-change", "Clifft release changes"),
    ]
    for kind, title in figure_items:
        lines += [
            f"### {title}", "", f"[Dark version]({kind}-dark.png)", "",
            f"![{title}]({kind}-light.png)", "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _workload_label(case: dict) -> str:
    workload = case["workload"]
    family, parameters = workload["family"], workload["parameters"]
    if family == "magic-state-cultivation":
        fold = workload["id"].startswith("fold-transversal-")
        name = "Fold-transversal" if fold else "Cultivation"
        return f"{name} d{parameters['distance']}"
    if family in {"surface-code-memory", "coherent-noise-surface-code"}:
        name = "Surface code" if family == "surface-code-memory" else "Coherent"
        return f"{name} d{parameters['distance']}, r{parameters['rounds']}"
    if family == "magic-state-distillation":
        if workload["id"].startswith("cain-15to1-"):
            return f"Cain 15-to-1 d{parameters['distance']}"
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
        not variant.startswith("clifft-bulk")
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


def _number_label(value: float) -> str:
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= divisor:
            return f"{value / divisor:.3g}{suffix}"
    return f"{value:.3g}"


def _blend_hex(base: str, tint: str, alpha: float) -> str:
    b = tuple(int(base[i:i + 2], 16) for i in (1, 3, 5))
    t = tuple(int(tint[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(bc * (1 - alpha) + tc * alpha) for bc, tc in zip(b, t, strict=True))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _draw_section_band(
    axes: list, y0: float, y1: float, heading: str,
    *, header_left: float, col_gap: float, col_w: float, fill: str,
) -> None:
    gap_span = col_gap / col_w if len(axes) > 1 else 0.0
    for index, axis in enumerate(axes):
        xmin = header_left if index == 0 else 0.0
        xmax = 1.0 + (gap_span if index < len(axes) - 1 else 0.0)
        axis.axhspan(y0, y1, xmin=xmin, xmax=xmax, color=fill, clip_on=False, zorder=2)
    axes[0].text(
        header_left + 0.02, (y0 + y1) / 2, heading,
        transform=axes[0].get_yaxis_transform(), va="center",
        fontsize=11.5, fontweight="bold", zorder=3,
    )


def _style_axis(axis, extent: float, grid: str) -> None:
    axis.set_ylim(extent, -0.02)
    axis.set_yticks([])
    axis.grid(axis="x", color=grid, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(axis="x", labelsize=11)
    for spine in axis.spines.values():
        spine.set_visible(False)


def _throughput_series(report: dict, entries: list[dict]) -> list[dict]:
    adapters = ("clifft", "symft", "xtim", "stim", "tsim")
    markers = ("circle", "square", "diamond", "triangle", "plus")
    variants = list(dict.fromkeys(v for entry in entries for v in entry["cases"]))
    variants.sort(key=lambda v: (
        adapters.index(report["variants"][v]["adapter"])
        if v in report["variants"] and report["variants"][v]["adapter"] in adapters
        else len(adapters), v,
    ))
    series = []
    for index, variant in enumerate(variants):
        simulator = report["variants"].get(variant)
        adapter = simulator["adapter"] if simulator else ""
        position = adapters.index(adapter) if adapter in adapters else index
        series.append({
            "variant_id": variant,
            "simulator": {key: simulator[key] for key in (
                "implementation_id", "adapter", "name", "version", "display_version",
                "commit_sha", "source_url",
            )} if simulator else None,
            "marker": markers[position % len(markers)],
            "colors": {theme: colors[position % len(colors)]
                       for theme, (_, _, _, colors) in THEMES.items()},
        })
    return series


def _throughput_panels(entries: list[dict]) -> dict[str, list[dict]]:
    panels = {}
    for entry in entries:
        case = _representative(entry)
        family = case["workload"]["family"]
        heading = _workload_group(case)[2]
        if family == "quantum-volume":
            heading = "Quantum Volume"
        elif family in {"prepared-basis-arithmetic", "code-switching"}:
            heading = "Arithmetic & code switching"
        endpoint = ("Postselection on — survivor counts"
                    if case["execution"]["postselect"] == "on"
                    else "Postselection off — packed detector and observable samples")
        threads = case["execution"]["threads_requested"]
        core_label = "1 core" if threads == 1 else f"{threads} cores"
        panels.setdefault(f"{endpoint} · {core_label} · {heading}", []).append(entry)
    return panels or {"Other workloads": []}


def _throughput_limits(rates: list[float]) -> tuple[float, float]:
    low = min(rates) / 1.5 if rates else 0.1
    peak = max(rates, default=1)
    return low, peak * max(1.5, (peak / low) ** 0.14)


def throughput_data(report: dict) -> dict:
    """Project the plotted view; raw executions remain the authoritative evidence."""
    sources = {}
    case_sources = {}
    for role, source in report["sources"].items():
        document = source["document"]
        sources[role] = {
            "run": {key: document["run"][key] for key in (
                "id", "profile_id", "classification", "started_at", "finished_at",
                "selection", "overrides",
            )},
            "suite_source": document["runner"]["suite_source"],
            "host": {key: document["runner"].get(key) for key in (
                "cpu_model", "os", "os_release", "machine", "python",
            )},
            "hardware_epoch": document["campaign"].get("hardware_epoch"),
            "measurement": document["campaign"]["measurement"],
            "resources": document["campaign"]["resources"],
        }
        case_sources.update({case["case_id"]: role for case in source["cases"]})
    entries = _throughput_entries(report)
    series = _throughput_series(report, entries)
    variants = [item["variant_id"] for item in series]
    panels = []
    for title, group in _throughput_panels(entries).items():
        rows, rates = [], []
        for entry in group:
            representative = _representative(entry)
            workload = representative["workload"]
            values = []
            for variant in sorted(entry["cases"], key=variants.index):
                case = entry["cases"][variant]
                outcome = _outcome(case, representative, report["variants"].get(variant))
                row = report["rows"][case["case_id"]] if case is not None else {}
                value = {
                    "variant_id": variant,
                    "outcome": outcome,
                    "case_id": case["case_id"] if case is not None else None,
                    "source": case_sources[case["case_id"]] if case is not None else None,
                    "error": case["error"] if case is not None else None,
                    **{key: None if row.get(key, "") == "" else row[key] for key in (
                        "median_attempted_shots_per_second", "mad_attempted_shots_per_second",
                        "sample_count", "shots_per_call", "batch_size_effective",
                        "threads_requested", "threads_effective", "eight_core_speedup",
                        "peak_active_width", "compile_seconds", "first_call_warmup_seconds",
                    )},
                }
                values.append(value)
                if outcome == "measured":
                    rates.append(value["median_attempted_shots_per_second"])
            rows.append({
                "workload_id": workload["id"], "baseline_variant": entry["variant"],
                "label": _workload_label(representative),
                "artifact_sha256": workload["artifact"]["sha256"],
                "semantics": workload["semantics"],
                "postselect": representative["execution"]["postselect"], "values": values,
            })
        panels.append({"title": title, "x_limits": _throughput_limits(rates), "rows": rows})
    return {
        "format": "clifft-bench/throughput/v2",
        "sources": sources,
        "themes": {theme: {"background": background, "foreground": foreground, "grid": grid}
                   for theme, (background, foreground, grid, _) in THEMES.items()},
        "series": series,
        "panels": panels,
    }


def _core_label(threads: int) -> str:
    return "1 core" if threads == 1 else f"{threads} cores"


def _configure_log_x_axis(axis, low: float, high: float, formatter) -> None:
    from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator

    axis.set_xscale("log")
    axis.set_xlim(low, high)
    if high / low < 30:
        axis.xaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0), numticks=6))
    else:
        axis.xaxis.set_major_locator(LogLocator(base=10, numticks=6))
    axis.xaxis.set_major_formatter(FuncFormatter(formatter))
    axis.xaxis.set_minor_locator(NullLocator())


def _visible_cases(entry: dict, report: dict, variants: list[str]) -> list[tuple[str, dict | None]]:
    representative = _representative(entry)
    return sorted(
        (
            (variant, case)
            for variant, case in entry["cases"].items()
            if _outcome(case, representative, report["variants"].get(variant)) != "unsupported"
        ),
        key=lambda item: variants.index(item[0]),
    )


def _has_peer_comparisons(report: dict, entries: list[dict], variants: list[str]) -> bool:
    return any(
        any(variant != entry["variant"] for variant, _ in _visible_cases(entry, report, variants))
        for entry in entries
    )


def _throughput_figure_specs(report: dict) -> list[tuple[str, str, str, str]]:
    present = {_representative(entry)["execution"]["postselect"]
               for entry in _throughput_entries(report)}
    specs = [spec for spec in THROUGHPUT_MODES if spec[0] in present]
    return specs or [THROUGHPUT_MODES[0]]


def _throughput_plot_limits(rates: list[float], *, columns: int) -> tuple[float, float]:
    if columns <= 1:
        return _throughput_limits(rates)
    low = min(rates) / 1.5 if rates else 0.1
    peak = max(rates, default=1)
    return low, peak * max(2.2, (peak / low) ** 0.28)


def _throughput_band_key(case: dict) -> tuple[int, str]:
    family = case["workload"]["family"]
    if family == "quantum-volume":
        return len(WORKLOAD_GROUPS) + 1, "quantum-volume"
    if family in {"prepared-basis-arithmetic", "code-switching"}:
        return len(WORKLOAD_GROUPS), "arithmetic-code-switching"
    group_index, _, heading = _workload_group(case)
    return group_index, heading


def _throughput_band_heading(rows: list[dict]) -> str:
    families = {row["case"]["workload"]["family"] for row in rows}
    if families == {"quantum-volume"}:
        return "Quantum Volume"
    if families <= {"prepared-basis-arithmetic", "code-switching"}:
        if families == {"prepared-basis-arithmetic"}:
            return "Prepared-basis arithmetic"
        if families == {"code-switching"}:
            return "Code switching"
        return "Arithmetic & code switching"
    return _workload_group(rows[0]["case"])[2]


def _throughput_mode_bands(
    report: dict, entries: list[dict], variants: list[str], postselect: str,
) -> tuple[list[int], list[tuple[str, list[dict]]]]:
    mode_entries = [
        entry for entry in entries
        if _representative(entry)["execution"]["postselect"] == postselect
    ]
    has_peers = _has_peer_comparisons(report, entries, variants)
    grouped_rows: dict[str, dict[str, Any]] = {}
    for entry in mode_entries:
        case = _representative(entry)
        workload_id = case["workload"]["id"]
        threads = case["execution"]["threads_requested"]
        visible = _visible_cases(entry, report, variants)
        row = grouped_rows.setdefault(workload_id, {
            "workload_id": workload_id,
            "case": case,
            "label": _workload_label(case),
            "by_threads": {},
        })
        row["by_threads"][threads] = (entry, visible)
    rows = list(grouped_rows.values())
    if has_peers:
        rows = [
            row for row in rows
            if any(
                variant != entry["variant"]
                for entry, visible in row["by_threads"].values()
                for variant, _ in visible
            )
        ]
    budgets = sorted({
        threads for row in rows for threads in row["by_threads"]
    }) or [1]
    bands_by_key: dict[tuple[int, str], list[dict]] = {}
    for row in rows:
        bands_by_key.setdefault(_throughput_band_key(row["case"]), []).append(row)
    bands = [(_throughput_band_heading(group), group) for group in bands_by_key.values()]
    return budgets, bands or [("Other workloads", [])]


def _render_throughput_mode(
    report: dict, postselect: str, title: str, subtitle: str, theme: str,
):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    background, _, grid, _ = THEMES[theme]
    band_fill = _blend_hex(background, grid, 0.45)
    entries = _throughput_entries(report)
    series = _throughput_series(report, entries)
    variants = [item["variant_id"] for item in series]
    markers = {"circle": "o", "square": "s", "diamond": "D", "triangle": "^", "plus": "P"}
    styles = {item["variant_id"]: (item["colors"][theme], markers[item["marker"]])
              for item in series}

    budgets, bands = _throughput_mode_bands(report, entries, variants, postselect)
    num_cols = len(budgets)
    fig_width = 13.0 if num_cols > 1 else 11.0
    left = 0.235 if num_cols > 1 else 0.285
    right = 0.975
    col_gap = 0.032 if num_cols > 1 else 0.0
    col_w = (right - left - col_gap * (num_cols - 1)) / num_cols
    header_left = (0.02 - left) / col_w
    bar_pitch = 0.21

    layouts = []
    for heading, rows in bands:
        names = [textwrap.fill(row["label"], 28) for row in rows]
        heights = []
        for row, name in zip(rows, names, strict=True):
            max_bars = max((len(visible) for _, visible in row["by_threads"].values()), default=1)
            heights.append(max(
                0.50, max_bars * bar_pitch + 0.16, (name.count("\n") + 1) * 0.22 + 0.16,
            ))
        positions, y = [], 0.46
        for row_height in heights:
            positions.append(y + row_height / 2)
            y += row_height
        layouts.append((heading, rows, names, heights, positions, max(0.75, y + 0.08)))

    top = 1.18 if num_cols > 1 else 1.06
    band_gap = 0.42
    bottom_pad = 0.64
    height = (
        top + sum(layout[-1] for layout in layouts)
        + band_gap * max(0, len(layouts) - 1) + bottom_pad
    )
    figure = plt.figure(figsize=(fig_width, height))
    used, consumed = set(), top

    for band_index, (heading, rows, names, heights, positions, extent) in enumerate(layouts):
        bottom = height - consumed - extent
        rates = [
            report["rows"][case["case_id"]]["median_attempted_shots_per_second"]
            for row in rows
            for _, visible in row["by_threads"].values()
            for _, case in visible
            if case is not None and case["status"] == "success"
        ]
        low, high = _throughput_plot_limits(rates, columns=num_cols)
        col_axes = []
        for col_index, threads in enumerate(budgets):
            x0 = left + col_index * (col_w + col_gap)
            axis = figure.add_axes(
                (x0, bottom / height, col_w, extent / height),
                sharey=col_axes[0] if col_axes else None,
            )
            col_axes.append(axis)
            _configure_log_x_axis(axis, low, high, lambda value, _: _number_label(value))
            _style_axis(axis, extent, grid)
            axis.tick_params(axis="x", labelsize=10.5)
            if band_index == 0 and num_cols > 1:
                axis.set_title(_core_label(threads), fontsize=12.5, fontweight="bold", pad=12)
            if band_index == len(layouts) - 1:
                axis.set_xlabel(
                    "Attempted shots per second · log scale · higher is faster",
                    labelpad=8, fontsize=10.5,
                )

        _draw_section_band(
            col_axes, -0.02, 0.34, heading,
            header_left=header_left, col_gap=col_gap, col_w=col_w, fill=band_fill,
        )

        for row_index, (row, name, y, row_height) in enumerate(
            zip(rows, names, positions, heights, strict=True)
        ):
            col_axes[0].text(
                -0.035, y, name, transform=col_axes[0].get_yaxis_transform(),
                ha="right", va="center", fontsize=11.5,
            )
            for col_index, threads in enumerate(budgets):
                axis = col_axes[col_index]
                cell = row["by_threads"].get(threads)
                if cell is not None:
                    entry, visible = cell
                    representative = _representative(entry)
                    for offset, (variant, case) in enumerate(visible):
                        color, marker = styles[variant]
                        py = y + (offset - (len(visible) - 1) / 2) * bar_pitch
                        state = _outcome(case, representative, report["variants"].get(variant))
                        used.add(variant)
                        if state == "measured":
                            rate = report["rows"][case["case_id"]][
                                "median_attempted_shots_per_second"
                            ]
                            axis.hlines(py, low, rate, color=color, linewidth=2.2, zorder=3)
                            axis.plot(rate, py, marker, color=color, markersize=6.5, zorder=4)
                            axis.annotate(
                                f"{_number_label(rate)}/s", (rate, py), xytext=(6, 0),
                                textcoords="offset points", va="center", color=color,
                                fontsize=10.5, fontweight="bold", zorder=4,
                            )
                        else:
                            simulator = report["variants"].get(variant)
                            label = simulator["name"] if simulator else variant
                            axis.text(
                                0.02, py, f"{label}: {state}",
                                transform=axis.get_yaxis_transform(),
                                va="center", fontsize=10, color=color, zorder=4,
                            )
                if row_index < len(rows) - 1:
                    axis.axhline(y + row_height / 2, color=grid, linewidth=0.5, alpha=0.55)

        if not rows:
            col_axes[0].text(
                0.5, 0.5, "No main workloads selected",
                ha="center", transform=col_axes[0].transAxes,
            )
        consumed += extent + band_gap

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
        handles.append(Line2D([], [], color=color, marker=marker, linewidth=2.2, label=name))
    if handles:
        figure.legend(
            handles=handles, loc="upper left", bbox_to_anchor=(0.015, 1 - 0.58 / height),
            ncol=len(handles), frameon=False, fontsize=11, handlelength=1.4, columnspacing=1.3,
        )
    figure.suptitle(
        title, x=0.02, ha="left", fontsize=16.5, fontweight="bold", y=1 - 0.08 / height,
    )
    figure.text(
        0.02, 1 - 0.40 / height, subtitle, ha="left", va="top",
        fontsize=10.5, alpha=0.78,
    )
    return figure


def _render_throughput(report, theme, *, postselect: str | None = None):
    specs = _throughput_figure_specs(report)
    if postselect is None:
        postselect, _, title, subtitle = specs[0]
    else:
        match = next((s for s in THROUGHPUT_MODES if s[0] == postselect), specs[0])
        postselect, _, title, subtitle = match
    return _render_throughput_mode(report, postselect, title, subtitle, theme)


def _render_release(report, colors, foreground, grid, background="#ffffff"):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    band_fill = _blend_hex(background, grid, 0.42)
    pairs = _release_pairs(report)
    modes = {pair["candidate"]["execution"]["postselect"] for pair in pairs}
    budgets = sorted({pair["candidate"]["execution"]["threads_requested"] for pair in pairs}) or [1]
    grouped: dict[Any, dict[str, Any]] = {}
    for pair in pairs:
        candidate = pair["candidate"]
        mode = candidate["execution"]["postselect"]
        row_key = (pair["workload_id"], mode) if len(modes) > 1 else pair["workload_id"]
        label = _workload_label(candidate)
        if len(modes) > 1:
            suffix = "survivors" if mode == "on" else "packed"
            label = f"{label} ({suffix})"
        row = grouped.setdefault(row_key, {
            "case": candidate, "label": label, "by_threads": {},
        })
        row["by_threads"][candidate["execution"]["threads_requested"]] = pair

    rows = list(grouped.values())
    cases = [row["case"] for row in rows]
    names = [textwrap.fill(row["label"], 28) for row in rows]
    heights = [max(0.46, (name.count("\n") + 1) * 0.22 + 0.20) for name in names]

    headers, positions = [], []
    y, last = 0.0, None
    for case, row_height in zip(cases, heights, strict=True):
        group, _, heading = _workload_group(case)
        if group != last:
            if last is not None:
                y += 0.16
            headers.append((heading, y + 0.14))
            y += 0.46
            last = group
        positions.append(y + row_height / 2)
        y += row_height
    extent = max(0.75, y + 0.08)

    use_log = any(pair["ratio"] < 0.5 or pair["ratio"] > 1.5 for pair in pairs)
    num_cols = len(budgets)
    fig_width = 12.0 if num_cols > 1 else 10.0
    left = 0.255 if num_cols > 1 else 0.30
    right = 0.97
    col_gap = 0.042 if num_cols > 1 else 0.0
    col_w = (right - left - col_gap * (num_cols - 1)) / num_cols
    header_left = (0.02 - left) / col_w

    top = 1.12 if num_cols > 1 else 0.92
    bottom_pad = 0.66
    height = top + extent + bottom_pad
    figure = plt.figure(figsize=(fig_width, height))

    band = _release_change_band(report["document"])
    if use_log:
        ratios = [pair["ratio"] for pair in pairs]
        min_r = min(1.0, *ratios)
        max_r = max(1.0, *ratios)
        low = (
            min_r / max(1.8, (max_r / min_r) ** 0.26)
            if min_r < 1.0
            else 1.0 / max(1.18, max_r ** 0.06)
        )
        high = (
            max_r * max(1.9, (max_r / min_r) ** 0.26)
            if max_r > 1.0
            else max(1.18, (1.0 / min_r) ** 0.06)
        )
    else:
        span = max([3.0, *(abs((pair["ratio"] - 1) * 100) * 1.65 for pair in pairs)])

    axes = []
    for col_index, threads in enumerate(budgets):
        x0 = left + col_index * (col_w + col_gap)
        axis = figure.add_axes(
            (x0, bottom_pad / height, col_w, extent / height),
            sharey=axes[0] if axes else None,
        )
        axes.append(axis)
        if use_log:
            _configure_log_x_axis(axis, low, high, lambda value, _: f"{value:g}×")
            if band is not None:
                axis.axvspan(1 - band / 100, 1 + band / 100, color=grid, alpha=0.45, zorder=0)
            axis.axvline(1, color=foreground, linestyle="--", linewidth=0.9, zorder=1)
            axis.set_xlabel("Current / previous · log scale", labelpad=8, fontsize=10.5)
        else:
            axis.set_xlim(-span, span)
            axis.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}%"))
            if band is not None:
                axis.axvspan(-band, band, color=grid, alpha=0.45, zorder=0)
            axis.axvline(0, color=foreground, linestyle="--", linewidth=0.9, zorder=1)
            axis.set_xlabel("Percentage change · 0% = unchanged", labelpad=8, fontsize=10.5)
        _style_axis(axis, extent, grid)
        axis.tick_params(axis="x", labelsize=10.5)
        if num_cols > 1:
            axis.set_title(_core_label(threads), fontsize=12.5, fontweight="bold", pad=12)

    for heading, hy in headers:
        _draw_section_band(
            axes, hy - 0.16, hy + 0.18, heading,
            header_left=header_left, col_gap=col_gap, col_w=col_w, fill=band_fill,
        )

    for row_index, (row, name, ry, row_height) in enumerate(
        zip(rows, names, positions, heights, strict=True)
    ):
        axes[0].text(
            -0.035, ry, name, transform=axes[0].get_yaxis_transform(),
            ha="right", va="center", fontsize=11.5,
        )
        for col_index, threads in enumerate(budgets):
            axis = axes[col_index]
            pair = row["by_threads"].get(threads)
            if pair is not None:
                ratio = pair["ratio"]
                pct = (ratio - 1) * 100
                value, baseline = (ratio, 1.0) if use_log else (pct, 0.0)
                color = colors[0] if ratio >= 1 else colors[-1]
                if band is not None and abs(pct) <= band:
                    color = foreground
                axis.hlines(ry, baseline, value, color=color, linewidth=2.6, zorder=3)
                axis.plot(value, ry, "o", color=color, markersize=7.5, zorder=4)
                label_text = f"{ratio:.3g}×" if use_log else f"{pct:+.2f}%"
                axis.annotate(
                    label_text, (value, ry),
                    xytext=(7 if ratio >= 1 else -7, 0), textcoords="offset points",
                    ha="left" if ratio >= 1 else "right", va="center",
                    fontsize=11, fontweight="bold", color=color, zorder=4,
                )
            if row_index < len(rows) - 1:
                axis.axhline(ry + row_height / 2, color=grid, linewidth=0.5, alpha=0.45)

    if not pairs:
        axes[0].text(0.5, 0.5, "No measured release pairs", ha="center",
                     transform=axes[0].transAxes)

    versions = ""
    if report["release"]:
        pair = report["release"][0]
        current, previous = (
            report["variants"].get(pair[key], {}).get("display_version", "unmeasured")
            for key in ("candidate_variant", "baseline_variant")
        )
        versions = f" · {current} vs {previous}"
    figure.suptitle(
        "Clifft release changes" + versions, x=0.02, ha="left",
        fontsize=16.5, fontweight="bold", y=1 - 0.08 / height,
    )
    if len(modes) == 1:
        mode = next(iter(modes))
        subtitle = (
            "Survivor counts (postselection on) · higher is faster"
            if mode == "on"
            else "Packed samples (postselection off) · higher is faster"
        )
        figure.text(
            0.02, 1 - 0.42 / height, subtitle, ha="left", va="top",
            fontsize=10.5, alpha=0.78,
        )
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
    throughput_specs = _throughput_figure_specs(report)
    active_stems = {stem for _, stem, _, _ in throughput_specs}
    for theme, (background, foreground, grid, colors) in THEMES.items():
        with plt.rc_context({
            "font.family": "DejaVu Sans", "font.size": 12,
            "text.color": foreground, "axes.labelcolor": foreground,
            "xtick.color": foreground, "ytick.color": foreground,
            "figure.facecolor": background, "axes.facecolor": background,
            "savefig.facecolor": background,
        }):
            for postselect, stem, title, subtitle in throughput_specs:
                figure = _render_throughput_mode(report, postselect, title, subtitle, theme)
                if qualifier:
                    figure.text(0.5, 1.01, qualifier.rstrip(" ·"), ha="center",
                                transform=figure.transFigure, fontsize=14, color=colors[-1])
                path = output_dir / f"{stem}-{theme}.png"
                figure.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.16)
                plt.close(figure)
                outputs.append(path)
            figure = _render_release(report, colors, foreground, grid, background)
            if qualifier:
                figure.text(0.5, 1.01, qualifier.rstrip(" ·"), ha="center",
                            transform=figure.transFigure, fontsize=14, color=colors[-1])
            path = output_dir / f"release-change-{theme}.png"
            figure.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.16)
            plt.close(figure)
            outputs.append(path)
            # Remove superseded generated figures, preserving unrelated images.
            for stale_stem in ("clifft-throughput", "simulator-throughput", *THROUGHPUT_STEMS):
                if stale_stem not in active_stems:
                    (output_dir / f"{stale_stem}-{theme}.png").unlink(missing_ok=True)
            for kind in ("clifft-throughput", "simulator-throughput", *FIGURES):
                for path in output_dir.glob(f"{kind}-*-{theme}.png"):
                    page = path.name.removeprefix(f"{kind}-").removesuffix(f"-{theme}.png")
                    if page.isdecimal():
                        path.unlink()
    return outputs


def report_execution(
    execution_dir: Path, *, output_dir: Path | None = None, check: bool = False,
    peer_results: Path | None = None,
) -> str:
    document = read_result(execution_dir / "raw.json")
    peers = read_result(peer_results / "raw.json") if peer_results is not None else None
    report = build_report(document, peer_document=peers)
    data = throughput_data(report)
    summary = review_summary(report)
    if not check:
        # Regenerate the sole derived table from authoritative evidence; never audit
        # or trust a potentially stale second copy of the same facts.
        export_csv(document, execution_dir / "cases.csv")
        destination = output_dir or execution_dir / "report"
        render(report, destination)
        write_json(destination / "simulator-throughput.json", data)
        (destination / "review.md").write_text(summary)
    return summary
