"""One-off local BT69 evidence; reuse the maintained worker and exact reference."""
from __future__ import annotations

import hashlib
import json
import math
import resource
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from test_code_switching import _case, bt_observable_probability  # noqa: E402

from clifft_bench.runner import WorkerClient  # noqa: E402
from clifft_bench.system import choose_cpu, collect_runner_metadata, utc_now  # noqa: E402

output = Path(sys.argv[1])
assert not output.exists(), "refuse to overwrite evidence"
assert not subprocess.check_output(
    ["git", "status", "--porcelain"], cwd=ROOT, text=True
).strip(), "run from the committed evidence checkout"
seeds = json.loads(Path(__file__).with_name("seeds.json").read_text())
document = {
    "purpose": "Local independent BT observable-0 correctness; no performance claims",
    "started_at": utc_now(),
    "command": [sys.executable, *sys.argv],
    "host": collect_runner_metadata(ROOT),
    "reference": {
        "file": "tests/test_code_switching.py",
        "sha256": hashlib.sha256((ROOT / "tests/test_code_switching.py").read_bytes()).hexdigest(),
        "method": "Exact CNOT linear forms / T phase difference with deferred branch records",
        "scope": "Raw unselected observable-0 marginal only; not the full joint distribution",
    },
    "bounds": {
        "overall_seconds": 900, "setup_seconds": 300, "request_seconds": 120,
        "requested_memory_limit_gib": 12,
        "note": "The worker records actual affinity and address-space enforcement; macOS "
                "does not support the harness Linux affinity/RLIMIT_AS enforcement.",
    },
    "cases": [],
}
deadline = time.monotonic() + 900
try:
    for workload in ("bt27-direct-x", "bt81-direct-x"):
        original = _case(workload)
        probability = bt_observable_probability(original.workload.artifact_path.read_text())
        for batch in (1, 32):
            definition = deepcopy(original.definition)
            definition["execution"]["batch_size"] = batch
            case = replace(original, definition=definition)
            result = {
                "workload_id": workload,
                "workload": case.workload.definition,
                "artifact_sha256": case.workload.artifact_sha256,
                "implementation": case.implementation.definition,
                "execution": definition["execution"],
                "exact_probability": str(probability),
                "setup_seed": 17,
                "shots_per_sample": 65537,
                "seeds": seeds[f"{workload}--{batch}"],
                "samples": [],
            }
            document["cases"].append(result)
            worker = WorkerClient(
                case, choose_cpu(None), python_executable=sys.executable, deadline=deadline,
            )
            try:
                result["setup"] = worker.prepare(300, 12, seed=17)
                assert result["setup"]["runtime_metadata"]["reference_convention"] == (
                    "raw-record-parity"
                )
                for seed in result["seeds"]:
                    sample = worker.request(
                        {"command": "correctness", "shots": 65537, "seed": seed},
                        timeout_seconds=120,
                    )
                    result["samples"].append({"seed": seed, **sample})
                    assert sample["contract_errors"] == []
                    assert sample["attempted_shots"] == sample["accepted_shots"] == 65537
                    assert sample["discarded_shots"] == 0
                    p = float(probability)
                    tolerance = 6 * math.sqrt(65537 * p * (1 - p)) + 1
                    assert abs(sample["logical_errors"] - 65537 * p) <= tolerance
                shots = sum(s["attempted_shots"] for s in result["samples"])
                ones = sum(s["logical_errors"] for s in result["samples"])
                assert abs(ones - shots * p) <= 6 * math.sqrt(shots * p * (1 - p)) + 1
                result["passed"] = True
                print(f"{workload} batch={batch}: {ones}/{shots} = {ones / shots:.8f}", flush=True)
            finally:
                worker.close()
                result["children_peak_rss_native_units"] = resource.getrusage(
                    resource.RUSAGE_CHILDREN
                ).ru_maxrss
    document["passed"] = True
finally:
    document["finished_at"] = utc_now()
    with output.open("x") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")
