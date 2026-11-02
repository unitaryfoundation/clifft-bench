import copy
import csv

import pytest

from clifft_bench.calibration import CALIBRATION_POLICY
from clifft_bench.cli import main
from clifft_bench.results import (
    _validate_bounded_calibration,
    _validate_calibration_record,
    case_rows,
    comparison_pairs,
    export_csv,
    read_result,
    validate_result,
    write_json,
)


def test_round_trip_and_deterministic_csv_without_configuration(raw_result, tmp_path):
    raw = tmp_path / "raw.json"
    write_json(raw, raw_result)
    assert read_result(raw) == raw_result
    output = tmp_path / "cases.csv"
    export_csv(raw_result, output)
    original = output.read_bytes()
    # Table order comes from the resolved campaign, not JSON case ordering.
    raw_result["cases"].reverse()
    export_csv(raw_result, output)
    assert output.read_bytes() == original
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert [float(r["median_attempted_shots_per_second"]) for r in rows] == [100, 125]
    assert main(["csv", str(raw)]) == 0
    assert main(["validate", str(raw)]) == 0
    assert main(["csv", str(raw), "--output", str(raw)]) == 2
    assert read_result(raw) != raw_result  # Regeneration never rewrites authoritative evidence.


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "extra",
        "running",
        "empty-samples",
        "wrong-repetitions",
        "nan",
        "infinite",
        "negative-duration",
        "bad-counts",
        "wrong-rate",
        "version",
        "thread",
        "no-correctness",
        "call-size",
        "no-error",
        "unknown-format",
        "unfinished",
    ],
)
def test_invalid_persisted_evidence_is_rejected(raw_result, damage):
    case = raw_result["cases"][0]
    if damage == "missing":
        raw_result["cases"].pop()
    elif damage == "duplicate":
        raw_result["cases"].append(copy.deepcopy(case))
    elif damage == "extra":
        extra = copy.deepcopy(case)
        extra["case_id"] = "unexpected"
        raw_result["cases"].append(extra)
    elif damage == "running":
        case["status"] = "running"
    elif damage == "empty-samples":
        case["samples"] = []
    elif damage == "wrong-repetitions":
        case["samples"].pop()
    elif damage == "nan":
        case["samples"][0]["duration_seconds"] = float("nan")
    elif damage == "infinite":
        case["setup"]["runtime_metadata"]["compile_seconds"] = float("inf")
    elif damage == "negative-duration":
        case["samples"][0]["duration_seconds"] = -1
    elif damage == "bad-counts":
        case["samples"][0]["accepted_shots"] += 1
    elif damage == "wrong-rate":
        case["samples"][0]["throughput_attempted_shots_per_second"] *= 2
    elif damage == "version":
        case["simulator"]["version"] = "9.0"
    elif damage == "thread":
        case["execution"]["threads_effective"] = 2
    elif damage == "no-correctness":
        case["correctness"] = None
    elif damage == "call-size":
        case["execution"]["shots_per_call"] += 1
    elif damage == "no-error":
        case["status"] = "error"
    elif damage == "unknown-format":
        raw_result["format"] = "clifft-bench/result/v1"
    else:
        raw_result["run"]["finished_at"] = None
    with pytest.raises(ValueError):
        validate_result(raw_result)


@pytest.mark.parametrize("phase", ["setup", "warmup", "correctness", "sampling"])
def test_failed_cases_keep_evidence_without_exporting_throughput(raw_result, phase):
    case = raw_result["cases"][1]
    case.update(
        status="error", error={"phase": phase, "type": "WorkerTimeout", "message": "timeout"}
    )
    if phase == "setup":
        case.update(setup=None, warmup=None, correctness=None, samples=[])
        case["execution"].pop("batch_size_effective")
    validate_result(raw_result)
    row = case_rows(raw_result)[1]
    assert row["status"] == "error" and row["error_phase"] == phase
    assert row["median_attempted_shots_per_second"] == ""
    assert row["mad_attempted_shots_per_second"] == ""
    assert row["sample_count"] == len(case["samples"])
    assert comparison_pairs(raw_result)[0]["ratio"] is None


@pytest.mark.parametrize("field", ["digest", "selection", "calls", "memory"])
def test_comparisons_reject_incompatible_inputs(raw_result, field):
    case = raw_result["cases"][1]
    if field == "digest":
        case["workload"]["artifact"]["sha256"] = "f" * 64
    elif field == "selection":
        case["workload"]["semantics"]["observable_index"] += 1
    elif field == "calls":
        case["execution"]["shots_per_call"] += 1
    else:
        case["execution"]["memory_limit_bytes"] = 1 << 30
    with pytest.raises(ValueError, match="incompatible comparison"):
        comparison_pairs(raw_result)


