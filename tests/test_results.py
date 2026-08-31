from __future__ import annotations

import copy
import csv
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.manifest import Campaign
from clifft_bench.results import _comparison_rows, finalize_execution
from clifft_bench.schema import repository_root, validate_path


def _result(tmp_path: Path, *, run_id: str, case_id: str, rate: float) -> Path:
    document = validate_path(repository_root() / "examples/result.v1.json")
    document = copy.deepcopy(document)
    document["run"]["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, run_id))
    document["run"]["profile_id"] = "test-campaign"
    document["run"]["campaign_run_id"] = run_id
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
    case = document["cases"][0]
    case["case_id"] = case_id
    case["simulator"]["implementation_id"] = run_id
    case["execution"]["memory_limit_bytes"] = 1 << 30
    case["setup"]["runtime_metadata"]["address_space_limit_bytes"] = 1 << 30
    if run_id == "candidate":
        case["execution"]["batch_enabled"] = True
        case["execution"]["batch_size"] = 32
        case["execution"]["batch_size_effective"] = 32
        case["execution"]["shots_per_call"] = 64
    case["samples"][0]["throughput_attempted_shots_per_second"] = rate
    case["summary"]["median_attempted_shots_per_second"] = rate
    case["summary"]["min_attempted_shots_per_second"] = rate
    case["summary"]["max_attempted_shots_per_second"] = rate
    path = tmp_path / f"{run_id}-raw.json"
    path.write_text(json.dumps(document))
    return path


def test_finalize_writes_index_and_plot_ready_comparison_tables(tmp_path: Path) -> None:
    root = repository_root()
    run_path = root / "manifests/run-smoke.v1.json"
    campaign = Campaign(
        path=root / "campaigns/current-tools-v1/campaign.v1.json",
        document={
            "id": "test-campaign",
            "hardware_epoch": "test-epoch",
            "collection": {
                "placements": 1,
                "replicas_per_placement": 1,
                "run_timeout_minutes": 1,
                "memory_limit_gib": 1,
            },
            "comparisons": [
                {
                    "id": "candidate-vs-baseline",
                    "baseline_run": "baseline",
                    "candidate_runs": ["candidate"],
                }
            ],
            "runs": [
                {"id": "baseline", "run_manifest": "baseline.json"},
                {"id": "candidate", "run_manifest": "candidate.json"},
            ],
        },
        suites=(SimpleNamespace(run_path=run_path), SimpleNamespace(run_path=run_path)),
    )
    raw_paths = [
        _result(tmp_path, run_id="baseline", case_id="workload--baseline", rate=100.0),
        _result(tmp_path, run_id="candidate", case_id="workload--candidate", rate=125.0),
    ]
    output_dir = tmp_path / "finalized"

    index = finalize_execution(
        campaign,
        execution_id="test-execution",
        raw_paths=raw_paths,
        output_dir=output_dir,
    )

    validate_path(output_dir / "index.json")
    assert index["campaign_id"] == "test-campaign"
    assert index["placements"][0]["raw_results"] == [
        "raw/baseline-raw.json",
        "raw/candidate-raw.json",
    ]
    with (output_dir / "comparisons.csv").open(newline="") as stream:
        comparisons = list(csv.DictReader(stream))
    assert len(comparisons) == 1
    assert float(comparisons[0]["ratio_candidate_over_baseline"]) == 1.25
    assert comparisons[0]["baseline_batch_enabled"] == "false"
    assert comparisons[0]["candidate_batch_enabled"] == "true"
    assert comparisons[0]["candidate_batch_size_effective"] == "32"
    assert comparisons[0]["candidate_shots_per_call"] == "64"
    assert not (output_dir / "samples.csv").exists()
    assert not (output_dir / "summary.json").exists()

    changed = json.loads(raw_paths[1].read_text())
    changed["runner"]["cloud"]["instance_id"] = "i-different"
    raw_paths[1].write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="fixed launch configuration"):
        finalize_execution(
            campaign,
            execution_id="test-execution",
            raw_paths=raw_paths,
            output_dir=tmp_path / "rejected",
        )

    changed["runner"]["cloud"]["instance_id"] = "i-example"
    changed["cases"][0]["execution"]["memory_limit_bytes"] = None
    raw_paths[1].write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="memory limit does not match"):
        finalize_execution(
            campaign,
            execution_id="test-execution",
            raw_paths=raw_paths,
            output_dir=tmp_path / "rejected-memory-limit",
        )

    changed["cases"][0]["execution"]["memory_limit_bytes"] = 1 << 30
    changed["cases"][0]["setup"]["runtime_metadata"][
        "address_space_limit_bytes"
    ] = None
    raw_paths[1].write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="worker memory limit does not match"):
        finalize_execution(
            campaign,
            execution_id="test-execution",
            raw_paths=raw_paths,
            output_dir=tmp_path / "rejected-worker-memory-limit",
        )


def test_comparison_rejects_multiple_successful_cases_for_one_run_and_workload() -> None:
    campaign = Campaign(
        path=repository_root() / "campaigns/current-tools-v1/campaign.v1.json",
        document={
            "id": "test-campaign",
            "hardware_epoch": "test-epoch",
            "collection": {"placements": 1, "replicas_per_placement": 1},
            "comparisons": [
                {
                    "id": "candidate-vs-baseline",
                    "baseline_run": "baseline",
                    "candidate_runs": ["candidate"],
                }
            ],
        },
        suites=(),
    )
    rows = [
        {
            "status": "success",
            "campaign_run_id": run_id,
            "placement": 1,
            "replica": 1,
            "workload_id": "workload",
        }
        for run_id in ["baseline", "baseline", "candidate"]
    ]

    with pytest.raises(ValueError, match="multiple successful baseline cases"):
        _comparison_rows(campaign, "execution", rows)
