"""Bounded calibration and collection, with immutable attempts and resume checks."""

from __future__ import annotations

import argparse
import copy
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .common import (
    EXPERIMENT,
    ROOT,
    TOOLS,
    candidates,
    digest,
    environment,
    matrix,
    memory_skip,
    now,
    provenance,
    read,
    stream_seed,
    write,
)

DEFAULTS = {
    "workers": 64,
    "max_rss_gib": 224.0,
    "max_output_gib": 16.0,
    "setup_timeout_seconds": 180.0,
    "call_timeout_seconds": 120.0,
    "candidate_timeout_seconds": 600.0,
    "collection_timeout_seconds": 10800.0,
    "probe_seconds": 1.0,
    "probe_repetitions": 3,
    "sample_seconds": 30.0,
    "repetitions": 5,
    "seed": 20260909,
}


def fingerprint(metadata):
    value = copy.deepcopy(metadata)
    value.pop("created_at", None)
    value.pop("fingerprint", None)
    value["provenance"]["host"].pop("load_average_at_start", None)
    if value["provenance"].get("cgroup"):
        value["provenance"]["cgroup"].pop("path", None)
    return digest(value)


def stop(process):
    # Descendant pools inherit the session's process group. Kill the group even
    # if its leader already exited, so failed imports cannot leave orphan workers.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def supervise(command, directory, request, *, deadline):
    started = time.monotonic()
    peak = 0
    interrupted = False
    failure = None
    checkpoint = directory / "checkpoint.json"
    options = request["options"]
    with (directory / "worker.log").open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=EXPERIMENT,
            env=environment(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                state = read(checkpoint) if checkpoint.exists() else {}
                current = time.monotonic()
                if current > min(deadline, started + options["candidate_timeout_seconds"]):
                    failure = ("timeout", "candidate or collection deadline exceeded")
                elif current > state.get("deadline", started + options["setup_timeout_seconds"]):
                    failure = ("timeout", f"{state.get('phase', 'startup')} deadline exceeded")
                else:
                    try:
                        parent = psutil.Process(process.pid)
                        members = [parent, *parent.children(recursive=True)]
                        rss = 0
                        for member in members:
                            try:
                                rss += member.memory_info().rss
                            except psutil.NoSuchProcess:
                                pass
                        peak = max(peak, rss)
                        if rss > options["max_rss_gib"] * 2**30:
                            failure = ("memory-limit", "aggregate process-tree RSS exceeded budget")
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.Error, PermissionError) as error:
                        failure = ("error", f"cannot monitor process tree: {error}")
                if failure:
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            interrupted = True
            failure = ("interrupted", "operator interrupted collection")
        finally:
            stop(process)
    path = directory / "worker-result.json"
    result = read(path) if path.exists() else (read(checkpoint) if checkpoint.exists() else {})
    if failure:
        result.update(status=failure[0], error=failure[1])
    elif result.get("status") != "success" or process.returncode != 0:
        result.update(
            status="error", error=result.get("error", f"worker exited {process.returncode}")
        )
    result.update(
        fingerprint=request["fingerprint"],
        case_id=request["case"]["id"],
        wall_seconds=time.monotonic() - started,
        peak_process_tree_rss_bytes=peak,
        exit_code=process.returncode,
    )
    result.pop("deadline", None)
    write(directory / "result.json", result)
    if interrupted:
        raise KeyboardInterrupt
    return result


def attempt(base, request, retry, deadline):
    existing = sorted(base.glob("attempt-*"))
    if existing and (existing[-1] / "result.json").exists():
        result = read(existing[-1] / "result.json")
        if result["fingerprint"] != request["fingerprint"]:
            raise ValueError("attempt fingerprint differs from collection")
        if result["status"] == "success" or (not retry and result["status"] != "interrupted"):
            return result, str((existing[-1] / "result.json"))
    directory = base / f"attempt-{len(existing) + 1:03d}"
    directory.mkdir(parents=True)
    write(directory / "request.json", request)
    result = supervise(
        [sys.executable, "-m", "whole_machine.worker", str(directory / "request.json")],
        directory,
        request,
        deadline=deadline,
    )
    return result, str(directory / "result.json")


def choose(probes):
    successes = [p for p in probes if p["result"]["status"] == "success"]
    if not successes:
        return None
    return max(
        successes,
        key=lambda p: (
            p["result"]["summary"]["median_attempted_shots_per_second"],
            -p["result"]["config"]["batch_size"],
            -p["result"]["config"]["shots_per_call"],
        ),
    )


