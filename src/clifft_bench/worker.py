"""Line-oriented worker used to isolate simulator Python environments."""

from __future__ import annotations

import json
import os
import signal
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
from clifft_bench.system import apply_address_space_limit, apply_affinity, utc_now


class WorkerTimeout(TimeoutError):
    pass


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


def aggregate_sample(
    prepared: Any,
    *,
    shots_per_call: int,
    min_seconds: float,
    seed: int,
    postselect: bool,
    max_api_calls: int,
) -> dict[str, Any]:
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
        "api_calls": calls,
        **total.as_dict(),
        "throughput_attempted_shots_per_second": total.attempted_shots / elapsed,
        "seed_first": seed,
        "seed_last": seed + calls - 1,
        "adapter_call_timings": [],
        "adapter_timing_totals": dict(sorted(adapter_timing_totals.items())),
    }


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
                        prepared = adapter.prepare(
                            artifact_path=Path(request["artifact_path"]),
                            workload=workload,
                            execution=request["execution"],
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
                counts, duration = timed_sample(
                    prepared, int(request["shots"]), int(request["seed"])
                )
                errors = validate_counts(
                    counts,
                    postselect=bool(workload["semantics"]["postselect_all_detectors"]),
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
                            postselect=bool(workload["semantics"]["postselect_all_detectors"]),
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
                },
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
