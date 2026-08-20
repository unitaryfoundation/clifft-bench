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
