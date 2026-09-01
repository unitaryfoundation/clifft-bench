"""Serial orchestration for the standalone Quantum Volume experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from qv_experiment.generator import generate_qv_qasm
from qv_experiment.system import (
    ec2_identity,
    git_metadata,
    select_physical_cpus,
    system_metadata,
)

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
PAPER_SOURCE = {
    "repository": "https://github.com/unitaryfoundation/clifft-paper",
    "path": "qv_bench",
    "commit": "db7dc9f13a2c2854690e92390c779048a1ac1400",
}
CLIFFT_SOURCE = {
    "release_version": "0.9.0",
    "artifact_version": "0.9.0",
    "artifact_kind": "release",
    "commit": "87175b513b8d944955102230b9c7931be1570ef2",
    "requested_build": {
        "CLIFFT_MAX_QUBITS": "64",
        "CLIFFT_OPENMP": "ON",
        "CLIFFT_CPU_BASELINE": "native",
    },
}
DISTRIBUTIONS = [
    "clifft",
    "numpy",
    "ply",
    "pyqrack",
    "qiskit",
    "qiskit-aer",
    "qiskit-qrack-provider",
    "qsimcirq",
    "qulacs",
]
CSV_COLUMNS = [
    "case_id",
    "simulator",
    "qubits",
    "seed",
    "threads",
    "status",
    "execution_seconds",
    "compile_seconds",
    "sample_seconds",
    "peak_rss_bytes",
    "circuit_sha256",
    "error_type",
    "error_message",
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in DISTRIBUTIONS:
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = "missing"
    return versions


def clifft_runtime_identity() -> dict[str, str]:
    import clifft

    return {
        "distribution_version": version("clifft"),
        "runtime_version": str(clifft.version()),
        "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
    }


def validate_official_clifft(identity: dict[str, str]) -> None:
    expected_version = str(CLIFFT_SOURCE["artifact_version"])
    errors = []
    for key in ("distribution_version", "runtime_version"):
        if identity[key] != expected_version:
            errors.append(f"{key} is {identity[key]!r}, expected {expected_version!r}")
    if identity["cpu_baseline"] != "native":
        errors.append(
            f"cpu_baseline is {identity['cpu_baseline']!r}, expected 'native'"
        )
    if errors:
        raise ValueError("official QV collection has the wrong Clifft build: " + "; ".join(errors))


def parse_integers(value: str) -> list[int]:
    result = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not result or len(result) != len(set(result)) or min(result) < 1:
        raise argparse.ArgumentTypeError("expected unique positive comma-separated integers")
    return result


def parse_simulators(value: str) -> list[str]:
    supported = {"clifft", "qiskit", "qulacs", "qsim", "qrack"}
    result = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(result) - supported)
    if not result or len(result) != len(set(result)) or unknown:
        raise argparse.ArgumentTypeError(
            "expected unique simulators from clifft,qiskit,qulacs,qsim,qrack"
        )
    return result


def schedule_cases(
    qubits: list[int],
    seeds: list[int],
    simulators: list[str],
) -> list[tuple[int, int, str]]:
    cases: list[tuple[int, int, str]] = []
    group = 0
    for width in qubits:
        for seed in seeds:
            order = simulators if group % 2 == 0 else list(reversed(simulators))
            cases.extend((width, seed, simulator) for simulator in order)
            group += 1
    return cases


def worker_environment(threads: int) -> dict[str, str]:
    environment = os.environ.copy()
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
            "QRACK_DISABLE_OPENCL": "1",
        }
    )
    return environment


def parse_worker_output(stdout: str, *, returncode: int) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and "status" in document:
            return document
    return {
        "status": "error",
        "error": {
            "type": "WorkerOutputError",
            "message": f"worker produced no JSON result (exit code {returncode})",
        },
    }


def output_tail(output: str | bytes | None) -> list[str]:
    if output is None:
        return []
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    return output.strip().splitlines()[-10:]


def write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument(
        "--qubits",
        type=parse_integers,
        default=parse_integers("6,8,10,12,14,16,18,20,22,24,26,28"),
    )
    parser.add_argument(
        "--seeds",
        type=parse_integers,
        default=parse_integers("42,43,44"),
    )
    parser.add_argument(
        "--simulators",
        type=parse_simulators,
        default=parse_simulators("clifft,qiskit,qulacs,qsim,qrack"),
    )
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--memory-limit-gib", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--output-root", type=Path, default=EXPERIMENT_ROOT / "results")
    parser.add_argument("--require-ec2", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", args.execution_id):
        raise SystemExit(
            "execution id must contain only letters, digits, dots, dashes, or underscores"
        )
    if args.threads < 1 or args.memory_limit_gib <= 0 or args.timeout_seconds < 1:
        raise SystemExit("threads, memory limit, and timeout must be positive")

    source = git_metadata(REPOSITORY_ROOT)
    if args.require_clean and source["dirty"]:
        raise SystemExit("official collection requires a clean checkout")

    clifft_identity = clifft_runtime_identity()
    if args.require_ec2:
        try:
            validate_official_clifft(clifft_identity)
        except ValueError as error:
            raise SystemExit(str(error)) from error

    cloud = ec2_identity(required=args.require_ec2)
    if args.require_ec2 and cloud.get("instanceType") != "c8i.8xlarge":
        raise SystemExit(
            f"official QV collection requires c8i.8xlarge, got {cloud.get('instanceType')}"
        )
    cpu_set = select_physical_cpus(args.threads)
    if args.require_ec2 and len(cpu_set) != args.threads:
        raise SystemExit("official QV collection requires one logical CPU per physical core")

    target = args.output_root.resolve() / args.execution_id
    target.mkdir(parents=True, exist_ok=False)
    circuits_dir = target / "circuits"
    raw_dir = target / "raw"
    circuits_dir.mkdir()
    raw_dir.mkdir()

    circuits: dict[tuple[int, int], tuple[Path, str]] = {}
    for width in args.qubits:
        for seed in args.seeds:
            path = circuits_dir / f"qv-q{width}-seed{seed}.qasm"
            path.write_text(generate_qv_qasm(width, seed))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            circuits[(width, seed)] = (path, digest)

    metadata = {
        "execution_id": args.execution_id,
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "paper_source": PAPER_SOURCE,
        "clifft_source": {**CLIFFT_SOURCE, "observed": clifft_identity},
        "packages": package_versions(),
        "git": source,
        "system": system_metadata(),
        "ec2": cloud,
        "configuration": {
            "qubits": args.qubits,
            "seeds": args.seeds,
            "simulators": args.simulators,
            "threads": args.threads,
            "cpu_set": cpu_set,
            "memory_limit_gib": args.memory_limit_gib,
            "timeout_seconds": args.timeout_seconds,
            "circuit_depth": "width",
            "basis_gates": ["cx", "u3"],
            "timed_region": "original-clifft-paper-qv-v1",
            "qrack_opencl_disabled": True,
        },
    }
    write_json(target / "metadata.json", metadata)

    cases = schedule_cases(args.qubits, args.seeds, args.simulators)
    failures = 0
    cases_path = target / "cases.csv"
    with cases_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for index, (width, seed, simulator) in enumerate(cases, start=1):
            circuit_path, circuit_digest = circuits[(width, seed)]
            case_id = f"{simulator}-q{width}-seed{seed}-t{args.threads}"
            print(f"[{index}/{len(cases)}] {case_id}", flush=True)
            command = [
                sys.executable,
                "-m",
                "qv_experiment.worker",
                simulator,
                str(circuit_path),
                "--threads",
                str(args.threads),
                "--seed",
                str(seed),
                "--memory-limit-gib",
                str(args.memory_limit_gib),
                "--cpu-set",
                ",".join(str(cpu) for cpu in cpu_set),
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=args.timeout_seconds,
                    env=worker_environment(args.threads),
                )
                response = parse_worker_output(
                    completed.stdout,
                    returncode=completed.returncode,
                )
                if completed.returncode != 0 and response["status"] == "success":
                    response = {
                        "status": "error",
                        "error": {
                            "type": "WorkerExitError",
                            "message": f"worker exited with code {completed.returncode}",
                        },
                    }
                response["worker_returncode"] = completed.returncode
                response["stderr_tail"] = output_tail(completed.stderr)
            except subprocess.TimeoutExpired as error:
                response = {
                    "status": "timeout",
                    "error": {
                        "type": "WorkerTimeout",
                        "message": f"case exceeded {args.timeout_seconds} seconds",
                    },
                    "worker_returncode": None,
                    "stdout_tail": output_tail(error.stdout),
                    "stderr_tail": output_tail(error.stderr),
                }

            raw = {
                "case_id": case_id,
                "simulator": simulator,
                "qubits": width,
                "depth": width,
                "seed": seed,
                "threads": args.threads,
                "circuit": {
                    "path": str(circuit_path.relative_to(target)),
                    "sha256": circuit_digest,
                },
                "result": response,
            }
            write_json(raw_dir / f"{case_id}.json", raw)
            timing = response.get("timing") or {}
            error = response.get("error") or {}
            row = {
                "case_id": case_id,
                "simulator": simulator,
                "qubits": width,
                "seed": seed,
                "threads": args.threads,
                "status": response["status"],
                "execution_seconds": timing.get("execution_seconds", ""),
                "compile_seconds": timing.get("compile_seconds", ""),
                "sample_seconds": timing.get("sample_seconds", ""),
                "peak_rss_bytes": response.get("peak_rss_bytes", ""),
                "circuit_sha256": circuit_digest,
                "error_type": error.get("type", ""),
                "error_message": error.get("message", ""),
            }
            writer.writerow(row)
            stream.flush()
            if response["status"] != "success":
                failures += 1

    metadata["status"] = "complete" if failures == 0 else "complete-with-failures"
    metadata["finished_at"] = utc_now()
    metadata["case_count"] = len(cases)
    metadata["failure_count"] = failures
    write_json(target / "metadata.json", metadata)
    print(f"Results written to {target}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
