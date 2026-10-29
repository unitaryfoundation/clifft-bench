from __future__ import annotations

import csv
import json
import math
import statistics
import struct
from pathlib import Path

import pytest

from clifft_bench.manifest import load_suite
from clifft_bench.results import COMPARISON_FIELDS
from clifft_bench.results import _comparison_rows as collect_comparisons
from reporting.qec import (
    ADDER_WORKLOAD,
    CURRENT_WORKLOAD_ORDER,
    DISTILLATION_WORKLOAD,
    TOOL_COMPARISONS,
    WORKLOAD_ORDER,
    _load_release,
    build_report,
    web_output_paths,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "reporting/sources.json"


def _source_document() -> dict:
    return json.loads(SOURCES.read_text())


def _comparison_rows(execution: Path) -> list[dict[str, str]]:
    with (execution / "comparisons.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        fields = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def copied_sources(tmp_path: Path) -> Path:
    document = _source_document()
    history = ROOT / document["history_execution"]
    history_copy = tmp_path / history.name
    history_copy.mkdir()
    (history_copy / "cases.csv").write_bytes((history / "cases.csv").read_bytes())
    document["history_execution"] = str(history_copy)
    releases = []
    for relative in document["release_executions"]:
        source = ROOT / relative
        destination = tmp_path / source.name
        destination.mkdir()
        for name in ("comparisons.csv", "cases.csv", "index.json"):
            (destination / name).write_bytes((source / name).read_bytes())
        releases.append(str(destination))
    document["release_executions"] = releases
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(document))
    return path


def test_reporting_selects_the_release_core_from_archived_results(copied_sources: Path) -> None:
    expected = build_report(SOURCES)
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    core = {
        case.workload.id for case in suite.cases
        if case.workload.definition["family"] in {
            "magic-state-cultivation", "magic-state-distillation",
            "surface-code-memory", "coherent-noise-surface-code",
        }
        and case.workload.id != "distillation-15to1-rm15-p1e-3"
    }
    assert len(core) == 6
    assert set(WORKLOAD_ORDER) == core
    assert set(expected.history.speedups) == core
    assert expected.history.medians == tuple(
        statistics.median(values) for values in zip(*expected.history.speedups.values())
    )

    document = json.loads(copied_sources.read_text())
    paths = [Path(document["history_execution"]) / "cases.csv"] + [
        Path(execution) / "comparisons.csv" for execution in document["release_executions"]
    ]
    archived_extras = set()
    for path in paths:
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        archived_extras.update({row["workload_id"] for row in rows} - core)
        _write_rows(path, [row for row in rows
                           if row["workload_id"] in core | {DISTILLATION_WORKLOAD, ADDER_WORKLOAD}])
    assert archived_extras == {
        "coherent-surface-d3-r1-p1e-3-rz2e-2",
        "coherent-surface-d5-r1-p1e-3-rz2e-2",
        DISTILLATION_WORKLOAD,
        ADDER_WORKLOAD,
    }

    # Retired workloads do not affect the report; new workloads are required
    # only for current figures, while the historical median retains its core.
    assert build_report(copied_sources) == expected


@pytest.mark.parametrize("section", ["history", "current-vs-previous", "alternatives-vs-current"])
def test_reporting_rejects_missing_core_measurements(copied_sources: Path, section: str) -> None:
    document = json.loads(copied_sources.read_text())
    if section == "history":
        path = Path(document["history_execution"]) / "cases.csv"
    else:
        path = Path(document["release_executions"][-1]) / "comparisons.csv"
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    _write_rows(path, [
        row for row in rows
        if not (
            row["workload_id"] == "coherent-surface-d3-r3-p1e-3-rz2e-2"
            and (
                row["simulator_display_version"] == "0.5.0"
                if section == "history" else row["comparison_id"] == section
            )
        )
    ])

    with pytest.raises(ValueError, match="does not cover the reporting core"):
        build_report(copied_sources)


def test_reporting_chains_calibrated_release_onto_scalar_history() -> None:
    report = build_report(SOURCES)

    assert report.history.versions == (
        "0.1.0",
        "0.2.0",
        "0.3.0",
        "0.4.1",
        "0.5.0",
        "0.6.0",
        "0.7.0",
        "0.8.0",
        "0.9.0",
        "0.10.0",
        "0.11.0",
        "0.12.0",
    )
    assert report.history.source_executions[8:] == (
        "clifft-history-v1-20260902",
        "release-v1-20260903-133252",
        "release-v1-20260930-180134",
        "release-v1-20261009-192718",
    )
    assert set(report.history.speedups) == set(WORKLOAD_ORDER)
    assert all(
        len(values) == len(report.history.versions)
        for values in report.history.speedups.values()
    )

    first_release = ROOT / _source_document()["release_executions"][0]
    current_row = next(
        row
        for row in _comparison_rows(first_release)
        if row["comparison_id"] == "current-vs-previous"
        and row["workload_id"] == "msc-d3-inject-cultivate-p1e-3"
    )
    cultivation_d3 = report.history.speedups["msc-d3-inject-cultivate-p1e-3"]
    assert math.isclose(
        cultivation_d3[9] / cultivation_d3[8],
        float(current_row["ratio_candidate_over_baseline"]),
    )


def test_reporting_uses_latest_calibrated_cross_tool_comparison() -> None:
    report = build_report(SOURCES)

    latest_release = ROOT / _source_document()["release_executions"][-1]
    assert report.release_executions[-1] == latest_release.name
    assert len(report.relative_points) == len(CURRENT_WORKLOAD_ORDER)
    assert len(report.throughput_points) == len(CURRENT_WORKLOAD_ORDER)

    alternative_row = next(
        row
        for row in _comparison_rows(latest_release)
        if row["comparison_id"] == "alternatives-vs-current"
        and row["workload_id"] == "msc-d3-inject-cultivate-p1e-3"
    )
    points = {point.workload_id: point for point in report.relative_points}
    cultivation_d3 = points["msc-d3-inject-cultivate-p1e-3"]
    assert math.isclose(
        cultivation_d3.clifft_over_alternative,
        1 / float(alternative_row["ratio_candidate_over_baseline"]),
    )
    throughput = {point.workload_id: point for point in report.throughput_points}
    assert math.isclose(
        throughput["msc-d3-inject-cultivate-p1e-3"].attempted_shots_per_second,
        float(alternative_row["baseline_rate"]),
    )

    slow_coherent = points["coherent-surface-d5-r5-p1e-3-rz2e-2"]
    assert slow_coherent.clifft_over_alternative > 80


def test_release_loader_accepts_current_campaign_with_stim_anchor(tmp_path: Path) -> None:
    source = ROOT / _source_document()["release_executions"][-1]
    expected = _load_release(source)
    with (source / "cases.csv").open(newline="") as stream:
        cases = [row for row in csv.DictReader(stream) if row["variant_id"] != "stim-current"]
    surface = "surface-code-d7-r7-p1e-3"
    template = next(
        row for row in cases
        if row["variant_id"] == "symft-current" and row["workload_id"] == surface
    )
    cases.append({
        **template,
        "case_id": f"{surface}--stim-current",
        "variant_id": "stim-current",
        "implementation_id": "stim-1.16.0",
        "simulator_name": "Stim",
        "simulator_version": "1.16.0",
        "simulator_display_version": "1.16.0",
        "median_attempted_shots_per_second": "1000000",
    })
    # Use the real producer and active manifest so putting Stim back into the
    # SymFT comparison makes this fail, whether or not the source already has Stim.
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    rows = collect_comparisons(suite, source.name, cases)
    execution = tmp_path / source.name
    execution.mkdir()
    (execution / "index.json").write_bytes((source / "index.json").read_bytes())
    with (execution / "comparisons.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COMPARISON_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    assert _load_release(execution) == expected
    anchors = [row for row in rows if row["candidate_simulator_name"] == "Stim"]
    assert len(anchors) == 1
    assert anchors[0]["comparison_id"] == "stim-anchor-vs-current"
    assert anchors[0]["workload_id"] == surface
    assert anchors[0]["baseline_variant_id"] == "clifft-current"


def test_reporting_exposes_release_comparison_and_qv_source() -> None:
    report = build_report(SOURCES)
    source_document = _source_document()

    assert report.qv_execution == Path(source_document["qv_execution"]).name
    assert len(report.release_points) == len(CURRENT_WORKLOAD_ORDER)
    release_points = {point.workload_id: point for point in report.release_points}
    assert release_points["distillation-color-code-85q-p5e-2"].current_packed
    assert not release_points["coherent-surface-d5-r5-p1e-3-rz2e-2"].current_packed


def test_combined_throughput_uses_measured_rates_and_sparse_tool_coverage() -> None:
    report = build_report(SOURCES)
    latest_release = ROOT / _source_document()["release_executions"][-1]
    rows = _comparison_rows(latest_release)
    points = {point.workload_id: point for point in report.tool_throughput_points}
    assert set(points) == set(CURRENT_WORKLOAD_ORDER)
    assert report.tool_versions == {
        "Clifft": "0.12.0", "SymFT": "0.1.1", "xtim": "3.1.4", "Stim": "1.16.0",
    }
    for workload, point in points.items():
        current = [row for row in rows if row["workload_id"] == workload
                   and row["comparison_id"] == "current-vs-previous"]
        assert point.rates["Clifft"] == statistics.median(float(row["candidate_rate"])
                                                         for row in current)
        for tool, (comparison, workloads) in TOOL_COMPARISONS.items():
            if workload not in workloads:
                assert tool not in point.rates  # No zeros or synthetic missing-tool rates.
                continue
            selected = [row for row in rows if row["workload_id"] == workload
                        and row["comparison_id"] == comparison]
            assert point.rates[tool] == statistics.median(float(row["candidate_rate"])
                                                         for row in selected)
    assert sum(len(point.rates) for point in points.values()) == 19
    assert points[DISTILLATION_WORKLOAD].rates["xtim"] > 53_000_000
    assert "Stim" not in points[DISTILLATION_WORKLOAD].rates


@pytest.mark.parametrize("comparison,workload", [
    ("current-vs-previous", DISTILLATION_WORKLOAD),
    ("alternatives-vs-current", DISTILLATION_WORKLOAD),
    ("xtim-vs-current", DISTILLATION_WORKLOAD),
    ("stim-anchor-vs-current", "surface-code-d7-r7-p1e-3"),
    ("current-vs-previous", ADDER_WORKLOAD),
])
def test_current_figures_reject_missing_measurements(
    copied_sources: Path, comparison: str, workload: str,
) -> None:
    path = Path(json.loads(copied_sources.read_text())["release_executions"][-1])
    rows = _comparison_rows(path)
    _write_rows(path / "comparisons.csv", [row for row in rows if not (
        row["comparison_id"] == comparison and row["workload_id"] == workload
    )])
    with pytest.raises(ValueError, match="does not cover the reporting core"):
        build_report(copied_sources)


@pytest.mark.parametrize("field", [
    "baseline_case_id", "baseline_rate", "baseline_batch_size_effective",
    "baseline_simulator_version", "baseline_result_id",
])
def test_new_tools_must_reuse_the_current_clifft_case(copied_sources: Path, field: str) -> None:
    path = Path(json.loads(copied_sources.read_text())["release_executions"][-1])
    rows = _comparison_rows(path)
    row = next(row for row in rows if row["comparison_id"] == "xtim-vs-current")
    row[field] = "123"
    _write_rows(path / "comparisons.csv", rows)
    with pytest.raises(ValueError, match="does not reuse current Clifft"):
        build_report(copied_sources)


def test_adder_uses_paired_rates_and_compiler_evidence() -> None:
    report = build_report(SOURCES)
    adder = report.adder
    assert adder.previous_rate == pytest.approx(6584.225081387494)
    assert adder.current_rate == pytest.approx(217607772.89363268)
    assert adder.current_over_previous == pytest.approx(33049.86847360027)
    assert adder.previous_compile_seconds == pytest.approx(0.003413411)
    assert adder.current_compile_seconds == pytest.approx(0.002063026)
    assert (adder.previous_peak_active_width, adder.current_peak_active_width) == (15, 0)
    assert ADDER_WORKLOAD not in report.history.speedups
    assert ADDER_WORKLOAD not in {point.workload_id for point in report.release_points}


def test_adder_rejects_mismatched_compiler_case(copied_sources: Path) -> None:
    path = Path(json.loads(copied_sources.read_text())["release_executions"][-1]) / "cases.csv"
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    next(row for row in rows if row["workload_id"] == ADDER_WORKLOAD)["case_id"] = "different"
    _write_rows(path, rows)
    with pytest.raises(ValueError, match="adder cases do not match comparison"):
        build_report(copied_sources)


def test_web_output_paths_cover_all_qec_assets(tmp_path: Path) -> None:
    assert {path.name for path in web_output_paths(tmp_path)} == {
        "clifft-throughput-light.png",
        "clifft-throughput-dark.png",
        "simulator-throughput-light.png",
        "simulator-throughput-dark.png",
        "clifft-vs-symft-light.png",
        "clifft-vs-symft-dark.png",
        "performance-over-time-light.png",
        "performance-over-time-dark.png",
        "v012-vs-v011-light.png",
        "v012-vs-v011-dark.png",
        "draper-adder-light.png",
        "draper-adder-dark.png",
    }


def test_checked_in_web_assets_cover_reporting_outputs() -> None:
    output_dir = ROOT / "reporting/figures/web"
    expected = {path.name for path in web_output_paths(output_dir)} | {
        "quantum-volume-light.png",
        "quantum-volume-dark.png",
    }
    assert {path.name for path in output_dir.glob("*.png")} == expected

    for path in output_dir.glob("*.png"):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert width == 1920
        if path.name.startswith("performance-over-time"):
            assert height == 760
        elif path.name.startswith("quantum-volume"):
            assert height == 940
        elif path.name.startswith("simulator-throughput"):
            assert height == 1400
        else:
            assert height == 900
        assert data[25] == 6  # RGBA


@pytest.fixture
def sources_with_tsim(copied_sources: Path) -> Path:
    path = Path(json.loads(copied_sources.read_text())["release_executions"][-1])
    rows = _comparison_rows(path)
    # Synthetic rates test selection only; never used as publication evidence.
    with (path / "cases.csv").open(newline="") as stream:
        cases = list(csv.DictReader(stream))
    tsim_cases = [{**row, "variant_id": "tsim-current", "adapter": "tsim",
                   "case_id": f"{row['workload_id']}--tsim-current",
                   "implementation_id": "tsim-0.1.5-e9406ed", "simulator_name": "Tsim",
                   "simulator_version": "0.1.5", "simulator_display_version": "0.1.5+e9406ed"}
                  for row in cases if row["variant_id"] == "symft-current"]
    cases += tsim_cases
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    tsim = [row for row in collect_comparisons(suite, path.name, cases)
            if row["comparison_id"] == "tsim-vs-current"]
    bulk = [{**row, "comparison_id": "bulk-alternatives-vs-current",
             "candidate_variant_id": "tsim-bulk",
             "baseline_shots_per_call": "4096", "candidate_shots_per_call": "4096",
             "candidate_rate": "999999999"} for row in tsim]
    cases.append({**tsim_cases[0], "variant_id": "tsim-bulk",
                  "case_id": "bulk--tsim", "shots_per_call": "4096"})
    _write_rows(path / "cases.csv", cases)
    _write_rows(path / "comparisons.csv", rows + tsim + bulk)
    return copied_sources


def test_reporting_includes_tsim_without_mixing_bulk_or_changing_history(sources_with_tsim):
    old = build_report(SOURCES)
    new = build_report(sources_with_tsim)
    assert new.tool_versions == {**old.tool_versions, "Tsim": "0.1.5+e9406ed"}
    assert new.history == old.history
    assert new.release_points == old.release_points
    for before, after in zip(old.tool_throughput_points, new.tool_throughput_points, strict=True):
        assert before.workload_id == after.workload_id
        assert after.rates == {**before.rates, "Tsim": before.rates["SymFT"]}


@pytest.mark.parametrize("problem, message", [
    ("missing", "does not match successful Tsim cases"),
    ("candidate", "does not match successful Tsim cases"),
    ("duplicate", "duplicate case"),
    ("baseline", "does not reuse current Clifft"),
    ("shots", "unequal shots_per_call"),
])
def test_reporting_rejects_incomplete_or_unmatched_tsim(sources_with_tsim, problem, message):
    path = Path(json.loads(sources_with_tsim.read_text())["release_executions"][-1])
    rows = _comparison_rows(path)
    tsim = [row for row in rows if row["comparison_id"] == "tsim-vs-current"]
    if problem == "missing":
        rows = [row for row in rows if not (
            row in tsim and row["workload_id"] == DISTILLATION_WORKLOAD
        )]
    elif problem == "baseline":
        tsim[0]["baseline_case_id"] = "wrong-case"
    elif problem == "candidate":
        tsim[0]["candidate_case_id"] = "wrong-case"
    elif problem == "duplicate":
        rows.append(tsim[0].copy())
    else:
        tsim[0]["candidate_shots_per_call"] = "4096"
    _write_rows(path / "comparisons.csv", rows)
    with pytest.raises(ValueError, match=message):
        build_report(sources_with_tsim)


def _fail_tsim_cases(path, workloads, phase, *, replica=None):
    with (path / "cases.csv").open(newline="") as stream:
        cases = list(csv.DictReader(stream))
    for row in cases:
        if (row["variant_id"] == "tsim-current" and row["workload_id"] in workloads
                and (replica is None or row["replica"] == replica)):
            row.update(status="error", error_phase=phase, error_type="WorkerTimeout",
                       error_message="synthetic timeout", median_attempted_shots_per_second="")
            if phase == "setup":
                row.update(batch_size_effective="", setup_seconds="")
    _write_rows(path / "cases.csv", cases)
    # Use the real finalizer's join: failed cases have no comparison row.
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    suite.run["collection"]["replicas_per_placement"] = max(int(row["replica"]) for row in cases)
    _write_rows(path / "comparisons.csv", collect_comparisons(suite, path.name, cases))


@pytest.mark.parametrize("phase", ["setup", "warmup", "correctness", "sampling"])
@pytest.mark.parametrize("all_failed", [False, True])
def test_reporting_keeps_recorded_tsim_failures(sources_with_tsim, phase, all_failed):
    before = build_report(sources_with_tsim)
    path = Path(json.loads(sources_with_tsim.read_text())["release_executions"][-1])
    failed = set(CURRENT_WORKLOAD_ORDER) if all_failed else {DISTILLATION_WORKLOAD}
    _fail_tsim_cases(path, failed, phase)
    after = build_report(sources_with_tsim)
    assert after.tool_versions == before.tool_versions
    assert after.history == before.history
    assert after.release_points == before.release_points
    for old, new in zip(before.tool_throughput_points, after.tool_throughput_points, strict=True):
        if new.workload_id in failed:
            assert new.failures == {"Tsim": "1/1 failed"}
            assert new.rates == {tool: rate for tool, rate in old.rates.items() if tool != "Tsim"}
        else:
            assert new == old


def test_reporting_labels_mixed_tsim_replica_outcomes(sources_with_tsim):
    path = Path(json.loads(sources_with_tsim.read_text())["release_executions"][-1])
    with (path / "cases.csv").open(newline="") as stream:
        cases = list(csv.DictReader(stream))
    cases += [{**row, "replica": "2", "result_id": "second-replica"} for row in cases]
    _write_rows(path / "cases.csv", cases)
    _fail_tsim_cases(path, {DISTILLATION_WORKLOAD}, "sampling", replica="1")
    report = build_report(sources_with_tsim)
    for point in report.tool_throughput_points:
        if point.workload_id == DISTILLATION_WORKLOAD:
            assert point.failures == {"Tsim": "1/2 failed"}
            assert "Tsim" not in point.rates  # Never silently select only successful replicas.
        else:
            assert point.failures == {}
            assert "Tsim" in point.rates


@pytest.mark.parametrize("problem,message", [
    ("missing", "cases do not cover the reporting core"),
    ("duplicate", "duplicate case"),
    ("running", "no terminal outcome"),
    ("no-error", "no terminal outcome"),
    ("shots", "unequal shots_per_call"),
])
def test_reporting_rejects_incomplete_tsim_outcomes(sources_with_tsim, problem, message):
    path = Path(json.loads(sources_with_tsim.read_text())["release_executions"][-1])
    _fail_tsim_cases(path, {DISTILLATION_WORKLOAD}, "setup")
    with (path / "cases.csv").open(newline="") as stream:
        cases = list(csv.DictReader(stream))
    failed = next(row for row in cases if row["status"] == "error")
    if problem == "missing":
        cases.remove(failed)
    elif problem == "duplicate":
        cases.append(failed.copy())
    elif problem == "running":
        failed["status"] = "running"
    elif problem == "no-error":
        failed["error_type"] = ""
    else:
        failed["shots_per_call"] = "4096"
    _write_rows(path / "cases.csv", cases)
    with pytest.raises(ValueError, match=message):
        build_report(sources_with_tsim)


def test_transition_policy_comparisons_do_not_enter_historical_reports(copied_sources):
    expected = build_report(copied_sources)
    document = json.loads(copied_sources.read_text())
    path = Path(document['release_executions'][-1]) / 'comparisons.csv'
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    extra = []
    for row in rows:
        candidate = dict(row)
        candidate['comparison_id'] = 'tuned-alternatives-vs-current'
        candidate['baseline_variant_id'] = 'clifft-tuned'
        candidate['candidate_variant_id'] += '-tuned'
        candidate['candidate_rate'] = str(float(candidate['candidate_rate']) * 100)
        candidate['ratio_candidate_over_baseline'] = '100'
        extra.append(candidate)
    _write_rows(path, rows + extra)
    assert build_report(copied_sources) == expected
