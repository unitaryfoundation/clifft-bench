from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = Path(__file__).resolve().parents[1]
TSIM_COMMIT = "44f64ba79c91e6ada77d6b609595ad0a3d767c11"
# The paper's successful circuits first; old failures remain in the experiment.
WORKLOADS = (
    "surface-code-d7-r7-p1e-3",
    "msc-d3-inject-cultivate-p1e-3",
    "distillation-color-code-85q-p5e-2",
    "coherent-surface-d3-r1-p1e-3-rz2e-2",
    "coherent-surface-d5-r1-p1e-3-rz2e-2",
    "msc-d5-inject-cultivate-p1e-3",
    "coherent-surface-d3-r3-p1e-3-rz2e-2",
    "coherent-surface-d5-r5-p1e-3-rz2e-2",
)
ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "XLA_FLAGS": "",
    "JAX_PLATFORMS": "cuda",  # Failure to initialize CUDA must be fatal.
    "JAX_ENABLE_COMPILATION_CACHE": "false",  # Cold JIT timing is reproducible.
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
}

# Strip inherited CPU-harness overrides and use the pinned JAX defaults.
JAX_DEFAULT_ENVIRONMENT = (
    "JAX_ENABLE_X64",
    "JAX_DEFAULT_MATMUL_PRECISION",
    "XLA_PYTHON_CLIENT_PREALLOCATE",
    "XLA_PYTHON_CLIENT_MEM_FRACTION",
    "XLA_PYTHON_CLIENT_ALLOCATOR",
)


def worker_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in {**os.environ, **ENVIRONMENT}.items()
        if key not in JAX_DEFAULT_ENVIRONMENT
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def workloads() -> dict:
    manifest = ROOT / "manifests/workloads.v1.json"
    selected = {}
    for workload in json.loads(manifest.read_text())["workloads"]:
        if workload["id"] not in WORKLOADS:
            continue
        artifact = (manifest.parent / workload["artifact"]["path"]).resolve()
        if file_digest(artifact) != workload["artifact"]["sha256"]:
            raise ValueError(f"circuit digest mismatch: {workload['id']}")
        if workload["semantics"]["reference_convention"] != "raw-record-parity":
            raise ValueError("unsupported reference convention")
        selected[workload["id"]] = {**workload, "artifact_path": str(artifact)}
    if set(selected) != set(WORKLOADS):
        raise ValueError("incomplete QEC corpus")
    return selected


def require_gpu(jax) -> list:
    try:
        devices = jax.devices()
    except Exception as error:
        raise RuntimeError("CUDA GPU initialization failed; CPU fallback is forbidden") from error
    if not devices or any(device.platform != "gpu" for device in devices):
        raise RuntimeError("This experiment requires CUDA GPU devices; CPU fallback is forbidden")
    if len(devices) != 1:
        raise RuntimeError("Select one GPU with CUDA_VISIBLE_DEVICES before running")
    return devices


def command(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, cwd=ROOT, timeout=10).strip()


def environment_metadata(*, allow_dirty: bool) -> dict:
    # Called in a short-lived process so the controller never reserves GPU memory.
    import jax
    import psutil

    devices = require_gpu(jax)
    source = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain"]))
    if dirty and not allow_dirty:
        raise ValueError("Commit the runner/configuration before collection (or use --allow-dirty)")
    distribution = importlib.metadata.distribution("bloqade-tsim")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct_url.get("vcs_info", {}).get("commit_id") != TSIM_COMMIT:
        raise ValueError(
            "Tsim installation does not match the pinned source commit; uv sync --locked"
        )
    if distribution.version != "0.1.5":
        raise ValueError("unexpected Tsim version")
    cpuinfo = Path("/proc/cpuinfo")
    cpu_model = platform.processor()
    if cpuinfo.exists():
        cpu_model = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu_model,
        )
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    return {
        "source_commit": source,
        "source_dirty": dirty,
        "runner_sha256": digest(
            {p.name: file_digest(p) for p in sorted((EXPERIMENT / "tsim_gpu").glob("*.py"))}
        ),
        "lock_sha256": file_digest(EXPERIMENT / "uv.lock"),
        "packages": dict(sorted(packages.items())),
        "tsim_direct_url": direct_url,
        "python": sys.version,
        "os": platform.platform(),
        "hostname": platform.node(),
        "cpu_model": cpu_model,
        "logical_cpus": psutil.cpu_count(),
        "cpu_affinity": psutil.Process().cpu_affinity() if sys.platform == "linux" else None,
        "host_memory_bytes": psutil.virtual_memory().total,
        "environment": {
            key: os.environ.get(key) for key in (*ENVIRONMENT, *JAX_DEFAULT_ENVIRONMENT)
        },
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "jax_devices": [
            {"id": d.id, "kind": d.device_kind, "platform": d.platform} for d in devices
        ],
        "nvidia_smi": command(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,driver_version,memory.total",
                "--format=csv,noheader",
            ]
        ),
        "jax_x64_enabled": bool(jax.config.jax_enable_x64),
        "jax_default_matmul_precision": jax.config.jax_default_matmul_precision,
        "jax_allocation_policy": "JAX defaults (no allocator/preallocation overrides)",
        "jax_compilation_cache_enabled": bool(jax.config.jax_enable_compilation_cache),
        "host_thread_policy": "BLAS/OpenMP=1; JAX may use the available host CPUs",
        "endpoint": "raw aggregate counts including host transfer and NumPy reduction",
    }
