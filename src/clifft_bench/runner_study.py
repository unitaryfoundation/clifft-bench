"""Reproducible summaries for identical-software A/A runner studies."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from clifft_bench.schema import validate_document

PAIR_FIELDS = [
    "source_result",
    "run_id",
    "workflow_run_id",
    "run_attempt",
    "hardware_key",
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


def _hardware_key(identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
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


def _check_aa_pair(pair_id: str, cases: list[dict[str, Any]]) -> None:
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
    stable_simulator_fields = [
        "name",
        "version",
        "commit_datetime",
        "release_datetime",
        "source_url",
        "adapter",
        "build",
        "dependencies",
    ]
    if any(left["simulator"][key] != right["simulator"][key] for key in stable_simulator_fields):
        raise ValueError(f"A/A pair {pair_id!r} uses different simulator identities")
    if left["execution"] != right["execution"]:
        raise ValueError(f"A/A pair {pair_id!r} uses different execution settings")


def _document_observations(path: Path, document: dict[str, Any]) -> list[dict[str, Any]]:
    validate_document(document, source=str(path))
    identity = _hardware_identity(document)
    hardware_key = _hardware_key(identity)
    workflow = document["run"]["workflow"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in document["cases"]:
        pair_id = case["pair_id"]
        if pair_id is None:
            raise ValueError(f"case {case['case_id']!r} has no A/A pair_id")
        groups[str(pair_id)].append(case)

    observations = []
    for pair_id, cases in sorted(groups.items()):
        cases.sort(key=lambda case: str(case["case_id"]))
        _check_aa_pair(pair_id, cases)
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
                    "absolute_delta_percent": 200 * abs(rate_b - rate_a) / (rate_a + rate_b),
                }
            )
    return observations


def analyze_runner_study(paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not paths:
        raise ValueError("at least one raw result is required")
    observations = []
    for path in paths:
        document = json.loads(path.read_text())
        observations.extend(_document_observations(path, document))

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
                    key: items[0][key]
                    for key in [
                        "cpu_model",
                        "physical_cores",
                        "logical_cpus",
                        "memory_bytes",
                        "image_os",
                    ]
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

    report = {
        "schema_version": "clifft-bench/runner-study-summary/v1",
        "result_count": len(paths),
        "observation_count": len(observations),
        "groups": summaries,
    }
    return report, observations


def write_runner_study_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def write_runner_study_csv(path: Path, observations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PAIR_FIELDS)
        writer.writeheader()
        writer.writerows(observations)
