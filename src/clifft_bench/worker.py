"""Line-oriented worker used to isolate simulator Python environments."""

from __future__ import annotations

import json
import os
import resource
import signal
import statistics
import sys
import time
import traceback
from collections import defaultdict
from contextlib import contextmanager, redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterator, TextIO

from clifft_bench.adapters import load_adapter
from clifft_bench.adapters.base import Counts, validate_counts
from clifft_bench.calibration import (
    CALIBRATION_POLICY,
    TUNING_BUDGET_SECONDS,
    calibration_candidates,
)
from clifft_bench.selection import has_postselection
from clifft_bench.system import apply_address_space_limit, apply_affinity, utc_now

BATCH_CALIBRATION_REPETITIONS = 3
BATCH_CALIBRATION_SECONDS = 1.0
BATCH_CALIBRATION_MAX_API_CALLS = 1_000_000
BATCH_CALIBRATION_SEED_RESERVE = 4_000_000
SEED_MAX_EXCLUSIVE = 2**32


class WorkerTimeout(TimeoutError):
    pass


class CalibrationError(RuntimeError):
    def __init__(self, message: str, record: dict[str, Any]) -> None:
        super().__init__(message)
        self.record = record


_PROTOCOL_STREAM: TextIO | None = None


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    if not hasattr(signal, "SIGALRM") or seconds <= 0:
        yield
        return

    def expired(_signum: int, _frame: Any) -> None:
        raise WorkerTimeout(f"preparation exceeded {seconds:g} seconds")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def emit(message: dict[str, Any]) -> None:
    stream = _PROTOCOL_STREAM or sys.stdout
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def isolate_protocol_stream() -> None:
    """Keep JSON on a private fd and send all Python/native stdout to stderr."""
    global _PROTOCOL_STREAM
    protocol_fd = os.dup(sys.stdout.fileno())
    _PROTOCOL_STREAM = os.fdopen(
        protocol_fd,
        "w",
        buffering=1,
        encoding=sys.stdout.encoding or "utf-8",
        closefd=True,
    )
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


def timed_sample(prepared: Any, shots: int, seed: int) -> tuple[Counts, float]:
    started = time.perf_counter()
    with redirect_stdout(sys.stderr):
        counts = prepared.sample(shots, seed)
    return counts, time.perf_counter() - started


def begin_sample(prepared: Any, seed: int) -> float:
    hook = getattr(prepared, "begin_sample", None)
    if hook is None:
        return 0.0
    started = time.perf_counter()
    with redirect_stdout(sys.stderr):
        hook(seed)
    return time.perf_counter() - started


def aggregate_sample(
    prepared: Any,
    *,
    shots_per_call: int,
    min_seconds: float,
    seed: int,
    postselect: bool,
    max_api_calls: int,
) -> dict[str, Any]:
    stream_setup_seconds = begin_sample(prepared, seed)
    total = Counts(0, 0, 0, 0)
    calls = 0
    elapsed = 0.0
    adapter_timing_totals: dict[str, float] = defaultdict(float)
    started_at = utc_now()
    while calls == 0 or elapsed < min_seconds:
        if calls >= max_api_calls:
            raise RuntimeError(
                f"sample exceeded its non-overlapping seed range of {max_api_calls} calls"
            )
        counts, duration = timed_sample(prepared, shots_per_call, seed + calls)
        errors = validate_counts(counts, postselect=postselect)
        if errors:
            raise RuntimeError("adapter returned invalid counts: " + "; ".join(errors))
        total = Counts(
            total.attempted_shots + counts.attempted_shots,
            total.accepted_shots + counts.accepted_shots,
            total.discarded_shots + counts.discarded_shots,
            total.logical_errors + counts.logical_errors,
        )
        if counts.adapter_timing:
            for key, value in counts.adapter_timing.items():
                adapter_timing_totals[key] += float(value)
        elapsed += duration
        calls += 1
    return {
        "started_at": started_at,
        "duration_seconds": elapsed,
        "stream_setup_seconds": stream_setup_seconds,
        "api_calls": calls,
        **total.as_dict(),
        "throughput_attempted_shots_per_second": total.attempted_shots / elapsed,
        "seed_first": seed,
        "seed_last": seed + calls - 1,
        "adapter_timing_totals": dict(sorted(adapter_timing_totals.items())),
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        * (1 if sys.platform == "darwin" else 1024),
    }


def _calibration_seed(seed: int, repetition: int) -> int:
    if seed < 0 or seed + BATCH_CALIBRATION_SEED_RESERVE > SEED_MAX_EXCLUSIVE:
        raise ValueError("batch calibration seed range exceeds unsigned 32-bit space")
    return seed + repetition * BATCH_CALIBRATION_MAX_API_CALLS


