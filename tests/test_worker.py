from __future__ import annotations

import weakref
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


def test_aggregate_sample_excludes_stream_setup_and_records_peak_rss(monkeypatch):
    calls = []

    def sample(_prepared, shots, seed):
        calls.append(seed)
        return Counts(shots, shots, 0, 0), 0.1

    monkeypatch.setattr(worker, "timed_sample", sample)
    monkeypatch.setattr(worker, "begin_sample", lambda *_: 2.0)
    prepared = SimpleNamespace()
    result = worker.aggregate_sample(
        prepared, shots_per_call=10, min_seconds=0.4, seed=1,
        postselect=False, max_api_calls=10,
    )
    assert calls == [1, 2, 3, 4]
    assert result["attempted_shots"] == 40
    assert result["duration_seconds"] == pytest.approx(0.4)
    assert result["stream_setup_seconds"] == 2.0
    assert result["peak_rss_bytes"] > 0


class _CalibrationAdapter:
    name = "clifft"

    def __init__(self) -> None:
        self.prepared_batch_sizes: list[int] = []
        self.prepared_seeds: list[int] = []

    def prepare(self, *, artifact_path, workload, execution, seed=0):
        del artifact_path, workload
        batch_size = int(execution["batch_size"])
        assert execution["batch_enabled"] is (batch_size > 1)
        self.prepared_batch_sizes.append(batch_size)
        self.prepared_seeds.append(seed)
        return SimpleNamespace(
            batch_size=batch_size,
            runtime_metadata={
                "batch_enabled": batch_size > 1,
                "effective_batch_size": batch_size,
            },
        )


def test_batch_calibration_selects_best_median_and_records_probes(monkeypatch) -> None:
    adapter = _CalibrationAdapter()
    durations = {1: 0.5, 32: 0.25, 64: 0.4}

    def timed_sample(prepared, shots: int, seed: int):
        return Counts(shots, shots, 0, seed % 2), durations[prepared.batch_size]

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    prepared = worker.calibrate_batch_size(
        adapter,
        artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={
            "mode": "throughput",
            "batch_enabled": True,
            "batch_size": "calibrate",
            "sample_chunk_shots": 0,
        },
        shots_per_call=64,
        seed=10,
    )

    calibration = prepared.runtime_metadata["batch_calibration"]
    assert adapter.prepared_batch_sizes == [1, 32, 64, 32]
    assert adapter.prepared_seeds == [3000010, 3000010, 3000010, 0]
    assert calibration["candidates"] == [1, 32, 64]
    assert calibration["selected_batch_size"] == 32
    assert calibration["selection_statistic"] == "median_attempted_shots_per_second"
    assert calibration["tie_break"] == "smaller_batch_size"
    assert all(len(result["samples"]) == 3 for result in calibration["results"])


def test_batch_calibration_breaks_throughput_ties_toward_smaller_size(
    monkeypatch,
) -> None:
    adapter = _CalibrationAdapter()

    def timed_sample(_prepared, shots: int, _seed: int):
        return Counts(shots, shots, 0, 0), 0.25

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    prepared = worker.calibrate_batch_size(
        adapter,
        artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={
            "mode": "throughput",
            "batch_enabled": True,
            "batch_size": "calibrate",
            "sample_chunk_shots": 0,
        },
        shots_per_call=64,
        seed=10,
    )

    assert prepared.runtime_metadata["batch_calibration"]["selected_batch_size"] == 1
    assert adapter.prepared_batch_sizes == [1, 32, 64, 1]


def test_batch_calibration_records_unsupported_candidates_and_selects_scalar(
    monkeypatch,
) -> None:
    class ScalarOnlyAdapter(_CalibrationAdapter):
        def prepare(self, *, artifact_path, workload, execution, seed=0):
            if execution["batch_size"] != 1:
                raise TypeError("batch_size is unsupported")
            return super().prepare(
                artifact_path=artifact_path,
                workload=workload,
                execution=execution,
            )

    adapter = ScalarOnlyAdapter()

    def timed_sample(_prepared, shots: int, seed: int):
        return Counts(shots, shots, 0, seed % 2), 0.25

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    prepared = worker.calibrate_batch_size(
        adapter,
        artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={
            "mode": "throughput",
            "batch_enabled": True,
            "batch_size": "calibrate",
            "sample_chunk_shots": 0,
        },
        shots_per_call=2048,
        seed=10,
    )

    calibration = prepared.runtime_metadata["batch_calibration"]
    assert calibration["selected_batch_size"] == 1
    assert [result["status"] for result in calibration["results"]] == [
        "success", "error", "error", "error", "error", "error",
    ]


