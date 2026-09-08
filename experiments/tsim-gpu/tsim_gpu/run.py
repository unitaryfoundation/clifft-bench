"""Resume-safe controller with hard process deadlines, independent of the CPU campaign."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .common import (
    EXPERIMENT,
    ROOT,
    WORKLOADS,
    digest,
    now,
    worker_environment,
    workloads,
    write_json,
)

DEFAULTS = {
    "strategies": ["cutting", "cat5", "bss"],
    "batch_sizes": [1024, 8192, 65536],
    "shots_per_call": 65536,
    "compile_timeout_seconds": 120.0,
    "prepare_budget_seconds": 600.0,
    "call_timeout_seconds": 120.0,
    "import_timeout_seconds": 60.0,
    "probe_seconds": 1.0,
    "probe_repetitions": 3,
    "sample_seconds": 30.0,
    "repetitions": 5,
    "max_rss_gib": 12.0,
    "max_output_mib": 256.0,
    "seed": 20260908,
}


def validate_options(options: dict) -> None:
    for key, value in options.items():
        if key not in {"strategies", "batch_sizes"} and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"{key} must be positive")
    if not options["batch_sizes"] or any(b <= 0 for b in options["batch_sizes"]):
        raise ValueError("batch sizes must be positive")
    if not options["strategies"] or set(options["strategies"]) - {"cat5", "bss", "cutting"}:
        raise ValueError("strategies must be cat5, bss, or cutting")
    if len(set(options["strategies"])) != len(options["strategies"]):
        raise ValueError("duplicate strategies")
    if options["seed"] + len(WORKLOADS) * 3 >= 2**32:
        raise ValueError("seed exceeds the unsigned 32-bit space")


def read_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def stop(process) -> None:
    # Native compilers/JIT may ignore Python alarms. Kill the whole process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def supervise(command: list[str], directory: Path, request: dict, environment: dict) -> dict:
    import psutil

    options = request["options"]
    checkpoint = directory / "checkpoint.json"
    started = time.monotonic()
    preparation_started = None
    preparation_elapsed = 0.0
    peak_rss = 0
    monitor_errors = set()
    reason = None
    last = {}
    with (directory / "worker.log").open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=EXPERIMENT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                last = read_checkpoint(checkpoint)
                current = time.monotonic()
                phase = last.get("phase", "import")
                phase_started = last.get("phase_started", started)
                if phase in {"compile", "tune"}:
                    if preparation_started is None:
                        preparation_started = phase_started
                    preparation_elapsed = current - preparation_started
                phase_limit = (
                    options["import_timeout_seconds"]
                    if phase == "import"
                    else options["compile_timeout_seconds"]
                    if phase == "compile"
                    else None
                )
                if phase_limit is not None and current - phase_started > phase_limit:
                    reason = ("timeout", f"{phase} exceeded {phase_limit:g}s")
                elif (
                    phase in {"compile", "tune"}
                    and preparation_elapsed > request["remaining_prepare_seconds"]
                ):
                    reason = ("budget-exhausted", "circuit preparation budget exhausted")
                elif (
                    last.get("call_started") is not None
                    and current - last["call_started"] > options["call_timeout_seconds"]
                ):
                    reason = ("timeout", "public sampling/JIT call exceeded its deadline")
                # Bound even hangs between public calls or while writing metadata.
                elif phase == "measure" and current - phase_started > options["repetitions"] * (
                    options["sample_seconds"] + options["call_timeout_seconds"] + 10
                ):
                    reason = ("timeout", "measurement phase exceeded its deadline")
                try:
                    parent = psutil.Process(process.pid)
                    processes = [parent, *parent.children(recursive=True)]
                    rss = sum(p.memory_info().rss for p in processes if p.is_running())
                    peak_rss = max(peak_rss, rss)
                    if rss > options["max_rss_gib"] * 2**30:
                        reason = (
                            "memory-limit",
                            "worker process-tree RSS exceeded configured limit",
                        )
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, PermissionError) as error:
                    monitor_errors.add(str(error))
                if reason:
                    stop(process)
                    break
                time.sleep(0.2)
        finally:
            if process.poll() is None:
                stop(process)
    result = read_checkpoint(checkpoint) or last
    result.setdefault("workload", request["workload"])
    result.setdefault("strategy", request["strategy"])
    if reason:
        result.update(
            status=reason[0], error={"phase": result.get("phase", "import"), "message": reason[1]}
        )
    elif process.returncode != 0 or result.get("status") != "success":
        result.setdefault(
            "error",
            {
                "phase": result.get("phase", "import"),
                "message": f"worker exited with status {process.returncode}",
            },
        )
        result["status"] = "error"
    # Prefer worker timings if phases completed faster than the polling interval.
    preparation_elapsed = max(
        preparation_elapsed, result.get("compile_seconds", 0) + result.get("tune_seconds", 0)
    )
    result.update(
        preparation_seconds=preparation_elapsed,
        peak_process_tree_rss_bytes=peak_rss,
        memory_monitor_errors=sorted(monitor_errors),
        wall_seconds=time.monotonic() - started,
        exit_code=process.returncode,
        finished_at=now(),
    )
    return result


def choose_strategy(results: list[dict]) -> dict | None:
    successful = [r for r in results if r["status"] == "success"]
    # Select on independent warm probes; final repetitions are never used to pick a winner.
    return max(successful, key=lambda r: r["selection_probe_rate"], default=None)


def run_attempt(
    case_dir: Path, request: dict, environment: dict, signature: str
) -> tuple[dict, Path]:
    attempt = case_dir / f"attempt-{len(list(case_dir.glob('attempt-*'))) + 1:03d}"
    attempt.mkdir()
    request_path = attempt / "request.json"
    write_json(request_path, request)
    options, workload = request["options"], request["workload"]
    expected = workload["expected_metadata"]
    output_bytes = options["shots_per_call"] * (
        expected["num_detectors"] + expected["num_observables"]
    )
    remaining = request["remaining_prepare_seconds"]
    if remaining <= 0 or output_bytes > options["max_output_mib"] * 2**20:
        result = {
            "status": "budget-exhausted" if remaining <= 0 else "output-limit",
            "workload": workload,
            "strategy": request["strategy"],
            "preparation_seconds": 0,
            "error": {
                "phase": "preflight",
                "message": "no preparation budget"
                if remaining <= 0
                else "unpacked output exceeds configured allocation bound",
            },
        }
    else:
        print(
            f"{workload['id']} / {request['strategy']}: {remaining:.0f}s preparation budget",
            flush=True,
        )
        result = supervise(
            [
                sys.executable,
                "-m",
                "tsim_gpu.worker",
                str(request_path),
                str(attempt / "checkpoint.json"),
            ],
            attempt,
            request,
            environment,
        )
    result.update(
        fingerprint=signature,
        strategy_preparation_seconds=request.get("prior_preparation_seconds", 0)
        + result["preparation_seconds"],
    )
    if "recovery_of" in request:
        result.update(
            recovery_of=request["recovery_of"], recovery_batch_sizes=request["recovery_batch_sizes"]
        )
    write_json(attempt / "result.json", result)
    return result, attempt


def collect_strategy(
    case_dir: Path, request: dict, environment: dict, signature: str, *, retry_failures: bool
) -> dict:
    paths = sorted(case_dir.glob("attempt-*/result.json"))
    cached = read_checkpoint(paths[-1]) if paths else None
    if cached and (cached["status"] == "success" or not retry_failures):
        result, attempt = cached, paths[-1].parent
        result.setdefault("strategy_preparation_seconds", result["preparation_seconds"])
    else:
        result, attempt = run_attempt(case_dir, request, environment, signature)
    spent = result["strategy_preparation_seconds"]
    remaining = request["remaining_prepare_seconds"] - spent
    completed = sorted(
        {
            candidate["batch_size"]
            for candidate in result.get("tuning", [])
            if candidate.get("status") == "success"
        }
    )
    if (
        result["status"] == "timeout"
        and result.get("error", {}).get("phase") == "tune"
        and not result.get("recovery_of")
        and completed
        and remaining > 0
    ):
        print(f"  recovering {request['strategy']} using completed batches {completed}", flush=True)
        result, _ = run_attempt(
            case_dir,
            {
                **request,
                "remaining_prepare_seconds": remaining,
                "prior_preparation_seconds": spent,
                "recovery_of": attempt.name,
                "recovery_batch_sizes": completed,
            },
            environment,
            signature,
        )
    return result


def collect(
    output: Path,
    options: dict,
    selected_ids: list[str],
    *,
    allow_dirty: bool,
    retry_failures: bool,
    host_label: str,
) -> None:
    validate_options(options)
    corpus = workloads()
    if set(selected_ids) - set(corpus):
        raise ValueError("unknown workload requested")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("duplicate workloads requested")
    environment = worker_environment()
    preflight = output / "preflight.json"
    with (output / "preflight.log").open("w") as log:
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tsim_gpu.worker",
                    "preflight",
                    str(preflight),
                    *(["--allow-dirty"] if allow_dirty else []),
                ],
                cwd=EXPERIMENT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=options["import_timeout_seconds"],
            )
        except (subprocess.SubprocessError, OSError) as error:
            raise RuntimeError(
                f"GPU preflight failed; inspect {output / 'preflight.log'}"
            ) from error
    hardware = read_checkpoint(preflight)
    # Absolute paths are not circuit identity. Moving a checkout is resume-safe.
    identities = {
        key: {k: v for k, v in corpus[key].items() if k != "artifact_path"} for key in selected_ids
    }
    signature = digest(
        {
            "hardware": hardware,
            "options": options,
            "workloads": identities,
            "host_label": host_label,
        }
    )
    metadata_path = output / "metadata.json"
    if metadata_path.exists():
        metadata = read_checkpoint(metadata_path)
        if metadata["fingerprint"] != signature:
            raise ValueError(
                "resume fingerprint changed (code/software/hardware/options/circuits); "
                "choose a new output directory"
            )
    else:
        metadata = {
            "schema_version": "clifft-bench/tsim-gpu-execution/v1",
            "created_at": now(),
            "fingerprint": signature,
            "hardware": hardware,
            "options": options,
            "workload_ids": selected_ids,
            "host_label": host_label,
        }
        write_json(metadata_path, metadata)
    summary = {"fingerprint": signature, "workloads": []}
    for workload_id in selected_ids:
        workload = corpus[workload_id]
        remaining = options["prepare_budget_seconds"]
        results = []
        strategies = options["strategies"]
        # For the paper's direct and distillation cases, try the current default first.
        if workload_id.startswith(("surface-code", "distillation")):
            strategies = sorted(strategies, key=lambda s: s != "cat5")
        for strategy in strategies:
            case_dir = output / "raw" / workload_id / strategy
            case_dir.mkdir(parents=True, exist_ok=True)
            request = {
                "workload": workload,
                "strategy": strategy,
                "options": options,
                "seed": options["seed"]
                + WORKLOADS.index(workload_id) * 3
                + ["cat5", "bss", "cutting"].index(strategy),
                "remaining_prepare_seconds": remaining,
            }
            result = collect_strategy(
                case_dir, request, environment, signature, retry_failures=retry_failures
            )
            remaining -= result["strategy_preparation_seconds"]
            results.append(result)
            print(f"  {strategy}: {result['status']}", flush=True)
        chosen = choose_strategy(results)
        summary["workloads"].append(
            {
                "workload_id": workload_id,
                "artifact_sha256": workload["artifact"]["sha256"],
                "semantics": workload["semantics"],
                "status": "success" if chosen else "no-success",
                "selected_strategy": chosen["strategy"] if chosen else None,
                "selected_batch_size": chosen["selected_batch_size"] if chosen else None,
                "selection_rule": "highest warm-probe median; independent final samples",
                "summary": chosen["summary"] if chosen else None,
                "runtime_metadata": chosen.get("runtime_metadata") if chosen else None,
                "strategies": [
                    {
                        "strategy": r["strategy"],
                        "status": r["status"],
                        "error": r.get("error"),
                        "compile_seconds": r.get("compile_seconds"),
                        "preparation_seconds": r["strategy_preparation_seconds"],
                    }
                    for r in results
                ],
            }
        )
        write_json(output / "summary.json", summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workload", action="append", choices=WORKLOADS)
    parser.add_argument("--host-label", required=True, help="e.g. provider, instance type, region")
    parser.add_argument("--allow-dirty", action="store_true", help="development results only")
    parser.add_argument(
        "--retry-failures",
        action="store_true",
        help="append new attempts; successful strategies are still reused",
    )
    for key, value in DEFAULTS.items():
        parser.add_argument(
            "--" + key.replace("_", "-"),
            default=value,
            type=str if key == "strategies" else int if isinstance(value, (int, list)) else float,
            nargs="+" if isinstance(value, list) else None,
        )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("spool results outside the checkout to preserve clean source identity")
    output.mkdir(parents=True, exist_ok=True)
    # An interrupted controller leaves the lock file, but the OS releases its flock.
    with (output / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        collect(
            output,
            {key: getattr(args, key) for key in DEFAULTS},
            args.workload or list(WORKLOADS),
            allow_dirty=args.allow_dirty,
            retry_failures=args.retry_failures,
            host_label=args.host_label,
        )


if __name__ == "__main__":
    main()