def test_ratios_use_each_side_sample_median(raw_result):
    case = raw_result["cases"][1]
    case["samples"][1]["throughput_attempted_shots_per_second"] = 175
    case["samples"][1]["duration_seconds"] = case["samples"][1]["attempted_shots"] / 175
    validate_result(raw_result)
    assert comparison_pairs(raw_result)[0]["ratio"] == 1.5


@pytest.mark.parametrize("damage", [None, "source", "cloud", "memory", "affinity", "partial"])
def test_official_evidence_requires_applied_host_constraints(raw_result, damage):
    raw_result["run"].update(classification="official", overrides={}, selection=None)
    campaign = raw_result["campaign"]
    campaign.update(
        classification="official",
        collection={"memory_limit_gib": 1},
        reference_host={"provider": "aws", "instance_type": "m7a.xlarge", "architecture": "x86_64"},
    )
    campaign["resources"].update(logical_cpu=0, memory_limit_bytes=1 << 30)
    runner = raw_result["runner"]
    runner.update(
        os="Linux",
        machine="x86_64",
        suite_source={"commit": "a" * 40, "dirty": False},
        cloud={
            "provider": "aws",
            "instance_type": "m7a.xlarge",
            "instance_id": "i-test",
            "image_id": "ami-test",
            "region": "us-east-1",
            "availability_zone": "us-east-1a",
            "lifecycle": "on-demand",
            "boot_id": "boot-test",
        },
    )
    for case in raw_result["cases"]:
        case["execution"]["memory_limit_bytes"] = 1 << 30
        case["setup"]["runtime_metadata"]["address_space_limit_bytes"] = 1 << 30
        case["setup"]["affinity"].update(applied=True, available_after=[0])
    if damage == "source":
        runner["suite_source"]["dirty"] = True
    elif damage == "cloud":
        runner["cloud"]["instance_type"] = "different"
    elif damage == "memory":
        raw_result["cases"][0]["setup"]["runtime_metadata"]["address_space_limit_bytes"] = None
    elif damage == "affinity":
        raw_result["cases"][0]["setup"]["affinity"]["applied"] = False
    elif damage == "partial":
        raw_result["run"]["selection"] = "current"
    if damage:
        with pytest.raises(ValueError):
            validate_result(raw_result)
    else:
        validate_result(raw_result)


