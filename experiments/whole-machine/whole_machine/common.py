from __future__ import annotations

import hashlib
import importlib.metadata as im
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import ROOT

EXPERIMENT = Path(__file__).resolve().parents[1]
TOOLS = ("clifft", "symft", "stim", "tsim")
DIRECT = (
    "surface-code-d7-r7-p1e-3",
    "coherent-surface-d3-r1-p1e-3-rz2e-2",
    "coherent-surface-d5-r1-p1e-3-rz2e-2",
)
PINNED = {
    "clifft": ("0.10.0rc1", None, None),
    "stim": ("1.16.0", None, None),
    "symft": (
        "0.1.1",
        "https://github.com/haoliri0/SOFT",
        "c89b98514a919240b8afa53a271e08d926d3c987",
    ),
    "bloqade-tsim": (
        "0.1.5",
        "https://github.com/QuEraComputing/tsim",
        "44f64ba79c91e6ada77d6b609595ad0a3d767c11",
    ),
    "numpy": ("2.5.2", None, None),
    "jax": ("0.11.1", None, None),
    "jaxlib": ("0.11.1", None, None),
    "pyzx-param": ("0.9.3", None, None),
}
ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "OMP_PROC_BIND": "false",
    "OMP_DYNAMIC": "false",
    "OMP_MAX_ACTIVE_LEVELS": "1",
    "JAX_PLATFORMS": "cpu",
    "JAX_ENABLE_COMPILATION_CACHE": "false",
    "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
    "CUDA_VISIBLE_DEVICES": "",
    "PYTHONHASHSEED": "0",
}
COUNT_KEYS = ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors")
PRIOR_CPU = ROOT / "results/release-v1/release-v1-20260909-130000/raw/release-v1-p01-r01-raw.json"
PRIOR_GPU = ROOT / "experiments/tsim-gpu/results/tsim-gpu-20260909-131821/summary.json"


def now():
    return datetime.now(UTC).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stream_seed(*parts):
    return int(digest(parts)[:16], 16)


def environment():
    unset = {
        "JAX_ENABLE_X64",
        "JAX_DEFAULT_MATMUL_PRECISION",
        "XLA_PYTHON_CLIENT_ALLOCATOR",
        "XLA_PYTHON_CLIENT_MEM_FRACTION",
        "XLA_PYTHON_CLIENT_PREALLOCATE",
        "OMP_PLACES",
        "GOMP_CPU_AFFINITY",
        "KMP_AFFINITY",
        "OMP_THREAD_LIMIT",
    }
    return {k: v for k, v in {**os.environ, **ENVIRONMENT}.items() if k not in unset}


def validate_counts(counts):
    if any(type(counts[k]) is not int or counts[k] < 0 for k in COUNT_KEYS):
        raise ValueError("counts must be nonnegative integers")
    if counts["attempted_shots"] != counts["accepted_shots"] + counts["discarded_shots"]:
        raise ValueError("attempted != accepted + discarded")
    if counts["logical_errors"] > counts["accepted_shots"]:
        raise ValueError("logical errors exceed accepted shots")


def summarize(samples):
    rates = [s["attempted_shots"] / s["duration_seconds"] for s in samples]
    median = statistics.median(rates)
    return {
        "median_attempted_shots_per_second": median,
        "mad_attempted_shots_per_second": statistics.median(abs(r - median) for r in rates),
    }


def corpus():
    path = ROOT / "manifests/workloads.v1.json"
    result = {}
    for w in read(path)["workloads"]:
        if w["family"] == "quantum-volume":
            continue
        artifact = (path.parent / w["artifact"]["path"]).resolve()
        if file_digest(artifact) != w["artifact"]["sha256"]:
            raise ValueError(f"circuit digest mismatch: {w['id']}")
        if w["semantics"]["reference_convention"] != "raw-record-parity":
            raise ValueError("unsupported parity convention")
        result[w["id"]] = {**w, "artifact_path": str(artifact)}
    if len(result) != 8 or not set(DIRECT) <= set(result):
        raise ValueError("unexpected QEC corpus")
    return result


def matrix(tools=TOOLS, workload_ids=None):
    return [
        {"tool": tool, "workload": w, "id": f"{w['id']}--{tool}"}
        for tool in tools
        for w in corpus().values()
        if (workload_ids is None or w["id"] in workload_ids)
        and (tool not in {"stim", "tsim"} or w["id"] in (DIRECT if tool == "tsim" else DIRECT[:1]))
    ]


def prior(tool, workload_id):
    if tool == "tsim":
        w = next(w for w in read(PRIOR_GPU)["workloads"] if w["workload_id"] == workload_id)
        return {"batch": 0, "rate": w["summary"]["median_attempted_shots_per_second"], "width": 0}
    c = next(
        c
        for c in read(PRIOR_CPU)["cases"]
        if c["variant_id"] == tool + "-current" and c["workload"]["id"] == workload_id
    )
    m = c["setup"]["runtime_metadata"]
    return {
        "batch": c["execution"]["batch_size"],
        "rate": c["summary"]["median_attempted_shots_per_second"],
        "width": m.get("peak_active_width", 0),
    }


def power_two(value):
    return 1 << max(0, int(math.log2(max(1, value))))


