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
        max_api_calls=10,
    )

    assert sample["api_calls"] == 3
    assert sample["adapter_timing_totals"]["native_seconds"] == pytest.approx(0.075)
    assert sample["adapter_call_timings"] == []


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


class _CalibrationAdapter:
    def __init__(self) -> None:
        self.prepared_batch_sizes: list[int] = []

    def prepare(self, *, artifact_path, workload, execution):
        del artifact_path, workload
        batch_size = int(execution["batch_size"])
        assert execution["batch_enabled"] is (batch_size > 1)
        self.prepared_batch_sizes.append(batch_size)
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
