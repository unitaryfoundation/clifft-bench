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


def _suite():
    return SimpleNamespace(
        run_path=repository_root() / "campaigns/release-v1/run.v1.json",
        cases=(
            SimpleNamespace(id="workload--baseline"),
            SimpleNamespace(id="workload--candidate"),
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
    with (output_dir / "comparisons.csv").open(newline="") as stream:
        comparisons = list(csv.DictReader(stream))
    assert len(comparisons) == 1
    assert float(comparisons[0]["ratio_candidate_over_baseline"]) == 1.25
    assert comparisons[0]["baseline_variant_id"] == "baseline"
    assert comparisons[0]["candidate_variant_id"] == "candidate"
    assert comparisons[0]["candidate_batch_size_effective"] == "32"

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
