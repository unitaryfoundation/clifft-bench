"""Validate self-contained raw evidence and project one deterministic case table."""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from clifft_bench.adapters import load_adapter
from clifft_bench.adapters.base import Counts, validate_counts
from clifft_bench.selection import detector_mask
from clifft_bench.system import thread_environment

FORMAT = "clifft-bench/raw/v2"
CONTRACT_KEYS = (
    "postselect_all_detectors",
    "postselection_detector_indices",
    "observable_index",
)
CASE_FIELDS = (
    "case_id",
    "variant_id",
    "workload_id",
    "workload_family",
    "implementation_id",
    "simulator_name",
    "simulator_version",
    "simulator_display_version",
    "adapter",
    "status",
    "error_phase",
    "error_type",
    "error_message",
    "postselect",
    "shots_per_call",
    "threads_requested",
    "threads_effective",
    "batch_size_effective",
    "batch_parameter",
    "batch_size_applicable",
    "sample_count",
    "median_attempted_shots_per_second",
    "mad_attempted_shots_per_second",
    "setup_seconds",
    "compile_seconds",
    "peak_active_width",
    "first_call_warmup_seconds",
    "peak_rss_bytes",
)


def read_result(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON: {error}") from error
    validate_result(document)
    return document


def write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: Any, label: str, *, integer: bool = False, positive: bool = False) -> None:
    _require(
        type(value) in ((int,) if integer else (int, float))
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0),
        f"invalid {label}: expected finite {'positive' if positive else 'nonnegative'} number",
    )


def _finite(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)
    elif isinstance(value, float):
        _require(math.isfinite(value), "nonfinite number in raw evidence")


