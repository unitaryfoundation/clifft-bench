import copy
import os
import sys
import time

import psutil
import pytest

from whole_machine.common import (
    COUNT_KEYS,
    PINNED,
    candidates,
    matrix,
    memory_skip,
    read,
    stream_seed,
    summarize,
    write,
)
from whole_machine.finalize import finalize, validate_execution, validate_result
from whole_machine.run import DEFAULTS, attempt, choose, fingerprint, supervise
from whole_machine.worker import Pool


class SleepingSampler:
    """A slow counting API, to distinguish concurrent wall time from CPU-time sums."""

    def __init__(self, tool, workload, config, workers, seed):
        self.metadata = {"threads": workers}

    def begin(self, seed):
        pass

    def sample(self, shots):
        time.sleep(0.04)
        return dict(zip(COUNT_KEYS, (shots, shots, 0, 0)))


def request(tool="stim"):
    cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else [0, 1]
    return {
        "fingerprint": "test-only",
        "case": {"id": "test-only", "tool": tool, "workload": {}},
        "cpus": cpus[:2],
        "development": True,
        "seed": 42,
        "options": {**DEFAULTS, "workers": 2, "max_rss_gib": 1},
        "config": {"batch_size": 1, "sample_chunk_shots": 0, "shots_per_call": 32},
    }


@pytest.mark.parametrize("tool,native", [("stim", False), ("clifft", True)])
def test_persistent_workers_and_common_wall_clock(tool, native):
    req = request(tool)
    pool = Pool(req, req["config"], factory=SleepingSampler)
    processes = pool.processes.copy()
    try:
        first = pool.measure(32, 0.08, 0)
        second = pool.measure(32, 0.08, 1)
        n = 1 if native else 2
        assert len(pool.metadata) == n
        assert len({m["seed"] for m in pool.metadata}) == n
        assert [p.pid for p in pool.processes] == [m["pid"] for m in pool.metadata]
        assert all(p.is_alive() for p in pool.processes)
        for sample in (first, second):
            assert sample["attempted_shots"] == sample["api_calls"] * 32
            assert sample["accepted_shots"] == sample["attempted_shots"]
            assert 0.08 <= sample["duration_seconds"] < 0.5
            end = max(w["ended"] for w in sample["workers"])
            assert sample["duration_seconds"] >= end - sample["scheduled_start"]
            if not native:
                # A summed-duration denominator would almost double this interval.
                assert sample["duration_seconds"] < 0.8 * sum(
                    w["ended"] - sample["scheduled_start"] for w in sample["workers"]
                )
        if hasattr(os, "sched_getaffinity"):
            assert [m["affinity"] for m in pool.metadata] == (
                [req["cpus"]] if native else [[cpu] for cpu in req["cpus"]]
            )
    finally:
        pool.close()
    assert all(not p.is_alive() for p in processes)


def test_matrix_and_memory_budget():
    cases = matrix()
    assert len(cases) == 20
    assert {t: sum(c["tool"] == t for c in cases) for t in ("clifft", "symft", "stim", "tsim")} == {
        "clifft": 8,
        "symft": 8,
        "stim": 1,
        "tsim": 3,
    }
    wide = next(c for c in cases if c["tool"] == "symft" and "d5-r5" in c["id"])
    configs = candidates(wide, DEFAULTS)
    scalar = next(c for c in configs if c["batch_size"] == 1)
    assert memory_skip(wide, scalar, DEFAULTS) is None
    assert memory_skip(wide, next(c for c in configs if c["batch_size"] == 32), DEFAULTS) is None
    assert all(memory_skip(wide, c, DEFAULTS) for c in configs if c["batch_size"] > 32)
    for case in cases:
        for config in candidates(case, DEFAULTS):
            if case["tool"] == "symft":
                assert config["shots_per_call"] >= 64 * 4 * config["sample_chunk_shots"]


def test_failed_candidate_is_not_selected():
    probes = [
        {
            "candidate": 0,
            "result": {"status": "timeout", "summary": {"median_attempted_shots_per_second": 1e20}},
        },
        {
            "candidate": 1,
            "result": {
                "status": "success",
                "config": {"batch_size": 1, "shots_per_call": 32},
                "summary": {"median_attempted_shots_per_second": 100},
            },
        },
    ]
    assert choose(probes)["candidate"] == 1
    assert choose(probes[:1]) is None


