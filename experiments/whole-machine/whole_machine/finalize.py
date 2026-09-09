"""Validate complete evidence and copy it into a reviewable results directory."""

from __future__ import annotations

import argparse
import csv
import fcntl
import math
import re
import shutil
import tempfile
from pathlib import Path

from .common import (
    COUNT_KEYS,
    EXPERIMENT,
    PINNED,
    candidates,
    matrix,
    memory_skip,
    read,
    stream_seed,
    summarize,
    validate_counts,
)
from .run import choose, fingerprint

TERMINAL = {"success", "error", "timeout", "memory-limit", "memory-estimate-skip"}


def contained(source, relative):
    path = (source / relative).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ValueError("result path escapes spool")
    return path


def validate_result(result, request, metadata):
    if (
        request["options"] != metadata["options"]
        or request["cpus"] != metadata["provenance"]["cpus"]
        or request["development"]
    ):
        raise ValueError("raw request resource configuration mismatch")
    if (
        result["fingerprint"] != metadata["fingerprint"]
        or request["fingerprint"] != metadata["fingerprint"]
    ):
        raise ValueError("raw fingerprint mismatch")
    if result["case_id"] != request["case"]["id"] or result["status"] not in TERMINAL:
        raise ValueError("invalid raw identity/status")
    if result["status"] != "success":
        return
    options = metadata["options"]
    is_probe = request["kind"] == "probe"
    repetitions = options["probe_repetitions"] if is_probe else options["repetitions"]
    seconds = options["probe_seconds"] if is_probe else options["sample_seconds"]
    native = request["case"]["tool"] in {"clifft", "symft"}
    expected_workers = 1 if native else options["workers"]
    config = result["config"]
    if config["shots_per_call"] <= 0 or (not is_probe and config != request["config"]):
        raise ValueError("invalid or changed final configuration")
    for key in ("batch_size", "sample_chunk_shots"):
        if config[key] != request["config"][key]:
            raise ValueError("worker changed candidate batch/chunk")
    if len(result["samples"]) != repetitions or len(result["runtime"]) != expected_workers:
        raise ValueError("incomplete worker/sample coverage")
    for index, runtime in enumerate(result["runtime"]):
        cpus = metadata["provenance"]["cpus"] if native else [metadata["provenance"]["cpus"][index]]
        if runtime["affinity"] != cpus or runtime["metadata"]["threads"] != (
            options["workers"] if native else 1
        ):
            raise ValueError("incorrect effective worker resources")
        if request["case"]["tool"] == "tsim" and runtime["metadata"]["compiled_graph_count"] != 0:
            raise ValueError("CPU Tsim result contains GPU components")
    for sample in result["samples"]:
        validate_counts(sample)
        if not math.isfinite(sample["duration_seconds"]) or sample["duration_seconds"] < seconds:
            raise ValueError("invalid sample interval")
        if len(sample["workers"]) != expected_workers:
            raise ValueError("incomplete per-worker samples")
        if sample["shots_per_call"] != config["shots_per_call"]:
            raise ValueError("inconsistent public-call size")
        for worker in sample["workers"]:
            validate_counts(worker)
            if (
                worker["api_calls"] <= 0
                or worker["attempted_shots"] != worker["api_calls"] * config["shots_per_call"]
            ):
                raise ValueError("invalid per-worker shot accounting")
            if worker["ended"] < worker["started"] or worker["started"] < sample["scheduled_start"]:
                raise ValueError("invalid worker clock")
            if worker["ended"] - sample["scheduled_start"] > sample["duration_seconds"] + 1e-8:
                raise ValueError("sampling interval does not enclose all workers")
        for key in (*COUNT_KEYS, "api_calls"):
            if sample[key] != sum(w[key] for w in sample["workers"]):
                raise ValueError("aggregate disagrees with worker samples")
        rate = sample["attempted_shots"] / sample["duration_seconds"]
        if rate != sample["throughput_attempted_shots_per_second"]:
            raise ValueError("incorrect throughput")
    if result["summary"] != summarize(result["samples"]):
        raise ValueError("incorrect derived summary")


