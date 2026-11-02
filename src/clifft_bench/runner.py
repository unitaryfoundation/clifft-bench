"""Serial benchmark orchestration and raw result emission."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from clifft_bench.calibration import CALIBRATION_POLICY
from clifft_bench.config import Case, Suite, repository_root
from clifft_bench.results import FORMAT, export_csv, write_json
from clifft_bench.selection import has_postselection
from clifft_bench.system import (
    choose_cpu,
    collect_runner_metadata,
    collect_workflow_metadata,
    ec2_workflow,
    require_reference_host,
    restricted_environment,
    utc_now,
)

SEED_REPETITION_STRIDE = 100_000_000
SEED_MAX_EXCLUSIVE = 2**32
BATCH_CALIBRATION_SEED_RESERVE = 4_000_000

class WorkerError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", "worker failed")))
        self.error = error


class WorkerClient:
    def __init__(self, case: Case, cpu: int | None, *, python_executable: str | None = None,
                 deadline: float | None = None) -> None:
        self.case = case
        self.deadline = deadline
        environment = restricted_environment()
        source = str(repository_root() / "src")
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
        self.process = subprocess.Popen(
            [
                python_executable or case.implementation.python_executable(),
                "-m",
                "clifft_bench.worker",
            ],
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
        if self.deadline is not None:
            timeout_seconds = min(timeout_seconds, max(0.001, self.deadline - time.monotonic()))
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
        self,
        timeout_seconds: float,
        memory_limit_gib: float | None = None,
        *,
        seed: int,
        sampling_seed: int = 0,
    ) -> dict[str, Any]:
        definition = self.case.implementation.definition
        return self.request(
            {
                "command": "prepare",
                "adapter": definition["adapter"],
                "artifact_path": str(self.case.workload.artifact_path),
                "workload": self.case.workload.definition,
                "execution": self.case.definition["execution"],
                "shots_per_call": self.case.definition["shots_per_call"],
                "seed": seed,
                "sampling_seed": sampling_seed,
                "expected_version": definition["version"],
                "expected_commit": definition["commit_sha"],
                "source_url": definition["source_url"],
                "dependency_distributions": definition["dependency_distributions"],
                "logical_cpu": self.cpu,
                "memory_limit_gib": memory_limit_gib,
                "timeout_seconds": timeout_seconds,
            },
            timeout_seconds=timeout_seconds + 5,
        )

    def close(self, *, interrupted: bool = False) -> None:
        if interrupted:
            self._stop()
            return
        if self.process.poll() is None:
            try:
                self.request({"command": "close"}, timeout_seconds=5)
            except (OSError, WorkerError, json.JSONDecodeError):
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
        "expected_metadata": definition["expected_metadata"],
        "compatible_adapters": definition["compatible_adapters"],
        "semantics": definition["semantics"],
        "artifact": {
            "path": str(case.workload.artifact_path),
            "sha256": case.workload.artifact_sha256,
        },
        "provenance": definition["provenance"],
    }


def _simulator_record(case: Case, python_executable: str | None = None) -> dict[str, Any]:
    definition = case.implementation.definition
    record = {
        "implementation_id": definition["id"],
        "name": definition["name"],
        "version": definition["version"],
        "display_version": definition.get("display_version", definition["version"]),
        "commit_sha": definition["commit_sha"],
        "source_url": definition["source_url"],
        "adapter": definition["adapter"],
        "python_executable": python_executable or case.implementation.python_executable(),
        "build": definition["build"],
        "dependencies": {},
    }
    if "source_tag" in definition:
        record["source_tag"] = definition["source_tag"]
    return record


def _new_case_result(
    case: Case,
    measurement: dict[str, Any],
    memory_limit_gib: float | None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    execution = dict(case.definition["execution"])
    execution["batch_enabled"] = execution["batch_size"] != 1
    if execution["batch_size"] == "calibrate":
        execution["tuning_policy"] = CALIBRATION_POLICY
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
            "postselection": has_postselection(case.workload.definition["semantics"]),
        }
    )
    return {
        "case_id": case.id,
        "variant_id": case.definition["variant_id"],
        "status": "running",
        "workload": _workload_record(case),
        "simulator": _simulator_record(case, python_executable),
        "execution": execution,
        "setup": None,
        "warmup": None,
        "correctness": None,
        "samples": [],
        "error": None,
    }


def _effective_batch_size(
    runtime_metadata: dict[str, Any], shots_per_call: int
) -> int:
    capacity = int(runtime_metadata["effective_batch_size"])
    return min(capacity, shots_per_call)


def _record_runtime_execution(
    execution: dict[str, Any], runtime_metadata: dict[str, Any], shots_per_call: int
) -> None:
    execution["threads_effective"] = int(runtime_metadata["threads"])
    calibration = runtime_metadata.get("batch_calibration")
    if calibration is not None:
        execution["batch_size"] = int(calibration["selected_batch_size"])
        execution["batch_enabled"] = bool(runtime_metadata["batch_enabled"])
    execution["batch_size_effective"] = _effective_batch_size(
        runtime_metadata, shots_per_call
    )


def _select_cases(suite: Suite, pattern: str | None) -> list[Case]:
    if pattern is None:
        return list(suite.cases)
    expression = re.compile(pattern)
    return [case for case in suite.cases if expression.search(case.id)]


def run_suite(
    suite: Suite,
    *,
    output_dir: Path,
    case_pattern: str | None = None,
    cpu: int | None = None,
    memory_limit_gib: float | None = None,
    min_sample_seconds: float | None = None,
    repetitions: int | None = None,
    python_executable: str | None = None,
    require_ec2: bool = False,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite existing execution: {output_dir}")
    output_path = output_dir / "raw.json"
    if suite.run["classification"] == "official" and python_executable is not None:
        raise ValueError("official collection requires isolated implementation environments")
    measurement = dict(suite.run["measurement"])
    if min_sample_seconds is not None:
        measurement["min_sample_seconds"] = min_sample_seconds
    if repetitions is not None:
        measurement["repetitions"] = repetitions
    if (not math.isfinite(measurement["min_sample_seconds"])
            or measurement["min_sample_seconds"] <= 0
            or type(measurement["repetitions"]) is not int or measurement["repetitions"] < 1):
        raise ValueError("measurement overrides must be positive")
    if memory_limit_gib is not None and (
        not math.isfinite(memory_limit_gib) or memory_limit_gib <= 0
    ):
        raise ValueError("memory limit must be positive")
    cases = _select_cases(suite, case_pattern)
    if not cases:
        raise ValueError("no cases matched the selection")
    seed_range_end = (
        int(suite.run["seed"])
        + 10_000
        + int(measurement["repetitions"]) * SEED_REPETITION_STRIDE
    )
    calibration_seed = seed_range_end
    if any(
        case.definition["execution"]["batch_size"] == "calibrate"
        for case in cases
    ):
        seed_range_end += BATCH_CALIBRATION_SEED_RESERVE
    if seed_range_end > SEED_MAX_EXCLUSIVE:
        raise ValueError(
            "declared repetitions exceed the unsigned 32-bit benchmark seed space"
        )
    request_timeout = float(measurement["request_timeout_seconds"])
    if request_timeout <= measurement["min_sample_seconds"]:
        raise ValueError(
            "request_timeout_seconds must be greater than min_sample_seconds"
        )

    logical_cpu = choose_cpu(cpu if cpu is not None else suite.run["resources"]["logical_cpu"])
    root = repository_root()
    cloud = None
    if require_ec2:
        from clifft_bench.bootstrap import selected_implementations, verify_environment

        if "reference_host" not in suite.run or "collection" not in suite.run:
            raise ValueError("EC2 collection requires a reference host and collection settings")
        if python_executable is not None:
            raise ValueError("EC2 collection requires isolated implementation environments")
        if case_pattern is not None or min_sample_seconds is not None or repetitions is not None:
            raise ValueError("EC2 collection requires the complete declared campaign")
        declared_limit = suite.run["collection"]["memory_limit_gib"]
        if memory_limit_gib is not None and memory_limit_gib != declared_limit:
            raise ValueError("EC2 memory limit must match the campaign")
        memory_limit_gib = declared_limit
        if output_path.resolve().is_relative_to(root):
            raise ValueError("EC2 raw evidence must be written outside the source checkout")
        cloud = require_reference_host(
            root, suite.run["reference_host"], memory_limit_gib, logical_cpu
        )
        for implementation in selected_implementations(suite):
            verify_environment(implementation)
    elif suite.run["classification"] == "official":
        raise ValueError("official collection requires --require-ec2")
    deadline_minutes = suite.run.get("collection", {}).get("run_timeout_minutes")
    deadline = time.monotonic() + deadline_minutes * 60 if deadline_minutes else None
    started_at = utc_now()
    run_id = str(uuid.uuid4())
    document: dict[str, Any] = {
        "format": FORMAT,
        "campaign": {
            **{k: deepcopy(v) for k, v in suite.run.items()
               if k not in {"cases", "variants", "workloads_file", "software_file"}},
            "cases": [deepcopy(case.definition) for case in cases],
            "measurement": measurement,
            "resources": {"logical_cpu": logical_cpu, "memory_limit_bytes": (
                int(memory_limit_gib * (1 << 30)) if memory_limit_gib is not None else None
            )},
        },
        "run": {
            "id": run_id,
            "profile_id": suite.run["profile_id"],
            "classification": suite.run["classification"],
            "started_at": started_at,
            "finished_at": None,
            "overrides": {k: v for k, v in {
                "cpu": cpu, "min_sample_seconds": min_sample_seconds,
                "repetitions": repetitions, "python_executable": python_executable,
            }.items() if v is not None},
            "selection": case_pattern,
            "schedule_policy": "serial-campaign-order",
            "workflow": ec2_workflow(root, cloud, run_id) if cloud else collect_workflow_metadata(),
        },
        "runner": collect_runner_metadata(root, cloud=cloud),
        "cases": [
            _new_case_result(case, measurement, memory_limit_gib, python_executable)
            for case in cases
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_path, document)

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    previous_sigterm = signal.signal(signal.SIGTERM, terminate)
    try:
        sequence_index = 0
        for case_index, (case, result) in enumerate(
            zip(cases, document["cases"], strict=True), start=1
        ):
            client: WorkerClient | None = None
            try:
                print(f"[{case_index}/{len(cases)}] preparing {case.id}", flush=True)
                if deadline is not None and time.monotonic() >= deadline:
                    for pending in document["cases"][case_index - 1:]:
                        pending.update(status="error", error={
                            "phase": "setup", "type": "CampaignTimeout",
                            "message": "campaign deadline exceeded before case preparation",
                        })
                    break
                try:
                    client = WorkerClient(case, logical_cpu, python_executable=python_executable,
                                          deadline=deadline)
                    setup = client.prepare(
                        float(measurement["setup_timeout_seconds"]),
                        memory_limit_gib,
                        seed=calibration_seed,
                        sampling_seed=suite.run["seed"] - 1,
                    )
                    result["setup"] = {
                        "duration_seconds": setup["duration_seconds"],
                        "affinity": setup["affinity"],
                        "adapter_version": setup["adapter_version"],
                        "runtime_metadata": setup["runtime_metadata"],
                    }
                    _record_runtime_execution(
                        result["execution"],
                        setup["runtime_metadata"],
                        int(case.definition["shots_per_call"]),
                    )
                    result["simulator"]["dependencies"] = setup["dependencies"]
                except Exception as error:  # noqa: BLE001
                    result["status"] = "error"
                    result["error"] = _error_record(error, "setup")

                if result["status"] == "running":
                    try:
                        warmup_shots = int(measurement["warmup_shots"])
                        if setup["runtime_metadata"].get("warmup_full_call"):
                            warmup_shots = max(warmup_shots, case.definition["shots_per_call"])
                        warmup = client.request(
                            {
                                "command": "warmup",
                                "shots": warmup_shots,
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

                if result["status"] == "running":
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

                for repetition in range(int(measurement["repetitions"])):
                    if result["status"] != "running":
                        break
                    print(
                        f"sampling {case.id} (repetition {repetition + 1}, "
                        f"sequence {sequence_index})",
                        flush=True,
                    )
                    try:
                        sample = client.request(
                            {
                                "command": "sample",
                                "shots_per_call": case.definition["shots_per_call"],
                                "min_seconds": measurement["min_sample_seconds"],
                                "seed": suite.run["seed"]
                                + 10_000
                                + repetition * SEED_REPETITION_STRIDE,
                                "max_api_calls": SEED_REPETITION_STRIDE,
                            },
                            timeout_seconds=request_timeout,
                        )
                        sample.pop("ok", None)
                        sample["repetition"] = repetition
                        sample["sequence_index"] = sequence_index
                        result["samples"].append(sample)
                    except Exception as error:  # noqa: BLE001
                        result["status"] = "error"
                        result["error"] = _error_record(error, "sampling")
                    sequence_index += 1
                    write_json(output_path, document)

                if result["status"] == "running":
                    result["status"] = "success"
                write_json(output_path, document)
            finally:
                if client is not None:
                    interrupted = sys.exception()
                    try:
                        client.close(interrupted=interrupted is not None)
                    except Exception as cleanup_error:  # noqa: BLE001
                        if interrupted is None:
                            raise
                        interrupted.add_note(f"worker cleanup failed: {cleanup_error}")
    except BaseException as error:
        for result in document["cases"]:
            if result["status"] == "running":
                result["status"] = "interrupted"
                result["error"] = {
                    "phase": next((phase for phase in ("setup", "warmup", "correctness")
                                   if result[phase] is None), "sampling"),
                    "type": type(error).__name__,
                    "message": str(error) or "benchmark interrupted",
                }
        raise
    finally:
        interrupted = sys.exception()
        try:
            document["run"]["finished_at"] = utc_now()
            write_json(output_path, document)
            export_csv(document, output_dir / "cases.csv")
        except Exception as export_error:  # noqa: BLE001
            if interrupted is None:
                raise
            interrupted.add_note(f"evidence finalization failed: {export_error}")
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return document
