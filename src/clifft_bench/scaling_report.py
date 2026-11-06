"""Report the optional native threading pilot from validated raw evidence."""

from __future__ import annotations

from pathlib import Path

from clifft_bench.results import case_rows, export_csv, validate_result


def scaling_rows(document: dict) -> list[dict]:
    validate_result(document)
    if "scaling" not in document["campaign"]:
        raise ValueError("expected a native threading pilot")
    cases = {c["case_id"]: c for c in document["cases"]}
    rows = case_rows(document)
    baselines = {
        (row["implementation_id"], row["workload_id"]): row
        for row in rows if cases[row["case_id"]]["execution"]["threads_requested"] == 1
    }
    for row in rows:
        case = cases[row["case_id"]]
        execution = case["execution"]
        metadata = (case["setup"] or {}).get("runtime_metadata", {})
        samples = case["samples"]
        baseline = baselines.get((row["implementation_id"], row["workload_id"]))
        speedup = None
        if row["status"] == "success" and baseline and baseline["status"] == "success":
            speedup = (row["median_attempted_shots_per_second"]
                       / baseline["median_attempted_shots_per_second"])
        active = [(s["active_threads_min"], s["active_threads_max"]) for s in samples
                  if s["active_threads_min"] is not None]
        row.update(
            threads=execution["threads_requested"],
            runtime_threads=execution.get("threads_effective"),
            speedup=speedup,
            efficiency=speedup / execution["threads_requested"] if speedup is not None else None,
            cpu_cores=(sum(s["process_cpu_seconds"] for s in samples)
                       / sum(s["sampling_wall_seconds"] for s in samples)) if samples else None,
            active_threads=(f"{min(a for a, _ in active)}–{max(b for _, b in active)}"
                            if active else None),
            native_calibration_seconds=metadata.get("native_tuning", {}).get("elapsed_seconds"),
            configuration=metadata,
        )
    return rows


def _value(value, suffix="") -> str:
    if value is None or value == "":
        return "—"
    return f"{value:.4g}{suffix}" if isinstance(value, (int, float)) else str(value)


