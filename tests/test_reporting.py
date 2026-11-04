import copy

import pytest

from clifft_bench.cli import main
from clifft_bench.report import build_report, report_execution, review_summary
from clifft_bench.results import validate_result, write_json


def test_release_reporting_needs_only_one_execution(raw_result, tmp_path, monkeypatch):
    from clifft_bench import config

    def no_checkout(*args, **kwargs):
        raise AssertionError("report must not read current configuration")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_suite", no_checkout)
    monkeypatch.setattr(config, "repository_root", no_checkout)
    write_json(tmp_path / "raw.json", raw_result)
    before = set(tmp_path.iterdir())
    summary = report_execution(tmp_path, check=True)
    assert "1.25×" in summary and "1.1.0rc1" in summary
    assert set(tmp_path.iterdir()) == before
    assert main(["report", str(tmp_path), "--check"]) == 0


def test_current_coverage_without_previous_baseline_has_no_release_ratio(raw_result):
    case = copy.deepcopy(raw_result["cases"][1])
    expected = copy.deepcopy(raw_result["campaign"]["cases"][1])
    case.update(case_id="new-current")
    case["workload"]["id"] = "new-workload"
    expected.update(id="new-current", workload_id="new-workload")
    raw_result["cases"].append(case)
    raw_result["campaign"]["cases"].append(expected)
    validate_result(raw_result)
    report = build_report(raw_result)
    assert len(report["current"]) == 2
    assert report["release"][-1]["ratio"] is None
    assert "baseline: not measured" in review_summary(report)


def test_tool_failures_and_incompatible_absences_remain_visible(raw_result):
    case = copy.deepcopy(raw_result["cases"][1])
    expected = copy.deepcopy(raw_result["campaign"]["cases"][1])
    case.update(
        case_id="peer",
        variant_id="peer",
        status="error",
        error={"phase": "sampling", "type": "WorkerTimeout", "message": "timed out"},
    )
    case["simulator"].update(implementation_id="peer", adapter="symft", name="SymFT")
    expected.update(id="peer", variant_id="peer", implementation_id="peer")
    raw_result["cases"].append(case)
    raw_result["campaign"]["cases"].append(expected)
    raw_result["campaign"]["comparisons"].append(
        {"id": "peers", "baseline_variant": "current", "candidate_variants": ["peer"]}
    )
    extra = copy.deepcopy(raw_result["cases"][1])
    extra.update(case_id="clifft-only")
    extra["workload"].update(id="clifft-only", compatible_adapters=["clifft"])
    definition = copy.deepcopy(raw_result["campaign"]["cases"][1])
    definition.update(id="clifft-only", workload_id="clifft-only")
    raw_result["cases"].append(extra)
    raw_result["campaign"]["cases"].append(definition)
    validate_result(raw_result)
    report = build_report(raw_result)
    summary = review_summary(report)
    assert "candidate: error" in summary and "candidate: unsupported" in summary
    assert all(p["ratio"] is None for p in report["pairs"] if p["comparison_id"] == "peers")
    assert report["rows"]["peer"]["median_attempted_shots_per_second"] == ""


def test_bulk_and_short_calls_are_separate_report_groups(raw_result):
    for index in range(2):
        case = copy.deepcopy(raw_result["cases"][index])
        expected = copy.deepcopy(raw_result["campaign"]["cases"][index])
        variant = case["variant_id"] + "-bulk"
        case.update(case_id=variant, variant_id=variant)
        expected.update(id=variant, variant_id=variant, shots_per_call=16)
        case["execution"]["shots_per_call"] = 16
        for sample in case["samples"]:
            for key in ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors"):
                sample[key] *= 2
            sample["duration_seconds"] *= 2
        raw_result["cases"].append(case)
        raw_result["campaign"]["cases"].append(expected)
    raw_result["campaign"]["comparisons"].append(
        {"id": "bulk", "baseline_variant": "previous-bulk", "candidate_variants": ["current-bulk"]}
    )
    validate_result(raw_result)
    report = build_report(raw_result)
    assert len(report["release"]) == len(report["tools"]) == 1
    assert report["tools"][0]["cases"]["current-bulk"]["execution"]["shots_per_call"] == 16


@pytest.mark.parametrize("all_failed", [False, True])
def test_render_all_themes_and_regenerate_stale_csv(raw_result, tmp_path, all_failed):
    pytest.importorskip("matplotlib")
    if all_failed:
        for case in raw_result["cases"]:
            case.update(status="error", error={"phase": "sampling", "type": "WorkerTimeout",
                                              "message": "timed out"})
    write_json(tmp_path / "raw.json", raw_result)
    (tmp_path / "cases.csv").write_text("stale table")
    report_execution(tmp_path)
    assert ("125.0" in (tmp_path / "cases.csv").read_text()) is not all_failed
    for stem in ("clifft-throughput", "simulator-throughput", "release-change"):
        for theme in ("light", "dark"):
            path = tmp_path / "report" / f"{stem}-{theme}.png"
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    summary = (tmp_path / "report/review.md").read_text()
    assert ("1.25×" in summary) is not all_failed
    if all_failed:
        assert "candidate: error" in summary


