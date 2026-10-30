from __future__ import annotations

import copy
import csv
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.results import _comparison_rows, finalize_execution
from clifft_bench.schema import repository_root, validate_path

WORKLOAD_ID = "msc-d3-inject-cultivate-p1e-3"


def _expected_case(variant: str, *, batch_size: int | str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"workload--{variant}",
        workload=SimpleNamespace(definition=validate_path(
            repository_root() / "examples/result.v1.json"
        )["cases"][0]["workload"]),
        implementation=SimpleNamespace(definition={"adapter": "fixture"}),
        definition={
            "id": f"workload--{variant}",
            "variant_id": variant,
            "workload_id": WORKLOAD_ID,
            "implementation_id": variant,
            "shots_per_call": 64,
            "execution": {
                "mode": "throughput",
                "batch_enabled": batch_size != 1,
                "batch_size": batch_size,
                "sample_chunk_shots": 0,
            },
        },
    )


def _suite(*, calibrated_candidate: bool = False):
    return SimpleNamespace(
        run_path=repository_root() / "campaigns/release-v1/run.v1.json",
        cases=(
            _expected_case("baseline", batch_size=1),
            _expected_case(
                "candidate",
                batch_size="calibrate" if calibrated_candidate else 32,
            ),
        ),
        run={
            "profile_id": "test-campaign",
            "classification": "official",
            "hardware_epoch": "test-epoch",
            "reference_host": {"instance_type": "m7a.xlarge"},
            "collection": {
                "placements": 1,
                "replicas_per_placement": 1,
                "run_timeout_minutes": 1,
                "memory_limit_gib": 1,
            },
            "comparisons": [
                {
                    "id": "candidate-vs-baseline",
                    "baseline_variant": "baseline",
                    "candidate_variants": ["candidate"],
                }
            ],
        },
    )


def _case(template: dict, *, variant: str, rate: float) -> dict:
    case = copy.deepcopy(template)
    case["case_id"] = f"workload--{variant}"
    case["variant_id"] = variant
    case["simulator"]["implementation_id"] = variant
    if variant == "candidate":
        case["simulator"]["version"] = "0.10.0rc1"
        case["simulator"]["display_version"] = "0.10.0"
    else:
        case["simulator"]["version"] = "0.9.0"
        case["simulator"].pop("display_version", None)
    case["execution"]["memory_limit_bytes"] = 1 << 30
    case["setup"]["runtime_metadata"]["address_space_limit_bytes"] = 1 << 30
    if variant == "candidate":
        case["execution"]["batch_enabled"] = True
        case["execution"]["batch_size"] = 32
        case["execution"]["batch_size_effective"] = 32
        case["execution"]["shots_per_call"] = 64
    case["samples"][0]["throughput_attempted_shots_per_second"] = rate
    case["summary"]["median_attempted_shots_per_second"] = rate
    case["summary"]["min_attempted_shots_per_second"] = rate
    case["summary"]["max_attempted_shots_per_second"] = rate
    return case


def _result(tmp_path: Path) -> Path:
    document = copy.deepcopy(validate_path(repository_root() / "examples/result.v1.json"))
    document["run"]["id"] = str(uuid.uuid4())
    document["run"]["profile_id"] = "test-campaign"
    document["run"]["workflow"]["run_attempt"] = "1.1"
    document["runner"]["suite_source"] = {"commit": "1" * 40, "dirty": False}
    document["runner"]["cloud"] = {
        "provider": "aws",
        "instance_id": "i-example",
        "instance_type": "m7a.xlarge",
        "image_id": "ami-example",
        "region": "us-east-1",
        "availability_zone": "us-east-1c",
        "lifecycle": "on-demand",
        "boot_id": "boot-example",
    }
    template = document["cases"][0]
    document["cases"] = [
        _case(template, variant="baseline", rate=100.0),
        _case(template, variant="candidate", rate=125.0),
    ]
    path = tmp_path / "release-p01-r01-raw.json"
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize("change", [
    "mask", "missing_mask", "all", "reference", "observable", "output", "numerator", "execution",
])
def test_finalize_rejects_changed_selection_semantics(tmp_path, change):
    suite = _suite()
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    for expected, observed in zip(suite.cases, raw["cases"]):
        semantics = expected.workload.definition["semantics"]
        semantics.update(postselect_all_detectors=False, postselection_detector_indices=[0])
        observed["workload"]["semantics"] = copy.deepcopy(semantics)
        observed["execution"]["postselection"] = True
    raw_path.write_text(json.dumps(raw))
    finalize_execution(suite, execution_id="matched-mask", raw_paths=[raw_path],
                       output_dir=tmp_path / "matched")
    case = raw["cases"][0]
    semantics = case["workload"]["semantics"]
    if change == "mask":
        semantics["postselection_detector_indices"] = [1]
    elif change == "missing_mask":
        del semantics["postselection_detector_indices"]
    elif change == "all":
        semantics["postselect_all_detectors"] = True
    elif change == "reference":
        semantics["reference_convention"] = "reference-normalized"
    elif change == "observable":
        semantics["observable_index"] = 1
    elif change == "output":
        semantics["output_contract"] = "per-shot records"
    elif change == "numerator":
        semantics["throughput_numerator"] = "accepted shots"
    else:
        case["execution"]["postselection"] = False
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="mismatched workload semantics"):
        finalize_execution(suite, execution_id="changed-mask", raw_paths=[raw_path],
                           output_dir=tmp_path / "rejected")


