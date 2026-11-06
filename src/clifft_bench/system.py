"""Resource restriction and runner provenance helpers."""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

THREAD_LIMIT_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
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



def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def restricted_environment(threads: int | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(THREAD_LIMIT_ENVIRONMENT)
    if threads is not None:
        environment.update(native_thread_environment(threads))
    return environment


def native_thread_environment(threads: int) -> dict[str, str]:
    """Budget simulator pools while keeping nested numerical libraries serial."""
    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be a positive integer")
    return {
        **THREAD_LIMIT_ENVIRONMENT,
        "OMP_NUM_THREADS": str(threads),
        "NUMBA_NUM_THREADS": str(threads),
        "OMP_THREAD_LIMIT": str(threads),
        "OMP_DYNAMIC": "FALSE",
        "OMP_PROC_BIND": "FALSE",
        "OMP_MAX_ACTIVE_LEVELS": "1",
    }


def physical_core_allocation() -> list[dict[str, int]]:
    """Select one allowed logical CPU per Linux-visible physical core."""
    allowed = _allowed_cpus()
    if platform.system() != "Linux" or not allowed:
        raise ValueError("thread scaling requires Linux CPU affinity and topology")
    cores = {}
    for cpu in allowed:
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        package = int((topology / "physical_package_id").read_text())
        core = int((topology / "core_id").read_text())
        if package < 0 or core < 0:
            raise ValueError("thread scaling requires known physical core identities")
        cores.setdefault((package, core), {
            "logical_cpu": cpu, "package_id": package, "core_id": core,
        })
    return [cores[key] for key in sorted(cores)]


def choose_cpu(requested: int | None) -> int | None:
    if requested is not None:
        return requested
    if hasattr(os, "sched_getaffinity"):
        available = sorted(os.sched_getaffinity(0))
        return available[0] if available else None
    return None


def apply_affinity(cpu: int | list[int] | None) -> dict[str, Any]:
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
    requested = cpu if isinstance(cpu, list) else [cpu]
    if isinstance(cpu, list):
        result.pop("logical_cpu")
        result["logical_cpus"] = cpu
    if (not requested or any(type(value) is not int or value < 0 for value in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError("affinity requires distinct nonnegative CPU IDs")
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        result["reason"] = "process affinity is not supported by this operating system"
        return result
    before = sorted(os.sched_getaffinity(0))
    result["available_before"] = before
    if not set(requested).issubset(before):
        result["reason"] = f"logical CPU {cpu} is outside the allowed affinity mask"
        return result
    os.sched_setaffinity(0, set(requested))
    after = sorted(os.sched_getaffinity(0))
    result["available_after"] = after
    result["applied"] = after == sorted(requested)
    if not result["applied"]:
        result["reason"] = "operating system did not retain the requested affinity mask"
    return result


def apply_address_space_limit(memory_limit_gib: float | None) -> int | None:
    """Apply the declared Linux per-worker address-space ceiling."""
    if memory_limit_gib is None or not platform.system().startswith("Linux"):
        return None
    if memory_limit_gib <= 0:
        raise ValueError("memory limit must be positive")
    requested = int(memory_limit_gib * (1 << 30))
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    applied = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    resource.setrlimit(resource.RLIMIT_AS, (applied, hard))
    return int(resource.getrlimit(resource.RLIMIT_AS)[0])


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
    return result.stdout.strip()


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


def require_clean_checkout(root: Path) -> dict[str, Any]:
    source = _git_metadata(root)
    if source["commit"] is None or source["dirty"] is not False:
        raise ValueError("the benchmark checkout must be clean and committed")
    return source


def discover_ec2() -> dict[str, str]:
    """Read IMDSv2 directly, without proxies, retries, or a v1 fallback."""
    opener = build_opener(ProxyHandler({}))
    base = "http://169.254.169.254/latest/"

    def request(path: str, *, token: str | None = None) -> str:
        headers = ({"X-aws-ec2-metadata-token": token} if token is not None else
                   {"X-aws-ec2-metadata-token-ttl-seconds": "300"})
        req = Request(base + path, headers=headers, method="GET" if token else "PUT")
        with opener.open(req, timeout=2) as response:
            return response.read(65536).decode().strip()

    try:
        token = request("api/token")
        if not token:
            raise ValueError("empty IMDSv2 token")
        identity = json.loads(request("dynamic/instance-identity/document", token=token))
        lifecycle = request("meta-data/instance-life-cycle", token=token)
        cloud = {key: identity[field] for key, field in {
            "instance_id": "instanceId", "instance_type": "instanceType", "image_id": "imageId",
            "region": "region", "availability_zone": "availabilityZone",
        }.items()}
        cloud.update(provider="aws", lifecycle=lifecycle,
                     boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        if any(not isinstance(value, str) or not value for value in cloud.values()):
            raise ValueError("incomplete EC2 identity")
        return cloud
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"cannot discover EC2 identity using IMDSv2: {error}") from error


def require_reference_host(root: Path, reference: dict, memory_limit_gib: float,
                           cpu: int | None) -> dict[str, str]:
    require_clean_checkout(root)
    if platform.system() != "Linux" or platform.machine() != reference["architecture"]:
        raise ValueError("reference host requires Linux with the declared architecture")
    if reference["provider"] != "aws":
        raise ValueError("reference collection requires AWS")
    os_release = platform.freedesktop_os_release()
    if (os_release.get("ID"), os_release.get("VERSION_ID")) != (
        reference["os_id"],
        reference["os_version"],
    ):
        raise ValueError("reference host OS does not match the campaign")
    if _memory_bytes() is None or _memory_bytes() < memory_limit_gib * (1 << 30):
        raise ValueError("reference host has insufficient memory for the declared ceiling")
    allowed = _allowed_cpus()
    if allowed is None or cpu not in allowed:
        raise ValueError("reference host requires an available logical CPU")
    cloud = discover_ec2()
    if cloud["instance_type"] != reference["instance_type"]:
        raise ValueError("reference host instance type does not match the campaign")
    if cloud["lifecycle"] != "on-demand":
        raise ValueError("reference host requires on-demand lifecycle")
    return cloud


def ec2_workflow(root: Path, cloud: dict[str, str], run_id: str) -> dict[str, Any]:
    remote = _run_text(["git", "remote", "get-url", "origin"], root) or ""
    parsed = urlsplit(remote)
    if parsed.scheme in ("http", "https"):
        remote = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1],
                            parsed.path, "", ""))
    os_release = platform.freedesktop_os_release()
    return {
        "provider": "aws-ec2-manual", "repository": remote,
        "workflow": "scripts/ec2-run.sh", "run_id": run_id,
        "ref": _run_text(["git", "branch", "--show-current"], root),
        "sha": _git_metadata(root)["commit"], "runner_name": cloud["instance_id"],
        "runner_os": "Linux", "image_os": f"{os_release['ID']}-{os_release['VERSION_ID']}",
        "image_version": cloud["image_id"],
    }


def collect_runner_metadata(
    root: Path, *, thread_environment: dict[str, str] | None = None,
    cloud: dict[str, str] | None = None
) -> dict[str, Any]:
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
        "thread_environment": (thread_environment or THREAD_LIMIT_ENVIRONMENT).copy(),
        "accelerator": None,
        "cloud": cloud,
        "suite_source": _git_metadata(root),
    }


def collect_workflow_metadata() -> dict[str, Any]:
    """Capture GitHub Actions provenance for local smoke runs."""
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