def collect(spool, options, tools=TOOLS, workload_ids=None, *, development=False, retry=False):
    spool = spool.resolve()
    if spool.is_relative_to(ROOT):
        raise ValueError(
            "use a spool outside the checkout so results cannot dirty collection source"
        )
    cases = matrix(tools, workload_ids)
    if not cases:
        raise ValueError("no compatible cases selected")
    spool.mkdir(parents=True, exist_ok=True)
    with (spool / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = {
            "schema_version": "clifft-bench/whole-machine/v1",
            "created_at": now(),
            "options": options,
            "cases": cases,
            "provenance": provenance(options, development),
        }
        fp = fingerprint(metadata)
        metadata["fingerprint"] = fp
        if (spool / "metadata.json").exists():
            if read(spool / "metadata.json")["fingerprint"] != fp:
                raise ValueError("configuration/source/host changed: use a new spool")
        else:
            write(spool / "metadata.json", metadata)
        summary = (
            read(spool / "summary.json")
            if (spool / "summary.json").exists()
            else {"fingerprint": fp, "cases": []}
        )
        if summary["fingerprint"] != fp:
            raise ValueError("summary fingerprint mismatch")
        rows = {r["case_id"]: r for r in summary["cases"]}
        deadline = time.monotonic() + options["collection_timeout_seconds"]
        for case in cases:
            if time.monotonic() >= deadline:
                raise TimeoutError("collection budget exhausted; resume to finish remaining cases")
            old = rows.get(case["id"])
            if old and old["status"] == "success":
                print(f"Reuse {case['id']}", flush=True)
                continue
            print(f"Calibrate {case['id']} ({options['workers']} workers)", flush=True)
            base = {
                "fingerprint": fp,
                "case": case,
                "options": options,
                "cpus": metadata["provenance"]["cpus"],
                "development": development,
            }
            probes = []
            for index, config in enumerate(candidates(case, options)):
                if time.monotonic() >= deadline:
                    raise TimeoutError("collection budget exhausted; resume the same spool")
                why = memory_skip(case, config, options)
                if why:
                    probes.append(
                        {
                            "candidate": index,
                            "result": {
                                "status": "memory-estimate-skip",
                                "config": config,
                                "error": why,
                            },
                        }
                    )
                    continue
                req = {
                    **base,
                    "config": config,
                    "kind": "probe",
                    "seed": stream_seed(options["seed"], case["id"], "probe", index),
                }
                result, path = attempt(
                    spool / "raw" / case["id"] / f"probe-{index:02d}", req, retry, deadline
                )
                probes.append(
                    {
                        "candidate": index,
                        "path": str(Path(path).relative_to(spool)),
                        "result": result,
                    }
                )
            selected = choose(probes)
            row = {
                "case_id": case["id"],
                "tool": case["tool"],
                "workload_id": case["workload"]["id"],
                "artifact_sha256": case["workload"]["artifact"]["sha256"],
                "probes": probes,
                "selected_candidate": None,
                "status": "no-success",
                "summary": None,
            }
            if selected:
                row["selected_candidate"] = selected["candidate"]
                req = {
                    **base,
                    "config": selected["result"]["config"],
                    "kind": "final",
                    "seed": stream_seed(
                        options["seed"], case["id"], "final", selected["candidate"]
                    ),
                }
                result, path = attempt(
                    spool / "raw" / case["id"] / f"final-{selected['candidate']:02d}",
                    req,
                    retry,
                    deadline,
                )
                row.update(
                    status=result["status"],
                    config=req["config"],
                    final_path=str(Path(path).relative_to(spool)),
                    summary=result.get("summary") if result["status"] == "success" else None,
                )
            rows[case["id"]] = row
            summary["cases"] = [rows[c["id"]] for c in cases if c["id"] in rows]
            write(spool / "summary.json", summary)
            print(f"  {row['status']}: {row['summary']}", flush=True)
        return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("spool", type=Path)
    p.add_argument("--tool", action="append", choices=TOOLS)
    p.add_argument(
        "--workload", action="append", choices=list({c["workload"]["id"] for c in matrix()})
    )
    p.add_argument(
        "--development", action="store_true", help="non-publication smoke; permits other hosts"
    )
    p.add_argument("--retry-failures", action="store_true")
    for key, default in DEFAULTS.items():
        p.add_argument("--" + key.replace("_", "-"), type=type(default), default=default)
    return p


def main():
    p = parser()
    args = p.parse_args()
    options = {key: getattr(args, key) for key in DEFAULTS}
    if any(value <= 0 for value in options.values()):
        p.error("all numerical settings must be positive")
    if options["max_rss_gib"] > 224:
        p.error("maximum aggregate memory budget is 224 GiB")
    try:
        summary = collect(
            args.spool,
            options,
            args.tool or TOOLS,
            args.workload,
            development=args.development,
            retry=args.retry_failures,
        )
    except KeyboardInterrupt:
        print("Interrupted; worker group stopped. Resume with the same command.", file=sys.stderr)
        raise SystemExit(130) from None
    if any(c["status"] != "success" for c in summary["cases"]):
        print("Some cases failed; review summary.json and worker logs before finalizing.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
