"""Serial benchmark orchestration and raw result emission."""

from __future__ import annotations

import json
import os
import queue
import re
import statistics
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from clifft_bench.manifest import Case, Suite
from clifft_bench.schedule import balanced_schedule
from clifft_bench.schema import repository_root, validate_document
from clifft_bench.system import (
    choose_cpu,
    collect_runner_metadata,
    collect_workflow_metadata,
    restricted_environment,
    utc_now,
)

SEED_REPETITION_STRIDE = 100_000_000
SEED_MAX_EXCLUSIVE = 2**32


class WorkerError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", "worker failed")))
        self.error = error


class WorkerClient:
    def __init__(self, case: Case, cpu: int | None) -> None:
        self.case = case
        environment = restricted_environment()
        source = str(repository_root() / "src")
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
        self.process = subprocess.Popen(
            [case.implementation.python_executable(), "-m", "clifft_bench.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.cpu = cpu
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()

    def _read_responses(self) -> None:
        if self.process.stdout is None:
            self._responses.put(None)
            return
        for line in self.process.stdout:
            self._responses.put(line)
        self._responses.put(None)

    def _stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def request(self, message: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("worker pipes are unavailable")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        try:
            line = self._responses.get(timeout=timeout_seconds)
        except queue.Empty as error:
            command = str(message.get("command", "request"))
            self._stop()
            raise WorkerError(
                {
                    "type": "WorkerTimeout",
                    "message": f"worker {command} exceeded {timeout_seconds:g} seconds",
                }
            ) from error
        if line is None:
            code = self.process.poll()
            raise WorkerError(
                {
                    "type": "WorkerExited",
                    "message": f"worker exited without a response (exit code {code})",
                }
            )
        response = json.loads(line)
        if not response.get("ok"):
            raise WorkerError(response["error"])
        return response

    def prepare(
        self, timeout_seconds: float, memory_limit_gib: float | None = None
    ) -> dict[str, Any]:
        definition = self.case.implementation.definition
        return self.request(
            {
                "command": "prepare",
                "adapter": definition["adapter"],
                "artifact_path": str(self.case.workload.artifact_path),
                "workload": self.case.workload.definition,
                "execution": self.case.definition["execution"],
                "expected_version": definition["version"],
                "dependency_distributions": definition["dependency_distributions"],
                "logical_cpu": self.cpu,
                "memory_limit_gib": memory_limit_gib,
                "timeout_seconds": timeout_seconds,
            },
            timeout_seconds=timeout_seconds + 5,
        )

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.request({"command": "close"}, timeout_seconds=5)
            except (BrokenPipeError, WorkerError, json.JSONDecodeError):
                self._stop()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _error_record(error: Exception, phase: str) -> dict[str, Any]:
    if isinstance(error, WorkerError):
        details = error.error
    else:
        details = {"type": type(error).__name__, "message": str(error)}
    return {"phase": phase, **details}


def _workload_record(case: Case) -> dict[str, Any]:
    definition = case.workload.definition
    return {
        "id": definition["id"],
        "family": definition["family"],
        "parameters": definition["parameters"],
        "semantics": definition["semantics"],
        "artifact": {
            "path": str(case.workload.artifact_path),
            "sha256": case.workload.artifact_sha256,
        },
        "provenance": definition["provenance"],
    }


def _simulator_record(case: Case) -> dict[str, Any]:
    definition = case.implementation.definition
    return {
        "implementation_id": definition["id"],
        "name": definition["name"],
        "version": definition["version"],
        "commit_sha": definition["commit_sha"],
        "commit_datetime": definition["commit_datetime"],
        "release_datetime": definition["release_datetime"],
        "source_url": definition["source_url"],
        "adapter": definition["adapter"],
        "python_executable": case.implementation.python_executable(),
        "build": definition["build"],
        "dependencies": {},
    }


def _new_case_result(
    case: Case,
    measurement: dict[str, Any],
    memory_limit_gib: float | None,
) -> dict[str, Any]:
    execution = dict(case.definition["execution"])
    execution.update(
        {
            "threads_requested": 1,
            "memory_limit_bytes": (
                int(memory_limit_gib * (1 << 30))
                if memory_limit_gib is not None
                else None
            ),
            "shots_per_call": case.definition["shots_per_call"],
            "min_sample_seconds": measurement["min_sample_seconds"],
            "request_timeout_seconds": measurement["request_timeout_seconds"],
            "repetitions": measurement["repetitions"],
            "postselection": bool(
                case.workload.definition["semantics"]["postselect_all_detectors"]
            ),
        }
    )
    return {
        "case_id": case.id,
        "pair_id": case.definition.get("pair_id"),
        "status": "running",
        "workload": _workload_record(case),
        "simulator": _simulator_record(case),
        "execution": execution,
        "setup": None,
        "warmup": None,
        "correctness": None,
        "samples": [],
        "summary": None,
        "error": None,
    }


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    rates = [float(sample["throughput_attempted_shots_per_second"]) for sample in samples]
    median = statistics.median(rates)
    deviations = [abs(rate - median) for rate in rates]
    return {
        "sample_count": len(samples),
        "median_attempted_shots_per_second": median,
        "mad_attempted_shots_per_second": statistics.median(deviations),
        "min_attempted_shots_per_second": min(rates),
        "max_attempted_shots_per_second": max(rates),
        "total_attempted_shots": sum(int(sample["attempted_shots"]) for sample in samples),
        "total_duration_seconds": sum(float(sample["duration_seconds"]) for sample in samples),
    }


def _select_cases(suite: Suite, pattern: str | None) -> list[Case]:
    if pattern is None:
        return list(suite.cases)
    expression = re.compile(pattern)
    return [case for case in suite.cases if expression.search(case.id)]


def _case_groups(cases: list[Case]) -> list[list[Case]]:
    """Keep declared comparison pairs resident together, one pair at a time."""
    groups: dict[str, list[Case]] = {}
    for case in cases:
        group_id = str(case.definition.get("pair_id", case.id))
        groups.setdefault(group_id, []).append(case)
    return list(groups.values())


def run_suite(
    suite: Suite,
    *,
    output_path: Path,
    case_pattern: str | None = None,
    cpu: int | None = None,
    memory_limit_gib: float | None = None,
    min_sample_seconds: float | None = None,
    repetitions: int | None = None,
) -> dict[str, Any]:
    measurement = dict(suite.run["measurement"])
    if min_sample_seconds is not None:
        measurement["min_sample_seconds"] = min_sample_seconds
    if repetitions is not None:
        measurement["repetitions"] = repetitions
    if measurement["min_sample_seconds"] <= 0 or measurement["repetitions"] < 1:
        raise ValueError("measurement overrides must be positive")
    if memory_limit_gib is not None and memory_limit_gib <= 0:
        raise ValueError("memory limit must be positive")
    seed_range_end = (
        int(suite.run["seed"])
        + 10_000
        + int(measurement["repetitions"]) * SEED_REPETITION_STRIDE
    )
    if seed_range_end > SEED_MAX_EXCLUSIVE:
        raise ValueError(
            "declared repetitions exceed the unsigned 32-bit benchmark seed space"
        )
    request_timeout = float(measurement["request_timeout_seconds"])
    if request_timeout <= measurement["min_sample_seconds"]:
        raise ValueError(
            "request_timeout_seconds must be greater than min_sample_seconds"
        )

    cases = _select_cases(suite, case_pattern)
    if not cases:
        raise ValueError("no cases matched the selection")
    logical_cpu = choose_cpu(cpu if cpu is not None else suite.run["resources"]["logical_cpu"])
    started_at = utc_now()
    document: dict[str, Any] = {
        "schema_version": "clifft-bench/result/v1",
        "suite_version": suite.run["suite_version"],
        "run": {
            "id": str(uuid.uuid4()),
            "profile_id": suite.run["profile_id"],
            "campaign_run_id": suite.run["run_id"],
            "classification": suite.run["classification"],
            "started_at": started_at,
            "finished_at": None,
            "run_manifest": str(suite.run_path),
            "workloads_manifest": str(suite.workloads_path),
            "software_manifest": str(suite.software_path),
            "selection": case_pattern,
            "schedule_policy": "serial-alternating-forward-reverse",
            "workflow": collect_workflow_metadata(),
        },
        "runner": collect_runner_metadata(repository_root()),
        "cases": [
            _new_case_result(case, measurement, memory_limit_gib) for case in cases
        ],
    }
    case_results = {item["case_id"]: item for item in document["cases"]}
    _atomic_write(output_path, document)

    try:
        preparation_index = 0
        sequence_index = 0
        for group in _case_groups(cases):
            clients: dict[str, WorkerClient] = {}
            try:
                for case in group:
                    preparation_index += 1
                    result = case_results[case.id]
                    print(f"[{preparation_index}/{len(cases)}] preparing {case.id}", flush=True)
                    try:
                        client = WorkerClient(case, logical_cpu)
                        clients[case.id] = client
                        setup = client.prepare(
                            float(measurement["setup_timeout_seconds"]),
                            memory_limit_gib,
                        )
                        result["setup"] = {
                            "duration_seconds": setup["duration_seconds"],
                            "affinity": setup["affinity"],
                            "adapter_version": setup["adapter_version"],
                            "runtime_metadata": setup["runtime_metadata"],
                        }
                        result["execution"]["threads_effective"] = setup["runtime_metadata"][
                            "threads"
                        ]
                        result["execution"]["batch_size_effective"] = setup["runtime_metadata"][
                            "effective_batch_size"
                        ]
                        result["simulator"]["dependencies"] = setup["dependencies"]
                    except Exception as error:  # noqa: BLE001
                        result["status"] = "error"
                        result["error"] = _error_record(error, "setup")
                        _atomic_write(output_path, document)
                        continue

                    try:
                        warmup = client.request(
                            {
                                "command": "warmup",
                                "shots": measurement["warmup_shots"],
                                "seed": suite.run["seed"] - 1,
                            },
                            timeout_seconds=request_timeout,
                        )
                        result["warmup"] = {
                            key: value for key, value in warmup.items() if key != "ok"
                        }
                    except Exception as error:  # noqa: BLE001
                        result["status"] = "error"
                        result["error"] = _error_record(error, "warmup")
                        _atomic_write(output_path, document)
                        continue

                    try:
                        correctness = client.request(
                            {
                                "command": "correctness",
                                "shots": measurement["correctness_shots"],
                                "seed": suite.run["seed"],
                            },
                            timeout_seconds=request_timeout,
                        )
                        contract_errors = correctness.pop("contract_errors")
                        correctness.pop("ok", None)
                        result["correctness"] = {
                            "status": "passed" if not contract_errors else "failed",
                            "check_id": "aggregate-count-and-circuit-metadata-v1",
                            "details": correctness,
                            "errors": contract_errors,
                            "timed_region": False,
                        }
                        if contract_errors:
                            raise RuntimeError("; ".join(contract_errors))
                    except Exception as error:  # noqa: BLE001
                        result["status"] = "error"
                        result["error"] = _error_record(error, "correctness")
                    _atomic_write(output_path, document)

                runnable = [
                    case.id for case in group if case_results[case.id]["status"] == "running"
                ]
                local_schedule = balanced_schedule(runnable, int(measurement["repetitions"]))
                for scheduled in local_schedule:
                    result = case_results[scheduled.case_id]
                    if result["status"] != "running":
                        continue
                    case = next(item for item in group if item.id == scheduled.case_id)
                    print(
                        f"sampling {case.id} (repetition {scheduled.repetition + 1}, "
                        f"sequence {sequence_index})",
                        flush=True,
                    )
                    try:
                        sample = clients[case.id].request(
                            {
                                "command": "sample",
                                "shots_per_call": case.definition["shots_per_call"],
                                "min_seconds": measurement["min_sample_seconds"],
                                "seed": suite.run["seed"]
                                + 10_000
                                + scheduled.repetition * SEED_REPETITION_STRIDE,
                                "max_api_calls": SEED_REPETITION_STRIDE,
                            },
                            timeout_seconds=request_timeout,
                        )
                        sample.pop("ok", None)
                        sample["repetition"] = scheduled.repetition
                        sample["sequence_index"] = sequence_index
                        result["samples"].append(sample)
                    except Exception as error:  # noqa: BLE001
                        result["status"] = "error"
                        result["error"] = _error_record(error, "sampling")
                    sequence_index += 1
                    _atomic_write(output_path, document)

                for case in group:
                    result = case_results[case.id]
                    if result["status"] == "running":
                        result["summary"] = _summary(result["samples"])
                        result["status"] = "success"
                _atomic_write(output_path, document)
            finally:
                for client in clients.values():
                    client.close()
    except KeyboardInterrupt:
        for result in document["cases"]:
            if result["status"] == "running":
                result["status"] = "interrupted"
                result["error"] = {
                    "phase": "sampling",
                    "type": "KeyboardInterrupt",
                    "message": "benchmark interrupted by user",
                }
        raise
    finally:
        document["run"]["finished_at"] = utc_now()
        _atomic_write(output_path, document)

    validate_document(document, source=str(output_path))
    _atomic_write(output_path, document)
    return document