def test_fingerprint_ignores_transient_load_but_not_configuration():
    metadata = {
        "created_at": "first",
        "provenance": {
            "host": {"load_average_at_start": [0, 0, 0]},
            "cgroup": {"path": "first.scope", "memory.max": "100"},
        },
        "options": DEFAULTS,
    }
    fp = fingerprint(metadata)
    modified = copy.deepcopy(metadata)
    modified.update(created_at="later", fingerprint=fp)
    modified["provenance"]["host"]["load_average_at_start"] = [64, 64, 64]
    modified["provenance"]["cgroup"]["path"] = "second.scope"
    assert fingerprint(modified) == fp
    modified["options"]["workers"] = 63
    assert fingerprint(modified) != fp


def test_resume_reuses_evidence_and_rejects_stale_fingerprint(tmp_path):
    req = request()
    path = tmp_path / "attempt-001" / "result.json"
    write(path, {"status": "success", "fingerprint": req["fingerprint"]})
    result, reused = attempt(tmp_path, req, False, time.monotonic() + 2)
    assert reused == str(path) and result["status"] == "success"
    req["fingerprint"] = "changed"
    with pytest.raises(ValueError, match="fingerprint"):
        attempt(tmp_path, req, False, time.monotonic() + 2)


@pytest.mark.parametrize("guard", ["timeout", "memory-limit"])
def test_supervisor_stops_native_hang_and_descendant(tmp_path, guard):
    req = request()
    req["options"]["candidate_timeout_seconds"] = 0.7 if guard == "timeout" else 5
    req["options"]["max_rss_gib"] = 1 if guard == "timeout" else 0.00001
    script = tmp_path / "hang.py"
    pidfile = tmp_path / "child.pid"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(pidfile)!r}).write_text(str(p.pid))\ntime.sleep(60)\n"
    )
    result = supervise([sys.executable, str(script)], tmp_path, req, deadline=time.monotonic() + 10)
    assert result["status"] == guard
    assert result["wall_seconds"] < 6
    if pidfile.exists():
        pid = int(pidfile.read_text())
        for _ in range(20):
            if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(0.02)
        else:
            pytest.fail("supervisor left a descendant running")


