from __future__ import annotations

import copy
import csv
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from clifft_bench.manifest import Campaign
from clifft_bench.results import finalize_execution
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
    with (output_dir / "samples.csv").open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    assert {row["campaign_run_id"] for row in samples} == {"baseline", "candidate"}
    with (output_dir / "comparisons.csv").open(newline="") as stream:
        comparisons = list(csv.DictReader(stream))
    assert len(comparisons) == 1
    assert float(comparisons[0]["ratio_candidate_over_baseline"]) == 1.25
