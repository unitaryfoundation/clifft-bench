"""Clifft aggregate-count adapter."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedClifft(PreparedAdapter):
    def __init__(
        self,
        clifft: Any,
        program: Any,
        observable_index: int,
        runtime_metadata: dict[str, Any],
        batch_size: str | None = None,
    ) -> None:
        self._clifft = clifft
        self._program = program
        self._observable_index = observable_index
        self._batch_size = batch_size
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        kwargs = {"seed": seed, "keep_records": False}
        if self._batch_size is not None:
            kwargs["batch_size"] = self._batch_size
        result = self._clifft.sample_survivors(self._program, shots, **kwargs)
        return Counts(
            attempted_shots=int(result.total_shots),
            accepted_shots=int(result.passed_shots),
            discarded_shots=int(result.discards),
            logical_errors=int(result.observable_ones[self._observable_index]),
        )


class ClifftAdapter(Adapter):
    name = "clifft"

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreparedAdapter:
        reference_convention = str(workload["semantics"]["reference_convention"])
        if reference_convention != "raw-record-parity":
            raise ValueError(
                f"Clifft adapter does not support reference convention "
                f"{reference_convention!r}"
            )
        import clifft

        batch_enabled = bool(execution["batch_enabled"])
        requested_batch_size = execution["batch_size"]
        postselect = bool(workload["semantics"]["postselect_all_detectors"])
        call_batch_size: str | None = None
        if not batch_enabled and requested_batch_size == 1:
            effective_batch_size = 1
        elif batch_enabled and requested_batch_size == "auto":
            if not postselect:
                raise ValueError(
                    "Clifft automatic batching is only supported for postselected "
                    "workloads until Clifft reports its resolved batch size"
                )
            try:
                parameters = inspect.signature(clifft.sample_survivors).parameters
            except (TypeError, ValueError):
                parameters = {}
            documentation = str(getattr(clifft.sample_survivors, "__doc__", ""))
            if "batch_size" not in parameters and "batch_size" not in documentation:
                raise RuntimeError(
                    "installed Clifft does not support batch_size='auto'"
                )
            call_batch_size = "auto"
            # Clifft's current automatic policy keeps postselected plans scalar.
            effective_batch_size = 1
        else:
            raise ValueError(
                "Clifft supports batch_size=1 with batching disabled or "
                "batch_size='auto' with batching enabled"
            )

        if hasattr(clifft, "set_num_threads"):
            clifft.set_num_threads(1)
            if int(clifft.get_num_threads()) != 1:
                raise RuntimeError("Clifft did not accept the one-thread setting")

        text = artifact_path.read_text()
        parse_started = time.perf_counter()
        circuit = clifft.parse(text)
        parse_seconds = time.perf_counter() - parse_started

        compile_started = time.perf_counter()
        hir = clifft.trace(circuit)
        clifft.default_hir_pass_manager().run(hir)
        mask = [1] * int(hir.num_detectors) if postselect else []
        program = clifft.lower(hir, postselection_mask=mask)
        if hasattr(clifft, "default_bytecode_pass_manager"):
            clifft.default_bytecode_pass_manager().run(program)
        compile_seconds = time.perf_counter() - compile_started

        active_history = list(getattr(program, "active_k_history", []))
        peak_active_width = getattr(program, "peak_active_width", None)
        if peak_active_width is None:
            peak_active_width = max(active_history) if active_history else 0

        def circuit_size(name: str) -> int:
            value = getattr(program, name, None)
            if value is None:
                value = getattr(hir, name)
            return int(value)

        metadata = {
            "name": "clifft",
            "version": str(clifft.version()),
            "threads": int(clifft.get_num_threads())
            if hasattr(clifft, "get_num_threads")
            else 1,
            "precision": "complex-fp64",
            "reference_convention": reference_convention,
            "batch_enabled": batch_enabled,
            "effective_batch_size": effective_batch_size,
            "num_qubits": circuit_size("num_qubits"),
            "num_measurements": circuit_size("num_measurements"),
            "num_detectors": circuit_size("num_detectors"),
            "num_observables": circuit_size("num_observables"),
            "peak_active_width": int(peak_active_width),
            "parse_seconds": parse_seconds,
            "compile_seconds": compile_seconds,
            "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
            "sampling_backend": str(clifft.svm_backend())
            if hasattr(clifft, "svm_backend")
            else "symbolic-coordinate",
        }
        return _PreparedClifft(
            clifft,
            program,
            int(workload["semantics"]["observable_index"]),
            metadata,
            call_batch_size,
        )