def _timestamp(value: Any) -> datetime:
    _require(isinstance(value, str), "missing collection timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(result.tzinfo is not None, "collection timestamp requires timezone")
    return result


def _sample(sample: dict, execution: dict, metadata: dict, *, shots: int) -> None:
    _number(sample["attempted_shots"], "attempted_shots", integer=True, positive=True)
    if execution["postselect"] == "on":
        values = {key: sample[key] for key in (
            "attempted_shots", "accepted_shots", "discarded_shots", "logical_errors"
        )}
        for key, value in values.items():
            _number(value, key, integer=True)
        errors = validate_counts(Counts(**values), postselect=True)
        _require(not errors, "; ".join(errors))
        _require("packed_output" not in sample, "counts endpoint returned packed evidence")
    else:
        _require(not {"accepted_shots", "discarded_shots", "logical_errors"} & sample.keys(),
                 "packed endpoint must not report reduced counts")
        _require(sample["packed_output"] == {
            name: [shots, (metadata[f"num_{name}"] + 7) // 8]
            for name in ("detectors", "observables")
        }, "invalid packed output dimensions")
    _number(sample["duration_seconds"], "duration_seconds", positive=True)


def validate_result(document: dict[str, Any]) -> None:
    """Check completed evidence without loading a checkout."""
    try:
        _validate_result(document)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError(f"incomplete or malformed raw evidence: {error}") from error


def _validate_result(document: dict[str, Any]) -> None:
    _require(
        isinstance(document, dict) and document.get("format") == FORMAT,
        f"expected {FORMAT} evidence; archived formats use the pinned legacy revision",
    )
    _finite(document)
    run, campaign, runner = document["run"], document["campaign"], document["runner"]
    _require(isinstance(run["id"], str) and bool(run["id"]), "missing execution identity")
    _require(
        _timestamp(run["finished_at"]) >= _timestamp(run["started_at"]),
        "collection finished before it started",
    )
    _require(run["profile_id"] == campaign["profile_id"], "mismatched campaign identity")
    _require(
        run["classification"] == campaign["classification"]
        and run["classification"] in {"smoke", "official"},
        "invalid classification",
    )
    _require(isinstance(run["overrides"], dict), "missing override record")
    _require(run["selection"] is None or isinstance(run["selection"], str), "invalid selection")
    _require(isinstance(runner["suite_source"], dict), "missing source identity")
    for key in ("cpu_model", "os", "machine"):
        _require(isinstance(runner[key], str) and bool(runner[key]), f"missing host {key}")
    measurement = campaign["measurement"]
    for key in ("repetitions", "correctness_shots"):
        _number(measurement[key], key, integer=True, positive=True)
    for key in ("setup_timeout_seconds", "request_timeout_seconds", "min_sample_seconds"):
        _number(measurement[key], key, positive=True)
    _require(
        measurement["request_timeout_seconds"] > measurement["min_sample_seconds"],
        "sample interval exceeds request timeout",
    )
    resources = campaign["resources"]
    if resources["memory_limit_bytes"] is not None:
        _number(resources["memory_limit_bytes"], "memory limit", integer=True, positive=True)
    if resources["logical_cpu"] is not None:
        _number(resources["logical_cpu"], "logical CPU", integer=True)
    expected = {case["id"]: case for case in campaign["cases"]}
    _require(
        bool(expected) and len(expected) == len(campaign["cases"]),
        "duplicate or empty expected cases",
    )
    observed = {case["case_id"]: case for case in document["cases"]}
    _require(len(observed) == len(document["cases"]), "duplicate observed cases")
    _require(observed.keys() == expected.keys(), "missing or unexpected cases")
    identities: dict[str, dict] = {}
    for case_id, contract in expected.items():
        case = observed[case_id]
        _validate_case(case, contract, campaign)
        simulator = case["simulator"]
        identity = {k: v for k, v in simulator.items() if k != "dependencies"}
        _require(
            identities.setdefault(simulator["implementation_id"], identity) == identity,
            f"inconsistent implementation identity: {simulator['implementation_id']}",
        )
    if run["classification"] == "official":
        _validate_official(document)
    # Comparison validation also runs when regenerating CSV without plotting.
    comparison_pairs(document)


def _validate_case(case: dict, contract: dict, campaign: dict) -> None:
    identifier = case["case_id"]
    execution, simulator, workload = case["execution"], case["simulator"], case["workload"]
    _require(
        case["variant_id"] == contract["variant_id"]
        and workload["id"] == contract["workload_id"]
        and simulator["implementation_id"] == contract["implementation_id"],
        f"case {identifier}: identity differs from resolved campaign",
    )
    for key in ("name", "version", "display_version", "source_url", "python_executable"):
        _require(
            isinstance(simulator[key], str) and bool(simulator[key]),
            f"case {identifier}: missing simulator {key}",
        )
    _require(
        re.fullmatch(r"[0-9a-f]{40}", simulator["commit_sha"]) is not None,
        f"case {identifier}: invalid source commit",
    )
    _require(
        re.fullmatch(r"[0-9a-f]{64}", workload["artifact"]["sha256"]) is not None,
        f"case {identifier}: invalid workload digest",
    )
    semantics = workload["semantics"]
    expected_metadata = workload["expected_metadata"]
    for key in ("num_qubits", "num_measurements", "num_detectors", "num_observables"):
        _number(expected_metadata[key], key, integer=True)
    _number(semantics["observable_index"], "observable index", integer=True)
    _require(
        semantics["observable_index"] < expected_metadata["num_observables"],
        "observable index is outside declared outputs",
    )
    detector_mask(semantics, expected_metadata["num_detectors"])
    _require(simulator["adapter"] in workload["compatible_adapters"], "incompatible adapter")
    load_adapter(simulator["adapter"]).validate_options(execution["options"])
    _require(execution["options"] == contract["execution"]["options"], "mismatched options")
    requested_batch = contract["execution"]["batch_size"]
    if requested_batch != "auto":
        _number(requested_batch, "requested batch size", integer=True, positive=True)
    _require(execution["batch_size"] == requested_batch, "mismatched requested batch size")
    _number(execution["shots_per_call"], "shots_per_call", integer=True, positive=True)
    _require(execution["shots_per_call"] == contract["shots_per_call"], "mismatched call size")
    for key in ("min_sample_seconds", "request_timeout_seconds", "repetitions"):
        _require(execution[key] == campaign["measurement"][key], f"mismatched {key}")
    _require(
        execution["memory_limit_bytes"] == campaign["resources"]["memory_limit_bytes"],
        "mismatched memory limit",
    )
    threads = contract["execution"]["threads"]
    environment = thread_environment(threads)
    _require(execution["threads"] == execution["threads_requested"] == threads,
             "mismatched thread budget")
    _require(execution["postselect"] in workload["postselect_modes"]
             and execution["postselect"] in {"on", "off"}
             and execution["postselect"] == contract["execution"]["postselect"],
             "invalid selection contract")
    _require(case["status"] in {"success", "error", "unsupported", "interrupted"},
             "no terminal case outcome")
    if case["status"] == "unsupported":
        _require(case["setup"] is None and not case["samples"]
                 and case["error"]["phase"] == "setup"
                 and case["error"]["type"] == "UnsupportedConfiguration",
                 "unsupported case must retain its setup rejection")
    if case["status"] == "success":
        _require(case["error"] is None, "successful case has an error")
        _require(
            case["correctness"]["status"] == "passed"
            and not case["correctness"]["errors"]
            and case["correctness"]["timed_region"] is False,
            "correctness did not pass",
        )
        _sample(case["correctness"]["details"], execution, expected_metadata,
                shots=campaign["measurement"]["correctness_shots"])
        _require(case["correctness"]["details"]["attempted_shots"]
                 == campaign["measurement"]["correctness_shots"],
                 "correctness call differs from requested shots")
        _require(
            bool(case["samples"]) and len(case["samples"]) == execution["repetitions"],
            "incomplete timed samples",
        )
    else:
        error = case["error"]
        _require(
            error["phase"] in {"setup", "first_call_warmup", "correctness", "sampling"}
            and isinstance(error["type"], str)
            and bool(error["type"])
            and isinstance(error["message"], str),
            "failure lacks structured error",
        )
    first_call = case["first_call_warmup"]
    if first_call is not None:
        if first_call["contract_errors"]:
            _require(case["status"] == "error" and case["error"]["phase"] == "first_call_warmup",
                     "invalid first call was not recorded as a failure")
        else:
            _sample(first_call, execution, expected_metadata, shots=execution["shots_per_call"])
            _require(first_call["attempted_shots"] == execution["shots_per_call"],
                     "first call differs from public call size")
    if case["status"] == "success":
        _require(first_call is not None and not first_call["contract_errors"],
                 "missing or invalid first call")
    setup = case["setup"]
    if setup is None:
        _require(
            case["status"] == "interrupted"
            or (case["status"] in {"error", "unsupported"}
                and case["error"]["phase"] == "setup"),
            "missing setup evidence",
        )
    else:
        _number(setup["duration_seconds"], "setup duration")
        metadata = setup["runtime_metadata"]
        _number(metadata["compile_seconds"], "compile duration")
        if case["status"] == "success":
            _require(
                all(metadata[k] == v for k, v in expected_metadata.items()),
                "runtime circuit metadata mismatch",
            )
        effective = execution["threads_effective"]
        _require(metadata["threads"] == effective, "effective thread count mismatch")
        if effective is not None:
            _number(effective, "effective threads", integer=True, positive=True)
            _require(effective <= threads, "worker exceeded its thread budget")
        _require(metadata["thread_environment"] == environment,
                 "worker thread environment differs from its budget")
        affinity = setup["affinity"]
        if affinity["applied"]:
            cpus, cores = affinity["logical_cpus"], affinity["physical_cores"]
            _require(isinstance(cpus, list) and len(cpus) == len(set(cpus)) == threads,
                     "affinity does not allocate the requested number of cores")
            for cpu in cpus:
                _number(cpu, "affinity CPU", integer=True)
            _require(affinity["available_after"] == sorted(cpus)
                     and set(cpus) <= set(affinity["available_before"]),
                     "worker affinity mask differs from its allocation")
            anchor = campaign["resources"]["logical_cpu"]
            _require(anchor is None or cpus[0] == anchor, "worker changed its anchor CPU")
            _require([core["logical_cpu"] for core in cores] == cpus,
                     "physical topology does not match affinity")
            for core in cores:
                for key in ("socket", "core"):
                    _number(core[key], key, integer=True)
            _require(len({(core["socket"], core["core"]) for core in cores}) == threads,
                     "worker allocated sibling threads of the same physical core")
        else:
            _require(threads == 1, "multiple threads require applied physical-core affinity")
        _require(metadata["version"] == simulator["version"], "runtime version mismatch")
        _require(metadata["batch_enabled"] is execution["batch_enabled"]
                 and (metadata["batch_enabled"] is None
                      or type(metadata["batch_enabled"]) is bool), "runtime batching mismatch")
        capacity = metadata["effective_batch_size"]
        if capacity is not None:
            _number(capacity, "native batch capacity", integer=True, positive=True)
        _require(execution["batch_size_effective"] == (
            min(capacity, execution["shots_per_call"]) if capacity is not None else None
        ), "effective batch differs from native capacity")
        if "scheduler" in execution["options"] and case["status"] == "success":
            _require(
                type(metadata["scheduler_statistics"]["applied"]) is bool,
                "missing scheduler statistics",
            )
        if simulator["adapter"] == "tsim" and case["status"] == "success":
            _require(
                metadata["sampling_backend"] == "active_statevector"
                and metadata["installed_source_commit"] == simulator["commit_sha"]
                and metadata["trajectory_sharing"] is True,
                "invalid Tsim runtime provenance",
            )
    for repetition, sample in enumerate(case["samples"]):
        _sample(sample, execution, expected_metadata, shots=execution["shots_per_call"])
        _number(sample["api_calls"], "api_calls", integer=True, positive=True)
        _number(sample["peak_rss_bytes"], "peak RSS", integer=True, positive=True)
        _number(sample["throughput_attempted_shots_per_second"], "throughput", positive=True)
        _require(sample["repetition"] == repetition, "invalid sample repetition order")
        _require(
            sample["attempted_shots"] == sample["api_calls"] * execution["shots_per_call"],
            "sample attempted shots differ from public calls",
        )
        _require(
            math.isclose(
                sample["throughput_attempted_shots_per_second"],
                sample["attempted_shots"] / sample["duration_seconds"],
                rel_tol=1e-9,
            ),
            "sample throughput differs from counts/duration",
        )


def _validate_official(document: dict) -> None:
    run, campaign, runner = document["run"], document["campaign"], document["runner"]
    _require(
        run["selection"] is None and not (run["overrides"].keys() - {"cpu"}),
        "official execution must use the complete declared campaign",
    )
    source, cloud = runner["suite_source"], runner["cloud"]
    _require(
        source["dirty"] is False
        and isinstance(source["commit"], str)
        and re.fullmatch(r"[0-9a-f]{40}", source["commit"]) is not None,
        "official evidence requires clean committed source",
    )
    for key in (
        "provider",
        "instance_type",
        "instance_id",
        "image_id",
        "region",
        "availability_zone",
        "lifecycle",
        "boot_id",
    ):
        _require(isinstance(cloud[key], str) and bool(cloud[key]), "incomplete cloud identity")
    reference = campaign["reference_host"]
    _require(
        cloud["provider"] == reference["provider"] == "aws"
        and cloud["instance_type"] == reference["instance_type"]
        and cloud["lifecycle"] == "on-demand"
        and runner["os"] == "Linux"
        and runner["machine"] == reference["architecture"],
        "reference host mismatch",
    )
    expected_memory = int(campaign["collection"]["memory_limit_gib"] * (1 << 30))
    _require(
        campaign["resources"]["memory_limit_bytes"] == expected_memory,
        "official memory ceiling mismatch",
    )
    for case in document["cases"]:
        if case["setup"] is not None:
            _require(
                case["setup"]["runtime_metadata"]["address_space_limit_bytes"] == expected_memory,
                "worker memory ceiling mismatch",
            )
            affinity = case["setup"]["affinity"]
            _require(
                affinity["applied"] is True,
                "worker CPU affinity was not applied",
            )


def comparison_pairs(document: dict) -> list[dict]:
    """Join declared comparisons; retain failed and intentionally absent sides."""
    by_variant: dict[str, dict[str, dict]] = {}
    for case in document["cases"]:
        workload = case["workload"]["id"]
        variant = by_variant.setdefault(case["variant_id"], {})
        _require(workload not in variant, "duplicate variant/workload cases")
        variant[workload] = case
    pairs, seen = [], set()
    for comparison in document["campaign"].get("comparisons", []):
        _require(comparison["id"] not in seen, "duplicate comparison identity")
        seen.add(comparison["id"])
        baseline_id = comparison["baseline_variant"]
        candidates = comparison["candidate_variants"]
        _require(
            bool(candidates)
            and len(candidates) == len(set(candidates))
            and baseline_id not in candidates,
            "invalid comparison variants",
        )
        for candidate_id in candidates:
            if document["run"]["selection"] is None:
                _require(
                    baseline_id in by_variant and candidate_id in by_variant,
                    "comparison references unknown variant",
                )
            baseline = by_variant.get(baseline_id, {})
            candidate = by_variant.get(candidate_id, {})
            for workload in dict.fromkeys([*baseline, *candidate]):
                left, right = baseline.get(workload), candidate.get(workload)
                ratio = None
                if left is not None and right is not None:
                    _compatible(left, right)
                    if left["status"] == right["status"] == "success":
                        ratio = _rate(right) / _rate(left)
                        _number(ratio, "paired ratio", positive=True)
                pairs.append(
                    {
                        "comparison_id": comparison["id"],
                        "workload_id": workload,
                        "baseline_variant": baseline_id,
                        "candidate_variant": candidate_id,
                        "baseline": left,
                        "candidate": right,
                        "ratio": ratio,
                    }
                )
    return pairs


def _compatible(left: dict, right: dict) -> None:
    a, b = left["workload"], right["workload"]
    _require(
        a["artifact"]["sha256"] == b["artifact"]["sha256"]
        and all(a["semantics"].get(k) == b["semantics"].get(k) for k in CONTRACT_KEYS),
        "incompatible comparison workload/selection semantics",
    )
    for key in (
        "postselect",
        "shots_per_call",
        "memory_limit_bytes",
        "threads_requested",
        "min_sample_seconds",
        "request_timeout_seconds",
        "repetitions",
    ):
        _require(
            left["execution"][key] == right["execution"][key], f"incompatible comparison {key}"
        )


def _rate(case: dict) -> float:
    return statistics.median(s["throughput_attempted_shots_per_second"] for s in case["samples"])


def case_rows(document: dict) -> list[dict]:
    rows = []
    indexed = {c["case_id"]: c for c in document["cases"]}
    for expected in document["campaign"]["cases"]:
        case = indexed[expected["id"]]
        execution, simulator = case["execution"], case["simulator"]
        setup, error = case["setup"] or {}, case["error"] or {}
        metadata = setup.get("runtime_metadata", {})
        rate = _rate(case) if case["status"] == "success" else ""
        rows.append(
            {
                "case_id": case["case_id"],
                "variant_id": case["variant_id"],
                "workload_id": case["workload"]["id"],
                "workload_family": case["workload"]["family"],
                "implementation_id": simulator["implementation_id"],
                "simulator_name": simulator["name"],
                "simulator_version": simulator["version"],
                "simulator_display_version": simulator["display_version"],
                "adapter": simulator["adapter"],
                "status": case["status"],
                "error_phase": error.get("phase", ""),
                "error_type": error.get("type", ""),
                "error_message": error.get("message", ""),
                "postselect": execution["postselect"],
                "shots_per_call": execution["shots_per_call"],
                "threads_requested": execution["threads_requested"],
                "threads_effective": (
                    execution.get("threads_effective")
                    if execution.get("threads_effective") is not None else ""
                ),
                "batch_size_effective": execution.get("batch_size_effective") or "",
                "batch_parameter": metadata.get("batch_parameter", ""),
                "batch_size_applicable": (
                    str(metadata["batch_size_applicable"]).lower()
                    if "batch_size_applicable" in metadata
                    else ""
                ),
                "sample_count": len(case["samples"]),
                "median_attempted_shots_per_second": rate,
                "mad_attempted_shots_per_second": statistics.median(
                    abs(s["throughput_attempted_shots_per_second"] - rate) for s in case["samples"]
                )
                if rate != ""
                else "",
                "setup_seconds": setup.get("duration_seconds", ""),
                **{
                    k: metadata.get(k, "")
                    for k in ("compile_seconds", "peak_active_width")
                },
                "first_call_warmup_seconds": (case["first_call_warmup"] or {}).get(
                    "duration_seconds", ""
                ),
                "peak_rss_bytes": max((s["peak_rss_bytes"] for s in case["samples"]), default=""),
            }
        )
    return rows


def export_csv(document: dict, path: Path) -> None:
    validate_result(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CASE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(case_rows(document))
    temporary.replace(path)
