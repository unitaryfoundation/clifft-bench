"""Finalize raw campaign results into reviewable, plot-ready tables."""

from __future__ import annotations

import csv
import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from clifft_bench.manifest import Campaign
from clifft_bench.schema import (
    SchemaValidationError,
    repository_root,
    validate_document,
    validate_path,
)

SAMPLE_FIELDS = [
    "execution_id",
    "campaign_id",
    "hardware_epoch",
    "campaign_run_id",
    "result_id",
    "placement",
    "replica",
    "case_id",
    "pair_id",
    "workload_id",
    "workload_family",
    "implementation_id",
    "simulator_name",
    "simulator_version",
    "adapter",
    "mode",
    "batch_enabled",
    "batch_size_effective",
    "shots_per_call",
    "cpu_model",
    "instance_type",
    "image_id",
    "boot_id",
    "repetition",
    "sequence_index",
    "started_at",
    "duration_seconds",
    "api_calls",
    "attempted_shots",
    "accepted_shots",
    "discarded_shots",
    "logical_errors",
    "throughput_attempted_shots_per_second",
]

CASE_FIELDS = [
    "execution_id",
    "campaign_id",
    "hardware_epoch",
    "campaign_run_id",
    "result_id",
    "placement",
    "replica",
    "case_id",
    "pair_id",
    "workload_id",
    "workload_family",
    "implementation_id",
    "simulator_name",
    "simulator_version",
    "adapter",
    "mode",
    "batch_enabled",
    "batch_size_effective",
    "shots_per_call",
    "cpu_model",
    "instance_type",
    "image_id",
    "boot_id",
    "status",
    "error_phase",
    "error_type",
    "error_message",
    "setup_seconds",
    "sample_count",
    "median_attempted_shots_per_second",
    "mad_attempted_shots_per_second",
    "min_attempted_shots_per_second",
    "max_attempted_shots_per_second",
    "total_attempted_shots",
    "total_duration_seconds",
]

