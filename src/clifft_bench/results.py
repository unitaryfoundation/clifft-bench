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
from clifft_bench.calibration import (
    CALIBRATION_POLICY,
    TUNING_BUDGET_SECONDS,
    calibration_candidates,
)
from clifft_bench.selection import detector_mask, has_postselection

FORMAT = "clifft-bench/raw/v1"
CONTRACT_KEYS = (
    "postselect_all_detectors",
    "postselection_detector_indices",
    "reference_convention",
    "observable_index",
    "output_contract",
    "throughput_numerator",
    "batch_semantics",
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
    "mode",
    "shots_per_call",
    "batch_size_effective",
    "tuning_policy",
    "batch_parameter",
    "batch_size_applicable",
    "sample_count",
    "median_attempted_shots_per_second",
    "mad_attempted_shots_per_second",
    "setup_seconds",
    "compile_seconds",
    "peak_active_width",
    "kernel_warmup_seconds",
    "warmup_seconds",
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


def _counts(sample: dict, postselect: bool) -> None:
    values = {
        key: sample[key]
        for key in ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors")
    }
    for key, value in values.items():
        _number(value, key, integer=True)
    errors = validate_counts(Counts(**values), postselect=postselect)
    _require(not errors, "; ".join(errors))
    _number(sample["duration_seconds"], "duration_seconds", positive=True)


def validate_result(document: dict[str, Any]) -> None:
    """Check completed evidence without loading a checkout or replaying calibration."""
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
    for key in ("repetitions", "warmup_shots", "correctness_shots"):
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
    for key in ("name", "version", "display_version", "source_url", "commit_datetime"):
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
    _require(
        semantics["reference_convention"] == "raw-record-parity"
        and semantics["throughput_numerator"] == "attempted shots",
        f"case {identifier}: invalid output semantics",
    )
    _require(simulator["adapter"] in workload["compatible_adapters"], "incompatible adapter")
    load_adapter(simulator["adapter"]).validate_options(execution["options"])
    for key in ("mode", "batch_enabled", "options"):
        _require(
            execution[key] == contract["execution"][key], f"case {identifier}: mismatched {key}"
        )
    _require(
        execution["mode"] in {"latency", "throughput"} and type(execution["batch_enabled"]) is bool,
        "invalid execution mode/batching",
    )
    _number(execution["shots_per_call"], "shots_per_call", integer=True, positive=True)
    _require(execution["shots_per_call"] == contract["shots_per_call"], "mismatched call size")
    for key in ("min_sample_seconds", "request_timeout_seconds", "repetitions"):
        _require(execution[key] == campaign["measurement"][key], f"mismatched {key}")
    _require(
        execution["memory_limit_bytes"] == campaign["resources"]["memory_limit_bytes"],
        "mismatched memory limit",
    )
    _require(
        execution["threads_requested"] == 1
        and execution["postselection"] is has_postselection(semantics),
        "invalid thread or selection contract",
    )
    if contract["execution"]["batch_size"] != "calibrate":
        _number(execution["batch_size"], "batch size", integer=True, positive=True)
        _require(
            execution["batch_size"] == contract["execution"]["batch_size"],
            "mismatched fixed batch size",
        )
    _require(case["status"] in {"success", "error", "interrupted"}, "no terminal case outcome")
    if case["status"] == "success":
        _require(case["error"] is None, "successful case has an error")
        _require(
            case["correctness"]["status"] == "passed"
            and not case["correctness"]["errors"]
            and case["correctness"]["timed_region"] is False,
            "correctness did not pass",
        )
        _counts(case["warmup"], execution["postselection"])
        _counts(case["correctness"]["details"], execution["postselection"])
        _require(
            bool(case["samples"]) and len(case["samples"]) == execution["repetitions"],
            "incomplete timed samples",
        )
    else:
        error = case["error"]
        _require(
            error["phase"] in {"setup", "warmup", "correctness", "sampling"}
            and isinstance(error["type"], str)
            and bool(error["type"])
            and isinstance(error["message"], str),
            "failure lacks structured error",
        )
    setup = case["setup"]
    if setup is None:
        _require(
            case["status"] == "interrupted"
            or (case["status"] == "error" and case["error"]["phase"] == "setup"),
            "missing setup evidence",
        )
    else:
        _number(setup["duration_seconds"], "setup duration")
        metadata = setup["runtime_metadata"]
        if case["status"] == "success":
            _require(
                all(metadata[k] == v for k, v in expected_metadata.items()),
                "runtime circuit metadata mismatch",
            )
        _require(
            metadata["threads"] == execution["threads_effective"] == 1,
            "worker did not use one thread",
        )
        _require(metadata["version"] == simulator["version"], "runtime version mismatch")
        _number(execution["batch_size_effective"], "effective batch", integer=True, positive=True)
        _require(
            execution["batch_size_effective"] <= execution["shots_per_call"],
            "effective batch exceeds public call size",
        )
        if "scheduler" in execution["options"] and case["status"] == "success":
            _require(
                type(metadata["scheduler_statistics"]["applied"]) is bool,
                "missing scheduler statistics",
            )
        if simulator["adapter"] == "tsim" and case["status"] == "success":
            _require(
                metadata["sampling_backend"] == "active_statevector"
                and metadata["installed_source_commit"] == simulator["commit_sha"]
                and metadata["trajectory_sharing"]
                is execution["options"].get("trajectory_sharing", True),
                "invalid Tsim runtime provenance",
            )
            _require(
                case["warmup"]["attempted_shots"] >= execution["shots_per_call"],
                "Tsim requires a full-call warmup",
            )
    if case["status"] != "interrupted" or setup is not None:
        _validate_calibration_record(contract, case)
    for repetition, sample in enumerate(case["samples"]):
        _counts(sample, execution["postselection"])
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
                affinity["applied"] is True
                and affinity["available_after"] == [campaign["resources"]["logical_cpu"]],
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
        "shots_per_call",
        "mode",
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
                "mode": execution["mode"],
                "shots_per_call": execution["shots_per_call"],
                "batch_size_effective": execution.get("batch_size_effective", ""),
                "tuning_policy": execution.get("tuning_policy", "fixed"),
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
                    for k in ("compile_seconds", "peak_active_width", "kernel_warmup_seconds")
                },
                "warmup_seconds": (case["warmup"] or {}).get("duration_seconds", ""),
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