def validate_execution(source):
    metadata, summary = read(source / "metadata.json"), read(source / "summary.json")
    provenance = metadata["provenance"]
    if provenance["development"] or provenance["source"]["dirty"]:
        raise ValueError("development/dirty results cannot be finalized")
    if (
        fingerprint(metadata) != metadata["fingerprint"]
        or summary["fingerprint"] != metadata["fingerprint"]
    ):
        raise ValueError("execution fingerprint mismatch")
    options = metadata["options"]
    if (
        options["workers"] != 64
        or options["repetitions"] < 5
        or options["sample_seconds"] < 30
        or options["probe_repetitions"] < 3
        or options["probe_seconds"] < 1
    ):
        raise ValueError("publication evidence requires 64 workers, 3x1s probes and 5x30s samples")
    if (
        provenance["ec2"]["instanceType"] != "m8a.16xlarge"
        or provenance["ec2"]["region"] != "us-east-1"
    ):
        raise ValueError("unexpected reference instance")
    for name, (version, repo, commit) in PINNED.items():
        package = provenance["packages"][name]
        if package["version"] != version:
            raise ValueError("incorrect package pin")
        if commit and (
            package["direct_url"].get("vcs_info", {}).get("commit_id") != commit
            or package["direct_url"].get("url", "").removesuffix(".git") != repo
        ):
            raise ValueError("incorrect source pin")
    cases = {c["id"]: c for c in metadata["cases"]}
    rows = {r["case_id"]: r for r in summary["cases"]}
    expected_cases = {c["id"]: c for c in matrix()}
    if (
        set(cases) != set(expected_cases)
        or len(metadata["cases"]) != 20
        or set(cases) != set(rows)
        or len(rows) != len(summary["cases"])
    ):
        raise ValueError("full 20-case coverage is required; resume collection")
    for identifier, row in rows.items():
        case = cases[identifier]
        expected = expected_cases[identifier]
        # The collection host and the reviewing checkout may have different paths.
        recorded_workload = {k: v for k, v in case["workload"].items() if k != "artifact_path"}
        expected_workload = {k: v for k, v in expected["workload"].items() if k != "artifact_path"}
        if (
            recorded_workload != expected_workload
            or case["tool"] != expected["tool"]
            or row["tool"] != case["tool"]
            or row["workload_id"] != case["workload"]["id"]
            or row["artifact_sha256"] != case["workload"]["artifact"]["sha256"]
        ):
            raise ValueError("circuit identity mismatch")
        configs = candidates(case, options)
        if [p["candidate"] for p in row["probes"]] != list(range(len(configs))):
            raise ValueError("missing or duplicated candidate evidence")
        probes = []
        for probe in row["probes"]:
            config = configs[probe["candidate"]]
            if "path" in probe:
                path = contained(source, probe["path"])
                raw = read(path)
                req = read(path.parent / "request.json")
                if (
                    req["case"] != case
                    or req["kind"] != "probe"
                    or req["config"] != config
                    or req["seed"]
                    != stream_seed(options["seed"], identifier, "probe", probe["candidate"])
                ):
                    raise ValueError("probe request identity mismatch")
                validate_result(raw, req, metadata)
                if raw != probe["result"]:
                    raise ValueError("stale probe summary")
            elif (
                probe["result"]["status"] != "memory-estimate-skip"
                or probe["result"]["config"] != config
                or not memory_skip(case, config, options)
            ):
                raise ValueError("missing probe evidence")
            probes.append(probe)
        selected = choose(probes)
        if row["selected_candidate"] != (selected["candidate"] if selected else None):
            raise ValueError("selection differs from independent warm probes")
        if selected:
            path = contained(source, row["final_path"])
            result, req = read(path), read(path.parent / "request.json")
            if (
                req["case"] != case
                or req["kind"] != "final"
                or req["config"] != selected["result"]["config"]
                or row["config"] != req["config"]
                or req["seed"]
                != stream_seed(options["seed"], identifier, "final", selected["candidate"])
            ):
                raise ValueError("final run does not match selected probe configuration")
            validate_result(result, req, metadata)
            expected = result["summary"] if result["status"] == "success" else None
            if row["status"] != result["status"] or row["summary"] != expected:
                raise ValueError("stale final summary")
        elif row["status"] != "no-success" or row["summary"] is not None:
            raise ValueError("unsuccessful case has numerical evidence")
    return metadata, summary


def finalize(source, execution_id, output_root=None):
    source = source.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", execution_id):
        raise ValueError("execution ID must be a simple filename")
    target = (output_root or EXPERIMENT / "results") / execution_id
    if target.exists() or target.resolve().is_relative_to(source):
        raise ValueError("destination exists or is inside the spool")
    with (source / "collection.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("spool must not contain symlinks")
        metadata, summary = validate_execution(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            staged = Path(temp) / execution_id
            shutil.copytree(
                source, staged, ignore=shutil.ignore_patterns("collection.lock", "*.tmp")
            )
            columns = [
                "tool",
                "workload_id",
                "status",
                "workers",
                "hourly_usd",
                "batch_size",
                "sample_chunk_shots",
                "shots_per_call",
                "median_attempted_shots_per_second",
                "mad_attempted_shots_per_second",
                "attempted_shots_per_dollar",
            ]
            with (staged / "cases.csv").open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader()
                for r in summary["cases"]:
                    price = metadata["provenance"]["hourly_usd"]
                    values = {k: r[k] for k in ("tool", "workload_id", "status")}
                    values.update(workers=64, hourly_usd=price, **r.get("config", {}))
                    if r["summary"]:
                        values.update(r["summary"])
                        values["attempted_shots_per_dollar"] = (
                            r["summary"]["median_attempted_shots_per_second"] * 3600 / price
                        )
                    writer.writerow(values)
            staged.rename(target)
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("spool", type=Path)
    p.add_argument("--execution-id", required=True)
    args = p.parse_args()
    print(finalize(args.spool, args.execution_id))


if __name__ == "__main__":
    main()
