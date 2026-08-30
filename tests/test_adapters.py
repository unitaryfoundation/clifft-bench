from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from clifft_bench.adapters.clifft import ClifftAdapter, _PreparedClifft
from clifft_bench.adapters.symft import SymftAdapter
from clifft_bench.adapters.tsim import _PreparedTsim


class _Sampler:
    def __init__(
        self,
        *,
        batch_size: int,
        sample_chunk_shots: int,
        backend: str = "batch",
    ) -> None:
        self.info = {
            "threads": 1,
            "backend": backend,
            "batch_size": batch_size,
            "sample_chunk_shots": sample_chunk_shots,
            "num_qubits": 1,
            "num_measurements": 1,
            "num_detectors": 0,
            "max_active_qubits": 1,
        }
        self.preprocessing_timing = {}


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
                    "reference_convention": "raw-record-parity",
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


def test_clifft_auto_is_forwarded_and_records_effective_scalar_batch(
    tmp_path: Path, monkeypatch
) -> None:
    artifact = tmp_path / "circuit.stim"
    artifact.write_text("M 0\n")
    hir = SimpleNamespace(
        num_qubits=1,
        num_measurements=1,
        num_detectors=1,
        num_observables=1,
    )
    program = SimpleNamespace(
        num_qubits=1,
        num_measurements=1,
        num_detectors=1,
        num_observables=1,
        peak_active_width=3,
    )

    def sample_survivors(
        actual_program, shots, *, seed, keep_records, batch_size
    ):
        assert (actual_program, shots, seed, keep_records, batch_size) == (
            program,
            10,
            7,
            False,
            "auto",
        )
        return SimpleNamespace(
            total_shots=10,
            passed_shots=8,
            discards=2,
            observable_ones=[1],
        )

    manager = SimpleNamespace(run=lambda value: None)
    fake_clifft = SimpleNamespace(
        sample_survivors=sample_survivors,
        set_num_threads=lambda threads: None,
        get_num_threads=lambda: 1,
        parse=lambda text: text,
        trace=lambda circuit: hir,
        default_hir_pass_manager=lambda: manager,
        lower=lambda actual_hir, postselection_mask: program,
        version=lambda: "0.10.0",
        svm_backend=lambda: "test",
    )
    monkeypatch.setitem(sys.modules, "clifft", fake_clifft)

    prepared = ClifftAdapter().prepare(
        artifact_path=artifact,
        workload={
            "semantics": {
                "observable_index": 0,
                "postselect_all_detectors": True,
                "reference_convention": "raw-record-parity",
            }
        },
        execution={
            "batch_enabled": True,
            "batch_size": "auto",
            "sample_chunk_shots": 0,
        },
    )

    assert prepared.runtime_metadata["batch_enabled"] is True
    assert prepared.runtime_metadata["effective_batch_size"] == 1
    assert prepared.sample(10, 7).attempted_shots == 10


def test_clifft_auto_rejects_unresolved_nonpostselected_batch(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "clifft", SimpleNamespace())
    with pytest.raises(ValueError, match="only supported for postselected workloads"):
        ClifftAdapter().prepare(
            artifact_path=tmp_path / "unused.stim",
            workload={
                "semantics": {
                    "observable_index": 0,
                    "postselect_all_detectors": False,
                    "reference_convention": "raw-record-parity",
                }
            },
            execution={
                "batch_enabled": True,
                "batch_size": "auto",
                "sample_chunk_shots": 0,
            },
        )


def test_symft_auto_uses_native_sentinel_and_records_selected_batch(
    tmp_path: Path, monkeypatch
) -> None:
    class AutoCircuit(_Circuit):
        def compile_counts_sampler(self, **kwargs):
            assert kwargs["batch"] is True
            assert kwargs["batch_size"] == 0
            assert kwargs["sample_chunk_shots"] == 0
            return _Sampler(batch_size=256, sample_chunk_shots=4096)

    monkeypatch.setitem(
        sys.modules,
        "symft",
        SimpleNamespace(
            Circuit=AutoCircuit,
            __version__="0.1.0",
            simd_backend=lambda: "test",
        ),
    )
    prepared = SymftAdapter().prepare(
        artifact_path=tmp_path / "unused.stim",
        workload={
            "semantics": {
                "observable_index": 0,
                "postselect_all_detectors": True,
                "reference_convention": "raw-record-parity",
            }
        },
        execution={
            "batch_enabled": True,
            "batch_size": "auto",
            "sample_chunk_shots": 0,
        },
    )

    assert prepared.runtime_metadata["effective_batch_size"] == 256
    assert prepared.runtime_metadata["sample_chunk_shots"] == 4096


def test_symft_single_backend_normalizes_disabled_batch_sentinel(
    tmp_path: Path, monkeypatch
) -> None:
    class SingleCircuit(_Circuit):
        def compile_counts_sampler(self, **kwargs):
            return _Sampler(
                batch_size=0,
                sample_chunk_shots=int(kwargs["sample_chunk_shots"]),
                backend="single",
            )

    monkeypatch.setitem(
        sys.modules,
        "symft",
        SimpleNamespace(
            Circuit=SingleCircuit,
            __version__="0.1.0",
            simd_backend=lambda: "test",
        ),
    )
    prepared = SymftAdapter().prepare(
        artifact_path=tmp_path / "unused.stim",
        workload={
            "semantics": {
                "observable_index": 0,
                "postselect_all_detectors": False,
                "reference_convention": "raw-record-parity",
            }
        },
        execution={
            "batch_enabled": False,
            "batch_size": 1,
            "sample_chunk_shots": 2048,
        },
    )
    assert prepared.runtime_metadata["effective_batch_size"] == 1


def test_tsim_reduces_raw_detector_and_observable_arrays_to_counts() -> None:
    class Sampler:
        def sample(self, shots, **kwargs):
            assert shots == 4
            assert kwargs["batch_size"] == 32
            detectors = np.array([[False], [True], [False], [True]])
            observables = np.array([[True], [True], [False], [False]])
            return detectors, observables

    prepared = _PreparedTsim(
        Sampler(),
        np,
        batch_size=32,
        observable_index=0,
        postselection_mask=np.array([True]),
        runtime_metadata={},
    )

    counts = prepared.sample(4, seed=7)

    assert counts.attempted_shots == 4
    assert counts.accepted_shots == 2
    assert counts.discarded_shots == 2
    assert counts.logical_errors == 1


@pytest.mark.parametrize("adapter", [ClifftAdapter(), SymftAdapter()])
def test_adapters_reject_unsupported_reference_convention(
    adapter, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="does not support reference convention"):
        adapter.prepare(
            artifact_path=tmp_path / "unused.stim",
            workload={
                "semantics": {
                    "observable_index": 0,
                    "postselect_all_detectors": False,
                    "reference_convention": "reference-normalized-parity",
                }
            },
            execution={
                "batch_enabled": False,
                "batch_size": 1,
                "sample_chunk_shots": 0,
            },
        )
