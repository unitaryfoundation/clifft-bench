from __future__ import annotations

from types import SimpleNamespace

import pytest

from clifft_bench import worker
from clifft_bench.adapters.base import Counts


def test_aggregate_sample_sums_adapter_timings_without_retaining_each_call(
    monkeypatch,
) -> None:
    prepared = SimpleNamespace()

    def timed_sample(_prepared, shots: int, seed: int):
        assert shots == 4
        return Counts(4, 4, 0, seed % 2, {"native_seconds": 0.025}), 0.1

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    sample = worker.aggregate_sample(
        prepared,
        shots_per_call=4,
        min_seconds=0.25,
        seed=10,
        postselect=False,
        max_api_calls=100,
    )

    assert sample["api_calls"] == 3
    assert sample["adapter_timing_totals"]["native_seconds"] == pytest.approx(0.075)


def test_aggregate_sample_stops_before_reusing_the_next_repetition_seed(monkeypatch) -> None:
    def timed_sample(_prepared, _shots: int, _seed: int):
        return Counts(1, 1, 0, 0), 0.1

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    with pytest.raises(RuntimeError, match="non-overlapping seed range"):
        worker.aggregate_sample(
            SimpleNamespace(),
            shots_per_call=1,
            min_seconds=0.3,
            seed=10,
            postselect=False,
            max_api_calls=2,
        )


def test_aggregate_sample_records_timing_and_peak_rss(monkeypatch):
    calls = []

    def sample(_prepared, shots, seed):
        calls.append(seed)
        return Counts(shots, shots, 0, 0), 0.1

    monkeypatch.setattr(worker, "timed_sample", sample)
    prepared = SimpleNamespace()
    result = worker.aggregate_sample(
        prepared, shots_per_call=10, min_seconds=0.4, seed=1,
        postselect=False, max_api_calls=10,
    )
    assert calls == [1, 2, 3, 4]
    assert result["attempted_shots"] == 40
    assert result["duration_seconds"] == pytest.approx(0.4)
    assert result["peak_rss_bytes"] > 0


def test_aggregate_sample_rejects_a_partial_call(monkeypatch):
    monkeypatch.setattr(worker, "timed_sample", lambda *args: (Counts(3, 3, 0, 0), 0.1))
    with pytest.raises(RuntimeError, match="attempted shots differ from requested shots"):
        worker.aggregate_sample(
            SimpleNamespace(), shots_per_call=4, min_seconds=0.1, seed=1,
            postselect=False, max_api_calls=10,
        )


@pytest.mark.parametrize("reject_installation", [False, True])
@pytest.mark.parametrize("partial_call", [False, True])
def test_worker_compiles_once_then_times_first_full_call_and_reuses_sampler(
    monkeypatch, reject_installation, partial_call
):
    import io
    import json

    clock, calls, emitted, preparations = [0.0], [], [], []

    def verify(**kwargs):
        if reject_installation:
            raise RuntimeError("source identity mismatch")
        return {}

    def sample(shots, seed):
        calls.append((shots, seed))
        clock[0] += 5.0 if len(calls) == 1 else 0.25
        if partial_call and len(calls) == 1:
            shots -= 1
        return Counts(shots, shots, 0, 0)

    def prepare(**kwargs):
        assert not calls
        preparations.append(kwargs)
        clock[0] += 2
        return SimpleNamespace(sample=sample, runtime_metadata={
            "version": "test", "compile_seconds": 2, "num_qubits": 1,
        })

    adapter = SimpleNamespace(adapter_version="1", prepare=prepare,
                              verify_installation=verify,
                              validate_execution=lambda execution, workload: 1)
    for key, value in worker.thread_environment(1).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(worker, "isolate_protocol_stream", lambda: None)
    monkeypatch.setattr(worker, "apply_affinity", lambda cpu, threads: {"applied": True})
    monkeypatch.setattr(worker, "load_adapter", lambda _: adapter)
    monkeypatch.setattr(worker, "emit", emitted.append)
    monkeypatch.setattr(worker.time, "perf_counter", lambda: clock[0])
    requests = [
        {"command": "prepare", "adapter": "fixture", "artifact_path": "unused",
         "workload": {"semantics": {"postselect_all_detectors": False},
                      "expected_metadata": {"num_qubits": 1}},
         "execution": {"threads": 1, "batch_size": "auto"}, "seed": 9,
         "timeout_seconds": 5, "expected_commit": "test", "source_url": "test",
         "expected_version": "test", "dependency_distributions": []},
        {"command": "first_call_warmup", "shots": 16, "seed": 9},
        {"command": "correctness", "shots": 4, "seed": 10},
        {"command": "sample", "shots_per_call": 16, "seed": 10010,
         "min_seconds": 0.4, "max_api_calls": 10},
        {"command": "close"},
    ]
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(
        "\n".join(json.dumps(request) for request in requests) + "\n"
    ))
    assert worker.main() == int(reject_installation)
    if reject_installation:
        assert emitted[0]["error"]["message"] == "source identity mismatch"
        assert not calls
        assert not preparations
    else:
        assert len(preparations) == 1
        assert calls == [(16, 9), (4, 10), (16, 10010), (16, 10011)]
        assert emitted[0]["duration_seconds"] == 2
        assert emitted[0]["runtime_metadata"]["compile_seconds"] == 2
        assert emitted[1]["duration_seconds"] == 5
        assert emitted[1]["contract_errors"] == (
            ["attempted shots differ from requested shots"] if partial_call else []
        )
        assert emitted[3]["duration_seconds"] == 0.5
        assert emitted[3]["attempted_shots"] == 32
