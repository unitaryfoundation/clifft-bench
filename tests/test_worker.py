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
    durations = {1: 0.5, 32: 0.25}

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
    assert adapter.prepared_batch_sizes == [1, 32, 32]
    assert adapter.prepared_seeds == [3000010, 3000010, 0]
    assert calibration["candidates"] == [1, 32]
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
    assert adapter.prepared_batch_sizes == [1, 32, 1]


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
        "success",
        "error",
        "error",
        "error",
        "error",
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
    assert adapter.batches == [1, 32, 256, 1]
    assert prepared.batch_size == 1
    assert [r["status"] for r in prepared.runtime_metadata["batch_calibration"]["results"]] == [
        "success", "error", "success",
    ]


def test_tsim_small_capacities_are_versioned():
    from clifft_bench.calibration import calibration_candidates

    assert calibration_candidates("tsim", 100000) == [256, 1024, 8192, 65536]
    assert calibration_candidates("tsim", 100000, "bounded-v2") == [
        1, 32, 256, 1024, 8192, 65536,
    ]
    assert calibration_candidates("tsim", 16, "bounded-v2") == [1, 16]
    with pytest.raises(ValueError, match="unknown tuning policy"):
        calibration_candidates("tsim", 100, "unknown")


def test_bounded_tuning_freezes_native_proposals_and_measures_duplicate_sizes_once(monkeypatch):
    class Adapter(_CalibrationAdapter):
        name = "clifft"
        native_calls = 0

        def native_batch_choices(self, *, budget_seconds, shots, **kwargs):
            self.native_calls += 1
            assert budget_seconds == 0.25 and shots == 256
            return {"status": "success", "report": {
                "batch_size": 128, "baseline_batch_size": 32,
                "shot_workers": 1, "intra_shot_workers": 1,
            }}

    adapter = Adapter()
    monkeypatch.setattr(worker, "timed_sample", lambda p, shots, seed: (
        Counts(shots, shots, 0, 0), 0.1 if p.batch_size == 128 else 0.5,
    ))
    prepared = worker.calibrate_batch_size(
        adapter, artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={"batch_enabled": True, "batch_size": "calibrate",
                   "tuning_policy": "bounded-v2"}, shots_per_call=256, seed=10,
    )
    record = prepared.runtime_metadata["batch_calibration"]
    assert adapter.native_calls == 1
    assert record["candidates"] == [128, 32, 1, 256]
    assert adapter.prepared_batch_sizes == [128, 32, 1, 256, 128]
    assert record["selected_batch_size"] == 128
    assert record["policy"] == "bounded-v2"
    assert record["native"]["report"]["baseline_batch_size"] == 32
    assert all(type(n) is int for n in adapter.prepared_batch_sizes)
    assert all(s["peak_rss_bytes"] > 0 for r in record["results"] for s in r["samples"])


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
        execution={"batch_enabled": True, "batch_size": "calibrate",
                   "tuning_policy": "bounded-v2"}, shots_per_call=2048, seed=10,
    )
    record = prepared.runtime_metadata["batch_calibration"]
    assert adapter.prepared_batch_sizes == [1, 1]
    assert record["search_seconds"] == 80
    assert len(record["results"][0]["samples"]) == 3
    assert [row["status"] for row in record["results"]] == [
        "success", "budget-skipped", "budget-skipped", "budget-skipped", "budget-skipped",
    ]


def test_native_tuning_failure_does_not_discard_the_fixed_search(monkeypatch):
    class Adapter(_CalibrationAdapter):
        name = "clifft"

        def native_batch_choices(self, **kwargs):
            raise MemoryError("native tuner could not allocate its trial")

    adapter = Adapter()
    monkeypatch.setattr(worker, "timed_sample", lambda p, shots, seed: (
        Counts(shots, shots, 0, 0), 0.5,
    ))
    prepared = worker.calibrate_batch_size(
        adapter, artifact_path=SimpleNamespace(),
        workload={"semantics": {"postselect_all_detectors": False}},
        execution={"batch_enabled": True, "batch_size": "calibrate",
                   "tuning_policy": "bounded-v2"}, shots_per_call=32, seed=10,
    )
    record = prepared.runtime_metadata["batch_calibration"]
    assert record["native"]["error"]["type"] == "MemoryError"
    assert record["candidates"] == [1, 32]
    assert record["selected_batch_size"] == 1
