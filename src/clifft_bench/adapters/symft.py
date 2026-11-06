"""SymFT aggregate-count adapter."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter
from clifft_bench.selection import require_all_or_none


class _PreparedSymft(PreparedAdapter):
    def __init__(self, sampler: Any, runtime_metadata: dict[str, Any]) -> None:
        self._sampler = sampler
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        result = self._sampler.sample(shots=shots, stream_id=seed)
        if self.runtime_metadata.get("batch_control") == "native-auto":
            active = result["active_threads"]
            if type(active) is not int or not 1 <= active <= self.runtime_metadata["threads"]:
                raise RuntimeError("SymFT returned an invalid active thread count")
            self._active_threads = active
        timing = {key: float(value) for key, value in dict(result.get("timing", {})).items()}
        return Counts(
            attempted_shots=int(result["shots"]),
            accepted_shots=int(result["accepted"]),
            discarded_shots=int(result["discarded"]),
            logical_errors=int(result["logical_errors"]),
            adapter_timing=timing or None,
        )

    def sampling_metadata(self) -> dict[str, Any]:
        return ({"active_threads": self._active_threads}
                if hasattr(self, "_active_threads") else {})


class _PreparedSymftRecords(PreparedAdapter):
    """Count raw observable parity without the counts API's implicit filtering."""

    def __init__(self, sampler, record_indices, metadata):
        self._sampler = sampler
        self._record_indices = record_indices
        self.runtime_metadata = metadata

    def sample(self, shots: int, seed: int) -> Counts:
        import numpy as np

        if shots < 1:
            raise ValueError("SymFT shots per call must be positive")
        records = self._sampler.sample(shots=shots, seed=seed, bit_packed=True)
        width = (self.runtime_metadata["num_measurements"] + 7) // 8
        if records.shape != (shots, width) or records.dtype != np.uint8:
            raise RuntimeError("SymFT must return one packed measurement row per attempted shot")
        parity = np.zeros(shots, dtype=np.uint8)
        for record in self._record_indices:
            parity ^= (records[:, record // 8] >> (record % 8)) & 1
        return Counts(shots, shots, 0, int(np.count_nonzero(parity)))


def _prepare_unselected_records(circuit, execution, observable, symft):
    if not 0 <= observable < circuit.num_observables:
        raise ValueError("observable index is outside the circuit")
    # SymFT's parsed references are absolute, ONE-based measurement indices.
    # Multiple declarations and duplicate references combine by XOR, not OR.
    occurrences = Counter(
        int(record) - 1
        for include in circuit.observables
        if int(include["index"]) == observable
        for record in include["records"]
    )
    if any(not 0 <= record < circuit.num_measurements for record in occurrences):
        raise ValueError("SymFT observable references a measurement outside the circuit")
    record_indices = sorted(record for record, count in occurrences.items() if count % 2)
    batch_size = int(execution["batch_size"])
    batch = batch_size > 1
    chunk = int(execution.get("options", {}).get("sample_chunk_shots", 0))
    if batch_size < 1 or chunk < 0:
        raise ValueError("SymFT requires a positive batch size and nonnegative sample chunk")
    started = time.perf_counter()
    sampler = circuit.compile_sampler(
        batch=batch,
        batch_size=batch_size,
        sample_chunk_shots=chunk,
        cuda=False,
    )
    metadata = {
        "name": "symft",
        "version": str(symft.__version__),
        "threads": 1,
        "precision": "complex-fp64",
        "reference_convention": "raw-record-parity",
        "batch_enabled": batch,
        "effective_batch_size": batch_size if batch else 1,
        "batch_control": "explicit-measurement-api-arguments",
        # The pinned batch measurement API does not use sample_chunk_shots.
        "sample_chunk_shots": 0 if batch else chunk,
        "requested_sample_chunk_shots": chunk,
        "sample_chunk_shots_applicable": not batch,
        "sampling_backend": "compile_sampler",
        "record_materialization": "bit-packed-measurements",
        "count_reduction": "raw-observable-parity-all-shots",
        "seed_policy": "per-call-measurement-seed",
        "num_observables": int(circuit.num_observables),
        "peak_active_width": int(sampler.max_active_qubits),
        "simd_backend": str(symft.simd_backend()),
        "compile_seconds": time.perf_counter() - started,
        **{
            key: int(getattr(sampler, key))
            for key in ("num_qubits", "num_measurements", "num_detectors")
        },
    }
    return _PreparedSymftRecords(sampler, record_indices, metadata)


class SymftAdapter(Adapter):
    name = "symft"
    native_threads = True

    def validate_native_options(self, options: dict[str, Any]) -> None:
        self.validate_options(options)
        if options.get("sample_chunk_shots", 0) != 0:
            raise ValueError("native SymFT requires automatic sample_chunk_shots=0")

    def validate_options(self, options: dict[str, Any]) -> None:
        if options.keys() - {"sample_chunk_shots"}:
            raise ValueError(f"unknown SymFT options: {sorted(options)}")
        chunk = options.get("sample_chunk_shots", 0)
        if type(chunk) is not int or chunk < 0:
            raise ValueError("sample_chunk_shots must be a nonnegative integer")


    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            self.name, expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
        seed: int = 0,
    ) -> PreparedAdapter:
        self.validate_options(execution.get("options", {}))
        threads = self.thread_budget(execution)
        native = execution["batch_size"] == "native"
        if native:
            self.validate_native_options(execution.get("options", {}))
        require_all_or_none(workload["semantics"], self.name)
        import symft

        circuit = symft.Circuit(path=artifact_path)
        observable = int(workload["semantics"]["observable_index"])
        postselect = bool(workload["semantics"]["postselect_all_detectors"])
        if not postselect and circuit.num_detectors:
            if native:
                raise NotImplementedError("SymFT's unselected-record path has no native threading")
            return _prepare_unselected_records(circuit, execution, observable, symft)
        requested_batch_size = 0 if native else int(execution["batch_size"])
        batch = native or requested_batch_size > 1
        chunk = 0 if native else int(execution.get("options", {}).get("sample_chunk_shots", 0))
        sampler = circuit.compile_counts_sampler(
            batch=batch,
            observable=observable,
            postselect_detectors=postselect,
            batch_size=requested_batch_size,
            sample_chunk_shots=chunk,
            threads=threads,
            cuda=False,
        )
        info = dict(sampler.info)
        if not 1 <= int(info["threads"]) <= threads:
            raise RuntimeError(f"SymFT exceeded the thread budget: {info}")
        expected_backend = "batch" if batch else "single"
        if str(info["backend"]) != expected_backend:
            raise RuntimeError(
                f"SymFT backend {info['backend']!r} does not match {expected_backend!r}"
            )
        native_batch_size = int(info["batch_size"])
        if batch and not native and native_batch_size != requested_batch_size:
            raise RuntimeError(
                f"SymFT batch size {info['batch_size']!r} does not match "
                f"requested {requested_batch_size}"
            )
        requested_chunk_shots = chunk
        if not native and int(info["sample_chunk_shots"]) != requested_chunk_shots:
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
            "reference_convention": "raw-record-parity",
            "batch_enabled": batch,
            **({"batch_control": "native-auto"} if native else {}),
            "effective_batch_size": native_batch_size if batch else 1,
            "sample_chunk_shots": int(info["sample_chunk_shots"]),
            "num_qubits": int(info["num_qubits"]),
            "num_measurements": int(info["num_measurements"]),
            "num_detectors": int(info["num_detectors"]),
            "num_observables": int(circuit.num_observables),
            "peak_active_width": int(info["max_active_qubits"]),
            "simd_backend": str(symft.simd_backend()),
            "preprocessing_timing": preprocessing,
            "sampling_backend": "compile_counts_sampler",
            "record_materialization": "native-aggregate-counts",
            "seed_policy": "per-call-counts-stream-id",
        }
        return _PreparedSymft(sampler, metadata)