@pytest.mark.parametrize("key", ["logical_work", "batch_semantics"])
def test_finalize_accepts_description_edits_without_rewriting_raw_semantics(tmp_path, key):
    suite = _suite()
    raw_path = _result(tmp_path)
    original = raw_path.read_bytes()
    for expected in suite.cases:
        expected.workload.definition["semantics"][key] = "Reworded description."
    finalize_execution(suite, execution_id="reworded", raw_paths=[raw_path],
                       output_dir=tmp_path / "finalized")
    assert raw_path.read_bytes() == original


def test_finalize_writes_index_and_plot_ready_comparison_tables(tmp_path: Path) -> None:
    raw_path = _result(tmp_path)
    output_dir = tmp_path / "finalized"

    index = finalize_execution(
        _suite(),
        execution_id="test-execution",
        raw_paths=[raw_path],
        output_dir=output_dir,
    )

    validate_path(output_dir / "index.json")
    assert index["campaign_id"] == "test-campaign"
    assert index["placements"][0]["raw_results"] == [
        "raw/release-p01-r01-raw.json"
    ]
    with (output_dir / "cases.csv").open(newline="") as stream:
        cases = list(csv.DictReader(stream))
    candidate_case = next(row for row in cases if row["variant_id"] == "candidate")
    assert candidate_case["simulator_version"] == "0.10.0rc1"
    assert candidate_case["simulator_display_version"] == "0.10.0"
    baseline_case = next(row for row in cases if row["variant_id"] == "baseline")
    assert baseline_case["simulator_display_version"] == "0.9.0"
    with (output_dir / "comparisons.csv").open(newline="") as stream:
        comparisons = list(csv.DictReader(stream))
    assert len(comparisons) == 1
    assert float(comparisons[0]["ratio_candidate_over_baseline"]) == 1.25
    assert comparisons[0]["baseline_variant_id"] == "baseline"
    assert comparisons[0]["candidate_variant_id"] == "candidate"
    assert comparisons[0]["candidate_batch_size_effective"] == "32"
    assert comparisons[0]["baseline_simulator_version"] == "0.9.0"
    assert comparisons[0]["baseline_simulator_display_version"] == "0.9.0"
    assert comparisons[0]["candidate_simulator_version"] == "0.10.0rc1"
    assert comparisons[0]["candidate_simulator_display_version"] == "0.10.0"

    changed = json.loads(raw_path.read_text())
    changed["runner"]["cloud"]["instance_type"] = "c8i.8xlarge"
    raw_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="expected instance type"):
        finalize_execution(
            _suite(),
            execution_id="test-execution",
            raw_paths=[raw_path],
            output_dir=tmp_path / "rejected-instance",
        )

    changed["runner"]["cloud"]["instance_type"] = "m7a.xlarge"
    changed["cases"][0]["execution"]["memory_limit_bytes"] = None
    raw_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="memory limit does not match"):
        finalize_execution(
            _suite(),
            execution_id="test-execution",
            raw_paths=[raw_path],
            output_dir=tmp_path / "rejected-memory-limit",
        )

    changed["cases"][0]["execution"]["memory_limit_bytes"] = 1 << 30
    changed["cases"][0]["setup"]["runtime_metadata"][
        "address_space_limit_bytes"
    ] = None
    raw_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="worker memory limit does not match"):
        finalize_execution(
            _suite(),
            execution_id="test-execution",
            raw_paths=[raw_path],
            output_dir=tmp_path / "rejected-applied-memory-limit",
        )


