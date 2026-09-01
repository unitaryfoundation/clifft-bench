"""Finalize raw campaign results into reviewable, plot-ready tables."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from clifft_bench.manifest import Suite
from clifft_bench.schema import (
    SchemaValidationError,
    repository_root,
    validate_document,
    validate_path,
    write_json,
)

CASE_FIELDS = [
    "execution_id",
    "campaign_id",
    "hardware_epoch",
    "result_id",
    "placement",
    "replica",
    "case_id",
    "variant_id",
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
    "baseline_variant_id",
    "baseline_result_id",
    "baseline_case_id",
    "baseline_implementation_id",
    "baseline_mode",
    "baseline_batch_enabled",
    "baseline_batch_size_effective",
    "baseline_shots_per_call",
    "candidate_variant_id",
    "candidate_result_id",
    "candidate_case_id",
    "candidate_implementation_id",
    "candidate_mode",
    "candidate_batch_enabled",
    "candidate_batch_size_effective",
    "candidate_shots_per_call",
    "statistic",
    "baseline_rate",
    "candidate_rate",
    "ratio_candidate_over_baseline",
    "symmetric_delta_percent",
]


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


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
    suite: Suite,
    execution_id: str,
    result: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    placement, replica = _placement_and_replica(result)
    cloud = result["runner"]["cloud"]
    execution = case["execution"]
    return {
        "execution_id": execution_id,
        "campaign_id": suite.run["profile_id"],
        "hardware_epoch": suite.run["hardware_epoch"],
        "result_id": result["run"]["id"],
        "placement": placement,
        "replica": replica,
        "case_id": case["case_id"],
        "variant_id": case["variant_id"],
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


def _case_rows(
    suite: Suite,
    execution_id: str,
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    case_rows: list[dict[str, Any]] = []
    for result in results:
        for case in result["cases"]:
            common = _common_row(suite, execution_id, result, case)
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
    return case_rows


def _comparison_rows(
    suite: Suite,
    execution_id: str,
    case_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in case_rows:
        if row["status"] != "success":
            continue
        key = (
            str(row["variant_id"]),
            int(row["placement"]),
            int(row["replica"]),
            str(row["workload_id"]),
        )
        indexed[key].append(row)

    comparisons: list[dict[str, Any]] = []
    workloads = sorted({str(row["workload_id"]) for row in case_rows})
    collection = suite.run["collection"]
    for comparison in suite.run["comparisons"]:
        baseline_variant = str(comparison["baseline_variant"])
        for candidate_variant in comparison["candidate_variants"]:
            for placement in range(1, int(collection["placements"]) + 1):
                for replica in range(1, int(collection["replicas_per_placement"]) + 1):
                    for workload_id in workloads:
                        baseline_rows = indexed.get(
                            (baseline_variant, placement, replica, workload_id), []
                        )
                        candidate_rows = indexed.get(
                            (str(candidate_variant), placement, replica, workload_id), []
                        )
                        if len(baseline_rows) > 1:
                            raise ValueError(
                                f"comparison {comparison['id']!r} found multiple successful "
                                f"baseline cases for {baseline_variant!r}/{workload_id!r}"
                            )
                        if len(candidate_rows) > 1:
                            raise ValueError(
                                f"comparison {comparison['id']!r} found multiple successful "
                                f"candidate cases for {candidate_variant!r}/{workload_id!r}"
                            )
                        if not baseline_rows or not candidate_rows:
                            continue
                        baseline = baseline_rows[0]
                        candidate = candidate_rows[0]
                        baseline_rate = float(
                            baseline["median_attempted_shots_per_second"]
                        )
                        candidate_rate = float(
                            candidate["median_attempted_shots_per_second"]
                        )
                        comparisons.append(
                            {
                                "execution_id": execution_id,
                                "campaign_id": suite.run["profile_id"],
                                "hardware_epoch": suite.run["hardware_epoch"],
                                "placement": placement,
                                "replica": replica,
                                "comparison_id": comparison["id"],
                                "workload_id": workload_id,
                                "baseline_variant_id": baseline_variant,
                                "baseline_result_id": baseline["result_id"],
                                "baseline_case_id": baseline["case_id"],
                                "baseline_implementation_id": baseline[
                                    "implementation_id"
                                ],
                                "baseline_mode": baseline["mode"],
                                "baseline_batch_enabled": baseline["batch_enabled"],
                                "baseline_batch_size_effective": baseline[
                                    "batch_size_effective"
                                ],
                                "baseline_shots_per_call": baseline["shots_per_call"],
                                "candidate_variant_id": candidate_variant,
                                "candidate_result_id": candidate["result_id"],
                                "candidate_case_id": candidate["case_id"],
                                "candidate_implementation_id": candidate[
                                    "implementation_id"
                                ],
                                "candidate_mode": candidate["mode"],
                                "candidate_batch_enabled": candidate["batch_enabled"],
                                "candidate_batch_size_effective": candidate[
                                    "batch_size_effective"
                                ],
                                "candidate_shots_per_call": candidate["shots_per_call"],
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


def _validate_execution(
    suite: Suite,
    raw_paths: list[Path],
) -> list[dict[str, Any]]:
    if len(raw_paths) != len({path.resolve() for path in raw_paths}):
        raise ValueError("duplicate raw result path")
    results = [validate_path(path.resolve()) for path in sorted(raw_paths)]
    collection = suite.run["collection"]
    expected_count = int(collection["placements"]) * int(
        collection["replicas_per_placement"]
    )
    if len(results) != expected_count:
        raise ValueError(f"expected {expected_count} raw results, received {len(results)}")

    source = results[0]["runner"]["suite_source"]
    if source["dirty"] is not False or source["commit"] is None:
        raise ValueError("raw results must come from one clean committed source")
    if any(result["runner"]["suite_source"] != source for result in results[1:]):
        raise ValueError("raw results do not share one source identity")

    placements: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expected_memory_limit_bytes = int(float(collection["memory_limit_gib"]) * (1 << 30))
    expected_instance_type = suite.run["reference_host"]["instance_type"]
    expected_case_ids = {case.id for case in suite.cases}
    for result in results:
        if result["run"]["profile_id"] != suite.run["profile_id"]:
            raise ValueError(
                f"raw result profile {result['run']['profile_id']!r} does not match "
                f"campaign {suite.run['profile_id']!r}"
            )
        cloud = result["runner"].get("cloud")
        if cloud is None:
            raise ValueError("campaign results require complete cloud identity")
        if cloud["instance_type"] != expected_instance_type:
            raise ValueError(
                f"expected instance type {expected_instance_type!r}, "
                f"received {cloud['instance_type']!r}"
            )
        observed_case_ids = {case["case_id"] for case in result["cases"]}
        if observed_case_ids != expected_case_ids:
            raise ValueError("raw result does not contain every declared campaign case")
        for case in result["cases"]:
            observed_memory_limit = case["execution"].get("memory_limit_bytes")
            if observed_memory_limit != expected_memory_limit_bytes:
                raise ValueError(
                    "raw result memory limit does not match the campaign: "
                    f"expected {expected_memory_limit_bytes}, received "
                    f"{observed_memory_limit!r}"
                )
            setup = case.get("setup")
            if setup is not None:
                applied_memory_limit = setup["runtime_metadata"].get(
                    "address_space_limit_bytes"
                )
                if applied_memory_limit != expected_memory_limit_bytes:
                    raise ValueError(
                        "worker memory limit does not match the campaign: "
                        f"expected {expected_memory_limit_bytes}, received "
                        f"{applied_memory_limit!r}"
                    )
        placement, _ = _placement_and_replica(result)
        placements[placement].append(result)

    if len({result["runner"]["cloud"]["instance_id"] for result in results}) != 1:
        raise ValueError("raw results must come from one reference instance")
    expected_placements = set(range(1, int(collection["placements"]) + 1))
    if set(placements) != expected_placements:
        raise ValueError(f"raw result placements must be {sorted(expected_placements)}")
    expected_replicas = int(collection["replicas_per_placement"])
    boot_ids = set()
    for placement, placement_results in placements.items():
        if len(placement_results) != expected_replicas:
            raise ValueError(
                f"placement {placement} must contain {expected_replicas} raw results"
            )
        replicas = {_placement_and_replica(result)[1] for result in placement_results}
        if replicas != set(range(1, expected_replicas + 1)):
            raise ValueError(
                f"placement {placement} replicas must be 1 through {expected_replicas}"
            )
        placement_boots = {result["runner"]["cloud"]["boot_id"] for result in placement_results}
        if len(placement_boots) != 1:
            raise ValueError(f"placement {placement} spans multiple boot IDs")
        boot_ids.update(placement_boots)
    if len(boot_ids) != len(placements):
        raise ValueError("each placement must use a distinct boot ID")
    return results


def finalize_execution(
    suite: Suite,
    *,
    execution_id: str,
    raw_paths: list[Path],
    output_dir: Path,
) -> dict[str, Any]:
    if suite.run["classification"] != "official":
        raise ValueError("only official run manifests can be finalized")
    resolved_paths = sorted(path.resolve() for path in raw_paths)
    results = _validate_execution(suite, resolved_paths)
    case_rows = _case_rows(suite, execution_id, results)
    comparison_rows = _comparison_rows(suite, execution_id, case_rows)
    _write_csv(output_dir / "cases.csv", CASE_FIELDS, case_rows)
    _write_csv(output_dir / "comparisons.csv", COMPARISON_FIELDS, comparison_rows)

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
        "campaign_id": suite.run["profile_id"],
        "hardware_epoch": suite.run["hardware_epoch"],
        "run_manifest": str(suite.run_path.relative_to(root)),
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
            "cases": "cases.csv",
            "comparisons": "comparisons.csv",
        },
    }
    validate_document(index)
    write_json(output_dir / "index.json", index)
    return index
