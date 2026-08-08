from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.adapters.clifft import _PreparedClifft
from clifft_bench.adapters.symft import SymftAdapter


class _Sampler:
    def __init__(self, *, batch_size: int, sample_chunk_shots: int) -> None:
        self.info = {
            "threads": 1,
            "backend": "batch",
            "batch_size": batch_size,
            "sample_chunk_shots": sample_chunk_shots,
        }


class _Circuit:
    num_observables = 1

    def __init__(self, *, path: Path) -> None:
        self.path = path

    def compile_counts_sampler(self, **kwargs):
        self.compile_kwargs = kwargs
        return _Sampler(
            batch_size=int(kwargs["batch_size"]),
            sample_chunk_shots=int(kwargs["sample_chunk_shots"]),
        )


def _install_fake_symft(monkeypatch, *, batch_delta: int = 0, chunk_delta: int = 0) -> None:
    class Circuit(_Circuit):
        def compile_counts_sampler(self, **kwargs):
            return _Sampler(
                batch_size=int(kwargs["batch_size"]) + batch_delta,
                sample_chunk_shots=int(kwargs["sample_chunk_shots"]) + chunk_delta,
            )

    monkeypatch.setitem(
        sys.modules,
        "symft",
        SimpleNamespace(Circuit=Circuit, __version__="0.1.0", simd_backend=lambda: "test"),
    )


@pytest.mark.parametrize(
    ("batch_delta", "chunk_delta", "message"),
    [(1, 0, "batch size"), (0, 1, "sample chunk")],
)
def test_symft_rejects_effective_batching_mismatch(
    tmp_path: Path,
    monkeypatch,
    batch_delta: int,
    chunk_delta: int,
    message: str,
) -> None:
    _install_fake_symft(monkeypatch, batch_delta=batch_delta, chunk_delta=chunk_delta)
    with pytest.raises(RuntimeError, match=message):
        SymftAdapter().prepare(
            artifact_path=tmp_path / "unused.stim",
            workload={
                "semantics": {
                    "observable_index": 0,
                    "postselect_all_detectors": False,
                }
            },
            execution={
                "batch_enabled": True,
                "batch_size": 32,
                "sample_chunk_shots": 2048,
            },
        )


def test_clifft_counts_the_selected_observable() -> None:
    class FakeClifft:
        @staticmethod
        def sample_survivors(program, shots, *, seed, keep_records):
            assert (program, shots, seed, keep_records) == ("program", 10, 7, False)
            return SimpleNamespace(
                total_shots=10,
                passed_shots=10,
                discards=0,
                logical_errors=10,
                observable_ones=[2, 7],
            )

    counts = _PreparedClifft(FakeClifft, "program", 1, {}).sample(10, 7)
    assert counts.logical_errors == 7