@pytest.mark.parametrize("applied", [False, True])
def test_finalize_records_and_validates_clifft_scheduler(tmp_path: Path, applied: bool) -> None:
    suite = _suite()
    options = json.loads((
        repository_root() / "campaigns/release-v1/clifft-scheduled-execution.json"
    ).read_text())["clifft_scheduler"]
    suite.cases[1].definition["execution"]["clifft_scheduler"] = options
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    candidate = raw["cases"][1]
    candidate["execution"]["clifft_scheduler"] = options
    metadata = candidate["setup"]["runtime_metadata"]
    metadata.update(
        scheduler_statistics={"applied": applied},
        compile_seconds=0.125, peak_active_width=3,
    )
    raw_path.write_text(json.dumps(raw))
    output = tmp_path / "finalized"

    finalize_execution(
        suite, execution_id="test-execution", raw_paths=[raw_path], output_dir=output,
    )

    with (output / "cases.csv").open(newline="") as stream:
        row = next(row for row in csv.DictReader(stream) if row["variant_id"] == "candidate")
    assert json.loads(row["clifft_scheduler"]) == options
    assert float(row["compile_seconds"]) == 0.125
    assert int(row["peak_active_width"]) == 3

    metadata.pop("scheduler_statistics")
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="missing scheduler statistics"):
        finalize_execution(
            suite, execution_id="test-execution", raw_paths=[raw_path],
            output_dir=tmp_path / "rejected-metadata",
        )


def test_finalize_rejects_smoke_manifest(tmp_path: Path) -> None:
    suite = _suite()
    suite.run["classification"] = "smoke"

    with pytest.raises(ValueError, match="only official run manifests"):
        finalize_execution(
            suite,
            execution_id="test-execution",
            raw_paths=[],
            output_dir=tmp_path,
        )


def test_finalize_requires_complete_calibration_evidence(tmp_path: Path) -> None:
    raw_path = _result(tmp_path)

    with pytest.raises(ValueError, match="has no batch_calibration metadata"):
        finalize_execution(
            _suite(calibrated_candidate=True),
            execution_id="test-execution",
            raw_paths=[raw_path],
            output_dir=tmp_path / "missing-calibration",
        )

    document = json.loads(raw_path.read_text())
    candidate = next(
        case for case in document["cases"] if case["variant_id"] == "candidate"
    )
    runtime_metadata = candidate["setup"]["runtime_metadata"]
    runtime_metadata["batch_enabled"] = True
    runtime_metadata["effective_batch_size"] = 32
    runtime_metadata["batch_calibration"] = {
        "candidates": [1, 32],
        "selected_batch_size": 32,
        "results": [
            {"batch_size": 1, "status": "success"},
            {"batch_size": 32, "status": "success"},
        ],
    }
    raw_path.write_text(json.dumps(document))

    finalize_execution(
        _suite(calibrated_candidate=True),
        execution_id="test-execution",
        raw_paths=[raw_path],
        output_dir=tmp_path / "valid-calibration",
    )

    candidate["execution"]["batch_size"] = 1
    raw_path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="does not record selected numeric batch_size"):
        finalize_execution(
            _suite(calibrated_candidate=True),
            execution_id="test-execution",
            raw_paths=[raw_path],
            output_dir=tmp_path / "mismatched-selected-size",
        )


def test_comparison_rejects_duplicate_variant_and_workload() -> None:
    rows = [
        {
            "status": "success",
            "variant_id": variant,
            "placement": 1,
            "replica": 1,
            "workload_id": "workload",
        }
        for variant in ["baseline", "baseline", "candidate"]
    ]

    with pytest.raises(ValueError, match="multiple successful baseline cases"):
        _comparison_rows(_suite(), "execution", rows)


