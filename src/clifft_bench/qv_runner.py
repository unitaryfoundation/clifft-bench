"""Serial orchestration for deterministic, single-shot QV measurements."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import uuid
from pathlib import Path
from typing import Any

from clifft_bench.qv import QVCampaign, scheduled_cases, select_physical_cpus
from clifft_bench.schema import repository_root, validate_document, write_json
from clifft_bench.system import collect_runner_metadata, collect_workflow_metadata, utc_now


def _source_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source = str(repository_root() / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
    return environment


def _parse_worker_output(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    for line in reversed(completed.stdout.splitlines()):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and "status" in document:
            return document
    tail = "\n".join(completed.stderr.strip().splitlines()[-5:])
    return {
        "status": "error",
        "error": {
            "type": "WorkerExited",
            "message": tail or f"worker exited with code {completed.returncode}",
        },
    }


def _worker_environment(threads: int) -> dict[str, str]:
    environment = _source_environment()
    value = str(threads)
    environment.update(
        {
            "OMP_NUM_THREADS": value,
            "MKL_NUM_THREADS": value,
            "OPENBLAS_NUM_THREADS": value,
            "NUMEXPR_NUM_THREADS": value,
            "OMP_DYNAMIC": "false",
            "OMP_PROC_BIND": "true",
            "OMP_PLACES": "threads",
        }
    )
    return environment


def _generate_circuits(
    campaign: QVCampaign, environment_root: Path, circuit_dir: Path
) -> tuple[dict[tuple[int, int], Path], dict[str, str]]:
    generator_id = str(campaign.document["circuit"]["generator_environment"])
    python = environment_root / generator_id / "bin/python"
    if not python.is_file():
        raise ValueError(f"missing generator environment {generator_id!r}: {python}")
    paths: dict[tuple[int, int], Path] = {}
    dependencies: dict[str, str] | None = None
    for qubits in campaign.document["circuit"]["qubits"]:
        for seed in campaign.document["circuit"]["seeds"]:
            key = (int(qubits), int(seed))
            path = circuit_dir / f"qv-q{qubits}-seed{seed}.qasm"
            if not path.is_file():
                command = [
                    str(python),
                    "-m",
                    "clifft_bench.qv_worker",
                    "generate",
                    "--qubits",
                    str(qubits),
                    "--seed",
                    str(seed),
                    "--output",
                    str(path),
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    env=_source_environment(),
                    timeout=120,
                )
                response = _parse_worker_output(completed)
                if completed.returncode != 0 or response["status"] != "success":
                    raise RuntimeError(
                        f"failed to generate QV{qubits} seed {seed}: "
                        f"{(response.get('error') or {}).get('message', 'unknown error')}"
                    )
                current = dict(response["dependencies"])
                if dependencies is not None and current != dependencies:
                    raise RuntimeError("circuit generator dependencies changed during collection")
                dependencies = current
            paths[key] = path
    if dependencies is None:
        # Existing artifacts are only reused within one execution. Query the
        # same environment so their provenance remains explicit in every raw file.
        completed = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import json; from importlib.metadata import version; "
                    "names=['dill','numpy','qiskit','rustworkx','scipy','stevedore',"
                    "'typing-extensions']; "
                    "print(json.dumps({name: version(name) for name in names}))"
                ),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
        dependencies = json.loads(completed.stdout)
    return paths, dependencies


def _case_record(specification: dict[str, Any], circuit: Path) -> dict[str, Any]:
    run = specification["run"]
    qubits = int(specification["qubits"])
    seed = int(specification["seed"])
    threads = int(specification["threads"])
    return {
        "case_id": f"{run['id']}--q{qubits}-seed{seed}-t{threads}",
        "sequence_index": -1,
        "status": "pending",
        "phase": run["phase"],
        "simulator": {
            "run_id": run["id"],
            "name": run["name"],
            "version": run["version"],
            "distribution": run["distribution"],
            "expected_distribution_version": run["expected_distribution_version"],
            "commit_sha": run["commit_sha"],
            "source_url": run["source_url"],
            "adapter": run["adapter"],
            "environment_id": run["environment_id"],
            "dependencies": {},
        },
        "circuit": {
            "family": "quantum-volume",
            "qubits": qubits,
            "depth": qubits,
            "seed": seed,
            "basis_gates": ["cx", "u3"],
            "path": circuit.name,
            "sha256": hashlib.sha256(circuit.read_bytes()).hexdigest(),
        },
        "threads": {
            "requested": threads,
            "effective": None,
            "cpu_set": [],
            "policy": "one-logical-cpu-per-physical-core",
        },
        "timing": None,
        "peak_rss_bytes": None,
        "runtime_metadata": {},
        "error": None,
    }


def _run_case(
    campaign: QVCampaign,
    environment_root: Path,
    case: dict[str, Any],
    circuit_dir: Path,
) -> dict[str, Any]:
    threads = int(case["threads"]["requested"])
    cpu_set = select_physical_cpus(threads)
    case["threads"]["cpu_set"] = cpu_set
    environment_id = str(case["simulator"]["environment_id"])
    python = environment_root / environment_id / "bin/python"
    if not python.is_file():
        raise ValueError(f"missing environment {environment_id!r}: {python}")
    command = [
        str(python),
        "-m",
        "clifft_bench.qv_worker",
        "run",
        "--adapter",
        str(case["simulator"]["adapter"]),
        "--qasm",
        str(circuit_dir / case["circuit"]["path"]),
        "--threads",
        str(threads),
        "--cpu-set",
        ",".join(str(cpu) for cpu in cpu_set),
        "--memory-limit-gib",
        str(campaign.document["collection"]["memory_limit_gib"]),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=_worker_environment(threads),
            timeout=int(campaign.document["collection"]["case_timeout_seconds"]),
        )
    except subprocess.TimeoutExpired:
        case["status"] = "timeout"
        case["error"] = {
            "type": "WorkerTimeout",
            "message": (
                "case exceeded "
                f"{campaign.document['collection']['case_timeout_seconds']} seconds"
            ),
        }
        return case
    response = _parse_worker_output(completed)
    case["peak_rss_bytes"] = response.get("peak_rss_bytes")
    if completed.returncode != 0 or response["status"] != "success":
        case["status"] = "error"
        case["error"] = response.get("error") or {
            "type": "WorkerError",
            "message": f"worker exited with code {completed.returncode}",
        }
        return case

    dependencies = dict(response["dependencies"])
    distribution = str(case["simulator"]["distribution"])
    expected_version = case["simulator"]["expected_distribution_version"]
    actual_version = dependencies.get(distribution)
    if expected_version is not None and actual_version != expected_version:
        case["status"] = "error"
        case["error"] = {
            "type": "VersionMismatch",
            "message": (
                f"expected {distribution} {expected_version}, received {actual_version!r}"
            ),
        }
        return case
    case["simulator"]["dependencies"] = dependencies
    case["threads"]["effective"] = response["threads_effective"]
    case["timing"] = response["timing"]
    case["runtime_metadata"] = response["runtime_metadata"]
    case["status"] = "success"
    return case


def run_qv_campaign(
    campaign: QVCampaign,
    *,
    environment_root: Path,
    circuit_dir: Path,
    output_path: Path,
    execution_id: str,
    placement: int,
    replica: int,
    verify_host: bool = True,
) -> dict[str, Any]:
    circuits, generator_dependencies = _generate_circuits(
        campaign, environment_root.resolve(), circuit_dir.resolve()
    )
    specifications = scheduled_cases(campaign)
    cases = []
    for sequence_index, specification in enumerate(specifications):
        case = _case_record(
            specification, circuits[(specification["qubits"], specification["seed"])]
        )
        case["sequence_index"] = sequence_index
        cases.append(case)
    runner = collect_runner_metadata(
        repository_root(),
        thread_environment={
            "OMP_NUM_THREADS": "per-case",
            "MKL_NUM_THREADS": "per-case",
            "OPENBLAS_NUM_THREADS": "per-case",
            "OMP_DYNAMIC": "false",
            "OMP_PROC_BIND": "true",
            "OMP_PLACES": "threads",
        },
    )
    reference = campaign.document["reference_host"]
    if verify_host:
        if runner["physical_cores"] != reference["physical_cores"]:
            raise ValueError(
                f"host has {runner['physical_cores']} physical cores; "
                f"campaign requires {reference['physical_cores']}"
            )
        if runner["logical_cpus"] != reference["logical_cpus"]:
            raise ValueError(
                f"host has {runner['logical_cpus']} logical CPUs; "
                f"campaign requires {reference['logical_cpus']}"
            )

    document: dict[str, Any] = {
        "schema_version": "clifft-bench/qv-result/v1",
        "campaign": {
            "id": campaign.id,
            "classification": campaign.document["classification"],
            "hardware_epoch": campaign.document["hardware_epoch"],
            "manifest": str(campaign.path),
            "manifest_sha256": campaign.manifest_sha256,
            "circuit_generator_dependencies": generator_dependencies,
        },
        "run": {
            "id": str(uuid.uuid4()),
            "profile_id": campaign.id,
            "execution_id": execution_id,
            "started_at": utc_now(),
            "finished_at": None,
            "placement": placement,
            "replica": replica,
            "schedule_policy": "serial-alternating-forward-reverse",
            "workflow": collect_workflow_metadata(),
        },
        "runner": runner,
        "cases": cases,
    }
    write_json(output_path, document)

    interrupted = False
    previous_handler = signal.getsignal(signal.SIGINT)

    def request_interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_interrupt)
    try:
        for sequence_index, case in enumerate(cases):
            print(
                f"[{sequence_index + 1}/{len(cases)}] {case['case_id']}",
                flush=True,
            )
            try:
                _run_case(campaign, environment_root, case, circuit_dir)
            except Exception as error:  # noqa: BLE001
                case["status"] = "error"
                case["error"] = {"type": type(error).__name__, "message": str(error)}
            write_json(output_path, document)
    except KeyboardInterrupt:
        interrupted = True
        for case in cases:
            if case["status"] == "pending":
                case["status"] = "interrupted"
                case["error"] = {
                    "type": "Interrupted",
                    "message": "campaign collection was interrupted",
                }
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        document["run"]["finished_at"] = utc_now()
        write_json(output_path, document)

    validate_document(document)
    if interrupted:
        raise KeyboardInterrupt
    return document
