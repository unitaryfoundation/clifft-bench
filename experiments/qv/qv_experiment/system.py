"""Small resource and provenance helpers for the standalone QV experiment."""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def select_physical_cpus(
    count: int,
    topology: list[tuple[int, int, int]] | None = None,
) -> list[int]:
    if count < 1:
        raise ValueError("thread count must be positive")
    if topology is None:
        if not hasattr(os, "sched_getaffinity"):
            return []
        topology = []
        for logical_cpu in sorted(os.sched_getaffinity(0)):
            root = Path(f"/sys/devices/system/cpu/cpu{logical_cpu}/topology")
            try:
                package = int((root / "physical_package_id").read_text().strip())
                core = int((root / "core_id").read_text().strip())
            except (OSError, ValueError):
                continue
            topology.append((logical_cpu, package, core))

    selected: list[int] = []
    seen: set[tuple[int, int]] = set()
    for logical_cpu, package, core in sorted(topology):
        identity = (package, core)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(logical_cpu)
        if len(selected) == count:
            return selected
    if topology:
        raise ValueError(
            f"requested {count} physical cores, but only {len(selected)} are available"
        )
    return []


def apply_resources(cpu_set: list[int], memory_limit_gib: float) -> dict[str, Any]:
    affinity_applied = False
    if cpu_set:
        if not hasattr(os, "sched_setaffinity"):
            raise RuntimeError("CPU affinity is unavailable")
        os.sched_setaffinity(0, set(cpu_set))
        affinity_applied = sorted(os.sched_getaffinity(0)) == sorted(cpu_set)
        if not affinity_applied:
            raise RuntimeError("operating system did not retain the requested CPU set")

    address_space_limit = None
    if platform.system() == "Linux":
        requested = int(memory_limit_gib * (1 << 30))
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        applied = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        resource.setrlimit(resource.RLIMIT_AS, (applied, hard))
        address_space_limit = int(resource.getrlimit(resource.RLIMIT_AS)[0])
        if address_space_limit != requested:
            raise RuntimeError(
                f"requested {requested} address-space bytes, got {address_space_limit}"
            )
    return {
        "cpu_set": cpu_set,
        "affinity_applied": affinity_applied,
        "address_space_limit_bytes": address_space_limit,
    }


def peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if platform.system() == "Darwin" else peak * 1024


def git_metadata(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def _cpu_model() -> str:
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


def system_metadata() -> dict[str, Any]:
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "allowed_cpus": (
            sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
        ),
        "boot_id": boot_path.read_text().strip() if boot_path.is_file() else None,
    }


def ec2_identity(*, required: bool) -> dict[str, Any] | None:
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    try:
        with urllib.request.urlopen(token_request, timeout=2) as response:
            token = response.read().decode()
        identity_request = urllib.request.Request(
            "http://169.254.169.254/latest/dynamic/instance-identity/document",
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(identity_request, timeout=2) as response:
            return json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        if required:
            raise RuntimeError("EC2 IMDSv2 identity is required") from error
        return None