@pytest.mark.parametrize("field,value", [
    (None, None), ("threads", 2), ("sampling_backend", "cat5"),
    ("trajectory_sharing", False), ("installed_source_commit", "d" * 40),
    ("warmup_shots", 1),
])
def test_tsim_finalizer_checks_runtime_identity_and_full_warmup(tmp_path, field, value):
    suite = _suite()
    expected = suite.cases[1]
    expected.implementation.definition.update(adapter="tsim", commit_sha="c" * 40)
    expected.definition["execution"]["tsim_trajectory_sharing"] = True
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    observed = raw["cases"][1]
    observed["simulator"]["adapter"] = "tsim"
    observed["setup"]["runtime_metadata"].update(
        sampling_backend="active_statevector", threads=1, trajectory_sharing=True,
        installed_source_commit="c" * 40,
    )
    observed["warmup"].update(
        attempted_shots=64, accepted_shots=64, discarded_shots=0, logical_errors=0,
    )
    if field == "warmup_shots":
        observed["warmup"].update(attempted_shots=value, accepted_shots=value)
    elif field:
        observed["setup"]["runtime_metadata"][field] = value
    raw_path.write_text(json.dumps(raw))
    args = dict(execution_id="test-execution", raw_paths=[raw_path],
                output_dir=tmp_path / "finalized")
    if field:
        with pytest.raises(ValueError, match="Tsim"):
            finalize_execution(suite, **args)
    else:
        finalize_execution(suite, **args)


@pytest.mark.parametrize("error_type", ["WorkerTimeout", "MemoryError", "ValueError"])
def test_finalize_preserves_failed_setup_without_calibration(tmp_path, error_type):
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    candidate = raw["cases"][1]
    candidate.update(status="error", setup=None, warmup=None, correctness=None,
                     samples=[], summary=None,
                     error={"phase": "setup", "type": error_type, "message": "setup failed"})
    candidate["execution"].update(batch_size="calibrate")
    candidate["execution"].pop("batch_size_effective")
    raw_path.write_text(json.dumps(raw))
    output = tmp_path / "finalized"
    finalize_execution(_suite(calibrated_candidate=True), execution_id="test-execution",
                       raw_paths=[raw_path], output_dir=output)
    with (output / "cases.csv").open(newline="") as stream:
        row = next(row for row in csv.DictReader(stream) if row["variant_id"] == "candidate")
    assert row["status"] == "error"
    assert row["error_phase"] == "setup"
    assert row["error_type"] == error_type
    assert row["error_message"] == "setup failed"
    assert row["median_attempted_shots_per_second"] == ""
    assert row["batch_size_effective"] == ""
    with (output / "comparisons.csv").open(newline="") as stream:
        assert list(csv.DictReader(stream)) == []
    assert json.loads(raw_path.read_text()) == raw


@pytest.mark.parametrize("phase,has_setup", [
    ("setup", True), ("warmup", False), ("correctness", True), ("sampling", True),
])
def test_finalize_still_requires_calibration_after_setup(tmp_path, phase, has_setup):
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    candidate = raw["cases"][1]
    candidate.update(status="error", summary=None,
                     error={"phase": phase, "type": "WorkerTimeout", "message": "timeout"})
    if not has_setup:
        candidate["setup"] = None
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="has no batch_calibration metadata"):
        finalize_execution(_suite(calibrated_candidate=True), execution_id="test-execution",
                           raw_paths=[raw_path], output_dir=tmp_path / "rejected")


def test_finalize_does_not_export_partial_failure_throughput(tmp_path):
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    # A stale/partial summary must not turn an unsuccessful case into a rate.
    raw["cases"][1].update(status="error", error={
        "phase": "sampling", "type": "WorkerTimeout", "message": "later repetition failed",
    })
    raw_path.write_text(json.dumps(raw))
    output = tmp_path / "finalized"
    finalize_execution(_suite(), execution_id="test-execution",
                       raw_paths=[raw_path], output_dir=output)
    with (output / "cases.csv").open(newline="") as stream:
        row = next(row for row in csv.DictReader(stream) if row["variant_id"] == "candidate")
    assert row["median_attempted_shots_per_second"] == ""
    assert row["min_attempted_shots_per_second"] == ""
    assert row["max_attempted_shots_per_second"] == ""
    assert row["sample_count"] == "0"


@pytest.mark.parametrize("applicable", [True, False, None])
def test_finalize_exports_batch_parameter_applicability(tmp_path, applicable):
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    metadata = raw["cases"][1]["setup"]["runtime_metadata"]
    if applicable is not None:
        metadata.update(batch_parameter="statevector-batch-shots", batch_size_applicable=applicable)
    raw_path.write_text(json.dumps(raw))
    output = tmp_path / "finalized"
    finalize_execution(_suite(), execution_id="test-execution",
                       raw_paths=[raw_path], output_dir=output)
    with (output / "cases.csv").open(newline="") as stream:
        row = next(row for row in csv.DictReader(stream) if row["variant_id"] == "candidate")
    assert row["batch_parameter"] == ("" if applicable is None else "statevector-batch-shots")
    assert row["batch_size_applicable"] == ("" if applicable is None else str(applicable).lower())
    assert row["batch_size_effective"] == "32"


