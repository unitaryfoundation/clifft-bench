"""SymFT aggregate-count adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedSymft(PreparedAdapter):
    def __init__(self, sampler: Any, runtime_metadata: dict[str, Any]) -> None:
        self._sampler = sampler
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        result = self._sampler.sample(shots=shots, stream_id=seed)
        timing = {key: float(value) for key, value in dict(result.get("timing", {})).items()}
        return Counts(
            attempted_shots=int(result["shots"]),
            accepted_shots=int(result["accepted"]),
            discarded_shots=int(result["discarded"]),
            logical_errors=int(result["logical_errors"]),
            adapter_timing=timing or None,
        )


class SymftAdapter(Adapter):
    name = "symft"

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreparedAdapter:
        import symft

        circuit = symft.Circuit(path=artifact_path)
        sampler = circuit.compile_counts_sampler(
            batch=bool(execution["batch_enabled"]),
            observable=int(workload["semantics"]["observable_index"]),
            postselect_detectors=bool(workload["semantics"]["postselect_all_detectors"]),
            batch_size=int(execution["batch_size"]),
            sample_chunk_shots=int(execution.get("sample_chunk_shots", 0)),
            threads=1,
            cuda=False,
        )
        info = dict(sampler.info)
        if int(info["threads"]) != 1:
            raise RuntimeError(f"SymFT planned more than one thread: {info}")
        expected_backend = "batch" if execution["batch_enabled"] else "single"
        if str(info["backend"]) != expected_backend:
            raise RuntimeError(
                f"SymFT backend {info['backend']!r} does not match {expected_backend!r}"
            )
        requested_batch_size = int(execution["batch_size"])
        if int(info["batch_size"]) != requested_batch_size:
            raise RuntimeError(
                f"SymFT batch size {info['batch_size']!r} does not match "
                f"requested {requested_batch_size}"
            )
        requested_chunk_shots = int(execution.get("sample_chunk_shots", 0))
        if int(info["sample_chunk_shots"]) != requested_chunk_shots:
            raise RuntimeError(
                f"SymFT sample chunk {info['sample_chunk_shots']!r} does not match "
                f"requested {requested_chunk_shots}"
            )

        preprocessing = {
            key: float(value) for key, value in dict(sampler.preprocessing_timing).items()
        }
        metadata = {
            "name": "symft",
            "version": str(symft.__version__),
            "threads": int(info["threads"]),
            "precision": "complex-fp64",
            "batch_enabled": bool(execution["batch_enabled"]),
            "effective_batch_size": int(info["batch_size"]),
            "sample_chunk_shots": int(info["sample_chunk_shots"]),
            "num_qubits": int(info["num_qubits"]),
            "num_measurements": int(info["num_measurements"]),
            "num_detectors": int(info["num_detectors"]),
            "num_observables": int(circuit.num_observables),
            "peak_active_width": int(info["max_active_qubits"]),
            "simd_backend": str(symft.simd_backend()),
            "preprocessing_timing": preprocessing,
        }
        return _PreparedSymft(sampler, metadata)
