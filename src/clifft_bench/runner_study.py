"""Reproducible summaries for identical-software A/A runner studies."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from clifft_bench.schema import read_json, validate_document

PAIR_FIELDS = [
    "source_result",
    "run_id",
    "workflow_run_id",
    "run_attempt",
    "hardware_key",
    "machine",
    "cpu_model",
    "physical_cores",
    "logical_cpus",
    "memory_bytes",
    "image_os",
    "image_version",
    "pair_id",
    "workload_id",
    "case_a",
    "case_b",
    "repetition",
    "sequence_a",
    "sequence_b",
    "rate_a",
    "rate_b",
    "ratio_b_over_a",
    "absolute_delta_percent",
]

HARDWARE_MEMORY_BUCKET_BYTES = 1024**3


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty observation set")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _distribution(values: list[float]) -> dict[str, float]:
    median = statistics.median(values)
    return {
        "median": median,
        "mad": statistics.median(abs(value - median) for value in values),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
    }


def _hardware_identity(document: dict[str, Any]) -> dict[str, Any]:
    runner = document["runner"]
    workflow = document["run"]["workflow"]
    return {
        "machine": runner["machine"],
        "cpu_model": runner["cpu_model"],
        "physical_cores": runner["physical_cores"],
        "logical_cpus": runner["logical_cpus"],
        "memory_bytes": runner["memory_bytes"],
        "image_os": workflow["image_os"],
    }


def _memory_bucket(memory_bytes: int) -> int:
    return (
        (memory_bytes + HARDWARE_MEMORY_BUCKET_BYTES // 2)
        // HARDWARE_MEMORY_BUCKET_BYTES
        * HARDWARE_MEMORY_BUCKET_BYTES
    )


def _hardware_key(identity: dict[str, Any]) -> str:
    grouped_identity = {
        key: value for key, value in identity.items() if key != "memory_bytes"
    }
    grouped_identity["memory_bytes_bucket"] = _memory_bucket(identity["memory_bytes"])
    encoded = json.dumps(
        grouped_identity, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def _sample_by_repetition(case: dict[str, Any]) -> dict[int, dict[str, Any]]:
    samples: dict[int, dict[str, Any]] = {}
    for sample in case["samples"]:
        repetition = int(sample["repetition"])
        if repetition in samples:
            raise ValueError(
                f"case {case['case_id']!r} repeats repetition {repetition} more than once"
            )
        samples[repetition] = sample
    return samples


def _simulator_identity(simulator: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in simulator.items() if key != "python_executable"}


def _check_aa_pair(pair_id: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cases) != 2:
        raise ValueError(f"A/A pair {pair_id!r} must contain exactly two cases")
    left, right = cases
    if left["status"] != "success" or right["status"] != "success":
        raise ValueError(f"A/A pair {pair_id!r} contains an unsuccessful case")
    if left["workload"]["id"] != right["workload"]["id"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different workloads")
    if left["workload"]["artifact"]["sha256"] != right["workload"]["artifact"]["sha256"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different workload artifacts")
    if left["workload"] != right["workload"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different workload definitions")
    if left["simulator"]["implementation_id"] != right["simulator"]["implementation_id"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different implementations")
    if left["simulator"]["commit_sha"] != right["simulator"]["commit_sha"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different simulator commits")
    simulator_identity = _simulator_identity(left["simulator"])
    if simulator_identity != _simulator_identity(right["simulator"]):
        raise ValueError(f"A/A pair {pair_id!r} uses different simulator identities")
    if left["execution"] != right["execution"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different execution settings")
    return {
        "case_ids": [left["case_id"], right["case_id"]],
        "workload": {
            key: value for key, value in left["workload"].items() if key != "artifact"
        },
        "artifact_sha256": left["workload"]["artifact"]["sha256"],
        "simulator": simulator_identity,
        "execution": left["execution"],
    }


def _document_observations(
    path: Path, document: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    validate_document(document, source=str(path))
    identity = _hardware_identity(document)
    hardware_key = _hardware_key(identity)
    workflow = document["run"]["workflow"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_pairs = []
    for case in document["cases"]:
        pair_id = case["pair_id"]
        if pair_id is None:
            skipped_pairs.append(
                {
                    "source_result": path.name,
                    "run_id": document["run"]["id"],
                    "pair_id": None,
                    "reason": f"case {case['case_id']!r} has no A/A pair_id",
                }
            )
            continue
        groups[str(pair_id)].append(case)

    observations = []
    identities = {}
    for pair_id, cases in sorted(groups.items()):
        try:
            pair_identity = _check_aa_pair(pair_id, cases)
            left, right = cases
            left_samples = _sample_by_repetition(left)
            right_samples = _sample_by_repetition(right)
            if left_samples.keys() != right_samples.keys():
                raise ValueError(f"A/A pair {pair_id!r} has unmatched repetitions")
            for repetition in sorted(left_samples):
                sample_a = left_samples[repetition]
                sample_b = right_samples[repetition]
                rate_a = float(sample_a["throughput_attempted_shots_per_second"])
                rate_b = float(sample_b["throughput_attempted_shots_per_second"])
                observations.append(
                    {
                        "source_result": path.name,
                        "run_id": document["run"]["id"],
                        "workflow_run_id": workflow["run_id"],
                        "run_attempt": workflow["run_attempt"],
                        "hardware_key": hardware_key,
                        "machine": identity["machine"],
                        "cpu_model": identity["cpu_model"],
                        "physical_cores": identity["physical_cores"],
                        "logical_cpus": identity["logical_cpus"],
                        "memory_bytes": identity["memory_bytes"],
                        "image_os": identity["image_os"],
                        "image_version": workflow["image_version"],
                        "pair_id": pair_id,
                        "workload_id": left["workload"]["id"],
                        "case_a": left["case_id"],
                        "case_b": right["case_id"],
                        "repetition": repetition,
                        "sequence_a": sample_a["sequence_index"],
                        "sequence_b": sample_b["sequence_index"],
                        "rate_a": rate_a,
                        "rate_b": rate_b,
                        "ratio_b_over_a": rate_b / rate_a,
                        "absolute_delta_percent": (
                            200 * abs(rate_b - rate_a) / (rate_a + rate_b)
                        ),
                    }
                )
            identities[pair_id] = pair_identity
        except ValueError as error:
            skipped_pairs.append(
                {
                    "source_result": path.name,
                    "run_id": document["run"]["id"],
                    "pair_id": pair_id,
                    "reason": str(error),
                }
            )
    return observations, skipped_pairs, identities


def _dispatch_summaries(
    observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    replicas: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    dispatch_metadata: dict[str, dict[str, Any]] = {}
    for observation in observations:
        workflow_run_id = observation["workflow_run_id"]
        run_attempt = observation["run_attempt"]
        if workflow_run_id is None:
            dispatch_id = str(observation["run_id"])
        else:
            dispatch_id = f"{workflow_run_id}:{run_attempt or 'unknown'}"
        dispatch_metadata[dispatch_id] = {
            "workflow_run_id": workflow_run_id,
            "run_attempt": run_attempt,
        }
        replicas[(dispatch_id, observation["pair_id"], observation["run_id"])].append(
            observation
        )

    dispatches: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (dispatch_id, pair_id, run_id), items in sorted(replicas.items()):
        replica_center = statistics.median(
            math.log(float(item["ratio_b_over_a"])) for item in items
        )
        dispatches[(dispatch_id, pair_id)].append(
            {
                "run_id": run_id,
                "log_ratio_b_over_a": replica_center,
                "workload_id": items[0]["workload_id"],
            }
        )

    estimates = []
    for (dispatch_id, pair_id), replica_items in sorted(dispatches.items()):
        center_log_ratio = statistics.median(
            item["log_ratio_b_over_a"] for item in replica_items
        )
        center_ratio = math.exp(center_log_ratio)
        signed_delta = 200 * (center_ratio - 1) / (center_ratio + 1)
        estimates.append(
            {
                "dispatch_id": dispatch_id,
                **dispatch_metadata[dispatch_id],
                "pair_id": pair_id,
                "workload_id": replica_items[0]["workload_id"],
                "replica_count": len(replica_items),
                "replica_run_ids": [item["run_id"] for item in replica_items],
                "ratio_b_over_a": center_ratio,
                "signed_delta_percent": signed_delta,
                "absolute_delta_percent": abs(signed_delta),
            }
        )

    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for estimate in estimates:
        by_pair[estimate["pair_id"]].append(estimate)
    groups = []
    for pair_id, items in sorted(by_pair.items()):
        groups.append(
            {
                "pair_id": pair_id,
                "workload_id": items[0]["workload_id"],
                "dispatch_count": len(items),
                "replica_counts": sorted({item["replica_count"] for item in items}),
                "ratio_b_over_a": _distribution(
                    [float(item["ratio_b_over_a"]) for item in items]
                ),
                "absolute_delta_percent": _distribution(
                    [float(item["absolute_delta_percent"]) for item in items]
                ),
            }
        )
    return estimates, groups


def analyze_runner_study(paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not paths:
        raise ValueError("at least one raw result is required")
    resolved_paths = [path.resolve() for path in paths]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ValueError("duplicate raw result path")
    observations = []
    skipped_pairs = []
    identities: dict[str, tuple[dict[str, Any], str]] = {}
    run_ids: dict[str, str] = {}
    for path in resolved_paths:
        try:
            document = read_json(path)
        except OSError as error:
            raise ValueError(f"{path}: cannot read raw result: {error}") from error
        document_observations, document_skips, document_identities = (
            _document_observations(path, document)
        )
        run_id = str(document["run"]["id"])
        if run_id in run_ids:
            raise ValueError(
                f"duplicate raw result run id {run_id!r} in "
                f"{run_ids[run_id]!r} and {path.name!r}"
            )
        run_ids[run_id] = path.name
        for pair_id, identity in document_identities.items():
            if pair_id in identities and identities[pair_id][0] != identity:
                previous_path = identities[pair_id][1]
                raise ValueError(
                    f"A/A pair {pair_id!r} changes identity between "
                    f"{previous_path!r} and {path.name!r}"
                )
            identities[pair_id] = (identity, path.name)
        observations.extend(document_observations)
        skipped_pairs.extend(document_skips)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[(observation["hardware_key"], observation["pair_id"])].append(observation)

    summaries = []
    for (hardware_key, pair_id), items in sorted(grouped.items()):
        rates = [value for item in items for value in [item["rate_a"], item["rate_b"]]]
        throughput = _distribution(rates)
        throughput["relative_mad_percent"] = (
            100 * throughput["mad"] / throughput["median"]
        )
        summaries.append(
            {
                "hardware_key": hardware_key,
                "hardware": {
                    "machine": items[0]["machine"],
                    "cpu_model": items[0]["cpu_model"],
                    "physical_cores": items[0]["physical_cores"],
                    "logical_cpus": items[0]["logical_cpus"],
                    "memory_bytes_bucket": _memory_bucket(items[0]["memory_bytes"]),
                    "observed_memory_bytes": {
                        "min": min(item["memory_bytes"] for item in items),
                        "max": max(item["memory_bytes"] for item in items),
                    },
                    "image_os": items[0]["image_os"],
                },
                "pair_id": pair_id,
                "workload_id": items[0]["workload_id"],
                "image_versions": sorted(
                    {
                        str(item["image_version"])
                        for item in items
                        if item["image_version"] is not None
                    }
                ),
                "run_count": len({item["run_id"] for item in items}),
                "observation_count": len(items),
                "ratio_b_over_a": _distribution(
                    [float(item["ratio_b_over_a"]) for item in items]
                ),
                "absolute_delta_percent": _distribution(
                    [float(item["absolute_delta_percent"]) for item in items]
                ),
                "throughput_attempted_shots_per_second": throughput,
            }
        )

    paired: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        paired[observation["pair_id"]].append(observation)
    pair_summaries = []
    for pair_id, items in sorted(paired.items()):
        pair_summaries.append(
            {
                "pair_id": pair_id,
                "workload_id": items[0]["workload_id"],
                "hardware_key_count": len({item["hardware_key"] for item in items}),
                "result_count": len({item["run_id"] for item in items}),
                "observation_count": len(items),
                "ratio_b_over_a": _distribution(
                    [float(item["ratio_b_over_a"]) for item in items]
                ),
                "absolute_delta_percent": _distribution(
                    [float(item["absolute_delta_percent"]) for item in items]
                ),
            }
        )

    dispatch_estimates, dispatch_groups = _dispatch_summaries(observations)
    report = {
        "report_format": "clifft-bench/runner-study-summary/v2",
        "result_count": len(resolved_paths),
        "observation_count": len(observations),
        "skipped_pair_count": len(skipped_pairs),
        "skipped_pairs": skipped_pairs,
        "groups": summaries,
        "pair_groups": pair_summaries,
        "dispatch_estimates": dispatch_estimates,
        "dispatch_groups": dispatch_groups,
    }
    return report, observations


def write_runner_study_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def write_runner_study_csv(path: Path, observations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PAIR_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(observations)
    os.replace(temporary, path)
