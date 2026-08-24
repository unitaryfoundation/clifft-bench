"""Validation and compact derived tables for QV multicore executions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from clifft_bench.qv import QVCampaign, scheduled_cases
from clifft_bench.schema import SchemaValidationError, validate_path

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


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content)
    os.replace(temporary, path)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CASE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _expected_case_ids(campaign: QVCampaign) -> set[str]:
    return {
        f"{spec['run']['id']}--q{spec['qubits']}-seed{spec['seed']}-t{spec['threads']}"
        for spec in scheduled_cases(campaign)
    }


def _validate_circuit_artifacts(
    results: list[dict[str, Any]], circuit_dir: Path
) -> list[dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for result in results:
        for case in result["cases"]:
            circuit = case["circuit"]
            path = circuit_dir / circuit["path"]
            if not path.is_file():
                raise SchemaValidationError(f"missing generated circuit artifact: {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != circuit["sha256"]:
                raise SchemaValidationError(
                    f"circuit artifact digest mismatch for {circuit['path']!r}"
                )
            previous = artifacts.setdefault(
                str(circuit["path"]),
                {
                    "path": circuit["path"],
                    "sha256": digest,
                    "qubits": circuit["qubits"],
                    "seed": circuit["seed"],
                },
            )
            if previous["sha256"] != digest:
                raise SchemaValidationError(
                    f"raw results disagree on circuit artifact {circuit['path']!r}"
                )
    return sorted(artifacts.values(), key=lambda item: (item["qubits"], item["seed"]))


def _validate_execution(
    campaign: QVCampaign,
    execution_id: str,
    raw_paths: list[Path],
    circuit_dir: Path,
    *,
    allow_partial_placements: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not raw_paths:
        raise SchemaValidationError("QV finalization requires at least one raw result")
    results = [validate_path(path.resolve()) for path in raw_paths]
    manifest_plans = {campaign.manifest_sha256: campaign.document["collection"]}
    for item in campaign.document.get("legacy_result_manifests", []):
        digest = str(item["sha256"])
        if digest in manifest_plans:
            raise SchemaValidationError(f"duplicate QV result manifest digest {digest}")
        manifest_plans[digest] = item
    expected_cases = _expected_case_ids(campaign)
    attempts: set[tuple[int, int]] = set()
    result_manifest_digests: set[str] = set()
    source_commits: set[str] = set()
    images: set[str] = set()
    regions: set[str] = set()
    zones: set[str] = set()
    cpu_models: set[str] = set()
    generator_dependencies: set[str] = set()
    boots_by_placement: dict[int, set[str]] = defaultdict(set)
    reference = campaign.document["reference_host"]
    for result in results:
        if result["campaign"]["id"] != campaign.id:
            raise SchemaValidationError("raw result belongs to another QV campaign")
        result_manifest_digest = str(result["campaign"]["manifest_sha256"])
        if result_manifest_digest not in manifest_plans:
            raise SchemaValidationError("raw result campaign manifest digest mismatch")
        result_manifest_digests.add(result_manifest_digest)
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
        images.add(str(cloud["image_id"]))
        regions.add(str(cloud["region"]))
        zones.add(str(cloud["availability_zone"]))
        cpu_models.add(str(runner["cpu_model"]))
        generator_dependencies.add(
            json.dumps(result["campaign"]["circuit_generator_dependencies"], sort_keys=True)
        )
        boots_by_placement[attempt[0]].add(str(cloud["boot_id"]))
    if len(result_manifest_digests) != 1:
        raise SchemaValidationError("QV results use different campaign manifest digests")
    result_manifest_digest = next(iter(result_manifest_digests))
    source_collection = manifest_plans[result_manifest_digest]
    placements = int(source_collection["placements"])
    replicas = int(source_collection["replicas_per_placement"])
    planned_placements = list(range(1, placements + 1))
    collected_placements = sorted({placement for placement, _replica in attempts})
    if allow_partial_placements:
        expected_collected = list(range(1, max(collected_placements) + 1))
        if collected_placements != expected_collected:
            raise SchemaValidationError(
                "partial QV placements must form a complete prefix starting at placement 1"
            )
        if not set(collected_placements).issubset(planned_placements):
            raise SchemaValidationError("raw result placement exceeds the campaign plan")
    else:
        expected_collected = planned_placements
    expected_attempts = {
        (placement, replica)
        for placement in expected_collected
        for replica in range(1, replicas + 1)
    }
    if attempts != expected_attempts:
        raise SchemaValidationError("QV placement/replica coverage does not match the campaign")
    stable_identities = (
        source_commits,
        images,
        regions,
        zones,
        cpu_models,
        generator_dependencies,
    )
    if any(len(values) != 1 for values in stable_identities):
        raise SchemaValidationError(
            "QV placements must share source, generator, CPU model, AMI, region, and AZ"
        )
    if any(len(boots) != 1 for boots in boots_by_placement.values()):
        raise SchemaValidationError("replicas in one placement must share one boot ID")
    placement_boots = {next(iter(boots)) for boots in boots_by_placement.values()}
    if len(placement_boots) != len(boots_by_placement):
        raise SchemaValidationError("each QV placement requires a distinct stop/start boot")
    circuits = _validate_circuit_artifacts(results, circuit_dir)
    coverage = {
        "source_manifest_sha256": result_manifest_digest,
        "planned_placements": placements,
        "completed_placements": collected_placements,
        "replicas_per_placement": replicas,
        "complete": collected_placements == planned_placements,
    }
    return results, circuits, coverage


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
    campaign: QVCampaign,
    execution_id: str,
    rows: list[dict[str, Any]],
    *,
    classification: str,
    collection: dict[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["case_id"])].append(row)
    cases = []
    for case_id, group in sorted(grouped.items()):
        first = group[0]
        durations = [
            float(row["execution_seconds"])
            for row in group
            if row["status"] == "success"
        ]
        cases.append(
            {
                "case_id": case_id,
                "phase": first["phase"],
                "run_id": first["run_id"],
                "qubits": first["qubits"],
                "seed": first["seed"],
                "threads_requested": first["threads_requested"],
                "statuses": dict(sorted(Counter(row["status"] for row in group).items())),
                "execution_seconds": _distribution(durations),
            }
        )
    return {
        "report_format": "clifft-bench/qv-summary/v1",
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "classification": classification,
        "collection": collection,
        "cases": cases,
    }


def finalize_qv_execution(
    campaign: QVCampaign,
    *,
    execution_id: str,
    raw_paths: list[Path],
    circuit_dir: Path,
    output_dir: Path,
    allow_partial_placements: bool = False,
) -> dict[str, Any]:
    results, circuits, collection = _validate_execution(
        campaign,
        execution_id,
        raw_paths,
        circuit_dir,
        allow_partial_placements=allow_partial_placements,
    )
    rows = _case_rows(campaign, execution_id, results)
    classification = campaign.document["classification"]
    if not collection["complete"] and classification != "smoke":
        classification = "exploratory"
    summary = _summary(
        campaign,
        execution_id,
        rows,
        classification=classification,
        collection=collection,
    )
    index = {
        "index_format": "clifft-bench/qv-execution/v1",
        "execution_id": execution_id,
        "campaign_id": campaign.id,
        "hardware_epoch": campaign.document["hardware_epoch"],
        "classification": classification,
        "collection": collection,
        "result_count": len(results),
        "case_rows": len(rows),
        "circuits": circuits,
        "files": {
            "raw": "raw/",
            "circuits": "circuits/",
            "cases": "cases.csv",
            "summary": "summary.json",
        },
    }
    _write_csv(output_dir / "cases.csv", rows)
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "index.json", index)
    return index
