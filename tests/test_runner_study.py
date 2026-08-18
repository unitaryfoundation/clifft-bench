from __future__ import annotations

import copy
import csv
import json
import uuid
from pathlib import Path

import pytest

from clifft_bench.runner_study import (
    analyze_runner_study,
    write_runner_study_csv,
    write_runner_study_json,
)
from clifft_bench.schema import SchemaValidationError, repository_root, validate_path


def _aa_result(
    tmp_path: Path,
    *,
    name: str = "aa.json",
    rate_a: float | list[float] = 100.0,
    rate_b: float | list[float] = 102.0,
) -> Path:
    document = validate_path(repository_root() / "examples/result.v1.json")
    document["run"]["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    case_a = copy.deepcopy(document["cases"][0])
    case_b = copy.deepcopy(case_a)
    case_a["case_id"] = "example-aa-a"
    case_b["case_id"] = "example-aa-b"
    case_a["pair_id"] = "example-aa"
    case_b["pair_id"] = "example-aa"
    rates_a = rate_a if isinstance(rate_a, list) else [rate_a]
    rates_b = rate_b if isinstance(rate_b, list) else [rate_b]
    assert len(rates_a) == len(rates_b)
    sample_a = case_a["samples"][0]
    sample_b = case_b["samples"][0]
    case_a["samples"] = []
    case_b["samples"] = []
    for repetition, (value_a, value_b) in enumerate(zip(rates_a, rates_b, strict=True)):
        repetition_a = copy.deepcopy(sample_a)
        repetition_b = copy.deepcopy(sample_b)
        repetition_a["repetition"] = repetition
        repetition_b["repetition"] = repetition
        repetition_a["sequence_index"] = 2 * repetition
        repetition_b["sequence_index"] = 2 * repetition + 1
        repetition_a["throughput_attempted_shots_per_second"] = value_a
        repetition_b["throughput_attempted_shots_per_second"] = value_b
        case_a["samples"].append(repetition_a)
        case_b["samples"].append(repetition_b)
    document["cases"] = [case_a, case_b]
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def test_runner_study_reports_paired_ratios_and_writes_derived_files(
    tmp_path: Path,
) -> None:
    report, observations = analyze_runner_study([_aa_result(tmp_path)])

    assert report["result_count"] == 1
    assert report["observation_count"] == 1
    assert report["skipped_pair_count"] == 0
    assert report["report_format"] == "clifft-bench/runner-study-summary/v2"
    assert "schema_version" not in report
    assert report["groups"][0]["ratio_b_over_a"]["median"] == pytest.approx(1.02)
    assert report["pair_groups"][0]["hardware_key_count"] == 1
    assert report["groups"][0]["absolute_delta_percent"]["p95"] == pytest.approx(
        200 * 2 / 202
    )
    assert report["dispatch_groups"][0]["dispatch_count"] == 1
    assert report["dispatch_estimates"][0]["replica_count"] == 1

    json_path = tmp_path / "summary.json"
    csv_path = tmp_path / "pairs.csv"
    write_runner_study_json(json_path, report)
    write_runner_study_csv(csv_path, observations)
    assert json.loads(json_path.read_text())["observation_count"] == 1
    assert not (tmp_path / "summary.json.tmp").exists()
    assert not (tmp_path / "pairs.csv.tmp").exists()
    with csv_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["pair_id"] == "example-aa"
    assert float(rows[0]["ratio_b_over_a"]) == pytest.approx(1.02)


def test_runner_study_rejects_identity_changes_across_results(tmp_path: Path) -> None:
    first = _aa_result(tmp_path, name="first.json")
    second = _aa_result(tmp_path, name="second.json")
    document = json.loads(second.read_text())
    for case in document["cases"]:
        case["simulator"]["commit_sha"] = "1" * 40
    second.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="changes identity between"):
        analyze_runner_study([first, second])


def test_runner_study_hardware_key_ignores_page_sized_memory_jitter(
    tmp_path: Path,
) -> None:
    first = _aa_result(tmp_path, name="first.json")
    second = _aa_result(tmp_path, name="second.json")
    second_document = json.loads(second.read_text())
    second_document["runner"]["memory_bytes"] += 4096
    second.write_text(json.dumps(second_document))

    report, observations = analyze_runner_study([first, second])

    assert len(report["groups"]) == 1
    assert len({item["hardware_key"] for item in observations}) == 1
    assert report["groups"][0]["hardware"]["observed_memory_bytes"] == {
        "min": 17179869184,
        "max": 17179873280,
    }