def synthetic_result(req):
    """Synthetic accounting fixture; never collected or published as performance data."""
    n = 1 if req["case"]["tool"] in {"clifft", "symft"} else 64
    q = req["config"]["shots_per_call"]
    is_probe = req["kind"] == "probe"
    seconds, repetitions = (1, 3) if is_probe else (30, 5)
    w = dict(zip(COUNT_KEYS, (q, q, 0, 0)))
    w.update(api_calls=1, call_seconds=seconds, started=100, ended=100 + seconds)
    sample = {k: w[k] * n for k in (*COUNT_KEYS, "api_calls")}
    sample.update(
        duration_seconds=seconds + 0.1,
        shots_per_call=q,
        scheduled_start=100,
        workers=[copy.deepcopy(w) for _ in range(n)],
        throughput_attempted_shots_per_second=q * n / (seconds + 0.1),
    )
    samples = [copy.deepcopy(sample) for _ in range(repetitions)]
    return {
        "case_id": req["case"]["id"],
        "fingerprint": req["fingerprint"],
        "status": "success",
        "config": req["config"],
        "samples": samples,
        "summary": summarize(samples),
        "runtime": [
            {
                "affinity": list(range(64)) if n == 1 else [i],
                "metadata": {"threads": 64 if n == 1 else 1, "compiled_graph_count": 0},
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def evidence(tmp_path):
    packages = {
        k: {"version": v, "direct_url": {"url": repo, "vcs_info": {"commit_id": sha}}}
        for k, (v, repo, sha) in PINNED.items()
    }
    metadata = {
        "options": DEFAULTS,
        "cases": matrix(),
        "provenance": {
            "development": False,
            "source": {"dirty": False},
            "host": {},
            "cpus": list(range(64)),
            "ec2": {"instanceType": "m8a.16xlarge", "region": "us-east-1"},
            "packages": packages,
            "hourly_usd": 3.89504,
        },
    }
    metadata["fingerprint"] = fingerprint(metadata)
    summary = {"fingerprint": metadata["fingerprint"], "cases": []}
    for ci, case in enumerate(metadata["cases"]):
        probes = []
        base = {
            "fingerprint": metadata["fingerprint"],
            "case": case,
            "options": DEFAULTS,
            "cpus": list(range(64)),
            "development": False,
        }
        for index, config in enumerate(candidates(case, DEFAULTS)):
            why = memory_skip(case, config, DEFAULTS)
            if why:
                probes.append(
                    {
                        "candidate": index,
                        "result": {
                            "status": "memory-estimate-skip",
                            "config": config,
                            "error": why,
                        },
                    }
                )
                continue
            req = {
                **base,
                "kind": "probe",
                "config": config,
                "seed": stream_seed(DEFAULTS["seed"], case["id"], "probe", index),
            }
            raw = (
                synthetic_result(req)
                if ci == 0 and index == 0
                else {
                    "fingerprint": metadata["fingerprint"],
                    "case_id": case["id"],
                    "status": "timeout",
                }
            )
            directory = tmp_path / case["id"] / f"probe-{index}"
            write(directory / "request.json", req)
            write(directory / "result.json", raw)
            probes.append(
                {
                    "candidate": index,
                    "result": raw,
                    "path": str((directory / "result.json").relative_to(tmp_path)),
                }
            )
        row = {
            "case_id": case["id"],
            "tool": case["tool"],
            "workload_id": case["workload"]["id"],
            "artifact_sha256": case["workload"]["artifact"]["sha256"],
            "probes": probes,
            "selected_candidate": None,
            "status": "no-success",
            "summary": None,
        }
        if selected := choose(probes):
            req = {
                **base,
                "kind": "final",
                "config": selected["result"]["config"],
                "seed": stream_seed(DEFAULTS["seed"], case["id"], "final", selected["candidate"]),
            }
            raw = synthetic_result(req)
            directory = tmp_path / case["id"] / "final"
            write(directory / "request.json", req)
            write(directory / "result.json", raw)
            row.update(
                selected_candidate=selected["candidate"],
                status="success",
                summary=raw["summary"],
                config=req["config"],
                final_path=str((directory / "result.json").relative_to(tmp_path)),
            )
        summary["cases"].append(row)
    write(tmp_path / "metadata.json", metadata)
    write(tmp_path / "summary.json", summary)
    (tmp_path / "collection.lock").touch()
    return tmp_path


def test_finalize_recomputes_evidence_and_preserves_failures(evidence, tmp_path_factory):
    validate_execution(evidence)
    target = finalize(evidence, "synthetic-test-only", tmp_path_factory.mktemp("export"))
    assert (target / "cases.csv").exists()
    assert len(read(target / "summary.json")["cases"]) == 20
    with pytest.raises(ValueError, match="destination exists"):
        finalize(evidence, "synthetic-test-only", target.parent)


@pytest.mark.parametrize(
    "corruption", ["missing-case", "missing-probe", "wrong-selection", "dirty"]
)
def test_finalize_rejects_incomplete_or_changed_evidence(evidence, corruption):
    summary = read(evidence / "summary.json")
    if corruption == "missing-case":
        summary["cases"].pop()
    elif corruption == "missing-probe":
        summary["cases"][0]["probes"].pop()
    elif corruption == "wrong-selection":
        summary["cases"][0]["selected_candidate"] = 1
    else:
        meta = read(evidence / "metadata.json")
        meta["provenance"]["source"]["dirty"] = True
        write(evidence / "metadata.json", meta)
    write(evidence / "summary.json", summary)
    with pytest.raises(ValueError):
        validate_execution(evidence)


@pytest.mark.parametrize("corruption", ["resources", "counts", "wall-clock", "summary"])
def test_raw_validation_rejects_bad_measurements(evidence, corruption):
    meta = read(evidence / "metadata.json")
    row = read(evidence / "summary.json")["cases"][0]
    path = evidence / row["final_path"]
    result, req = read(path), read(path.parent / "request.json")
    if corruption == "resources":
        result["runtime"][0]["affinity"].pop()
    elif corruption == "counts":
        result["samples"][0]["attempted_shots"] += 1
    elif corruption == "wall-clock":
        result["samples"][0]["workers"][0]["ended"] += 20
    else:
        result["summary"]["median_attempted_shots_per_second"] *= 2
    with pytest.raises(ValueError):
        validate_result(result, req, meta)