def _bounded_calibration():
    return {
        "policy": "bounded-v2", "budget_seconds": 60.0,
        "budget_scope": "between-candidates", "native_budget_seconds": 0.25,
        "probe_seconds": 1.0, "repetitions": 3, "search_seconds": 10.0,
        "selection_statistic": "median_attempted_shots_per_second",
        "tie_break": "smaller_batch_size", "selected_batch_size": 64,
        "duration_seconds": 11.0, "candidates": [64, 32, 1],
        "native": {"status": "success", "report": {
            "batch_size": 64, "baseline_batch_size": 32,
            "shot_workers": 1, "intra_shot_workers": 1, "elapsed_seconds": 0.25,
            "sufficient_measurements": True, "stop_reason": "completed", "trials": [],
        }},
        "results": [
            {"batch_size": batch, "status": "success", "effective_batch_size": batch,
             "median_attempted_shots_per_second": rate, "duration_seconds": 3.0,
             "error": None,
             "samples": [{"throughput_attempted_shots_per_second": rate,
                          "peak_rss_bytes": 1024} for _ in range(3)]}
            for batch, rate in ((64, 100.0), (32, 50.0), (1, 10.0))
        ],
    }


def test_finalize_bounded_native_choice_and_export_policy_columns(tmp_path):
    suite = _suite(calibrated_candidate=True)
    suite.cases[1].definition["execution"]["tuning_policy"] = "bounded-v2"
    suite.cases[1].implementation.definition["adapter"] = "clifft"
    raw_path = _result(tmp_path)
    raw = json.loads(raw_path.read_text())
    candidate = raw["cases"][1]
    candidate["simulator"]["adapter"] = "clifft"
    candidate["execution"].update(tuning_policy="bounded-v2", batch_size=64,
                                  batch_size_effective=64)
    candidate["setup"]["runtime_metadata"]["batch_calibration"] = _bounded_calibration()
    raw_path.write_text(json.dumps(raw))
    output = tmp_path / "bounded-result"
    finalize_execution(
        suite, execution_id="test-execution", raw_paths=[raw_path], output_dir=output,
    )
    with (output / "cases.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[1]["tuning_policy"] == "bounded-v2"
    with (output / "comparisons.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert row["baseline_tuning_policy"] == "fixed"
    assert row["candidate_tuning_policy"] == "bounded-v2"


@pytest.mark.parametrize("damage", [
    "policy", "native-workers", "missing-candidate", "wrong-winner", "partial-probe",
    "missing-memory", "wrong-budget", "premature-skip",
])
def test_bounded_finalizer_rejects_incomplete_or_mismatched_tuning(damage):
    from clifft_bench.results import _validate_calibration_record

    expected = _expected_case("candidate", batch_size="calibrate")
    expected.definition["execution"]["tuning_policy"] = "bounded-v2"
    expected.implementation.definition["adapter"] = "clifft"
    calibration = _bounded_calibration()
    observed = {"status": "success", "execution": {
        "batch_size": 64, "tuning_policy": "bounded-v2",
    }, "setup": {"runtime_metadata": {"batch_calibration": calibration}}}
    if damage == "policy":
        observed["execution"]["tuning_policy"] = "grid-v1"
    elif damage == "native-workers":
        calibration["native"]["report"]["shot_workers"] = 2
    elif damage == "missing-candidate":
        calibration["results"].pop()
    elif damage == "wrong-winner":
        calibration["selected_batch_size"] = observed["execution"]["batch_size"] = 32
    elif damage == "partial-probe":
        calibration["results"][0]["samples"].pop()
    elif damage == "missing-memory":
        calibration["results"][0]["samples"][0].pop("peak_rss_bytes")
    elif damage == "wrong-budget":
        calibration["budget_seconds"] = 3600
    else:
        calibration["results"][-1].update(status="budget-skipped", samples=[])
    with pytest.raises(ValueError):
        _validate_calibration_record(expected, observed)


def test_legacy_tsim_calibration_does_not_require_new_small_candidates():
    from clifft_bench.results import _validate_calibration_record

    expected = _expected_case("candidate", batch_size="calibrate")
    expected.implementation.definition["adapter"] = "tsim"
    observed = {"status": "success", "execution": {"batch_size": 64},
                "setup": {"runtime_metadata": {"batch_calibration": {
                    "candidates": [64], "selected_batch_size": 64,
                }}}}
    _validate_calibration_record(expected, observed)
