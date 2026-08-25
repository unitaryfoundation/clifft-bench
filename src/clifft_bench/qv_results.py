"""Validation and compact derived tables for QV multicore executions."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from clifft_bench.qv import QVCampaign, scheduled_cases
from clifft_bench.schema import SchemaValidationError, validate_path, write_json

CASE_FIELDS = [
    "execution_id",
    "campaign_id",
    "hardware_epoch",
    "placement",
    "replica",
    "result_id",
    "case_id",
    "sequence_index",
    "phase",
    "run_id",
    "simulator_name",
    "simulator_version",
    "distribution_version",
    "simulator_commit",
    "adapter",
    "qubits",
    "depth",
    "seed",
    "threads_requested",
    "threads_effective",
    "cpu_set",
    "status",
    "execution_seconds",
    "compile_seconds",
    "sample_seconds",
    "peak_rss_bytes",
    "error_type",
    "error_message",
    "cpu_model",
    "instance_type",
    "image_id",
    "boot_id",
]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CASE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _expected_case_ids(campaign: QVCampaign) -> set[str]:
    return {
        f"{spec['run']['id']}--q{spec['qubits']}-seed{spec['seed']}-t{spec['threads']}"
        for spec in scheduled_cases(campaign)
    }


def _validate_execution(
    campaign: QVCampaign, execution_id: str, raw_paths: list[Path]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    expected_results = int(campaign.document["collection"]["placements"]) * int(
        campaign.document["collection"]["replicas_per_placement"]
    )
    if len(raw_paths) != expected_results:
        raise SchemaValidationError(
            f"expected {expected_results} raw QV results, received {len(raw_paths)}"
        )
    results = [validate_path(path.resolve()) for path in raw_paths]
    expected_cases = _expected_case_ids(campaign)
    attempts: set[tuple[int, int]] = set()
    source_commits: set[str] = set()
    images: set[str] = set()
    instance_ids: set[str] = set()
    regions: set[str] = set()
    zones: set[str] = set()
    cpu_models: set[str] = set()
    generator_dependencies: set[str] = set()
    boots_by_placement: dict[int, set[str]] = defaultdict(set)
    curations: set[str] = set()
    reference = campaign.document["reference_host"]
    for result in results:
        if result["campaign"]["id"] != campaign.id:
            raise SchemaValidationError("raw result belongs to another QV campaign")
        if result["campaign"]["manifest_sha256"] != campaign.manifest_sha256:
            raise SchemaValidationError("raw result campaign manifest digest mismatch")
        if result["run"]["execution_id"] != execution_id:
            raise SchemaValidationError("raw result execution ID mismatch")
        if result["run"]["profile_id"] != campaign.id:
            raise SchemaValidationError("raw result profile ID mismatch")
        attempt = (int(result["run"]["placement"]), int(result["run"]["replica"]))
        if attempt in attempts:
            raise SchemaValidationError(f"duplicate placement/replica {attempt}")
        attempts.add(attempt)
        case_id_list = [str(case["case_id"]) for case in result["cases"]]
        case_ids = set(case_id_list)
        if len(case_id_list) != len(case_ids) or case_ids != expected_cases:
            raise SchemaValidationError("raw result case matrix does not match the campaign")
        for case in result["cases"]:
            if case["status"] == "pending":
                raise SchemaValidationError(f"case {case['case_id']!r} is still pending")
            if case["status"] == "success" and len(case["threads"]["cpu_set"]) != int(
                case["threads"]["requested"]
            ):
                raise SchemaValidationError(
                    f"case {case['case_id']!r} did not receive one CPU per requested core"
                )
        runner = result["runner"]
        cloud = runner["cloud"]
        if cloud is None:
            raise SchemaValidationError("official QV results require EC2 cloud metadata")
        if cloud["instance_type"] != reference["instance_type"]:
            raise SchemaValidationError("raw result instance type does not match campaign")
        if cloud["lifecycle"] != "on-demand":
            raise SchemaValidationError("raw result must come from On-Demand EC2")
        if runner["physical_cores"] != reference["physical_cores"]:
            raise SchemaValidationError("raw result physical core count does not match campaign")
        if runner["logical_cpus"] != reference["logical_cpus"]:
            raise SchemaValidationError("raw result logical CPU count does not match campaign")
        if runner["suite_source"]["dirty"] is not False:
            raise SchemaValidationError("QV collection requires a clean source checkout")
        source_commits.add(str(runner["suite_source"]["commit"]))
        instance_ids.add(str(cloud["instance_id"]))
        images.add(str(cloud["image_id"]))
        regions.add(str(cloud["region"]))
        zones.add(str(cloud["availability_zone"]))
        cpu_models.add(str(runner["cpu_model"]))
        generator_dependencies.add(
            json.dumps(result["campaign"]["circuit_generator_dependencies"], sort_keys=True)
        )
        curations.add(json.dumps(result.get("curation"), sort_keys=True))
        boots_by_placement[attempt[0]].add(str(cloud["boot_id"]))
    if len(curations) != 1:
        raise SchemaValidationError("QV results use different curation provenance")
    curation = json.loads(next(iter(curations)))
    placements = int(campaign.document["collection"]["placements"])
    replicas = int(campaign.document["collection"]["replicas_per_placement"])
    expected_attempts = {
        (placement, replica)
        for placement in range(1, placements + 1)
        for replica in range(1, replicas + 1)
    }
    if attempts != expected_attempts:
        raise SchemaValidationError("QV placement/replica coverage does not match the campaign")
    stable_identities = (
        source_commits,
        instance_ids,
        images,
        regions,
        zones,
        cpu_models,
        generator_dependencies,
    )
    if any(len(values) != 1 for values in stable_identities):
        raise SchemaValidationError(
            "QV placements must share source, instance, generator, CPU model, AMI, region, and AZ"
        )
    if any(len(boots) != 1 for boots in boots_by_placement.values()):
        raise SchemaValidationError("replicas in one placement must share one boot ID")
    placement_boots = {next(iter(boots)) for boots in boots_by_placement.values()}
    if len(placement_boots) != len(boots_by_placement):
        raise SchemaValidationError("each QV placement requires a distinct stop/start boot")
    return results, curation


def _case_rows(
    campaign: QVCampaign, execution_id: str, results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        cloud = result["runner"]["cloud"]
        for case in result["cases"]:
            timing = case["timing"] or {}
            error = case["error"] or {}
            simulator = case["simulator"]
            circuit = case["circuit"]
            threads = case["threads"]
            rows.append(
                {
                    "execution_id": execution_id,
                    "campaign_id": campaign.id,
                    "hardware_epoch": campaign.document["hardware_epoch"],
                    "placement": result["run"]["placement"],
                    "replica": result["run"]["replica"],
                    "result_id": result["run"]["id"],
                    "case_id": case["case_id"],
                    "sequence_index": case["sequence_index"],
                    "phase": case["phase"],
                    "run_id": simulator["run_id"],
                    "simulator_name": simulator["name"],
                    "simulator_version": simulator["version"],
                    "distribution_version": simulator["dependencies"].get(
                        simulator["distribution"], ""
                    ),
                    "simulator_commit": simulator["commit_sha"] or "",
                    "adapter": simulator["adapter"],
                    "qubits": circuit["qubits"],
                    "depth": circuit["depth"],
                    "seed": circuit["seed"],
                    "threads_requested": threads["requested"],
                    "threads_effective": threads["effective"] or "",
                    "cpu_set": ",".join(str(cpu) for cpu in threads["cpu_set"]),
                    "status": case["status"],
                    "execution_seconds": timing.get("execution_seconds", ""),
                    "compile_seconds": timing.get("compile_seconds", ""),
                    "sample_seconds": timing.get("sample_seconds", ""),
                    "peak_rss_bytes": case["peak_rss_bytes"] or "",
                    "error_type": error.get("type", ""),
                    "error_message": error.get("message", ""),
                    "cpu_model": result["runner"]["cpu_model"],
                    "instance_type": cloud["instance_type"],
                    "image_id": cloud["image_id"],
                    "boot_id": cloud["boot_id"],
                }
            )
    return rows


def finalize_qv_execution(
    campaign: QVCampaign,
    *,
    execution_id: str,
    raw_paths: list[Path],
    output_dir: Path,
) -> dict[str, Any]:
    results, curation = _validate_execution(campaign, execution_id, raw_paths)
    rows = _case_rows(campaign, execution_id, results)
    classification = campaign.document["classification"]
    if curation is not None and classification != "smoke":
        classification = "exploratory"
    index = {
        "index_format": "clifft-bench/qv-execution/v1",
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "classification": classification,
        "result_count": len(results),
        "case_rows": len(rows),
        "files": {
            "raw": "raw/",
            "cases": "cases.csv",
        },
    }
    if curation is not None:
        index["curation"] = curation
    _write_csv(output_dir / "cases.csv", rows)
    write_json(output_dir / "index.json", index)
    return index