def _validate_calibration_record(expected: dict, observed: dict[str, Any]) -> None:
    if expected["execution"]["batch_size"] != "calibrate":
        return

    case_id = expected["id"]
    if observed["execution"].get("tuning_policy") != CALIBRATION_POLICY:
        raise ValueError(f"raw case {case_id!r} has mismatched tuning policy")
    setup = observed.get("setup")
    # A worker can fail during calibration or preparation before returning any
    # setup metadata. Preserve that explicit failure without inventing a winner.
    if (
        setup is None
        and observed["status"] == "error"
        and (observed.get("error") or {}).get("phase") == "setup"
    ):
        return
    calibration = (
        setup.get("runtime_metadata", {}).get("batch_calibration")
        if isinstance(setup, dict)
        else None
    )
    if not isinstance(calibration, dict):
        raise ValueError(f"calibrated raw case {case_id!r} has no batch_calibration metadata")

    selected = calibration.get("selected_batch_size")
    if type(selected) is not int or selected < 1:
        raise ValueError(
            f"calibrated raw case {case_id!r} has invalid selected_batch_size {selected!r}"
        )
    shots_per_call = int(expected["shots_per_call"])
    if calibration.get("policy") != CALIBRATION_POLICY:
        raise ValueError(f"calibrated raw case {case_id!r} has mismatched tuning policy")
    expected_candidates = calibration_candidates(
        observed["simulator"]["adapter"],
        shots_per_call,
    )
    if calibration.get("candidates") != expected_candidates:
        raise ValueError(f"calibrated raw case {case_id!r} candidates do not match the contract")
    if selected not in expected_candidates:
        raise ValueError(f"calibrated raw case {case_id!r} selected an unsupported batch_size")
    if observed["execution"].get("batch_size") != selected:
        raise ValueError(
            f"calibrated raw case {case_id!r} does not record selected numeric batch_size"
        )
    _validate_bounded_calibration(calibration, case_id)


def _validate_bounded_calibration(calibration: dict[str, Any], case_id: str) -> None:
    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise ValueError(f"calibrated raw case {case_id!r}: {detail}")

    require(
        calibration.get("budget_seconds") == TUNING_BUDGET_SECONDS
        and calibration.get("budget_scope") == "between-candidates",
        "invalid tuning budget",
    )
    require(
        calibration.get("probe_seconds") == 1.0 and calibration.get("repetitions") == 3,
        "invalid tuning probe settings",
    )
    require(
        calibration.get("selection_statistic") == "median_attempted_shots_per_second"
        and calibration.get("tie_break") == "smaller_batch_size",
        "invalid tuning selection rule",
    )
    search_seconds = calibration.get("search_seconds")
    require(
        type(search_seconds) in (int, float)
        and math.isfinite(search_seconds)
        and search_seconds >= 0,
        "missing tuning duration",
    )
    rows = calibration.get("results")
    require(
        isinstance(rows, list) and len(rows) == len(calibration["candidates"]),
        "missing candidate outcomes",
    )
    successful = set()
    seen_skip = False
    for batch, row in zip(calibration["candidates"], rows, strict=True):
        require(
            isinstance(row, dict) and row.get("batch_size") == batch, "mismatched candidate order"
        )
        status = row.get("status")
        require(status in {"success", "error", "budget-skipped"}, "invalid candidate status")
        require(
            not seen_skip or status == "budget-skipped", "candidate attempted after budget skip"
        )
        duration = row.get("duration_seconds")
        require(
            type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
            "invalid candidate duration",
        )
        samples = row.get("samples")
        require(isinstance(samples, list), "missing candidate probes")
        if status == "budget-skipped":
            seen_skip = True
            require(not samples, "skipped candidate has timed samples")
        elif status == "error":
            require(isinstance(row.get("error"), dict), "candidate failure lacks an error")
        else:
            require(len(samples) == calibration["repetitions"], "incomplete candidate probes")
            effective = row.get("effective_batch_size")
            require(
                type(effective) is int and 1 <= effective <= batch, "invalid effective batch size"
            )
            rate = row.get("median_attempted_shots_per_second")
            require(
                type(rate) in (float, int) and math.isfinite(rate) and rate > 0,
                "invalid candidate median",
            )
            successful.add(batch)
        for sample in samples:
            require(isinstance(sample, dict), "invalid candidate probe")
            rate = sample.get("throughput_attempted_shots_per_second")
            require(
                type(rate) in (float, int) and math.isfinite(rate) and rate > 0,
                "invalid candidate throughput",
            )
            require(
                type(sample.get("peak_rss_bytes")) is int and sample["peak_rss_bytes"] > 0,
                "missing tuning memory measurement",
            )
    # Validate complete evidence and a usable selection, without replaying the
    # recorded medians, winner selection, or elapsed-budget decisions.
    require(
        calibration["selected_batch_size"] in successful, "selected batch has no successful probes"
    )
