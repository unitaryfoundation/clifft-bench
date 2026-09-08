from __future__ import annotations

import fcntl

import pytest
from tsim_gpu.common import write_json
from tsim_gpu.finalize import finalize


def spool(tmp_path, *, successful=False):
    source = tmp_path / "spool"
    source.mkdir()
    (source / "collection.lock").touch()
    workload = {
        "id": "circuit",
        "artifact": {"sha256": "a" * 64},
        "semantics": {"reference_convention": "raw-record-parity"},
    }
    metadata = {
        "hardware": {"source_dirty": False},
        "fingerprint": "test",
        "workload_ids": ["circuit"],
        "options": {"strategies": ["cutting"], "repetitions": 1, "shots_per_call": 10},
    }
    write_json(source / "metadata.json", metadata)
    result = {
        "workload": workload,
        "strategy": "cutting",
        "fingerprint": "test",
        "status": "timeout",
        "error": {"phase": "compile"},
    }
    if successful:
        result.update(
            status="success",
            selection_probe_rate=10,
            samples=[
                {
                    "attempted_shots": 10,
                    "accepted_shots": 7,
                    "discarded_shots": 3,
                    "logical_errors": 2,
                    "api_calls": 1,
                    "duration_seconds": 0.5,
                    "throughput_attempted_shots_per_second": 20,
                }
            ],
            summary={"median_attempted_shots_per_second": 20, "mad_attempted_shots_per_second": 0},
        )
    row = {
        "workload_id": "circuit",
        "artifact_sha256": workload["artifact"]["sha256"],
        "semantics": workload["semantics"],
        "selected_strategy": "cutting" if successful else None,
        "summary": result.get("summary"),
    }
    write_json(source / "summary.json", {"fingerprint": "test", "workloads": [row]})
    result_path = source / "raw/circuit/cutting/attempt-001/result.json"
    write_json(result_path, result)
    (result_path.parent / "worker.log").write_text("worker diagnostic\n")
    return source, result_path, result


@pytest.mark.parametrize("successful", [False, True])
def test_finalize_copies_successes_and_failures_without_overwriting(tmp_path, successful):
    source, path, _ = spool(tmp_path, successful=successful)
    target = finalize(source, "gpu-test", output_root=tmp_path / "results")
    assert (target / path.relative_to(source)).read_bytes() == path.read_bytes()
    assert (
        target / path.parent.relative_to(source) / "worker.log"
    ).read_text() == "worker diagnostic\n"
    assert not (target / "collection.lock").exists()
    assert path.exists()  # The recovery spool is preserved.
    with pytest.raises(FileExistsError, match="overwrite"):
        finalize(source, "gpu-test", output_root=tmp_path / "results")


def test_finalize_refuses_active_collection(tmp_path):
    source, _, _ = spool(tmp_path)
    with (source / "collection.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="still running"):
            finalize(source, "gpu-test", output_root=tmp_path / "results")


def test_finalize_rejects_incomplete_retry_and_leaves_destination_absent(tmp_path):
    source, path, _ = spool(tmp_path)
    (path.parent.parent / "attempt-002").mkdir()
    with pytest.raises(ValueError, match="incomplete strategy"):
        finalize(source, "gpu-test", output_root=tmp_path / "results")
    assert not (tmp_path / "results/gpu-test").exists()


@pytest.mark.parametrize("mutation", ["fingerprint", "counts", "rate", "selection", "dirty"])
def test_finalize_rejects_inconsistent_evidence(tmp_path, mutation):
    source, path, result = spool(tmp_path, successful=True)
    if mutation == "fingerprint":
        result["fingerprint"] = "different"
    elif mutation == "counts":
        result["samples"][0]["discarded_shots"] = 0
    elif mutation == "rate":
        result["samples"][0]["throughput_attempted_shots_per_second"] = 99
    elif mutation == "selection":
        result["selection_probe_rate"] = 0
        write_json(
            source / "summary.json",
            {
                "fingerprint": "test",
                "workloads": [
                    {
                        "workload_id": "circuit",
                        "artifact_sha256": "a" * 64,
                        "semantics": result["workload"]["semantics"],
                        "selected_strategy": None,
                        "summary": None,
                    }
                ],
            },
        )
    else:
        write_json(source / "metadata.json", {"hardware": {"source_dirty": True}})
    write_json(path, result)
    with pytest.raises(ValueError):
        finalize(source, "gpu-test", output_root=tmp_path / "results")