def scaling_summary(document: dict, rows: list[dict]) -> str:
    run, runner, campaign = document["run"], document["runner"], document["campaign"]
    lines = [
        "# Native thread scaling pilot", "",
        "For this workload and call size, how does throughput change with more threads, "
        "using the simulator's native configuration or tuning?", "",
        "Optional smoke/validation evidence; not the recurring official throughput series.", "",
        f"Run: `{run['id']}`. Host: {runner['cpu_model']} ({runner['os']}).",
        f"Harness: `{runner['suite_source']['commit']}`; "
        f"dirty checkout: {runner['suite_source']['dirty']}.",
        f"Collection: {run['started_at']} to {run['finished_at']}.",
        f"Selection: `{run['selection']}`; overrides: `{run['overrides']}`.",
        f"Memory ceiling: {campaign['resources']['memory_limit_bytes'] / (1 << 30):g} GiB "
        "per process, fixed across thread budgets. One logical CPU per physical core.",
        "Core allocation (prefix used for each budget): "
        f"`{campaign['resources']['physical_cores']}`.",
        "", "Rates use attempted shots. Speedup uses the same tool/workload's one-core rate "
        "in this run; efficiency is speedup divided by the allocated core count. "
        "Missing or failed one-core baselines have no ratio. MAD is sample dispersion.",
        "", "CPU cores used is process CPU time divided by wall time over the sampling loop, "
        "including adapter reduction and loop bookkeeping. It measures average CPU consumption, "
        "not a thread count. Runtime threads is the resolved worker/pool limit; active threads "
        "is SymFT's observed minimum–maximum across calls. Other tools do not expose that count.",
        "", "Clifft calibrates once per point with its native 0.25-second soft budget and "
        "reuses the selected batch/layout. SymFT and Tsim use native heuristics, with no "
        "measured calibration. Tsim's per-call automatic batch size is not exposed by its public "
        "API and remains unknown here. Setup includes native calibration; JIT/warmup costs "
        "are reported separately where available. Peak RSS is the process high-water mark "
        "and can include setup and tuning. These policies do not claim optimal configurations.",
    ]
    for workload in dict.fromkeys(row["workload_id"] for row in rows):
        selected = [r for r in rows if r["workload_id"] == workload]
        lines += ["", f"## {workload}", "",
                  f"Attempted shots per call: {selected[0]['shots_per_call']}.", "",
                  "| Tool | Cores | Status | Shots/s ± MAD | Speedup | Efficiency | "
                  "Runtime threads | Active threads | CPU cores used | Peak MiB |",
                  "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
        for row in selected:
            rate = _value(row["median_attempted_shots_per_second"])
            if row["status"] == "success":
                rate += " ± " + _value(row["mad_attempted_shots_per_second"])
            lines.append("| " + " | ".join([
                row["simulator_name"], str(row["threads"]), row["status"], rate,
                _value(row["speedup"], "×"), _value(row["efficiency"]),
                _value(row["runtime_threads"]), _value(row["active_threads"]),
                _value(row["cpu_cores"]),
                _value(row["peak_rss_bytes"] / (1 << 20) if row["peak_rss_bytes"] else None),
            ]) + " |")
        lines += ["", "| Tool | Cores | Setup s | Native calibration s | Compile s | "
                  "Kernel warmup s | Warmup s |", "|---|---:|---:|---:|---:|---:|---:|"]
        for row in selected:
            lines.append("| " + " | ".join([
                row["simulator_name"], str(row["threads"]),
                *[_value(row[key]) for key in ("setup_seconds", "native_calibration_seconds",
                                               "compile_seconds", "kernel_warmup_seconds",
                                               "warmup_seconds")],
            ]) + " |")
        for row in selected:
            if row["error_message"]:
                message = row["error_message"].replace("\n", " ")
                lines += ["", f"{row['case_id']}: {row['error_phase']} / "
                          f"{row['error_type']}: {message}"]
        lines += ["", "Resolved native settings:", ""]
        for row in selected:
            meta = row["configuration"]
            tuning = meta.get("native_tuning", {})
            lines.append(f"- {row['case_id']}: batch={_value(meta.get('effective_batch_size'))}; "
                         f"chunk={_value(meta.get('sample_chunk_shots'))}; "
                         f"layout={_value(tuning.get('shot_workers'))} × "
                         f"{_value(tuning.get('intra_shot_workers'))}; "
                         f"tuning stop={tuning.get('stop_reason', 'not applicable')}; "
                         f"sufficient measurements={tuning.get('sufficient_measurements', '—')}.")
    lines += ["", "## Software", ""]
    identities = {c["simulator"]["implementation_id"]: c["simulator"] for c in document["cases"]}
    for name, identity in identities.items():
        lines.append(f"- {name}: `{identity['version']}` at "
                     f"[{identity['commit_sha']}]({identity['source_url']}/commit/"
                     f"{identity['commit_sha']}); precision={identity['build']['precision']}.")
    return "\n".join(lines) + "\n"


def render_scaling(rows: list[dict], destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    workloads = list(dict.fromkeys(r["workload_id"] for r in rows))
    tools = list(dict.fromkeys(r["simulator_name"] for r in rows))
    palette = ("#3366b3", "#bb5712", "#12846e")
    colors = {tool: palette[index % len(palette)] for index, tool in enumerate(tools)}
    fig, axes = plt.subplots(len(workloads), 2, figsize=(12, 3.8 * len(workloads)), squeeze=False)
    for index, workload in enumerate(workloads):
        selected = [r for r in rows if r["workload_id"] == workload]
        for tool in tools:
            points = sorted((r for r in selected if r["simulator_name"] == tool),
                            key=lambda r: r["threads"])
            x = [r["threads"] for r in points]
            for column, key in enumerate(("median_attempted_shots_per_second", "speedup")):
                y = [r[key] if r["status"] == "success" and r[key] not in (None, "")
                     else float("nan") for r in points]
                axes[index, column].plot(x, y, "o-", label=tool, color=colors[tool])
        for column, label in enumerate(("Attempted shots / second", "Speedup vs one core")):
            ax = axes[index, column]
            ax.set_title(f"{workload}\n{selected[0]['shots_per_call']:,} shots per call",
                         fontsize=9)
            ax.set_xlabel("Allocated physical cores")
            ax.set_ylabel(label)
            ax.set_xticks(sorted({r["threads"] for r in selected}))
            ax.grid(alpha=0.25)
            ax.legend()
        if any(r["status"] == "success" for r in selected):
            axes[index, 0].set_yscale("log")
        if not any(r["speedup"] is not None for r in selected):
            axes[index, 1].set_ylim(0, 1)
            axes[index, 1].text(0.5, 0.5, "No successful one-core baseline",
                                transform=axes[index, 1].transAxes, ha="center")
    fig.suptitle("Native thread scaling · optional validation pilot", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(destination / "thread-scaling.png", dpi=160)
    plt.close(fig)


def report_scaling(document: dict, execution_dir: Path, *, output_dir: Path | None,
                   check: bool) -> str:
    rows = scaling_rows(document)
    summary = scaling_summary(document, rows)
    if not check:
        export_csv(document, execution_dir / "cases.csv")
        destination = output_dir or execution_dir / "report"
        destination.mkdir(parents=True, exist_ok=True)
        render_scaling(rows, destination)
        (destination / "review.md").write_text(summary)
    return summary