COMPARISON_FIELDS = [
    "execution_id",
    "campaign_id",
    "hardware_epoch",
    "placement",
    "replica",
    "comparison_id",
    "workload_id",
    "baseline_run_id",
    "baseline_result_id",
    "baseline_case_id",
    "baseline_implementation_id",
    "candidate_run_id",
    "candidate_result_id",
    "candidate_case_id",
    "candidate_implementation_id",
    "statistic",
    "baseline_rate",
    "candidate_rate",
    "ratio_candidate_over_baseline",
    "symmetric_delta_percent",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _placement_and_replica(result: dict[str, Any]) -> tuple[int, int]:
    attempt = result["run"]["workflow"]["run_attempt"]
    if not isinstance(attempt, str):
        raise SchemaValidationError("raw result is missing a manual placement/replica attempt")
    parts = attempt.split(".", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() and int(part) >= 1 for part in parts):
        raise SchemaValidationError(
            f"raw result run_attempt {attempt!r} must be PLACEMENT.REPLICA"
        )
    return int(parts[0]), int(parts[1])


def _common_row(
    campaign: Campaign,
    execution_id: str,
    result: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    placement, replica = _placement_and_replica(result)
    cloud = result["runner"]["cloud"]
    execution = case["execution"]
    return {
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "campaign_run_id": result["run"]["campaign_run_id"],
        "result_id": result["run"]["id"],
        "placement": placement,
        "replica": replica,
        "case_id": case["case_id"],
        "pair_id": case["pair_id"] or "",
        "workload_id": case["workload"]["id"],
        "workload_family": case["workload"]["family"],
        "implementation_id": case["simulator"]["implementation_id"],
        "simulator_name": case["simulator"]["name"],
        "simulator_version": case["simulator"]["version"],
        "adapter": case["simulator"]["adapter"],
        "mode": execution["mode"],
        "batch_enabled": str(execution["batch_enabled"]).lower(),
        "batch_size_effective": execution.get("batch_size_effective", ""),
        "shots_per_call": execution["shots_per_call"],
        "cpu_model": result["runner"]["cpu_model"],
        "instance_type": cloud["instance_type"],
        "image_id": cloud["image_id"],
        "boot_id": cloud["boot_id"],
    }


def _rows(
    campaign: Campaign,
    execution_id: str,
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sample_rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    for result in results:
        for case in result["cases"]:
            common = _common_row(campaign, execution_id, result, case)
            error = case.get("error") or {}
            summary = case.get("summary") or {}
            case_rows.append(
                {
                    **common,
                    "status": case["status"],
                    "error_phase": error.get("phase", ""),
                    "error_type": error.get("type", ""),
                    "error_message": error.get("message", ""),
                    "setup_seconds": (case.get("setup") or {}).get("duration_seconds", ""),
                    "sample_count": summary.get("sample_count", 0),
                    "median_attempted_shots_per_second": summary.get(
                        "median_attempted_shots_per_second", ""
                    ),
                    "mad_attempted_shots_per_second": summary.get(
                        "mad_attempted_shots_per_second", ""
                    ),
                    "min_attempted_shots_per_second": summary.get(
                        "min_attempted_shots_per_second", ""
                    ),
                    "max_attempted_shots_per_second": summary.get(
                        "max_attempted_shots_per_second", ""
                    ),
                    "total_attempted_shots": summary.get("total_attempted_shots", 0),
                    "total_duration_seconds": summary.get("total_duration_seconds", ""),
                }
            )
            for sample in case["samples"]:
                sample_rows.append(
                    {
                        **common,
                        **{field: sample[field] for field in SAMPLE_FIELDS if field in sample},
                    }
                )
    return sample_rows, case_rows


def _comparison_rows(
    campaign: Campaign,
    execution_id: str,
    case_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in case_rows:
        if row["status"] != "success":
            continue
        key = (
            str(row["campaign_run_id"]),
            int(row["placement"]),
            int(row["replica"]),
            str(row["workload_id"]),
        )
        indexed[key].append(row)

    comparisons: list[dict[str, Any]] = []
    workloads = sorted({str(row["workload_id"]) for row in case_rows})
    collection = campaign.document["collection"]
    for comparison in campaign.document["comparisons"]:
        baseline_run = str(comparison["baseline_run"])
        for candidate_run in comparison["candidate_runs"]:
            for placement in range(1, int(collection["placements"]) + 1):
                for replica in range(1, int(collection["replicas_per_placement"]) + 1):
                    for workload_id in workloads:
                        baseline_rows = indexed.get(
                            (baseline_run, placement, replica, workload_id), []
                        )
                        candidate_rows = indexed.get(
                            (str(candidate_run), placement, replica, workload_id), []
                        )
                        if len(baseline_rows) != 1:
                            continue
                        baseline = baseline_rows[0]
                        baseline_rate = float(
                            baseline["median_attempted_shots_per_second"]
                        )
                        for candidate in candidate_rows:
                            candidate_rate = float(
                                candidate["median_attempted_shots_per_second"]
                            )
                            comparisons.append(
                                {
                                    "execution_id": execution_id,
                                    "campaign_id": campaign.id,
                                    "hardware_epoch": campaign.document[
                                        "hardware_epoch"
                                    ],
                                    "placement": placement,
                                    "replica": replica,
                                    "comparison_id": comparison["id"],
                                    "workload_id": workload_id,
                                    "baseline_run_id": baseline_run,
                                    "baseline_result_id": baseline["result_id"],
                                    "baseline_case_id": baseline["case_id"],
                                    "baseline_implementation_id": baseline[
                                        "implementation_id"
                                    ],
                                    "candidate_run_id": candidate_run,
                                    "candidate_result_id": candidate["result_id"],
                                    "candidate_case_id": candidate["case_id"],
                                    "candidate_implementation_id": candidate[
                                        "implementation_id"
                                    ],
                                    "statistic": "result-sample-median",
                                    "baseline_rate": baseline_rate,
                                    "candidate_rate": candidate_rate,
                                    "ratio_candidate_over_baseline": (
                                        candidate_rate / baseline_rate
                                    ),
                                    "symmetric_delta_percent": (
                                        200
                                        * abs(candidate_rate - baseline_rate)
                                        / (candidate_rate + baseline_rate)
                                    ),
                                }
                            )
    return comparisons


def _distribution(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _summary(
    campaign: Campaign,
    execution_id: str,
    case_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    grouped_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in case_rows:
        grouped_cases[str(row["case_id"])].append(row)
    for case_id, rows in grouped_cases.items():
        rates = [
            float(row["median_attempted_shots_per_second"])
            for row in rows
            if row["status"] == "success"
        ]
        first = rows[0]
        cases.append(
            {
                "case_id": case_id,
                "workload_id": first["workload_id"],
                "implementation_id": first["implementation_id"],
                "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
                "result_median_throughput": _distribution(rates),
            }
        )

    comparisons: list[dict[str, Any]] = []
    grouped_comparisons: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in comparison_rows:
        key = (
            str(row["comparison_id"]),
            str(row["baseline_case_id"]),
            str(row["candidate_case_id"]),
        )
        grouped_comparisons[key].append(row)
    for (comparison_id, baseline, candidate), rows in grouped_comparisons.items():
        comparisons.append(
            {
                "comparison_id": comparison_id,
                "baseline_case_id": baseline,
                "candidate_case_id": candidate,
                "ratio_candidate_over_baseline": _distribution(
                    [float(row["ratio_candidate_over_baseline"]) for row in rows]
                ),
                "symmetric_delta_percent": _distribution(
                    [float(row["symmetric_delta_percent"]) for row in rows]
                ),
            }
        )
    return {
        "report_format": "clifft-bench/campaign-summary/v1",
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "result_count": len({row["result_id"] for row in case_rows}),
        "cases": cases,
        "comparisons": comparisons,
    }


def _validate_execution(
    campaign: Campaign,
    raw_paths: list[Path],
) -> list[dict[str, Any]]:
    if len(raw_paths) != len({path.resolve() for path in raw_paths}):
        raise ValueError("duplicate raw result path")
    results = [validate_path(path.resolve()) for path in sorted(raw_paths)]
    collection = campaign.document["collection"]
    expected_count = (
        int(collection["placements"])
        * int(collection["replicas_per_placement"])
        * len(campaign.suites)
    )
    if len(results) != expected_count:
        raise ValueError(f"expected {expected_count} raw results, received {len(results)}")

    source_identities = {
        json.dumps(result["runner"]["suite_source"], sort_keys=True)
        for result in results
    }
    if len(source_identities) != 1:
        raise ValueError("raw results do not share one source identity")
    source = results[0]["runner"]["suite_source"]
    if source["dirty"] is not False or source["commit"] is None:
        raise ValueError("raw results must come from one clean committed source")

    launches = set()
    placements: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if result["run"]["profile_id"] != campaign.id:
            raise ValueError(
                f"raw result profile {result['run']['profile_id']!r} does not match "
                f"campaign {campaign.id!r}"
            )
        cloud = result["runner"].get("cloud")
        if cloud is None:
            raise ValueError("campaign results require complete cloud identity")
        launches.add(
            tuple(
                cloud[key]
                for key in (
                    "provider",
                    "instance_type",
                    "image_id",
                    "region",
                    "availability_zone",
                    "lifecycle",
                )
            )
        )
        placement, _ = _placement_and_replica(result)
        placements[placement].append(result)
    if len(launches) != 1:
        raise ValueError("raw results do not share one fixed launch configuration")
    expected_placements = set(range(1, int(collection["placements"]) + 1))
    if set(placements) != expected_placements:
        raise ValueError(f"raw result placements must be {sorted(expected_placements)}")
    expected_replicas = int(collection["replicas_per_placement"])
    expected_results_per_placement = expected_replicas * len(campaign.suites)
    expected_run_ids = {str(item["id"]) for item in campaign.document["runs"]}
    boot_ids = set()
    for placement, placement_results in placements.items():
        if len(placement_results) != expected_results_per_placement:
            raise ValueError(
                f"placement {placement} must contain "
                f"{expected_results_per_placement} raw results"
            )
        replicas = {_placement_and_replica(result)[1] for result in placement_results}
        if replicas != set(range(1, expected_replicas + 1)):
            raise ValueError(
                f"placement {placement} replicas must be 1 through {expected_replicas}"
            )
        observed_runs = {
            (_placement_and_replica(result)[1], str(result["run"]["campaign_run_id"]))
            for result in placement_results
        }
        expected_runs = {
            (replica, run_id)
            for replica in range(1, expected_replicas + 1)
            for run_id in expected_run_ids
        }
        if observed_runs != expected_runs:
            raise ValueError(
                f"placement {placement} does not contain every campaign run per replica"
            )
        placement_boots = {result["runner"]["cloud"]["boot_id"] for result in placement_results}
        if len(placement_boots) != 1:
            raise ValueError(f"placement {placement} spans multiple boot IDs")
        boot_ids.update(placement_boots)
    if len(boot_ids) != len(placements):
        raise ValueError("each placement must use a distinct boot ID")
    return results


def finalize_execution(
    campaign: Campaign,
    *,
    execution_id: str,
    raw_paths: list[Path],
    output_dir: Path,
) -> dict[str, Any]:
    resolved_paths = sorted(path.resolve() for path in raw_paths)
    results = _validate_execution(campaign, resolved_paths)
    sample_rows, case_rows = _rows(campaign, execution_id, results)
    comparison_rows = _comparison_rows(campaign, execution_id, case_rows)
    _write_csv(output_dir / "samples.csv", SAMPLE_FIELDS, sample_rows)
    _write_csv(output_dir / "cases.csv", CASE_FIELDS, case_rows)
    _write_csv(output_dir / "comparisons.csv", COMPARISON_FIELDS, comparison_rows)
    _write_json(
        output_dir / "summary.json",
        _summary(campaign, execution_id, case_rows, comparison_rows),
    )

    placements: dict[int, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path, result in zip(resolved_paths, results, strict=True):
        placement, _ = _placement_and_replica(result)
        placements[placement].append((path, result))
    first = results[0]
    first_cloud = first["runner"]["cloud"]
    root = repository_root()
    index = {
        "schema_version": "clifft-bench/execution/v1",
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "campaign_manifest": str(campaign.path.relative_to(root)),
        "run_manifests": [str(suite.run_path.relative_to(root)) for suite in campaign.suites],
        "created_at": max(str(result["run"]["finished_at"]) for result in results),
        "source": first["runner"]["suite_source"],
        "cloud": {
            key: first_cloud[key]
            for key in (
                "provider",
                "instance_type",
                "image_id",
                "region",
                "availability_zone",
                "lifecycle",
            )
        },
        "placements": [
            {
                "number": number,
                "boot_id": items[0][1]["runner"]["cloud"]["boot_id"],
                "instance_id": items[0][1]["runner"]["cloud"]["instance_id"],
                "raw_results": [
                    f"raw/{path.name}"
                    for path, _ in sorted(items, key=lambda item: item[0])
                ],
            }
            for number, items in sorted(placements.items())
        ],
        "derived": {
            "samples": "samples.csv",
            "cases": "cases.csv",
            "comparisons": "comparisons.csv",
            "summary": "summary.json",
        },
    }
    validate_document(index)
    _write_json(output_dir / "index.json", index)
    return index