def _candidate_execution(execution: dict[str, Any], batch_size: int) -> dict[str, Any]:
    return {
        **execution,
        "batch_enabled": batch_size > 1,
        "batch_size": batch_size,
    }


def calibrate_batch_size(
    adapter: Any,
    *,
    artifact_path: Path,
    workload: dict[str, Any],
    execution: dict[str, Any],
    shots_per_call: int,
    seed: int,
    sampling_seed: int = 0,
) -> Any:
    candidates = calibration_candidates(adapter.name, shots_per_call)
    postselect = has_postselection(workload["semantics"])
    calibration_started = time.perf_counter()
    results = []
    successful: list[tuple[float, int]] = []

    for candidate in candidates:
        if (results
                and time.perf_counter() - calibration_started >= TUNING_BUDGET_SECONDS):
            results.append({
                "batch_size": candidate, "status": "budget-skipped",
                "effective_batch_size": None, "median_attempted_shots_per_second": None,
                "duration_seconds": 0.0, "samples": [], "error": None,
            })
            continue
        candidate_started = time.perf_counter()
        candidate_execution = _candidate_execution(execution, candidate)
        prepared = None
        samples = []
        try:
            prepared = adapter.prepare(
                artifact_path=artifact_path,
                workload=workload,
                execution=candidate_execution,
                seed=_calibration_seed(seed, BATCH_CALIBRATION_REPETITIONS),
            )
            begin_sample(prepared, _calibration_seed(seed, BATCH_CALIBRATION_REPETITIONS))
            warm_counts, _ = timed_sample(
                prepared,
                shots_per_call,
                _calibration_seed(seed, BATCH_CALIBRATION_REPETITIONS),
            )
            warm_errors = validate_counts(warm_counts, postselect=postselect)
            if warm_errors:
                raise RuntimeError("invalid warmup counts: " + "; ".join(warm_errors))

            for repetition in range(BATCH_CALIBRATION_REPETITIONS):
                sample = aggregate_sample(
                    prepared,
                    shots_per_call=shots_per_call,
                    min_seconds=BATCH_CALIBRATION_SECONDS,
                    seed=_calibration_seed(seed, repetition),
                    postselect=postselect,
                    max_api_calls=BATCH_CALIBRATION_MAX_API_CALLS,
                )
                samples.append(
                    {
                        "repetition": repetition,
                        "duration_seconds": sample["duration_seconds"],
                        "stream_setup_seconds": sample["stream_setup_seconds"],
                        "api_calls": sample["api_calls"],
                        "attempted_shots": sample["attempted_shots"],
                        "seed_first": sample["seed_first"],
                        "seed_last": sample["seed_last"],
                        "peak_rss_bytes": sample["peak_rss_bytes"],
                        "throughput_attempted_shots_per_second": sample[
                            "throughput_attempted_shots_per_second"
                        ],
                    }
                )
            median_rate = statistics.median(
                sample["throughput_attempted_shots_per_second"] for sample in samples
            )
            results.append(
                {
                    "batch_size": candidate,
                    "status": "success",
                    "effective_batch_size": min(
                        int(prepared.runtime_metadata["effective_batch_size"]),
                        shots_per_call,
                    ),
                    "median_attempted_shots_per_second": median_rate,
                    "duration_seconds": time.perf_counter() - candidate_started,
                    "samples": samples,
                    "error": None,
                }
            )
            successful.append((median_rate, candidate))
        except WorkerTimeout:
            raise
        except Exception as error:  # noqa: BLE001
            results.append(
                {
                    "batch_size": candidate,
                    "status": "error",
                    "effective_batch_size": None,
                    "median_attempted_shots_per_second": None,
                    "duration_seconds": time.perf_counter() - candidate_started,
                    "samples": samples,
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )
        finally:
            # Native samplers may own gigabytes of state. Release each trial
            # before constructing the next trial or the selected configuration.
            prepared = None

    selected_batch_size = (
        max(successful, key=lambda item: (item[0], -item[1]))[1] if successful else None
    )
    search_seconds = time.perf_counter() - calibration_started
    record = {
        "policy": CALIBRATION_POLICY,
        "budget_seconds": TUNING_BUDGET_SECONDS,
        "search_seconds": search_seconds,
        "budget_scope": "between-candidates",
        "candidates": candidates,
        "probe_seconds": BATCH_CALIBRATION_SECONDS,
        "repetitions": BATCH_CALIBRATION_REPETITIONS,
        "selection_statistic": "median_attempted_shots_per_second",
        "tie_break": "smaller_batch_size",
        "selected_batch_size": selected_batch_size,
        "duration_seconds": time.perf_counter() - calibration_started,
        "results": results,
    }
    if selected_batch_size is None:
        raise CalibrationError("batch calibration failed for every candidate", record)
    try:
        prepared = adapter.prepare(
            artifact_path=artifact_path,
            workload=workload,
            execution=_candidate_execution(execution, selected_batch_size),
            seed=sampling_seed,
        )
    except WorkerTimeout:
        raise
    except Exception as error:
        record["duration_seconds"] = time.perf_counter() - calibration_started
        raise CalibrationError(f"selected batch preparation failed: {error}", record) from error
    record["duration_seconds"] = time.perf_counter() - calibration_started
    prepared.runtime_metadata["batch_calibration"] = record
    return prepared


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def main() -> int:
    isolate_protocol_stream()
    prepared = None
    workload: dict[str, Any] | None = None
    try:
        for line in sys.stdin:
            request = json.loads(line)
            command = request["command"]
            if command == "prepare":
                workload = request["workload"]
                affinity = apply_affinity(request.get("logical_cpu"))
                address_space_limit = apply_address_space_limit(
                    request.get("memory_limit_gib")
                )
                adapter = load_adapter(request["adapter"])
                started = time.perf_counter()
                with deadline(float(request["timeout_seconds"])):
                    with redirect_stdout(sys.stderr):
                        installation = adapter.verify_installation(
                            expected_commit=request["expected_commit"],
                            source_url=request["source_url"],
                        )
                        artifact_path = Path(request["artifact_path"])
                        execution = request["execution"]
                        if execution["batch_size"] == "calibrate":
                            prepared = calibrate_batch_size(
                                adapter,
                                artifact_path=artifact_path,
                                workload=workload,
                                execution=execution,
                                shots_per_call=int(request["shots_per_call"]),
                                seed=int(request["seed"]),
                                sampling_seed=int(request["sampling_seed"]),
                            )
                        else:
                            prepared = adapter.prepare(
                                artifact_path=artifact_path,
                                workload=workload,
                                execution=execution,
                                seed=int(request["sampling_seed"]),
                            )
                duration = time.perf_counter() - started
                expected_version = str(request["expected_version"])
                runtime_version = str(prepared.runtime_metadata["version"])
                if expected_version != runtime_version:
                    raise RuntimeError(
                        f"runtime version {runtime_version!r} does not match manifest "
                        f"version {expected_version!r}"
                    )
                runtime_metadata = {
                    **prepared.runtime_metadata,
                    **installation,
                    "address_space_limit_bytes": address_space_limit,
                }
                emit(
                    {
                        "ok": True,
                        "duration_seconds": duration,
                        "affinity": affinity,
                        "adapter_version": adapter.adapter_version,
                        "runtime_metadata": runtime_metadata,
                        "dependencies": {
                            name: _package_version(name)
                            for name in request["dependency_distributions"]
                        },
                    }
                )
            elif command in {"warmup", "correctness"}:
                if prepared is None or workload is None:
                    raise RuntimeError("worker has not been prepared")
                stream_setup_seconds = begin_sample(prepared, int(request["seed"]))
                counts, duration = timed_sample(
                    prepared, int(request["shots"]), int(request["seed"])
                )
                errors = validate_counts(
                    counts,
                    postselect=has_postselection(workload["semantics"]),
                )
                if command == "correctness":
                    expected = workload["expected_metadata"]
                    for key, value in expected.items():
                        actual = prepared.runtime_metadata.get(key)
                        if actual != value:
                            errors.append(f"{key}: expected {value!r}, got {actual!r}")
                emit(
                    {
                        "ok": True,
                        "duration_seconds": duration,
                        "stream_setup_seconds": stream_setup_seconds,
                        **counts.as_dict(),
                        "contract_errors": errors,
                    }
                )
            elif command == "sample":
                if prepared is None:
                    raise RuntimeError("worker has not been prepared")
                emit(
                    {
                        "ok": True,
                        **aggregate_sample(
                            prepared,
                            shots_per_call=int(request["shots_per_call"]),
                            min_seconds=float(request["min_seconds"]),
                            seed=int(request["seed"]),
                            postselect=has_postselection(workload["semantics"]),
                            max_api_calls=int(request["max_api_calls"]),
                        ),
                    }
                )
            elif command == "close":
                emit({"ok": True})
                return 0
            else:
                raise ValueError(f"unknown worker command {command!r}")
    except Exception as error:  # noqa: BLE001
        emit(
            {
                "ok": False,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                    **({"batch_calibration": error.record}
                       if isinstance(error, CalibrationError) else {}),
                },
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
