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
    for stem in ("simulator-throughput", "release-change"):
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
    plotted = []
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
                    assert low < 1 < high
                    if workloads > 11:
                        assert 224 < high
                    assert all(float(line.get_xdata()[0]) > 1.5
                               for line in axis.lines if line.get_marker() == "o")
            assert figure.axes[0].get_ylim() == figure.axes[1].get_ylim()
        else:
            axis = figure.axes[0]
            legend = figure.legends[0]
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
                plotted.extend(float(line.get_xdata()[0]) for line in marks)
        annotations = [text for axis in figure.axes for text in axis.texts if hasattr(text, "xy")]
        for index, left in enumerate(annotations):
            for right in annotations[index + 1:]:
                assert not left.get_window_extent(renderer).overlaps(
                    right.get_window_extent(renderer)
                )
        inspected.append(path)
        savefig(figure, path, **kwargs)

    monkeypatch.setattr(Figure, "savefig", check_layout)
    write_json(tmp_path / "raw.json", raw_result)
    report_execution(tmp_path)
    assert sorted(plotted) == sorted(current_rates + [125] * workloads)
    summary = (tmp_path / "report/review.md").read_text()
    assert all(path.name in summary for path in inspected)


def test_main_figures_share_selection_without_dropping_detailed_outcomes(raw_result):
    from clifft_bench.report import _release_pairs, _throughput_entries

    report = build_report(raw_result)
    original = report["current"][0]
    for variant, shots, family, parameters in (
        ("clifft-bulk", 256, "coherent-noise-surface-code", {"distance": 5, "rounds": 5}),
        ("clifft-coverage", 8, "magic-state-cultivation",
         {"distance": 3, "postselection_pair": "selected"}),
        ("clifft-coverage", 8, "msc-performance-proxy", {"distance": 7}),
    ):
        case = copy.deepcopy(original)
        case.update(case_id=variant + family, variant_id=variant)
        case["workload"].update(family=family, parameters=parameters)
        case["execution"]["shots_per_call"] = shots
        report["rows"][case["case_id"]] = report["rows"][original["case_id"]]
        report["current"].append(case)
        report["document"]["cases"].append(case)
        pair = {**report["release"][0], "candidate": case, "candidate_variant": variant}
        report["release"].append(pair)
        report["pairs"].append(pair)
    assert [e["cases"][e["variant"]] for e in _throughput_entries(report)] == [original]
    assert [p["candidate"] for p in _release_pairs(report)] == [original]
    summary = review_summary(report)
    assert all(case["case_id"] in summary for case in report["current"])


def test_future_release_pairs_join_the_shared_thematic_order(raw_result):
    from clifft_bench.report import _release_pairs, _representative, _throughput_entries

    # This is a synthetic campaign: its ordering and rates deliberately disagree
    # with the desired figure order. No list of currently measured pairs is used.
    workloads = [
        ("cult-d3", "magic-state-cultivation", {"distance": 3}, 25),
        ("cult-d5", "magic-state-cultivation", {"distance": 5}, 61),
        ("end-to-end", "magic-state-cultivation-full", {"distance": 5}, 463),
        ("distill-small", "magic-state-distillation", {"code": "[[15,1,3]]"}, 15),
        ("distill-large", "magic-state-distillation", {"code": "large"}, 85),
        ("rm", "reed-muller", {}, 49),
        ("surface", "surface-code-memory", {"distance": 7, "rounds": 7}, 97),
        ("coherent-d3", "coherent-noise-surface-code", {"distance": 3, "rounds": 3}, 17),
        ("coherent-d5", "coherent-noise-surface-code", {"distance": 5, "rounds": 5}, 49),
        ("adder", "prepared-basis-arithmetic", {"bits_per_operand": 16}, 33),
        ("bt27", "code-switching", {}, 27),
        ("bt81", "code-switching", {}, 81),
        ("qv10", "quantum-volume", {"num_qubits": 10}, 10),
        ("qv20", "quantum-volume", {"num_qubits": 20}, 20),
    ]
    missing = {"end-to-end", "bt27", "bt81", "qv10", "qv20"}
    originals = list(zip(raw_result["cases"], raw_result["campaign"]["cases"], strict=True))
    raw_result["cases"], raw_result["campaign"]["cases"] = [], []
    future = []
    for identifier, family, parameters, qubits in reversed(workloads):
        for original, contract in originals:
            case, declared = copy.deepcopy(original), copy.deepcopy(contract)
            case["case_id"] += "-" + identifier
            case["workload"].update(id=identifier, family=family, parameters=parameters)
            case["workload"]["expected_metadata"]["num_qubits"] = qubits
            declared.update(id=case["case_id"], workload_id=identifier)
            if identifier in missing and case["variant_id"] == "previous":
                future.append((case, declared))
                continue
            raw_result["cases"].append(case)
            raw_result["campaign"]["cases"].append(declared)

    def figure_ids():
        report = build_report(raw_result)
        return (
            [_representative(e)["workload"]["id"] for e in _throughput_entries(report)],
            [p["workload_id"] for p in _release_pairs(report)],
        )

    expected = [identifier for identifier, *_ in workloads]
    throughput, release = figure_ids()
    assert throughput == expected
    assert release == [identifier for identifier in throughput if identifier not in missing]
    for case, declared in future:
        raw_result["cases"].append(case)
        raw_result["campaign"]["cases"].append(declared)
        missing.remove(case["workload"]["id"])
        throughput, release = figure_ids()
        assert throughput == expected
        assert release == [identifier for identifier in throughput if identifier not in missing]
    # Collection order and measured speed cannot rearrange either figure.
    raw_result["cases"].reverse()
    for index, case in enumerate(raw_result["cases"]):
        for sample in case["samples"]:
            rate = 10 ** (index % 4)
            sample["throughput_attempted_shots_per_second"] = rate
            sample["duration_seconds"] = sample["attempted_shots"] / rate
    assert figure_ids() == (expected, expected)