def test_runner_study_reports_median_of_replica_log_medians(tmp_path: Path) -> None:
    paths = [
        _aa_result(
            tmp_path,
            name="replica-1.json",
            rate_a=[100.0, 100.0, 100.0],
            rate_b=[101.0, 102.0, 150.0],
        ),
        _aa_result(
            tmp_path,
            name="replica-2.json",
            rate_a=[100.0, 100.0, 100.0],
            rate_b=[70.0, 104.0, 105.0],
        ),
        _aa_result(
            tmp_path,
            name="replica-3.json",
            rate_a=[100.0, 100.0, 100.0],
            rate_b=[99.0, 106.0, 200.0],
        ),
    ]
    for path in paths:
        document = json.loads(path.read_text())
        document["run"]["workflow"]["run_id"] = "example-dispatch"
        path.write_text(json.dumps(document))

    report, _ = analyze_runner_study(paths)

    assert len(report["dispatch_estimates"]) == 1
    estimate = report["dispatch_estimates"][0]
    assert estimate["replica_count"] == 3
    assert estimate["ratio_b_over_a"] == pytest.approx(1.04)
    assert estimate["signed_delta_percent"] == pytest.approx(200 * 0.04 / 2.04)
    assert estimate["absolute_delta_percent"] == pytest.approx(200 * 0.04 / 2.04)
    assert estimate["throughput_attempted_shots_per_second"] == pytest.approx(100.0)
    assert report["dispatch_groups"][0]["throughput_attempted_shots_per_second"][
        "median"
    ] == pytest.approx(100.0)


def test_runner_study_groups_cloud_boots_by_fixed_launch_configuration(
    tmp_path: Path,
) -> None:
    first = _aa_result(tmp_path, name="first.json")
    second = _aa_result(tmp_path, name="second.json")
    for index, path in enumerate((first, second), start=1):
        document = json.loads(path.read_text())
        document["runner"]["cloud"] = {
            "provider": "aws",
            "instance_id": f"i-{index}",
            "instance_type": "m7a.xlarge",
            "image_id": "ami-fixed",
            "region": "us-east-1",
            "availability_zone": "us-east-1a",
            "lifecycle": "on-demand",
            "boot_id": f"boot-{index}",
        }
        path.write_text(json.dumps(document))

    report, observations = analyze_runner_study([first, second])

    assert report["pair_groups"][0]["hardware_key_count"] == 1
    assert len({item["hardware_key"] for item in observations}) == 1
    assert report["groups"][0]["hardware"]["instance_type"] == "m7a.xlarge"
    assert {item["cloud_identity"]["instance_id"] for item in observations} == {
        "i-1",
        "i-2",
    }

    changed = json.loads(second.read_text())
    changed["runner"]["cloud"]["image_id"] = "ami-changed"
    second.write_text(json.dumps(changed))
    changed_report, _ = analyze_runner_study([first, second])
    assert changed_report["pair_groups"][0]["hardware_key_count"] == 2


def test_runner_study_skips_failed_pair_but_keeps_healthy_pair(tmp_path: Path) -> None:
    path = _aa_result(tmp_path)
    document = json.loads(path.read_text())
    broken_a, broken_b = copy.deepcopy(document["cases"])
    broken_a["case_id"] = "broken-a"
    broken_b["case_id"] = "broken-b"
    broken_a["pair_id"] = "broken"
    broken_b["pair_id"] = "broken"
    broken_a["status"] = "error"
    broken_a["error"] = {
        "phase": "sampling",
        "type": "ExampleError",
        "message": "intentional test failure",
    }
    document["cases"].extend([broken_a, broken_b])
    path.write_text(json.dumps(document))

    report, observations = analyze_runner_study([path])

    assert len(observations) == 1
    assert report["observation_count"] == 1
    assert report["skipped_pair_count"] == 1
    assert report["skipped_pairs"][0]["pair_id"] == "broken"
    assert "unsuccessful case" in report["skipped_pairs"][0]["reason"]


def test_runner_study_rejects_duplicate_paths(tmp_path: Path) -> None:
    path = _aa_result(tmp_path)
    with pytest.raises(ValueError, match="duplicate raw result path"):
        analyze_runner_study([path, path])

    copy_path = tmp_path / "copy.json"
    copy_path.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="duplicate raw result run id"):
        analyze_runner_study([path, copy_path])


def test_runner_study_json_errors_include_the_path(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    with pytest.raises(SchemaValidationError, match="malformed.json: invalid JSON"):
        analyze_runner_study([malformed])

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="missing.json: cannot read raw result"):
        analyze_runner_study([missing])
