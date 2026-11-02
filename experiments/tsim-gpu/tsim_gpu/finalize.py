"""Prepare an immutable GPU results directory for a normal GitHub data PR."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import re
import shutil
import tempfile
from pathlib import Path

from .common import EXPERIMENT, write_json
from .run import workload_summary

TERMINAL_STATUSES = {
    "success",
    "error",
    "timeout",
    "memory-limit",
    "budget-exhausted",
    "output-limit",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def validate_execution(source: Path) -> dict:
    metadata = read(source / "metadata.json")
    if metadata["hardware"]["source_dirty"]:
        raise ValueError("cannot finalize results collected from a dirty checkout")
    fingerprint = metadata["fingerprint"]
    summary = {"fingerprint": fingerprint, "workloads": []}
    declared = metadata["workloads"]
    if not declared or len({w["id"] for w in declared}) != len(declared):
        raise ValueError("workloads must be nonempty and unique")
    for workload in declared:
        workload_id = workload["id"]
        results = []
        for strategy in metadata["options"]["strategies"]:
            directory = source / "raw" / workload_id / strategy
            attempts = sorted(directory.glob("attempt-*"))
            if not attempts or not (attempts[-1] / "result.json").is_file():
                raise ValueError(
                    f"incomplete strategy: {workload_id}/{strategy}; resume collection"
                )
            for attempt in attempts:
                path = attempt / "result.json"
                if not path.exists():  # Preserve interrupted older attempts as evidence.
                    continue
                result = read(path)
                if result["fingerprint"] != fingerprint:
                    raise ValueError(f"result fingerprint mismatch: {path}")
                if (
                    result["status"] not in TERMINAL_STATUSES
                    or result["strategy"] != strategy
                    or result["workload"]["id"] != workload_id
                ):
                    raise ValueError(f"invalid result identity or status: {path}")
                if (
                    result["workload"]["artifact"]["sha256"] != workload["artifact"]["sha256"]
                    or result["workload"]["semantics"] != workload["semantics"]
                ):
                    raise ValueError(f"workload digest or semantics mismatch: {path}")
                if result["status"] == "success":
                    samples = result["samples"]
                    if len(samples) != metadata["options"]["repetitions"]:
                        raise ValueError(f"incomplete samples: {path}")
                    if not math.isfinite(result["selection_probe_rate"]) or (
                        result["selection_probe_rate"] <= 0
                    ):
                        raise ValueError(f"invalid selection probe: {path}")
                    for sample in samples:
                        for key in ("api_calls", "attempted_shots", "accepted_shots",
                                    "discarded_shots", "logical_errors"):
                            if type(sample[key]) is not int or sample[key] < 0:
                                raise ValueError(f"invalid aggregate counts: {path}")
                        if sample["api_calls"] < 1 or any(
                            not math.isfinite(sample[key]) or sample[key] <= 0
                            for key in ("duration_seconds", "throughput_attempted_shots_per_second")
                        ):
                            raise ValueError(f"invalid sample timing: {path}")
                        if (
                            sample["attempted_shots"]
                            != sample["api_calls"] * metadata["options"]["shots_per_call"]
                            or sample["attempted_shots"]
                            != sample["accepted_shots"] + sample["discarded_shots"]
                            or not 0 <= sample["logical_errors"] <= sample["accepted_shots"]
                        ):
                            raise ValueError(f"invalid aggregate counts: {path}")
                        rate = sample["attempted_shots"] / sample["duration_seconds"]
                        if rate != sample["throughput_attempted_shots_per_second"]:
                            raise ValueError(f"invalid throughput: {path}")
            results.append(read(attempts[-1] / "result.json"))
        summary["workloads"].append(workload_summary(workload, results))
    return summary


def finalize(source: Path, execution_id: str, *, output_root: Path | None = None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", execution_id):
        raise ValueError("execution ID must be a simple file name")
    source = source.resolve()
    target = (output_root or EXPERIMENT / "results") / execution_id
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing results: {target}")
    if target.resolve().is_relative_to(source):
        raise ValueError("results destination must be outside the spool")
    # Acquire the collector's lock while checking and copying the evidence.
    with (source / "collection.lock").open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("collection is still running; finish or interrupt it first") from error
        if any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError("result spool must not contain symlinks")
        summary = validate_execution(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{execution_id}-", dir=target.parent
        ) as temporary:
            staged = Path(temporary) / execution_id
            shutil.copytree(
                source, staged,
                ignore=shutil.ignore_patterns("collection.lock", "summary.json", "*.tmp"),
            )
            write_json(staged / "summary.json", summary)
            staged.rename(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="completed external result spool")
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    target = finalize(args.source, args.execution_id)
    print(f"Prepared reviewable execution: {target}")
    print("Review the results, then git add, commit, and push them from your data branch.")


if __name__ == "__main__":
    main()