@pytest.mark.parametrize("workloads", [1, 12])
def test_figures_cover_every_measurement_and_separate_release_scales(
    raw_result, tmp_path, monkeypatch, workloads,
):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    previous, current = raw_result["cases"]
    peer = copy.deepcopy(current)
    peer.update(case_id="peer", variant_id="peer")
    peer["simulator"].update(implementation_id="peer", adapter="symft", name="SymFT")
    peer_contract = copy.deepcopy(raw_result["campaign"]["cases"][1])
    peer_contract.update(id="peer", variant_id="peer", implementation_id="peer")
    originals = [previous, current, peer]
    contracts = [*raw_result["campaign"]["cases"], peer_contract]
    raw_result["cases"], raw_result["campaign"]["cases"] = [], []
    current_rates = []
    for index in range(workloads):
        for original, contract in zip(originals, contracts, strict=True):
            case, declared = copy.deepcopy(original), copy.deepcopy(contract)
            case["case_id"] += f"-{index}"
            case["workload"]["id"] = f"representative-workload-{index}"
            declared.update(id=case["case_id"], workload_id=case["workload"]["id"])
            if case["variant_id"] == "current":
                rate = 22400 if index == 11 else (98 if index % 2 == 0 else 102)
                current_rates.append(rate)
                for sample in case["samples"]:
                    sample["throughput_attempted_shots_per_second"] = rate
                    sample["duration_seconds"] = sample["attempted_shots"] / rate
            raw_result["cases"].append(case)
            raw_result["campaign"]["cases"].append(declared)
    raw_result["campaign"]["comparisons"].append(
        {"id": "peers", "baseline_variant": "current", "candidate_variants": ["peer"]}
    )
    savefig = Figure.savefig
    plotted = {"clifft-throughput": [], "simulator-throughput": []}
    inspected = []

    def check_layout(figure, path, **kwargs):
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        if path.name.startswith("release-change"):
            for axis in figure.axes:
                low, high = axis.get_xlim()
                if axis.get_xscale() == "linear":
                    assert -5 < low < -2 < 0 < 2 < high < 5
                    assert all(abs(float(line.get_xdata()[0])) <= 50
                               for line in axis.lines if line.get_marker() == "o")
                else:
                    assert low < 1 < 224 < high
                    assert all(float(line.get_xdata()[0]) > 1.5
                               for line in axis.lines if line.get_marker() == "o")
            assert len(figure.axes) == (2 if workloads > 11 else 1)
        else:
            axis = figure.axes[0]
            legend = axis.get_legend()
            assert legend.get_window_extent(renderer).y0 > axis.bbox.y1
            assert legend.get_window_extent(renderer).y1 < (
                figure._suptitle.get_window_extent(renderer).y0
            )
            marks = [line for line in axis.lines if line.get_marker() in ("o", "s")]
            # Every successful value is directly annotated at the plotted position.
            assert sorted(text.xy[0] for text in axis.texts if hasattr(text, "xy")) == (
                sorted(float(line.get_xdata()[0]) for line in marks)
            )
            if path.name.endswith("-light.png"):
                kind = next(kind for kind in plotted if path.name.startswith(kind))
                plotted[kind].extend(float(line.get_xdata()[0]) for line in marks)
        inspected.append(path)
        savefig(figure, path, **kwargs)

    monkeypatch.setattr(Figure, "savefig", check_layout)
    write_json(tmp_path / "raw.json", raw_result)
    report_execution(tmp_path)
    assert sorted(plotted["clifft-throughput"]) == sorted(current_rates)
    assert sorted(plotted["simulator-throughput"]) == sorted(current_rates + [125] * workloads)
    summary = (tmp_path / "report/review.md").read_text()
    assert all(path.name in summary for path in inspected)


def test_panel_grouping_preserves_configuration_and_selection(raw_result):
    from clifft_bench.report import _throughput_panels

    report = build_report(raw_result)
    original = report["current"][0]
    for variant, shots, family, parameters in (
        ("clifft-bulk", 256, "coherent-noise-surface-code", {"distance": 5, "rounds": 5}),
        ("clifft-coverage", 32, "quantum-volume", {"num_qubits": 20}),
        ("clifft-coverage", 8, "magic-state-cultivation",
         {"distance": 3, "postselection_pair": "selected"}),
    ):
        case = copy.deepcopy(original)
        case.update(case_id=variant + family, variant_id=variant)
        case["workload"].update(family=family, parameters=parameters)
        case["execution"]["shots_per_call"] = shots
        report["rows"][case["case_id"]] = report["rows"][original["case_id"]]
        report["current"].append(case)
    panels = _throughput_panels(report, "clifft-throughput")
    groups = [entries for _, entries in panels]
    assert all(len({e["variant"] for e in entries}) == 1 for entries in groups)
    cases = [case for entries in groups for entry in entries for case in entry["cases"].values()]
    assert sorted(c["case_id"] for c in cases) == sorted(c["case_id"] for c in report["current"])
    assert {c["execution"]["shots_per_call"] for c in cases} == {8, 32, 256}
    assert len(groups) == len(cases)


def test_regeneration_removes_obsolete_pages_but_keeps_unrelated_images(raw_result, tmp_path):
    pytest.importorskip("matplotlib")
    write_json(tmp_path / "raw.json", raw_result)
    destination = tmp_path / "report"
    destination.mkdir()
    stale = destination / "clifft-throughput-2-light.png"
    unrelated = destination / "clifft-throughput-notes-light.png"
    stale.write_bytes(b"stale")
    unrelated.write_bytes(b"keep")
    report_execution(tmp_path)
    assert not stale.exists()
    assert unrelated.read_bytes() == b"keep"
