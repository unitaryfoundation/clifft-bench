"""Build compiler-profile trials and propose reviewable per-workload settings."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from clifft_bench.compiler import scheduler_configuration
from clifft_bench.manifest import Suite
from clifft_bench.results import _validate_calibration_record, _validate_compiler_record
from clifft_bench.schema import validate_document

PROFILES = {
    "scheduler-off": {"enabled": False},
    "scheduler-default": {"enabled": True},
    "scheduler-deep": {"enabled": True, "search_budget": None},
}


def tuning_manifest(suite: Suite, variant: str, output: Path) -> dict[str, Any]:
    selected = [case for case in suite.cases if case.definition["variant_id"] == variant]
    if not selected or any(case.implementation.definition["adapter"] != "clifft"
                           for case in selected):
        raise ValueError("select a nonempty Clifft variant for compiler tuning")
    if len({case.workload.id for case in selected}) != len(selected):
        raise ValueError("compiler tuning requires one source case per workload")
    document = {
        key: deepcopy(suite.run[key])
        for key in ("schema_version", "suite_version", "seed", "resources", "measurement")
    }
    if "collection" in suite.run:
        document["collection"] = deepcopy(suite.run["collection"])
    document.update({
        "profile_id": "compiler-tuning",
        "title": "Clifft compiler profiles with independent batch calibration",
        "classification": "smoke",
        "workloads_manifest": os.path.relpath(suite.workloads_path, output.parent),
        "software_manifest": os.path.relpath(suite.software_path, output.parent),
        "cases": [],
    })
    for case in selected:
        for profile, options in PROFILES.items():
            definition = deepcopy(case.definition)
            definition.update(id=f"{case.workload.id}--{profile}", variant_id=profile)
            definition["execution"].update(
                mode="throughput", batch_enabled=True, batch_size="calibrate",
                clifft_scheduler=scheduler_configuration({"clifft_scheduler": options}),
            )
            document["cases"].append(definition)
    validate_document(document)
    return document


def tuning_summary(suite: Suite, result: dict[str, Any], raw_path: Path) -> dict[str, Any]:
    """Recommend compiler settings using held-out samples after batch calibration."""
    validate_document(result)
    if suite.run["classification"] != "smoke" or suite.run["profile_id"] != "compiler-tuning":
        raise ValueError("summarize a compiler-tuning manifest")
    expected = {case.id: case for case in suite.cases}
    observed = {case["case_id"]: case for case in result["cases"]}
    if len(observed) != len(result["cases"]) or set(observed) != set(expected):
        raise ValueError("tuning result must contain every declared case exactly once")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identities: dict[str, set[str]] = defaultdict(set)
    for case_id, definition in expected.items():
        case = observed[case_id]
        if case["status"] != "success":
            raise ValueError(f"tuning case {case_id!r} failed; resolve failures before selection")
        implementation = definition.implementation.definition
        identity = case["simulator"]
        if any(identity.get(key) != implementation[key]
               for key in ("version", "commit_sha", "adapter")):
            raise ValueError(f"tuning case {case_id!r} software identity does not match manifest")
        if (case["workload"]["id"] != definition.workload.id
                or case["workload"]["artifact"]["sha256"] != definition.workload.artifact_sha256
                or case["workload"]["semantics"] != definition.workload.definition["semantics"]
                or case["execution"]["shots_per_call"] != definition.definition["shots_per_call"]):
            raise ValueError(f"tuning case {case_id!r} workload does not match manifest")
        _validate_compiler_record(definition, case)
        _validate_calibration_record(definition, case)
        metadata = case["setup"]["runtime_metadata"]
        groups[definition.workload.id].append({
            "case_id": case_id,
            "clifft_scheduler": scheduler_configuration(case["execution"]),
            "batch_size": case["execution"]["batch_size"],
            "shots_per_call": case["execution"]["shots_per_call"],
            "compile_seconds": metadata["compile_seconds"],
            "peak_active_width": metadata["peak_active_width"],
            "scheduler_statistics": metadata.get("scheduler_statistics"),
            "batch_calibration_seconds": metadata["batch_calibration"]["duration_seconds"],
            "median_attempted_shots_per_second": case["summary"][
                "median_attempted_shots_per_second"
            ],
            "mad_attempted_shots_per_second": case["summary"][
                "mad_attempted_shots_per_second"
            ],
        })
        identities[definition.workload.id].add(json.dumps({
            "simulator": identity,
            "shots_per_call": case["execution"]["shots_per_call"],
            "threads": case["execution"]["threads_effective"],
            "precision": metadata["precision"],
        }, sort_keys=True))
    recommendations = []
    trials = []
    for workload, candidates in groups.items():
        if len(identities[workload]) != 1:
            raise ValueError(
                f"tuning profiles for {workload!r} have different measurement conditions"
            )
        # A pass that leaves the plan unchanged cannot explain a throughput gain.
        eligible = [row for row in candidates if not row["clifft_scheduler"]["enabled"]
                    or row["scheduler_statistics"]["applied"]]
        if not any(not row["clifft_scheduler"]["enabled"] for row in candidates):
            raise ValueError(f"tuning profiles for {workload!r} must include scheduling off")
        winner = max(eligible, key=lambda row: (
            row["median_attempted_shots_per_second"], -row["compile_seconds"],
            -row["batch_size"], row["case_id"],
        ))
        recommendations.append({
            "workload_id": workload,
            "shots_per_call": winner["shots_per_call"],
            "execution": {
                "clifft_scheduler": winner["clifft_scheduler"],
                "batch_enabled": True,
                "batch_size": "calibrate",
            },
        })
        trials.append({
            "workload_id": workload, "selected_case_id": winner["case_id"],
            "candidates": candidates,
        })
    return {
        "status": "provisional",
        "selection_statistic": "median_attempted_shots_per_second",
        "tie_break": "lower_compile_seconds_then_smaller_batch_size_then_case_id",
        "raw_result": str(raw_path.resolve()),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "run_manifest": str(suite.run_path),
        "workloads": recommendations,
        "trials": trials,
    }
