from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from clifft_bench.calibration import BATCH_CALIBRATION_CANDIDATES
from clifft_bench.manifest import load_suite
from clifft_bench.release_audit import audit_release_execution
from clifft_bench.schema import repository_root

VARIANTS = (
    "clifft-previous",
    "clifft-current",
    "clifft-current-calibrated",
    "symft-calibrated",
    "symft-single",
)
COMPARISONS = {
    "current-vs-previous": ("clifft-previous", "clifft-current"),
    "alternatives-vs-current": (
        "clifft-current-calibrated",
        "symft-calibrated",
    ),
    "scalar-alternatives-vs-current": ("clifft-current", "symft-single"),
}


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _comparison_row(
    comparison_id: str,
    baseline: str,
    candidate: str,
    workload: str,
) -> dict[str, str]:
    calibrated = comparison_id == "alternatives-vs-current"
    implementation_ids = {
        "clifft-previous": "clifft-0.9.0",
        "clifft-current": "clifft-0.10.0rc1",
        "clifft-current-calibrated": "clifft-0.10.0rc1",
        "symft-calibrated": "symft-0.1.0-9ec5790",
        "symft-single": "symft-0.1.0-9ec5790",
    }
    versions = {
        "clifft-previous": "0.9.0",
        "clifft-current": "0.10.0rc1",
        "clifft-current-calibrated": "0.10.0rc1",
        "symft-calibrated": "0.1.0",
        "symft-single": "0.1.0",
    }
    row = {
        "placement": "1",
        "replica": "1",
        "comparison_id": comparison_id,
        "workload_id": workload,
        "baseline_variant_id": baseline,
        "baseline_result_id": "result-1",
        "baseline_case_id": f"{workload}--{baseline}",
        "baseline_implementation_id": implementation_ids[baseline],
        "baseline_simulator_version": versions[baseline],
        "baseline_batch_enabled": "true" if calibrated else "false",
        "baseline_batch_size_effective": "32" if calibrated else "1",
        "baseline_shots_per_call": "2048" if calibrated else "1",
        "candidate_variant_id": candidate,
        "candidate_result_id": "result-1",
        "candidate_case_id": f"{workload}--{candidate}",
        "candidate_implementation_id": implementation_ids[candidate],
        "candidate_simulator_version": versions[candidate],
        "candidate_batch_enabled": "true" if calibrated else "false",
        "candidate_batch_size_effective": "32" if calibrated else "1",
        "candidate_shots_per_call": "2048" if calibrated else "1",
    }
    return row


def _write_execution(tmp_path: Path) -> Path:
    execution_dir = tmp_path / "release"
    raw_dir = execution_dir / "raw"
    raw_dir.mkdir(parents=True)
    (execution_dir / "index.json").write_text(
        json.dumps(
            {
                "campaign_id": "release-v1",
                "run_manifest": "campaigns/release-v1/run.v1.json",
            }
        )
    )
    suite = load_suite(repository_root() / "campaigns/release-v1/run.v1.json")
    workloads = sorted(
        {
            case.workload.id
            for case in suite.cases
            if case.definition["variant_id"] == "clifft-current"
        }
    )

    case_rows = [
        {
            "result_id": "result-1",
            "placement": "1",
            "replica": "1",
            "case_id": f"{workload}--{variant}",
            "variant_id": variant,
            "workload_id": workload,
            "status": "success",
        }
        for variant in VARIANTS
        for workload in workloads
    ]
    _write_csv(execution_dir / "cases.csv", case_rows)

    comparison_rows = [
        _comparison_row(comparison_id, baseline, candidate, workload)
        for comparison_id, (baseline, candidate) in COMPARISONS.items()
        for workload in workloads
    ]
    _write_csv(execution_dir / "comparisons.csv", comparison_rows)

    raw_cases = []
    for variant in ("clifft-current-calibrated", "symft-calibrated"):
        for workload in workloads:
            raw_cases.append(
                {
                    "case_id": f"{workload}--{variant}",
                    "variant_id": variant,
                    "execution": {
                        "batch_enabled": True,
                        "batch_size": 32,
                        "batch_size_effective": 32,
                    },
                    "setup": {
                        "runtime_metadata": {
                            "batch_calibration": {
                                "candidates": list(BATCH_CALIBRATION_CANDIDATES),
                                "selected_batch_size": 32,
                            }
                        }
                    },
                }
            )
    (raw_dir / "release-v1-p01-r01-raw.json").write_text(
        json.dumps({"run": {"id": "result-1"}, "cases": raw_cases})
    )
    return execution_dir


def test_release_audit_confirms_scalar_and_calibrated_comparisons(
    tmp_path: Path,
) -> None:
    execution_dir = _write_execution(tmp_path)

    assert audit_release_execution(execution_dir) == {
        "cases": 40,
        "comparisons": 24,
        "calibrated_cases": 16,
    }

    rows = _read_comparisons(execution_dir)
    rows[0]["candidate_variant_id"] = "symft-calibrated"
    _write_csv(execution_dir / "comparisons.csv", rows)
    with pytest.raises(ValueError, match="current-vs-previous"):
        audit_release_execution(execution_dir)


def _read_comparisons(execution_dir: Path) -> list[dict[str, str]]:
    with (execution_dir / "comparisons.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))