def test_regeneration_removes_obsolete_pages_but_keeps_unrelated_images(raw_result, tmp_path):
    pytest.importorskip("matplotlib")
    write_json(tmp_path / "raw.json", raw_result)
    destination = tmp_path / "report"
    destination.mkdir()
    stale = destination / "clifft-throughput-2-light.png"
    old_gallery = destination / "clifft-throughput-light.png"
    unrelated = destination / "clifft-throughput-notes-light.png"
    stale.write_bytes(b"stale")
    old_gallery.write_bytes(b"stale")
    unrelated.write_bytes(b"keep")
    report_execution(tmp_path)
    assert not stale.exists()
    assert not old_gallery.exists()
    assert unrelated.read_bytes() == b"keep"


@pytest.fixture
def peer_result(raw_result):
    source = copy.deepcopy(raw_result)
    source["run"].update(id="earlier-peer-run", started_at="2026-01-01T00:00:00+00:00",
                         finished_at="2026-01-01T01:00:00+00:00")
    peer = copy.deepcopy(source["cases"][1])
    peer.update(case_id="peer", variant_id="peer")
    peer["simulator"].update(implementation_id="peer", adapter="symft", name="SymFT")
    expected = copy.deepcopy(source["campaign"]["cases"][1])
    expected.update(id="peer", variant_id="peer", implementation_id="peer")
    source["cases"].append(peer)
    source["campaign"]["cases"].append(expected)
    source["campaign"]["comparisons"].append(
        {"id": "peers", "baseline_variant": "current", "candidate_variants": ["peer"]}
    )
    for case, rate in ((source["cases"][1], 50), (peer, 250)):
        for sample in case["samples"]:
            sample["throughput_attempted_shots_per_second"] = rate
            sample["duration_seconds"] = sample["attempted_shots"] / rate
    validate_result(source)
    return source


def test_peer_reuse_keeps_current_rates_release_pairs_and_sources(raw_result, peer_result):
    before = copy.deepcopy((raw_result, peer_result))
    original = build_report(raw_result)
    report = build_report(raw_result, peer_document=peer_result)
    assert report["pairs"] == original["pairs"]
    assert report["release"] == original["release"]
    assert report["tools"][0]["cases"]["current"] is raw_result["cases"][1]
    assert report["rows"]["current"]["median_attempted_shots_per_second"] == 125
    assert report["rows"]["peer"]["median_attempted_shots_per_second"] == 250
    assert (raw_result, peer_result) == before
    summary = review_summary(report)
    assert peer_result["run"]["id"] in summary
    assert peer_result["run"]["finished_at"] in summary
    identity = peer_result["cases"][-1]["simulator"]
    assert f"| {identity['implementation_id']} | {identity['version']} |" in summary


@pytest.mark.parametrize("section,key,value", [
    ("campaign", "classification", "official"),
    ("campaign", "hardware_epoch", "other-hardware"),
    ("campaign", "reference_host", {"instance_type": "other"}),
    ("campaign", "measurement", {}),
    ("campaign", "resources", {"logical_cpu": 99}),
    ("campaign", "seed", -1),
    ("runner", "cpu_model", "other-CPU"),
    ("runner", "python", "different-Python"),
])
def test_peer_reuse_rejects_incompatible_collection(raw_result, peer_result, section, key, value):
    peer_result[section][key] = value
    with pytest.raises(ValueError, match="incompatible peer"):
        build_report(raw_result, peer_document=peer_result)