def test_invalid_json_is_reported_without_traceback(tmp_path, capsys):
    path = tmp_path / "raw.json"
    path.write_text("{")
    assert main(["validate", str(path)]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def _bounded_calibration():
    return {
        "policy": CALIBRATION_POLICY,
        "budget_seconds": 60.0,
        "budget_scope": "between-candidates",
        "probe_seconds": 1.0,
        "repetitions": 3,
        "search_seconds": 10.0,
        "selection_statistic": "median_attempted_shots_per_second",
        "tie_break": "smaller_batch_size",
        "selected_batch_size": 64,
        "duration_seconds": 11.0,
        "candidates": [1, 32, 64],
        "results": [
            {
                "batch_size": batch,
                "status": "success",
                "effective_batch_size": batch,
                "median_attempted_shots_per_second": rate,
                "duration_seconds": 3.0,
                "error": None,
                "samples": [
                    {"throughput_attempted_shots_per_second": rate, "peak_rss_bytes": 1024}
                    for _ in range(3)
                ],
            }
            for batch, rate in ((1, 10.0), (32, 50.0), (64, 100.0))
        ],
    }


@pytest.mark.parametrize(
    "damage",
    [
        "policy",
        "missing-candidate",
        "failed-selection",
        "partial-probe",
        "missing-memory",
        "wrong-budget",
        "skipped-probe",
        "nonfinite-rate",
        "invalid-duration",
    ],
)
def test_raw_validation_rejects_incomplete_or_mismatched_tuning_evidence(damage):
    expected = {"id": "candidate", "shots_per_call": 64, "execution": {"batch_size": "calibrate"}}
    calibration = _bounded_calibration()
    observed = {
        "status": "success",
        "simulator": {"adapter": "clifft"},
        "execution": {
            "batch_size": 64,
            "tuning_policy": CALIBRATION_POLICY,
        },
        "setup": {"runtime_metadata": {"batch_calibration": calibration}},
    }
    if damage == "policy":
        calibration["policy"] = "different-policy"
    elif damage == "missing-candidate":
        calibration["results"].pop()
    elif damage == "failed-selection":
        calibration["results"][-1].update(status="error", samples=[], error={"type": "MemoryError"})
    elif damage == "partial-probe":
        calibration["results"][0]["samples"].pop()
    elif damage == "missing-memory":
        calibration["results"][0]["samples"][0].pop("peak_rss_bytes")
    elif damage == "wrong-budget":
        calibration["budget_seconds"] = 3600
    elif damage == "nonfinite-rate":
        sample = calibration["results"][0]["samples"][0]
        sample["throughput_attempted_shots_per_second"] = float("nan")
    elif damage == "invalid-duration":
        calibration["results"][0]["duration_seconds"] = -1
    else:
        calibration["results"][0]["status"] = "budget-skipped"
    with pytest.raises(ValueError):
        _validate_calibration_record(expected, observed)


@pytest.mark.parametrize(
    "skip_index, later_status",
    [
        (0, "success"),
        (1, "success"),
        (1, "error"),
    ],
)
def test_raw_validation_rejects_attempted_candidates_after_budget_skip(skip_index, later_status):
    calibration = _bounded_calibration()
    calibration["selected_batch_size"] = 64 if skip_index == 0 else 1
    calibration["results"][skip_index].update(
        status="budget-skipped",
        samples=[],
        duration_seconds=0.0,
        effective_batch_size=None,
        median_attempted_shots_per_second=None,
    )
    if later_status == "error":
        calibration["results"][skip_index + 1].update(
            status="error",
            samples=[],
            error={"type": "MemoryError"},
            effective_batch_size=None,
            median_attempted_shots_per_second=None,
        )
    with pytest.raises(ValueError, match="candidate attempted after budget skip"):
        _validate_bounded_calibration(calibration, "candidate")


def test_raw_validation_accepts_budget_skipped_suffix():
    calibration = _bounded_calibration()
    calibration["selected_batch_size"] = 1
    for row in calibration["results"][1:]:
        row.update(
            status="budget-skipped",
            samples=[],
            duration_seconds=0.0,
            effective_batch_size=None,
            median_attempted_shots_per_second=None,
        )
    _validate_bounded_calibration(calibration, "candidate")


@pytest.mark.parametrize("selected", [1, 32, 64])
def test_selected_calibration_state_exports_and_reports(raw_result, tmp_path, selected):
    from clifft_bench.report import report_execution

    for case, contract in zip(raw_result["cases"], raw_result["campaign"]["cases"], strict=True):
        contract["shots_per_call"] = case["execution"]["shots_per_call"] = 64
        for sample in case["samples"]:
            for key in ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors"):
                sample[key] *= 8
            sample["duration_seconds"] *= 8
    case, contract = raw_result["cases"][1], raw_result["campaign"]["cases"][1]
    contract["execution"]["batch_size"] = "calibrate"
    case["execution"].update(batch_size=selected, batch_enabled=selected > 1,
                             batch_size_effective=selected, tuning_policy=CALIBRATION_POLICY)
    calibration = _bounded_calibration()
    calibration["selected_batch_size"] = selected
    case["setup"]["runtime_metadata"].update(
        batch_calibration=calibration, batch_enabled=selected > 1, effective_batch_size=selected,
    )
    write_json(tmp_path / "raw.json", raw_result)
    export_csv(raw_result, tmp_path / "cases.csv")
    assert "1.25×" in report_execution(tmp_path, check=True)
    case["execution"]["batch_enabled"] = selected == 1
    with pytest.raises(ValueError, match="batching differs"):
        validate_result(raw_result)


@pytest.mark.parametrize("status", ["error", "interrupted"])
def test_calibration_without_setup_keeps_requested_state(raw_result, status):
    case, contract = raw_result["cases"][1], raw_result["campaign"]["cases"][1]
    contract["execution"]["batch_size"] = "calibrate"
    case["execution"].update(batch_size="calibrate", batch_enabled=True,
                             tuning_policy=CALIBRATION_POLICY)
    case.update(status=status, setup=None, warmup=None, correctness=None, samples=[],
                error={"phase": "setup", "type": "WorkerTimeout", "message": "timeout"})
    validate_result(raw_result)
    assert case_rows(raw_result)[1]["median_attempted_shots_per_second"] == ""
    case["execution"].update(batch_size=1, batch_enabled=False)
    with pytest.raises(ValueError, match="mismatched requested batch size"):
        validate_result(raw_result)