def test_stream_setup_runs_once_outside_accumulated_sampling_time(monkeypatch) -> None:
    events = []
    prepared = SimpleNamespace(begin_sample=lambda seed: events.append(("setup", seed)))

    def timed_sample(_prepared, shots, seed):
        events.append(("sample", seed))
        return Counts(shots, shots, 0, 0), 0.1

    monkeypatch.setattr(worker, "timed_sample", timed_sample)
    sample = worker.aggregate_sample(
        prepared, shots_per_call=4, min_seconds=0.25, seed=10, postselect=False, max_api_calls=100
    )
    assert events == [("setup", 10), ("sample", 10), ("sample", 11), ("sample", 12)]
    assert sample["duration_seconds"] == pytest.approx(0.3)
    assert sample["stream_setup_seconds"] >= 0


def test_installation_is_verified_before_any_calibration(monkeypatch):
    import io
    import json

    emitted = []

    def reject(**kwargs):
        assert kwargs == {"expected_commit": "wrong-commit", "source_url": "source-url"}
        raise RuntimeError("source identity mismatch")

    adapter = SimpleNamespace(verify_installation=reject)
    monkeypatch.setattr(worker, "isolate_protocol_stream", lambda: None)
    monkeypatch.setattr(worker, "emit", emitted.append)
    monkeypatch.setattr(worker, "load_adapter", lambda _: adapter)
    monkeypatch.setattr(
        worker.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "command": "prepare",
                    "workload": {},
                    "adapter": "symft",
                    "timeout_seconds": 1,
                    "expected_commit": "wrong-commit",
                    "source_url": "source-url",
                }
            )
            + "\n"
        ),
    )
    assert worker.main() == 1
    assert emitted[0]["error"]["message"] == "source identity mismatch"


def test_calibration_releases_native_state_before_preparing_another_trial(monkeypatch):
    class Prepared:
        def __init__(self, batch):
            self.batch_size = batch
            self.runtime_metadata = {"batch_enabled": batch > 1, "effective_batch_size": batch}

    class Adapter:
        name = "clifft"
        previous = None
        batches = []

        def prepare(self, *, execution, **kwargs):
            assert self.previous is None or self.previous() is None
            prepared = Prepared(execution["batch_size"])
            self.previous = weakref.ref(prepared)
            self.batches.append(prepared.batch_size)
            return prepared

    def sample(prepared, shots, seed):
        if prepared.batch_size == 32:
            raise MemoryError("native allocation failed during warmup")
        return Counts(shots, shots, 0, 0), 1.0

    monkeypatch.setattr(worker, "timed_sample", sample)
    adapter = Adapter()
    prepared = worker.calibrate_batch_size(
        adapter, artifact_path=None,
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={"batch_size": "calibrate", "batch_enabled": True},
        shots_per_call=256, seed=10,
    )
    assert adapter.batches == [1, 32, 64, 256, 1]
    assert prepared.batch_size == 1
    assert [r["status"] for r in prepared.runtime_metadata["batch_calibration"]["results"]] == [
        "success", "error", "success", "success",
    ]


@pytest.mark.parametrize("adapter, shots, expected", [
    ("clifft", 2048, [1, 32, 64, 256, 1024, 2048]),
    ("symft", 100, [1, 32, 64]),
    ("tsim", 100000, [1, 32, 256, 1024, 8192, 65536]),
    ("tsim", 16, [1, 16]),
    ("stim", 100000, [256, 1024, 4096, 16384, 65536, 100000]),
    ("xtim", 10, [10]),
])
def test_calibration_candidates_follow_tool_capacity_and_public_call(adapter, shots, expected):
    from clifft_bench.calibration import calibration_candidates

    assert calibration_candidates(adapter, shots) == expected


def test_bounded_tuning_stops_between_complete_candidates(monkeypatch):
    clock = [0.0]
    adapter = _CalibrationAdapter()
    monkeypatch.setattr(worker.time, "perf_counter", lambda: clock[0])

    def sample(prepared, shots, seed):
        clock[0] += 20
        return Counts(shots, shots, 0, 0), 1.0

    monkeypatch.setattr(worker, "timed_sample", sample)
    prepared = worker.calibrate_batch_size(
        adapter, artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={"batch_enabled": True, "batch_size": "calibrate"}, shots_per_call=2048, seed=10,
    )
    record = prepared.runtime_metadata["batch_calibration"]
    assert adapter.prepared_batch_sizes == [1, 1]
    assert record["search_seconds"] == 80
    assert len(record["results"][0]["samples"]) == 3
    assert [row["status"] for row in record["results"]] == [
        "success", *["budget-skipped"] * (len(record["candidates"]) - 1),
    ]