@pytest.mark.parametrize("field", ["digest", "selection", "shots_per_call"])
def test_peer_reuse_checks_cross_collection_workload_contract(raw_result, peer_result, field):
    # Change every source case consistently so its internal pairs still agree.
    for case in peer_result["cases"]:
        if field == "digest":
            case["workload"]["artifact"]["sha256"] = "f" * 64
        elif field == "selection":
            case["workload"]["semantics"]["postselect_all_detectors"] = (
                not case["workload"]["semantics"]["postselect_all_detectors"]
            )
        else:
            case["execution"][field] *= 2
    with pytest.raises(ValueError, match="incompatible comparison"):
        build_report(raw_result, peer_document=peer_result)


def test_peer_reuse_retains_failures_and_new_workload_absences(raw_result, peer_result):
    peer_result["cases"][-1].update(
        status="error", error={"phase": "sampling", "type": "WorkerTimeout", "message": "timeout"},
    )
    for identifier, adapters in (("new-shared", ["clifft", "symft"]),
                                 ("new-clifft-only", ["clifft"])):
        case = copy.deepcopy(raw_result["cases"][1])
        case.update(case_id=identifier)
        case["workload"].update(id=identifier, compatible_adapters=adapters)
        expected = copy.deepcopy(raw_result["campaign"]["cases"][1])
        expected.update(id=identifier, workload_id=identifier)
        raw_result["cases"].append(case)
        raw_result["campaign"]["cases"].append(expected)
    report = build_report(raw_result, peer_document=peer_result)
    from clifft_bench.report import _outcome

    assert report["rows"]["peer"]["median_attempted_shots_per_second"] == ""
    entries = {e["workload_id"]: e for e in report["tools"]}
    for workload, state in (("new-shared", "not measured"), ("new-clifft-only", "unsupported")):
        entry = entries[workload]
        assert _outcome(entry["cases"]["peer"], entry["cases"]["current"],
                        report["variants"]["peer"]) == state


@pytest.mark.parametrize("problem", [
    "collision", "identity-collision", "missing-current", "mixed-primary", "selection", "epoch",
])
def test_peer_reuse_rejects_ambiguous_inputs(raw_result, peer_result, problem):
    if problem == "collision":
        peer_result["cases"][-1]["case_id"] = "current"
    elif problem == "identity-collision":
        peer_result["cases"][-1]["simulator"]["implementation_id"] = "current"
    elif problem == "epoch":
        for source in (raw_result, peer_result):
            source["run"]["classification"] = source["campaign"]["classification"] = "official"
            source["campaign"].pop("hardware_epoch", None)
    elif problem == "missing-current":
        raw_result["cases"][1]["workload"]["id"] = "different-workload"
    elif problem == "mixed-primary":
        raw_result["cases"][1]["simulator"]["adapter"] = "symft"
    else:
        peer_result["run"]["selection"] = "partial"
    with pytest.raises(ValueError):
        build_report(raw_result, peer_document=peer_result)


def test_peer_report_cli_validates_both_sources_without_writing(raw_result, peer_result, tmp_path):
    primary, peers = tmp_path / "primary", tmp_path / "peers"
    primary.mkdir()
    peers.mkdir()
    write_json(primary / "raw.json", raw_result)
    write_json(peers / "raw.json", peer_result)
    assert main(["report", str(primary), "--peer-results", str(peers), "--check"]) == 0
    assert sorted(p.name for p in primary.iterdir()) == ["raw.json"]
    assert sorted(p.name for p in peers.iterdir()) == ["raw.json"]
    peer_result["cases"][-1]["samples"][0]["attempted_shots"] += 1
    write_json(peers / "raw.json", peer_result)
    assert main(["report", str(primary), "--peer-results", str(peers), "--check"]) == 2


def test_peer_render_preserves_release_figures_and_source_files(
    raw_result, peer_result, tmp_path, monkeypatch,
):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    primary, peers = tmp_path / "primary", tmp_path / "peers"
    primary.mkdir()
    peers.mkdir()
    write_json(primary / "raw.json", raw_result)
    write_json(peers / "raw.json", peer_result)
    report_execution(primary)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    savefig = Figure.savefig
    plotted = []

    def inspect(figure, path, **kwargs):
        if path.name.startswith("simulator-throughput"):
            plotted.append(sorted(float(line.get_xdata()[0]) for line in figure.axes[0].lines
                                  if line.get_marker() in ("o", "s")))
            assert any("separate collections" in text.get_text() for text in figure.texts)
        savefig(figure, path, **kwargs)

    monkeypatch.setattr(Figure, "savefig", inspect)
    report_execution(primary, peer_results=peers)
    assert plotted == [[125, 250], [125, 250]]
    for path, content in before.items():
        if (path.suffix != ".png" and path.name != "review.md"
                or path.name.startswith("release-change")):
            assert path.read_bytes() == content
    assert not (peers / "cases.csv").exists()