def candidates(case, options):
    tool, workers = case["tool"], options["workers"]
    old = prior(tool, case["workload"]["id"])
    if tool == "tsim":
        return [
            {"batch_size": 0, "sample_chunk_shots": 0, "shots_per_call": q}
            for q in (65536, 8192, 1048576)
        ]
    batches = [256, 1024, 4096, 16384, 65536, 0] if tool == "stim" else [1, 32, 256, 1024, 2048]
    batches = list(dict.fromkeys([old["batch"], *batches]))
    configs = []
    for batch in batches:
        chunk = max(batch, min(2048, power_two(old["rate"] * 0.25 / 4))) if tool == "symft" else 0
        minimum = 4 * max(1, batch, chunk)
        q = min(1048576, max(minimum, power_two(old["rate"] * 0.25)))
        configs.append(
            {
                "batch_size": batch,
                "sample_chunk_shots": chunk,
                "shots_per_call": q * (workers if tool in {"clifft", "symft"} else 1),
            }
        )
    return configs


def memory_skip(case, config, options):
    tool, workers = case["tool"], options["workers"]
    if tool in {"clifft", "symft"}:
        # A lower bound only; runtime RSS and the outer cgroup enforce the real ceiling.
        width = prior(tool, case["workload"]["id"])["width"]
        minimum = workers * 16 * (2**width) * max(1, config["batch_size"])
        if minimum > options["max_rss_gib"] * 2**30 * 0.75:
            return "dense state lower bound exceeds 75% of aggregate memory budget"
    if tool == "tsim":
        m = case["workload"]["expected_metadata"]
        output = workers * config["shots_per_call"] * (m["num_detectors"] + m["num_observables"])
        if output > options["max_output_gib"] * 2**30:
            return "aggregate returned detector/observable allocation exceeds output budget"
    return None


def installed():
    result = {}
    for name, (version, repo, commit) in PINNED.items():
        dist = im.distribution(name)
        url = json.loads(dist.read_text("direct_url.json") or "{}")
        if dist.version != version:
            raise ValueError(f"{name}: expected {version}, got {dist.version}")
        if commit and (
            url.get("vcs_info", {}).get("commit_id") != commit
            or url.get("url", "").removesuffix(".git") != repo
        ):
            raise ValueError(f"{name}: wrong Git source; run uv sync --locked")
        result[name] = {"version": dist.version, "direct_url": url}
    result["all_versions"] = {d.metadata["Name"]: d.version for d in im.distributions()}
    return result


def cgroup_memory():
    if not Path("/proc/self/cgroup").exists():
        return None
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            base = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
            return {
                "path": str(base),
                **{
                    name: (base / name).read_text().strip()
                    for name in ("memory.max", "memory.swap.max")
                    if (base / name).exists()
                },
            }
    return None


def ec2_identity():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://169.254.169.254/latest/"
    req = urllib.request.Request(
        url + "api/token", method="PUT", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}
    )
    with opener.open(req, timeout=2) as response:
        token = response.read().decode()
    req = urllib.request.Request(
        url + "dynamic/instance-identity/document", headers={"X-aws-ec2-metadata-token": token}
    )
    with opener.open(req, timeout=2) as response:
        return json.load(response)


def provenance(options, development):
    import psutil
    from clifft_bench.system import collect_runner_metadata

    def git(args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    source = {"commit": git(["rev-parse", "HEAD"]), "dirty": bool(git(["status", "--porcelain"]))}
    cpus = (
        sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else list(range(os.cpu_count()))
    )
    if len(cpus) < options["workers"]:
        raise ValueError("not enough available CPUs for requested workers")
    host = collect_runner_metadata(ROOT, thread_environment=ENVIRONMENT)
    limits = cgroup_memory()
    identity = None if development else ec2_identity()
    if not development:
        os_release = platform.freedesktop_os_release()
        if (
            identity["instanceType"] != "m8a.16xlarge"
            or identity["region"] != "us-east-1"
            or host["physical_cores"] != 64
            or len(cpus) != 64
            or options["workers"] != 64
            or os_release.get("ID") != "ubuntu"
            or os_release.get("VERSION_ID") != "24.04"
            or platform.machine() != "x86_64"
        ):
            raise ValueError("collection requires the approved 64-core m8a.16xlarge Ubuntu host")
        if source["dirty"]:
            raise ValueError("commit the experiment before collecting publication evidence")
        if (
            not limits
            or limits.get("memory.max", "max") == "max"
            or int(limits["memory.max"]) > 224 * 2**30
            or int(limits["memory.max"]) < options["max_rss_gib"] * 2**30
            or limits.get("memory.swap.max") != "0"
        ):
            raise ValueError(
                "use the documented systemd scope with MemoryMax=224G and MemorySwapMax=0"
            )
    if options["max_rss_gib"] * 2**30 > psutil.virtual_memory().total * 0.9:
        raise ValueError("memory budget must leave at least 10% of physical host RAM free")
    files = [
        *EXPERIMENT.glob("whole_machine/*.py"),
        EXPERIMENT / "uv.lock",
        EXPERIMENT / "pyproject.toml",
        PRIOR_CPU,
        PRIOR_GPU,
        ROOT / "manifests/workloads.v1.json",
        ROOT / "manifests/software.v1.json",
        *list((ROOT / "src/clifft_bench").rglob("*.py")),
    ]
    return {
        "source": source,
        "files": {str(p.relative_to(ROOT)): file_digest(p) for p in files},
        "packages": installed(),
        "host": host,
        "ec2": identity,
        "cgroup": limits,
        "cpus": cpus[: options["workers"]],
        "python": sys.version,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if Path("/proc/sys/kernel/random/boot_id").exists()
        else None,
        "development": development,
        "hourly_usd": 3.89504,
        "price_basis": "Linux On-Demand, shared tenancy, us-east-1, 2026-09-09; EBS excluded",
        "gpu_comparison_hourly_usd": 4.29,
    }