def test_calibration_retains_completed_probes_when_a_later_probe_fails(monkeypatch):
    adapter = _CalibrationAdapter()

    def sample(prepared, shots, seed):
        if prepared.batch_size == 32 and seed == 1000010:
            raise MemoryError("second probe failed")
        return Counts(shots, shots, 0, 0), 1.0

    monkeypatch.setattr(worker, "timed_sample", sample)
    prepared = worker.calibrate_batch_size(
        adapter, artifact_path=None,
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={"batch_enabled": True, "batch_size": "calibrate"},
        shots_per_call=32, seed=10,
    )
    failed = prepared.runtime_metadata["batch_calibration"]["results"][1]
    assert failed["status"] == "error"
    assert failed["error"]["type"] == "MemoryError"
    assert len(failed["samples"]) == 1
    assert failed["samples"][0]["peak_rss_bytes"] > 0


def test_calibration_does_not_swallow_the_hard_preparation_deadline(monkeypatch):
    adapter = _CalibrationAdapter()

    def sample(*args):
        raise worker.WorkerTimeout("preparation deadline")

    monkeypatch.setattr(worker, "timed_sample", sample)
    with pytest.raises(worker.WorkerTimeout, match="preparation deadline"):
        worker.calibrate_batch_size(
            adapter, artifact_path=None,
            workload={"semantics": {"postselect_all_detectors": False}},
            execution={"batch_enabled": True, "batch_size": "calibrate"},
            shots_per_call=64, seed=10,
        )
    assert adapter.prepared_batch_sizes == [1]


@pytest.mark.parametrize("final_prepare_fails", [False, True])
def test_calibration_failure_keeps_search_evidence(monkeypatch, final_prepare_fails):
    class Adapter(_CalibrationAdapter):
        def prepare(self, *, seed=0, **kwargs):
            if not final_prepare_fails or seed == 0:
                raise MemoryError("native allocation failed")
            return super().prepare(seed=seed, **kwargs)

    monkeypatch.setattr(worker, "timed_sample",
                        lambda p, shots, seed: (Counts(shots, shots, 0, 0), 1.0))
    with pytest.raises(worker.CalibrationError) as caught:
        worker.calibrate_batch_size(
            Adapter(), artifact_path=None,
            workload={"semantics": {"postselect_all_detectors": False}},
            execution={"batch_enabled": True, "batch_size": "calibrate"},
            shots_per_call=32, seed=10,
        )
    record = caught.value.record
    assert record["selected_batch_size"] == (1 if final_prepare_fails else None)
    assert [r["batch_size"] for r in record["results"]] == record["candidates"]
    assert all(r["status"] == ("success" if final_prepare_fails else "error")
               for r in record["results"])
    if final_prepare_fails:
        assert all(len(r["samples"]) == 3 for r in record["results"])
    else:
        assert all(r["error"]["type"] == "MemoryError" for r in record["results"])



def test_worker_returns_calibration_failures_with_their_evidence(monkeypatch):
    import io
    import json

    def fail(**kwargs):
        raise MemoryError("cannot prepare candidate")

    adapter = SimpleNamespace(name="clifft", prepare=fail, verify_installation=lambda **kw: {})
    emitted = []
    monkeypatch.setattr(worker, "isolate_protocol_stream", lambda: None)
    monkeypatch.setattr(worker, "emit", emitted.append)
    monkeypatch.setattr(worker, "load_adapter", lambda _: adapter)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps({
        "command": "prepare", "adapter": "clifft", "artifact_path": "unused.stim",
        "workload": {"semantics": {"postselect_all_detectors": False}},
        "execution": {"batch_enabled": True, "batch_size": "calibrate"},
        "shots_per_call": 32, "seed": 10, "sampling_seed": 7, "timeout_seconds": 5,
        "expected_commit": "test", "source_url": "test",
    }) + "\n"))
    assert worker.main() == 1
    assert emitted[0]["ok"] is False
    error = emitted[0]["error"]
    assert error["type"] == "CalibrationError"
    record = error["batch_calibration"]
    assert record["selected_batch_size"] is None
    assert [r["error"]["type"] for r in record["results"]] == ["MemoryError", "MemoryError"]
