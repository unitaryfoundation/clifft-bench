"""Resource restriction and runner provenance helpers."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

THREAD_LIMIT_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "JAX_ENABLE_X64": "true",
    "JAX_DEFAULT_MATMUL_PRECISION": "highest",
    "JAX_PLATFORMS": "cpu",
    "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
    "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
}

CLOUD_METADATA_ENVIRONMENT = {
    "provider": "CLIFFT_BENCH_CLOUD_PROVIDER",
    "instance_id": "CLIFFT_BENCH_CLOUD_INSTANCE_ID",
    "instance_type": "CLIFFT_BENCH_CLOUD_INSTANCE_TYPE",
    "image_id": "CLIFFT_BENCH_CLOUD_IMAGE_ID",
    "region": "CLIFFT_BENCH_CLOUD_REGION",
    "availability_zone": "CLIFFT_BENCH_CLOUD_AVAILABILITY_ZONE",
    "lifecycle": "CLIFFT_BENCH_CLOUD_LIFECYCLE",
    "boot_id": "CLIFFT_BENCH_CLOUD_BOOT_ID",
}

RUN_PROVENANCE_ENVIRONMENT = {
    "provider": "CLIFFT_BENCH_RUN_PROVIDER",
    "repository": "CLIFFT_BENCH_RUN_REPOSITORY",
    "workflow": "CLIFFT_BENCH_RUN_WORKFLOW",
    "run_id": "CLIFFT_BENCH_RUN_ID",
    "run_attempt": "CLIFFT_BENCH_RUN_ATTEMPT",
    "ref": "CLIFFT_BENCH_RUN_REF",
    "sha": "CLIFFT_BENCH_RUN_SHA",
    "runner_name": "CLIFFT_BENCH_RUNNER_NAME",
    "runner_os": "CLIFFT_BENCH_RUNNER_OS",
    "image_os": "CLIFFT_BENCH_IMAGE_OS",
    "image_version": "CLIFFT_BENCH_IMAGE_VERSION",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def restricted_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(THREAD_LIMIT_ENVIRONMENT)
    return environment


def choose_cpu(requested: int | None) -> int | None:
    if requested is not None:
        return requested
    if hasattr(os, "sched_getaffinity"):
        available = sorted(os.sched_getaffinity(0))
        return available[0] if available else None
    return None


def apply_affinity(cpu: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requested": cpu is not None,
        "logical_cpu": cpu,
        "applied": False,
        "available_before": None,
        "available_after": None,
        "reason": None,
    }
    if cpu is None:
        result["reason"] = "no logical CPU requested on this platform"
        return result
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        result["reason"] = "process affinity is not supported by this operating system"
        return result
    before = sorted(os.sched_getaffinity(0))
    result["available_before"] = before
    if cpu not in before:
        result["reason"] = f"logical CPU {cpu} is outside the allowed affinity mask"
        return result
    os.sched_setaffinity(0, {cpu})
    after = sorted(os.sched_getaffinity(0))
    result["available_after"] = after
    result["applied"] = after == [cpu]
    if not result["applied"]:
        result["reason"] = "operating system did not retain the requested affinity mask"
    return result


def _run_text(command: list[str], cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _cpu_model() -> str:
    if platform.system() == "Darwin":
        return _run_text(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _physical_cores() -> int | None:
    if platform.system() == "Darwin":
        value = _run_text(["sysctl", "-n", "hw.physicalcpu"])
        return int(value) if value and value.isdigit() else None
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        cores: set[tuple[str, str]] = set()
        physical_id = "0"
        core_id: str | None = None
        for line in cpuinfo.read_text(errors="replace").splitlines() + [""]:
            if line.startswith("physical id") and ":" in line:
                physical_id = line.split(":", 1)[1].strip()
            elif line.startswith("core id") and ":" in line:
                core_id = line.split(":", 1)[1].strip()
            elif not line and core_id is not None:
                cores.add((physical_id, core_id))
                core_id = None
        return len(cores) or None
    return None


def _allowed_cpus() -> list[int] | None:
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return None


def _git_metadata(root: Path) -> dict[str, Any]:
    commit = _run_text(["git", "rev-parse", "HEAD"], root)
    status = _run_text(["git", "status", "--porcelain"], root)
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def collect_cloud_metadata() -> dict[str, str] | None:
    """Capture explicitly supplied cloud identity without contacting a metadata service."""
    values = {key: os.environ.get(variable) for key, variable in CLOUD_METADATA_ENVIRONMENT.items()}
    supplied = {key: value for key, value in values.items() if value}
    if not supplied:
        return None
    missing = sorted(set(values) - set(supplied))
    if missing:
        names = ", ".join(CLOUD_METADATA_ENVIRONMENT[key] for key in missing)
        raise ValueError(f"incomplete cloud metadata environment; missing: {names}")
    return {key: str(value) for key, value in values.items()}


def collect_runner_metadata(root: Path) -> dict[str, Any]:
    uname = platform.uname()
    try:
        load_average = list(os.getloadavg())
    except OSError:
        load_average = None
    return {
        "hostname": uname.node,
        "os": uname.system,
        "os_release": uname.release,
        "kernel_version": uname.version,
        "machine": uname.machine,
        "python": platform.python_version(),
        "python_compiler": platform.python_compiler(),
        "cpu_model": _cpu_model(),
        "physical_cores": _physical_cores(),
        "logical_cpus": os.cpu_count(),
        "allowed_logical_cpus": _allowed_cpus(),
        "memory_bytes": _memory_bytes(),
        "load_average_at_start": load_average,
        "thread_environment": THREAD_LIMIT_ENVIRONMENT.copy(),
        "accelerator": None,
        "cloud": collect_cloud_metadata(),
        "suite_source": _git_metadata(root),
    }


def collect_workflow_metadata() -> dict[str, Any]:
    """Capture explicit run provenance, falling back to GitHub Actions metadata."""
    explicit = {
        key: os.environ.get(variable) for key, variable in RUN_PROVENANCE_ENVIRONMENT.items()
    }
    if any(value for value in explicit.values()):
        missing = sorted(key for key, value in explicit.items() if not value)
        if missing:
            names = ", ".join(RUN_PROVENANCE_ENVIRONMENT[key] for key in missing)
            raise ValueError(f"incomplete run provenance environment; missing: {names}")
        return explicit
    return {
        "provider": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else None,
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "ref": os.environ.get("GITHUB_REF"),
        "sha": os.environ.get("GITHUB_SHA"),
        "runner_name": os.environ.get("RUNNER_NAME"),
        "runner_os": os.environ.get("RUNNER_OS"),
        "image_os": os.environ.get("ImageOS"),
        "image_version": os.environ.get("ImageVersion"),
    }
