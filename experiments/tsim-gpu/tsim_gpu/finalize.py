"""Prepare an immutable GPU results directory for a normal GitHub data PR."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import shutil
import tempfile
from pathlib import Path

from .common import EXPERIMENT
from .run import choose_strategy
from .worker import summarize

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


def validate_execution(source: Path) -> None:
    metadata, summary = read(source / "metadata.json"), read(source / "summary.json")
    if metadata["hardware"]["source_dirty"]:
        raise ValueError("cannot finalize results collected from a dirty checkout")
    fingerprint = metadata["fingerprint"]
    if summary["fingerprint"] != fingerprint:
        raise ValueError("summary fingerprint differs from execution metadata")
    expected = metadata["workload_ids"]
    rows = {row["workload_id"]: row for row in summary["workloads"]}
    if set(rows) != set(expected) or len(rows) != len(summary["workloads"]):
        raise ValueError("incomplete workload coverage; resume collection before finalizing")
    for workload_id, row in rows.items():
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
                    result["workload"]["artifact"]["sha256"] != row["artifact_sha256"]
                    or result["workload"]["semantics"] != row["semantics"]
                ):
                    raise ValueError(f"workload digest or semantics mismatch: {path}")
                if result["status"] == "success":
                    samples = result["samples"]
                    if len(samples) != metadata["options"]["repetitions"]:
                        raise ValueError(f"incomplete samples: {path}")
                    for sample in samples:
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
                    if summarize(samples) != result["summary"]:
                        raise ValueError(f"incorrect sample summary: {path}")
            results.append(read(attempts[-1] / "result.json"))
        chosen = choose_strategy(results)
        if row["selected_strategy"] != (chosen["strategy"] if chosen else None) or row[
            "summary"
        ] != (chosen["summary"] if chosen else None):
            raise ValueError(f"stale strategy selection: {workload_id}; resume collection")


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
        validate_execution(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{execution_id}-", dir=target.parent
        ) as temporary:
            staged = Path(temporary) / execution_id
            shutil.copytree(
                source, staged, ignore=shutil.ignore_patterns("collection.lock", "*.tmp")
            )
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
